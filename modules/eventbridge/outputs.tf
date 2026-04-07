output "rule_arn" {
  description = "ARN of the EventBridge drift check rule"
  value       = aws_cloudwatch_event_rule.drift_check.arn
}

output "rule_name" {
  description = "Name of the EventBridge drift check rule"
  value       = aws_cloudwatch_event_rule.drift_check.name
}
