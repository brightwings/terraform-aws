# modules/iam/variables.tf
# Input variables for IAM module

variable "name_prefix" {
  description = "Prefix for all IAM resources (e.g., 'brightwings' or 'saas-automation')"
  type        = string
}

variable "aws_region" {
  description = "AWS region for resource ARNs"
  type        = string
}

variable "aws_account_id" {
  description = "AWS account ID for resource ARNs"
  type        = string
}

variable "provisioning_state_table_arn" {
  description = "ARN of the provisioning state DynamoDB table (for Lambda permissions)"
  type        = string
}

variable "drift_events_table_arn" {
  description = "ARN of the drift events DynamoDB table (for Lambda permissions)"
  type        = string
}

variable "tags" {
  description = "Common tags to apply to all IAM resources"
  type        = map(string)
  default     = {}
}
