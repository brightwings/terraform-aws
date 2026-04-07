variable "name_prefix" {
  description = "Prefix for all resource names"
  type        = string
}

variable "detect_drift_lambda_arn" {
  description = "ARN of the detect_drift Lambda function"
  type        = string
}

variable "detect_drift_lambda_name" {
  description = "Name of the detect_drift Lambda function"
  type        = string
}

variable "drift_check_schedule" {
  description = "Cron expression for drift check schedule"
  type        = string
  default     = "cron(0 9 * * ? *)"
  # Options:
  # "cron(0 9 * * ? *)"   - 9am UTC daily (default)
  # "rate(1 hour)"         - Every hour (expensive at scale)
  # "rate(30 minutes)"     - Every 30 minutes
  # "cron(0 * * * ? *)"   - Top of every hour
}

variable "tags" {
  description = "Tags to apply to all resources"
  type        = map(string)
  default     = {}
}
