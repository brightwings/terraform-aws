# modules/step_functions/variables.tf
# Input variables for Step Functions module

variable "name_prefix" {
  description = "Prefix for Step Functions state machine name"
  type        = string
}

variable "execution_role_arn" {
  description = "ARN of IAM role for Step Functions execution"
  type        = string
}

variable "lambda_validate_input_arn" {
  description = "ARN of validate_input Lambda function"
  type        = string
}

variable "lambda_create_record_arn" {
  description = "ARN of create_record Lambda function"
  type        = string
}

variable "lambda_provision_github_arn" {
  description = "ARN of provision_github Lambda function"
  type        = string
}

variable "lambda_finalize_execution_arn" {
  description = "ARN of finalize_execution Lambda function"
  type        = string
}

variable "aws_region" {
  description = "AWS region"
  type        = string
}

variable "aws_account_id" {
  description = "AWS account ID"
  type        = string
}

variable "cloudwatch_log_group_arn" {
  description = "ARN of CloudWatch log group for Step Functions logs"
  type        = string
}

variable "enable_xray_tracing" {
  description = "Enable AWS X-Ray tracing for Step Functions"
  type        = bool
  default     = true
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days"
  type        = number
  default     = 30
}

variable "tags" {
  description = "Common tags for all resources"
  type        = map(string)
  default     = {}
}
