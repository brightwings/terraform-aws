# outputs.tf (Root Module)
# Outputs from the SaaS Security Automation Platform

# DynamoDB Table Outputs
output "provisioning_state_table_name" {
  description = "Name of the provisioning state DynamoDB table"
  value       = module.dynamodb.provisioning_state_table_name
}

output "provisioning_state_table_arn" {
  description = "ARN of the provisioning state DynamoDB table"
  value       = module.dynamodb.provisioning_state_table_arn
}

output "drift_events_table_name" {
  description = "Name of the drift events DynamoDB table"
  value       = module.dynamodb.drift_events_table_name
}

output "drift_events_table_arn" {
  description = "ARN of the drift events DynamoDB table"
  value       = module.dynamodb.drift_events_table_arn
}

# IAM Role Outputs
output "lambda_provision_github_role_arn" {
  description = "ARN of the provision_github Lambda execution role"
  value       = module.iam.lambda_provision_github_role_arn
}

output "lambda_detect_drift_role_arn" {
  description = "ARN of the detect_drift Lambda execution role"
  value       = module.iam.lambda_detect_drift_role_arn
}

output "step_functions_execution_role_arn" {
  description = "ARN of the Step Functions execution role"
  value       = module.iam.step_functions_execution_role_arn
}

output "developers_group_name" {
  description = "Name of the developers IAM group (scoped permissions)"
  value       = module.iam.developers_group_name
}

# Environment Information
output "environment" {
  description = "Current environment (dev, staging, prod)"
  value       = var.environment
}

output "aws_region" {
  description = "AWS region where resources are deployed"
  value       = data.aws_region.current.name
}

output "aws_account_id" {
  description = "AWS account ID"
  value       = data.aws_caller_identity.current.account_id
}
