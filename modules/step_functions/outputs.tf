# modules/step_functions/outputs.tf
# Outputs from Step Functions module

output "state_machine_arn" {
  description = "ARN of the Step Functions state machine"
  value       = aws_sfn_state_machine.onboarding_workflow.arn
}

output "state_machine_name" {
  description = "Name of the Step Functions state machine"
  value       = aws_sfn_state_machine.onboarding_workflow.name
}

output "state_machine_id" {
  description = "ID of the Step Functions state machine"
  value       = aws_sfn_state_machine.onboarding_workflow.id
}

output "log_group_name" {
  description = "Name of the CloudWatch log group for Step Functions logs"
  value       = aws_cloudwatch_log_group.step_functions_logs.name
}

output "log_group_arn" {
  description = "ARN of the CloudWatch log group for Step Functions logs"
  value       = aws_cloudwatch_log_group.step_functions_logs.arn
}
