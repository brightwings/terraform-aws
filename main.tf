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

# MIGRATION NOTE: Existing IAM users and groups
# Your existing main.tf has these resources:
# - aws_iam_user.isaac
# - aws_iam_user.nicole
# - aws_iam_user.tf_user_isaac
# - aws_iam_user.tf_user_nicole
# - aws_iam_group.administrators
# - aws_iam_group_policy_attachment.administrators_admin_access (AdministratorAccess - INSECURE!)
#
# TO MIGRATE:
# 1. Keep existing user resources in main.tf (below) for now
# 2. Remove users from 'administrators' group
# 3. Add users to new 'developers' group (from iam module)
# 4. Remove AdministratorAccess policy attachment
# 5. Eventually: Move user definitions to separate users.tf file

# Existing IAM Users (Kept for compatibility during migration)
resource "aws_iam_user" "isaac" {
  name = "isaac"
  path = "/"

  tags = merge(
    local.common_tags,
    {
      Name = "isaac"
    }
  )
}

resource "aws_iam_user" "nicole" {
  name = "nicole"
  path = "/"

  tags = merge(
    local.common_tags,
    {
      Name = "nicole"
    }
  )
}

resource "aws_iam_user" "tf_user_isaac" {
  name = "tf-user-isaac"
  path = "/"

  tags = merge(
    local.common_tags,
    {
      Name    = "tf-user-isaac"
      Purpose = "Terraform automation"
    }
  )
}

resource "aws_iam_user" "tf_user_nicole" {
  name = "tf-user-nicole"
  path = "/"

  tags = merge(
    local.common_tags,
    {
      Name    = "tf-user-nicole"
      Purpose = "Terraform automation"
    }
  )
}

# NEW: Add isaac and nicole to secure developers group
resource "aws_iam_user_group_membership" "isaac_groups" {
  user = aws_iam_user.isaac.name
  groups = [
    module.iam.developers_group_name # Scoped permissions (replaces administrators)
  ]
}

resource "aws_iam_user_group_membership" "nicole_groups" {
  user = aws_iam_user.nicole.name
  groups = [
    module.iam.developers_group_name
  ]
}

# OLD administrators group (kept for reference, but no users assigned)
# SECURITY NOTE: This group has AdministratorAccess (full AWS access)
# We're migrating away from this to the new developers group
resource "aws_iam_group" "administrators" {
  name = "administrators"
  path = "/"
}

# TODO: REMOVE THIS - Insecure AdministratorAccess policy
# Kept temporarily for migration safety
# resource "aws_iam_group_policy_attachment" "administrators_admin_access" {
#   group      = aws_iam_group.administrators.name
#   policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
# }

# Empty developers group (kept for reference)
resource "aws_iam_group" "developers_legacy" {
  name = "developers"
  path = "/"
}

# TODO: Future modules (not implemented yet)
# module "lambda" {
#   source = "./modules/lambda"
#   # Lambda function infrastructure
# }
#
# module "step_functions" {
#   source = "./modules/step_functions"
#   # State machine definitions
# }
