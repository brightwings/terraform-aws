# modules/dynamodb/variables.tf
# Input variables for DynamoDB module

variable "provisioning_state_table_name" {
  description = "Name of the DynamoDB table for provisioning state tracking"
  type        = string
  default     = "saas-provisioning-state"
}

variable "drift_events_table_name" {
  description = "Name of the DynamoDB table for drift events"
  type        = string
  default     = "saas-drift-events"
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

variable "tags" {
  description = "Common tags to apply to all resources"
  type        = map(string)
  default     = {}
}
