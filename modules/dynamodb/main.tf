# modules/dynamodb/main.tf
# Purpose: DynamoDB tables for SaaS provisioning state tracking and drift detection

# Table 1: Provisioning State Tracking
# Stores: Which users have access to which SaaS systems
resource "aws_dynamodb_table" "provisioning_state" {
  name         = var.provisioning_state_table_name
  billing_mode = "PAY_PER_REQUEST" # Pay per request (cost-effective for low volume)

  # Primary key design: user_id (partition) + system (sort)
  # Enables query: "What systems does user X have access to?"
  hash_key  = "user_id" # Canonical user identifier (e.g., "bob")
  range_key = "system"  # SaaS system name (e.g., "github", "aws", "google_workspace")

  # Define attributes used in keys or indexes
  attribute {
    name = "user_id"
    type = "S" # String
  }

  attribute {
    name = "system"
    type = "S" # String
  }

  attribute {
    name = "status"
    type = "S" # String: "pending", "active", "deprovisioned"
  }

  attribute {
    name = "provisioned_at"
    type = "S" # ISO 8601 timestamp
  }

  # Global Secondary Index: Query by status
  # Use case: "Show all pending provisioning requests"
  global_secondary_index {
    name            = "status-index"
    hash_key        = "status"
    range_key       = "provisioned_at"
    projection_type = "ALL" # Include all attributes in index
  }

  # Global Secondary Index: Query by system
  # Use case: "Show all users with GitHub access"
  global_secondary_index {
    name            = "system-index"
    hash_key        = "system"
    range_key       = "provisioned_at"
    projection_type = "ALL"
  }

  # Enable point-in-time recovery (compliance requirement)
  # Allows restore to any point in last 35 days
  point_in_time_recovery {
    enabled = var.enable_point_in_time_recovery
  }

  # Server-side encryption at rest
  server_side_encryption {
    enabled = true
  }

  tags = merge(
    var.tags,
    {
      Name      = var.provisioning_state_table_name
      ManagedBy = "terraform"
      Purpose   = "SaaS provisioning state tracking"
    }
  )
}

# Table 2: Drift Events
# Stores: Configuration drift detection events (expected vs actual state)
resource "aws_dynamodb_table" "drift_events" {
  name         = var.drift_events_table_name
  billing_mode = "PAY_PER_REQUEST"

  # Primary key design: event_id (partition) + detected_at (sort)
  hash_key  = "event_id"    # UUID for each drift event
  range_key = "detected_at" # Timestamp when drift was detected

  attribute {
    name = "event_id"
    type = "S"
  }

  attribute {
    name = "detected_at"
    type = "S"
  }

  attribute {
    name = "drift_type"
    type = "S" # "unauthorized_access", "missing_access", "unknown_account"
  }

  attribute {
    name = "severity"
    type = "S" # "critical", "high", "medium", "low"
  }

  attribute {
    name = "system"
    type = "S"
  }

  attribute {
    name = "resolved"
    type = "S" # "true" or "false"
  }

  # Global Secondary Index: Query by drift type
  global_secondary_index {
    name            = "drift-type-index"
    hash_key        = "drift_type"
    range_key       = "detected_at"
    projection_type = "ALL"
  }

  # Global Secondary Index: Query by severity
  # Use case: "Show all critical drift events"
  global_secondary_index {
    name            = "severity-index"
    hash_key        = "severity"
    range_key       = "detected_at"
    projection_type = "ALL"
  }

  # Global Secondary Index: Query by system
  global_secondary_index {
    name            = "system-index"
    hash_key        = "system"
    range_key       = "detected_at"
    projection_type = "ALL"
  }

  # Global Secondary Index: Query unresolved drifts
  # Use case: "Show all active security issues"
  global_secondary_index {
    name            = "resolved-index"
    hash_key        = "resolved"
    range_key       = "detected_at"
    projection_type = "ALL"
  }

  # Time-to-live: Auto-delete resolved events after retention period
  # Keeps DynamoDB table small, reduces costs
  ttl {
    attribute_name = "ttl"
    enabled        = var.enable_drift_events_ttl
  }

  point_in_time_recovery {
    enabled = var.enable_point_in_time_recovery
  }

  server_side_encryption {
    enabled = true
  }

  tags = merge(
    var.tags,
    {
      Name      = var.drift_events_table_name
      ManagedBy = "terraform"
      Purpose   = "Configuration drift detection and alerting"
    }
  )
}
