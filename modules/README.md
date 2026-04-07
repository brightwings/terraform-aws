# Terraform Modules - SaaS Security Automation Platform

This directory contains reusable Terraform modules for the SaaS Security Automation Platform.

## Module Architecture

```
modules/
├── dynamodb/          # State tracking tables
├── iam/               # Least privilege roles and policies
├── lambda/            # Lambda function infrastructure (TODO)
└── step_functions/    # State machine definitions (TODO)
```

---

## Module 1: DynamoDB

**Purpose:** Manages DynamoDB tables for provisioning state tracking and drift detection

**Tables Created:**
- `provisioning_state` - Tracks which users have access to which SaaS systems
- `drift_events` - Stores configuration drift detection events

**Inputs:**
- `provisioning_state_table_name` - Name for provisioning state table
- `drift_events_table_name` - Name for drift events table
- `enable_point_in_time_recovery` - Enable backup/restore (compliance)
- `enable_drift_events_ttl` - Auto-delete resolved events after 90 days

**Outputs:**
- `provisioning_state_table_arn` - ARN for IAM policies
- `drift_events_table_arn` - ARN for IAM policies
- Table names for Lambda environment variables

**Example Usage:**
```hcl
module "dynamodb" {
  source = "./modules/dynamodb"

  provisioning_state_table_name = "my-app-provisioning-state"
  drift_events_table_name       = "my-app-drift-events"
  enable_point_in_time_recovery = true
  enable_drift_events_ttl       = true

  tags = {
    Environment = "dev"
    Project     = "MyApp"
  }
}
```

---

## Module 2: IAM

**Purpose:** Creates least privilege IAM roles and policies for Lambda, Step Functions, and human developers

**Resources Created:**

### Lambda Execution Roles:
- `lambda_provision_github` - Role for GitHub provisioning Lambda
  - Permissions: Secrets Manager (GitHub token), DynamoDB (write state), CloudWatch Logs
- `lambda_detect_drift` - Role for drift detection Lambda
  - Permissions: Secrets Manager (SaaS tokens), DynamoDB (read/write), CloudWatch Logs

### Step Functions Role:
- `step_functions_execution` - Role for Step Functions state machines
  - Permissions: Lambda invoke (scoped to specific functions), CloudWatch Logs

### Developer Group:
- `developers` - IAM group for human developers (replaces AdministratorAccess)
  - **ALLOWS:** Create/update IAM users, Lambda functions, DynamoDB tables, Step Functions
  - **DENIES:** Delete operations (explicit DENY prevents privilege escalation)
  - **DENIES:** Billing access, secret values

**Security Principles:**
1. **Least Privilege** - Each role has only the minimum permissions needed
2. **Resource Scoping** - Permissions restricted to specific ARNs, not `*`
3. **Explicit DENY** - Prevents privilege escalation attacks
4. **Defense in Depth** - Multiple layers of security controls

**Inputs:**
- `name_prefix` - Prefix for all IAM resources
- `aws_region` - AWS region for ARN construction
- `aws_account_id` - AWS account ID for ARN construction
- `provisioning_state_table_arn` - DynamoDB table ARN (from dynamodb module)
- `drift_events_table_arn` - DynamoDB table ARN (from dynamodb module)

**Outputs:**
- Lambda role ARNs (for Lambda function definitions)
- Step Functions role ARN (for state machine definitions)
- Developers group name (for user assignments)

**Example Usage:**
```hcl
module "iam" {
  source = "./modules/iam"

  name_prefix    = "my-app"
  aws_region     = "us-east-1"
  aws_account_id = "123456789012"

  provisioning_state_table_arn = module.dynamodb.provisioning_state_table_arn
  drift_events_table_arn       = module.dynamodb.drift_events_table_arn

  tags = {
    Environment = "dev"
  }
}
```

---

## Security Best Practices

### 1. Least Privilege IAM Design

**Problem:** Your current `main.tf` has:
```hcl
resource "aws_iam_group_policy_attachment" "administrators_admin_access" {
  group      = aws_iam_group.administrators.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"  # ⚠️ DANGEROUS
}
```

This grants **full AWS account access** (`Action: "*", Resource: "*"`)

**Solution:** Use the `developers` group from the IAM module:
- Scoped permissions (can create/update, but NOT delete)
- Explicit DENY prevents privilege escalation
- MFA enforcement for sensitive operations (optional)

### 2. Resource-Level Scoping

**Bad:**
```hcl
Action   = ["secretsmanager:GetSecretValue"]
Resource = "*"  # ⚠️ Can read ALL secrets (database passwords, API keys)
```

**Good:**
```hcl
Action   = ["secretsmanager:GetSecretValue"]
Resource = "arn:aws:secretsmanager:*:*:secret:github-api-token-*"  # ✅ Only GitHub token
```

### 3. Explicit DENY for Privilege Escalation Prevention

```hcl
{
  Effect = "Deny"
  Action = ["iam:DeleteUser", "lambda:DeleteFunction", "dynamodb:DeleteTable"]
  Resource = "*"
}
```

Even if a developer adds themselves to another group with delete permissions, this DENY **always wins**.

---

## Migration Guide

### From Monolithic `main.tf` to Modular Structure

**Current structure:**
```
main.tf (100+ lines, everything mixed together)
```

**New structure:**
```
main.tf                # Root orchestration (calls modules)
variables.tf           # Input variables
outputs.tf             # Exported values
modules/
  ├── dynamodb/        # State tracking (isolated)
  ├── iam/             # Roles and policies (isolated)
  ├── lambda/          # Lambda infrastructure (isolated)
  └── step_functions/  # State machines (isolated)
```

**Benefits:**
- ✅ **Isolation:** Changes to DynamoDB don't affect IAM
- ✅ **Reusability:** Use same modules in dev/prod with different inputs
- ✅ **Testing:** Test each module independently
- ✅ **Security Reviews:** Audit IAM module without reading Lambda code

### Migration Steps

1. **Review new structure** (you are here!)
2. **Validate Terraform:** Run `terraform init` and `terraform validate`
3. **Plan changes:** Run `terraform plan` to see what would change
4. **Migrate users:** Move alice/bob from `administrators` to `developers` group
5. **Remove AdministratorAccess:** Comment out insecure policy attachment
6. **Apply changes:** Run `terraform apply` (when ready for AWS deployment)

---

## Future Modules (Phase 2+)

### Lambda Module (Not Implemented Yet)
```hcl
module "lambda" {
  source = "./modules/lambda"

  function_name = "provision-github"
  runtime       = "python3.11"
  handler       = "handler.lambda_handler"
  role_arn      = module.iam.lambda_provision_github_role_arn
  source_code   = "./functions/provision_github"
}
```

### Step Functions Module (Not Implemented Yet)
```hcl
module "step_functions" {
  source = "./modules/step_functions"

  state_machine_name = "onboarding-workflow"
  definition         = file("./state_machines/onboarding.json")
  role_arn           = module.iam.step_functions_execution_role_arn
}
```

---

## Questions?

See the project design doc and CLAUDE.md for architectural context.
