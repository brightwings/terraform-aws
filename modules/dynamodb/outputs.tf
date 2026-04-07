# modules/dynamodb/outputs.tf
# Outputs from DynamoDB module (used by other modules and root config)

# Provisioning State Table Outputs
output "provisioning_state_table_name" {
  description = "Name of the provisioning state DynamoDB table"
  value       = aws_dynamodb_table.provisioning_state.name
}

output "provisioning_state_table_arn" {
  description = "ARN of the provisioning state DynamoDB table (used for IAM policies)"
  value       = aws_dynamodb_table.provisioning_state.arn
}

output "provisioning_state_table_id" {
  description = "ID of the provisioning state DynamoDB table"
  value       = aws_dynamodb_table.provisioning_state.id
}

# Drift Events Table Outputs
output "drift_events_table_name" {
  description = "Name of the drift events DynamoDB table"
  value       = aws_dynamodb_table.drift_events.name
}

output "drift_events_table_arn" {
  description = "ARN of the drift events DynamoDB table (used for IAM policies)"
  value       = aws_dynamodb_table.drift_events.arn
}

output "drift_events_table_id" {
  description = "ID of the drift events DynamoDB table"
  value       = aws_dynamodb_table.drift_events.id
}
