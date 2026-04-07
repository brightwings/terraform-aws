# main.tf (Root Module)
# Purpose: Orchestrates all modules for SaaS Security Automation Platform
# This is the "main" configuration that calls all the reusable modules

terraform {
  required_version = ">= 1.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.31.0"
    }
  }
}

provider "aws" {
  profile = var.aws_profile
  region  = var.aws_region
}

# Data source: Get current AWS account ID and region
data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# Local variables for reusability
locals {
  name_prefix    = var.environment == "prod" ? "saas-automation" : "saas-automation-${var.environment}"
  aws_account_id = data.aws_caller_identity.current.account_id
  aws_region     = data.aws_region.current.name

  common_tags = {
    Environment = var.environment
    Project     = "SaaS Security Automation Platform"
    ManagedBy   = "terraform"
    Owner       = "security-team"
  }
}

# Module 1: DynamoDB Tables (State Tracking)
module "dynamodb" {
  source = "./modules/dynamodb"

  provisioning_state_table_name = "${local.name_prefix}-provisioning-state"
  drift_events_table_name       = "${local.name_prefix}-drift-events"
  enable_point_in_time_recovery = var.enable_point_in_time_recovery
  enable_drift_events_ttl       = var.enable_drift_events_ttl

  tags = local.common_tags
}

# Module 2: IAM Roles and Policies (Least Privilege)
module "iam" {
  source = "./modules/iam"

  name_prefix    = local.name_prefix
  aws_region     = local.aws_region
  aws_account_id = local.aws_account_id

  # Pass DynamoDB table ARNs from dynamodb module outputs
  provisioning_state_table_arn = module.dynamodb.provisioning_state_table_arn
  drift_events_table_arn       = module.dynamodb.drift_events_table_arn

  tags = local.common_tags

  # Dependency: IAM module needs DynamoDB tables to exist first (for ARNs)
  depends_on = [module.dynamodb]
}

# =============================================================================
# IAM Users (Human and Automation)
# =============================================================================
# Human users (alice, bob) are assigned to the developers group
# Automation user (tf-user-saas-automation) is assigned to terraform_automation group
resource "aws_iam_user" "alice" {
  name = "alice"
  path = "/"

  tags = merge(
    local.common_tags,
    {
      Name = "alice"
    }
  )
}

resource "aws_iam_user" "bob" {
  name = "bob"
  path = "/"

  tags = merge(
    local.common_tags,
    {
      Name = "bob"
    }
  )
}

resource "aws_iam_user" "tf_user_saas_automation" {
  name = "tf-user-saas-automation"
  path = "/"

  tags = merge(
    local.common_tags,
    {
      Name    = "tf-user-saas-automation"
      Purpose = "Terraform automation for SaaS Security Automation Platform"
    }
  )
}

resource "aws_iam_user_group_membership" "tf_user_saas_automation_groups" {
  user   = aws_iam_user.tf_user_saas_automation.name
  groups = [module.iam.terraform_automation_group_name]
}

# tf-user-alice and tf-user-bob removed - replaced by tf-user-saas-automation

# NEW: Add alice and bob to secure developers group
resource "aws_iam_user_group_membership" "alice_groups" {
  user = aws_iam_user.alice.name
  groups = [
    module.iam.developers_group_name # Scoped permissions (replaces administrators)
  ]
}

resource "aws_iam_user_group_membership" "bob_groups" {
  user = aws_iam_user.bob.name
  groups = [
    module.iam.developers_group_name
  ]
}

# OLD administrators group (kept for break-glass emergency access)
# SECURITY NOTE: This group has AdministratorAccess (full AWS access)
# Should only be used temporarily with explicit approval
resource "aws_iam_group" "administrators" {
  name = "administrators"
  path = "/"
}

# AdministratorAccess policy attachment - commented out for security
# Uncomment only for break-glass emergency access
# resource "aws_iam_group_policy_attachment" "administrators_admin_access" {
#   group      = aws_iam_group.administrators.name
#   policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
# }

# Empty developers group (kept for reference)
resource "aws_iam_group" "developers_legacy" {
  name = "developers"
  path = "/"
}

# =============================================================================
# Secrets Manager
# =============================================================================

resource "aws_secretsmanager_secret" "github_token" {
  name        = "${local.name_prefix}-github-api-token"
  description = "GitHub personal access token for SaaS automation"
  tags        = local.common_tags
}

# NOTE: Secret VALUE must be populated manually after deployment.
# Terraform creates the secret container but does NOT manage the value
# (this prevents tf-user-saas-automation from needing GetSecretValue permission).
#
# To populate the secret:
# aws secretsmanager put-secret-value \
#   --secret-id ${local.name_prefix}-github-api-token \
#   --secret-string '{"token": "ghp_YOUR_GITHUB_TOKEN_HERE"}' \
#   --profile <your-admin-profile>

# =============================================================================
# SNS - Security Alerts
# =============================================================================

resource "aws_sns_topic" "security_alerts" {
  name = "${local.name_prefix}-security-alerts"
  tags = local.common_tags
}

# =============================================================================
# Lambda Functions - Onboarding
# =============================================================================

locals {
  common_lambda_env = {
    PROVISIONING_STATE_TABLE = module.dynamodb.provisioning_state_table_name
    NAME_PREFIX              = local.name_prefix
  }
  github_lambda_env = merge(local.common_lambda_env, {
    GITHUB_SECRET_ARN = aws_secretsmanager_secret.github_token.arn
    GITHUB_ORG        = "example-corp"
  })
}

module "lambda_validate_input" {
  source             = "./modules/lambda"
  function_name      = "${local.name_prefix}-validate-input"
  source_dir         = "${path.module}/functions/validate_input"
  execution_role_arn = module.iam.lambda_basic_execution_role_arn
  environment_variables = local.common_lambda_env
  tags               = local.common_tags
}

module "lambda_create_record" {
  source             = "./modules/lambda"
  function_name      = "${local.name_prefix}-create-record"
  source_dir         = "${path.module}/functions/create_record"
  execution_role_arn = module.iam.lambda_basic_execution_role_arn
  environment_variables = local.common_lambda_env
  tags               = local.common_tags
}

module "lambda_provision_github" {
  source             = "./modules/lambda"
  function_name      = "${local.name_prefix}-provision-github"
  source_dir         = "${path.module}/functions/provision_github"
  execution_role_arn = module.iam.lambda_provision_github_role_arn
  environment_variables = local.github_lambda_env
  tags               = local.common_tags
}

module "lambda_provision_slack" {
  source             = "./modules/lambda"
  function_name      = "${local.name_prefix}-provision-slack"
  source_dir         = "${path.module}/functions/provision_slack"
  execution_role_arn = module.iam.lambda_basic_execution_role_arn
  environment_variables = local.common_lambda_env
  tags               = local.common_tags
}

module "lambda_provision_aws" {
  source             = "./modules/lambda"
  function_name      = "${local.name_prefix}-provision-aws"
  source_dir         = "${path.module}/functions/provision_aws"
  execution_role_arn = module.iam.lambda_provision_aws_role_arn
  environment_variables = local.common_lambda_env
  tags               = local.common_tags
}

module "lambda_provision_jira" {
  source             = "./modules/lambda"
  function_name      = "${local.name_prefix}-provision-jira"
  source_dir         = "${path.module}/functions/provision_jira"
  execution_role_arn = module.iam.lambda_basic_execution_role_arn
  environment_variables = local.common_lambda_env
  tags               = local.common_tags
}

module "lambda_finalize_execution" {
  source             = "./modules/lambda"
  function_name      = "${local.name_prefix}-finalize-execution"
  source_dir         = "${path.module}/functions/finalize_execution"
  execution_role_arn = module.iam.lambda_basic_execution_role_arn
  environment_variables = local.common_lambda_env
  tags               = local.common_tags
}

# =============================================================================
# Lambda Functions - Offboarding
# =============================================================================

module "lambda_validate_offboarding" {
  source             = "./modules/lambda"
  function_name      = "${local.name_prefix}-validate-offboarding"
  source_dir         = "${path.module}/functions/validate_offboarding"
  execution_role_arn = module.iam.lambda_basic_execution_role_arn
  environment_variables = local.common_lambda_env
  tags               = local.common_tags
}

module "lambda_mark_deprovisioning" {
  source             = "./modules/lambda"
  function_name      = "${local.name_prefix}-mark-deprovisioning"
  source_dir         = "${path.module}/functions/mark_deprovisioning"
  execution_role_arn = module.iam.lambda_basic_execution_role_arn
  environment_variables = local.common_lambda_env
  tags               = local.common_tags
}

module "lambda_deprovision_github" {
  source             = "./modules/lambda"
  function_name      = "${local.name_prefix}-deprovision-github"
  source_dir         = "${path.module}/functions/deprovision_github"
  execution_role_arn = module.iam.lambda_provision_github_role_arn
  environment_variables = local.github_lambda_env
  tags               = local.common_tags
}

module "lambda_deprovision_slack" {
  source             = "./modules/lambda"
  function_name      = "${local.name_prefix}-deprovision-slack"
  source_dir         = "${path.module}/functions/deprovision_slack"
  execution_role_arn = module.iam.lambda_basic_execution_role_arn
  environment_variables = local.common_lambda_env
  tags               = local.common_tags
}

module "lambda_deprovision_aws" {
  source             = "./modules/lambda"
  function_name      = "${local.name_prefix}-deprovision-aws"
  source_dir         = "${path.module}/functions/deprovision_aws"
  execution_role_arn = module.iam.lambda_provision_aws_role_arn
  environment_variables = local.common_lambda_env
  tags               = local.common_tags
}

module "lambda_deprovision_jira" {
  source             = "./modules/lambda"
  function_name      = "${local.name_prefix}-deprovision-jira"
  source_dir         = "${path.module}/functions/deprovision_jira"
  execution_role_arn = module.iam.lambda_basic_execution_role_arn
  environment_variables = local.common_lambda_env
  tags               = local.common_tags
}

module "lambda_finalize_offboarding" {
  source             = "./modules/lambda"
  function_name      = "${local.name_prefix}-finalize-offboarding"
  source_dir         = "${path.module}/functions/finalize_offboarding"
  execution_role_arn = module.iam.lambda_basic_execution_role_arn
  environment_variables = local.common_lambda_env
  tags               = local.common_tags
}

# =============================================================================
# Lambda Functions - Ongoing
# =============================================================================

module "lambda_detect_drift" {
  source             = "./modules/lambda"
  function_name      = "${local.name_prefix}-detect-drift"
  source_dir         = "${path.module}/functions/detect_drift"
  execution_role_arn = module.iam.lambda_detect_drift_role_arn
  environment_variables = merge(local.github_lambda_env, {
    DRIFT_EVENTS_TABLE   = module.dynamodb.drift_events_table_name
    DRIFT_ALERT_SNS_ARN  = aws_sns_topic.security_alerts.arn
  })
  tags = local.common_tags
}

module "lambda_normalize_telemetry" {
  source             = "./modules/lambda"
  function_name      = "${local.name_prefix}-normalize-telemetry"
  source_dir         = "${path.module}/functions/normalize_telemetry"
  execution_role_arn = module.iam.lambda_basic_execution_role_arn
  environment_variables = local.common_lambda_env
  tags               = local.common_tags
}

# =============================================================================
# Step Functions State Machines
# =============================================================================

resource "aws_sfn_state_machine" "onboarding" {
  name     = "${local.name_prefix}-onboarding"
  role_arn = module.iam.step_functions_execution_role_arn

  definition = templatefile("${path.module}/state_machines/onboarding_workflow.json.tftpl", {
    validate_input_arn    = module.lambda_validate_input.arn
    create_record_arn     = module.lambda_create_record.arn
    provision_github_arn  = module.lambda_provision_github.arn
    provision_slack_arn   = module.lambda_provision_slack.arn
    provision_aws_arn     = module.lambda_provision_aws.arn
    provision_jira_arn    = module.lambda_provision_jira.arn
    finalize_execution_arn = module.lambda_finalize_execution.arn
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.step_functions.arn}:*"
    include_execution_data = true
    level                  = "ERROR"
  }

  tags = local.common_tags
}

resource "aws_sfn_state_machine" "offboarding" {
  name     = "${local.name_prefix}-offboarding"
  role_arn = module.iam.step_functions_execution_role_arn

  definition = templatefile("${path.module}/state_machines/offboarding_workflow.json.tftpl", {
    validate_offboarding_arn  = module.lambda_validate_offboarding.arn
    mark_deprovisioning_arn   = module.lambda_mark_deprovisioning.arn
    deprovision_github_arn    = module.lambda_deprovision_github.arn
    deprovision_slack_arn     = module.lambda_deprovision_slack.arn
    deprovision_aws_arn       = module.lambda_deprovision_aws.arn
    deprovision_jira_arn      = module.lambda_deprovision_jira.arn
    finalize_offboarding_arn  = module.lambda_finalize_offboarding.arn
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.step_functions.arn}:*"
    include_execution_data = true
    level                  = "ERROR"
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_log_group" "step_functions" {
  name              = "/aws/states/${local.name_prefix}"
  retention_in_days = 30
  tags              = local.common_tags
}

# =============================================================================
# EventBridge - Drift Detection Schedule
# =============================================================================

module "eventbridge" {
  source = "./modules/eventbridge"

  name_prefix              = local.name_prefix
  detect_drift_lambda_arn  = module.lambda_detect_drift.arn
  detect_drift_lambda_name = module.lambda_detect_drift.name
  drift_check_schedule     = "cron(0 9 * * ? *)"

  tags = local.common_tags
}
