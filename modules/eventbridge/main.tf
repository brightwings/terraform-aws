# =============================================================================
# EventBridge Scheduled Rule - Drift Detection
# =============================================================================
#
# Architecture:
# EventBridge (scheduled) → Lambda (detect_drift)
#
# Why EventBridge vs cron on Lambda?
# - No server to manage (serverless scheduling)
# - Reliable delivery (AWS-managed, survives Lambda cold starts)
# - Built-in monitoring (CloudWatch metrics)
# - Easy to disable without code changes (rule enable/disable)
# - Retry on failure (configurable dead-letter queue)
#
# =============================================================================

# Scheduled rule that fires hourly
resource "aws_cloudwatch_event_rule" "drift_check" {
  name                = "${var.name_prefix}-drift-check"
  description         = "Trigger drift detection Lambda hourly to detect configuration drift"
  schedule_expression = var.drift_check_schedule

  # Security: Rule is enabled by default
  # Can be disabled via AWS Console or Terraform without deleting
  state = "ENABLED"

  tags = merge(var.tags, {
    Name    = "${var.name_prefix}-drift-check"
    Purpose = "drift-detection"
  })
}

# Target: EventBridge → detect_drift Lambda
resource "aws_cloudwatch_event_target" "detect_drift" {
  rule = aws_cloudwatch_event_rule.drift_check.name
  arn  = var.detect_drift_lambda_arn

  # Unique target ID within the rule
  target_id = "detect-drift-lambda"

  # Optional: transform the event before sending to Lambda
  # This adds context about the trigger source
  input = jsonencode({
    source      = "saas-automation.drift-detection"
    detail-type = "Scheduled Drift Check"
    detail = {
      trigger    = "eventbridge-scheduled"
      schedule   = var.drift_check_schedule
      automation = "${var.name_prefix}"
    }
  })
}

# Permission: Allow EventBridge to invoke the Lambda
# Why this is needed:
# - Lambda functions are locked down by default
# - EventBridge needs explicit permission to invoke
# - Scoped to ONLY this specific EventBridge rule (not all EventBridge)
resource "aws_lambda_permission" "eventbridge_invoke_detect_drift" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = var.detect_drift_lambda_name
  principal     = "events.amazonaws.com"

  # Security: Scoped to specific rule ARN (not all EventBridge)
  # Prevents other EventBridge rules from invoking this Lambda
  source_arn = aws_cloudwatch_event_rule.drift_check.arn
}
