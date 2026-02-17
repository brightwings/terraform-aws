# variables.tf (Root Module)
# Input variables for the entire SaaS Security Automation Platform

variable "aws_profile" {
  description = "AWS CLI profile to use for authentication"
  type        = string
  default     = "tf-user-isaac"
}

variable "aws_region" {
  description = "AWS region to deploy resources"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "Environment must be dev, staging, or prod."
  }
}

variable "enable_point_in_time_recovery" {
  description = "Enable point-in-time recovery for DynamoDB tables (compliance requirement)"
  type        = bool
  default     = true
}

variable "enable_drift_events_ttl" {
  description = "Enable TTL for drift events table (auto-delete resolved events after 90 days)"
  type        = bool
  default     = true
}
