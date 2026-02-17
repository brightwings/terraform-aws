# modules/step_functions/main.tf
# Purpose: Step Functions state machine for SaaS onboarding workflow

resource "aws_sfn_state_machine" "onboarding_workflow" {
  name     = "${var.name_prefix}-onboarding-workflow"
  role_arn = var.execution_role_arn

  # State machine definition (JSON)
  definition = templatefile("${path.root}/state_machines/onboarding_workflow.json", {
    # Inject Lambda ARNs dynamically (from IAM module outputs)
    validate_input_arn      = var.lambda_validate_input_arn
    create_record_arn       = var.lambda_create_record_arn
    provision_github_arn    = var.lambda_provision_github_arn
    finalize_execution_arn  = var.lambda_finalize_execution_arn

    # AWS account info
    aws_region     = var.aws_region
    aws_account_id = var.aws_account_id
  })

  # Logging configuration
  logging_configuration {
    log_destination        = "${var.cloudwatch_log_group_arn}:*"
    include_execution_data = true
    level                  = "ALL"  # ALL, ERROR, FATAL, OFF
  }

  # Tracing with X-Ray (for debugging)
  tracing_configuration {
    enabled = var.enable_xray_tracing
  }

  tags = merge(
    var.tags,
    {
      Name      = "${var.name_prefix}-onboarding-workflow"
      ManagedBy = "terraform"
      Purpose   = "SaaS user onboarding orchestration"
    }
  )
}

# CloudWatch Log Group for Step Functions execution logs
resource "aws_cloudwatch_log_group" "step_functions_logs" {
  name              = "/aws/vendedlogs/states/${var.name_prefix}-onboarding-workflow"
  retention_in_days = var.log_retention_days

  tags = merge(
    var.tags,
    {
      Name      = "${var.name_prefix}-step-functions-logs"
      ManagedBy = "terraform"
    }
  )
}
