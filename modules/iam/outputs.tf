# modules/iam/outputs.tf
# Outputs from IAM module

# Lambda Execution Role Outputs
output "lambda_provision_github_role_arn" {
  description = "ARN of the provision_github Lambda execution role"
  value       = aws_iam_role.lambda_provision_github.arn
}

output "lambda_provision_github_role_name" {
  description = "Name of the provision_github Lambda execution role"
  value       = aws_iam_role.lambda_provision_github.name
}

output "lambda_detect_drift_role_arn" {
  description = "ARN of the detect_drift Lambda execution role"
  value       = aws_iam_role.lambda_detect_drift.arn
}

output "lambda_detect_drift_role_name" {
  description = "Name of the detect_drift Lambda execution role"
  value       = aws_iam_role.lambda_detect_drift.name
}

# Step Functions Role Output
output "step_functions_execution_role_arn" {
  description = "ARN of the Step Functions execution role"
  value       = aws_iam_role.step_functions_execution.arn
}

output "step_functions_execution_role_name" {
  description = "Name of the Step Functions execution role"
  value       = aws_iam_role.step_functions_execution.name
}

# Developer Group Output
output "developers_group_name" {
  description = "Name of the developers IAM group"
  value       = aws_iam_group.developers.name
}

output "developers_group_arn" {
  description = "ARN of the developers IAM group"
  value       = aws_iam_group.developers.arn
}
