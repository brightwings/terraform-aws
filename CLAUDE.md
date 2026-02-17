# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Repository Is

A SaaS Security Automation Platform for Brightwings. It automates the full user access lifecycle (onboarding, offboarding, drift detection, telemetry normalization) across GitHub, Slack, AWS IAM, and Jira using Terraform, Step Functions, and Lambda.

**AWS provider profile**: `tf-user-isaac` (must exist in `~/.aws/credentials`)
**AWS provider version**: `~> 6.31.0`
**State**: Local (`terraform.tfstate`) — no remote backend yet

## Commands

```bash
# Terraform
terraform init        # Required after cloning or adding modules
terraform validate    # Check syntax (no AWS calls)
terraform plan        # Preview changes (read-only AWS calls)
terraform apply       # Deploy — requires team design doc approval first

# Lambda local testing (Python 3, requires .venv)
python3 -m venv .venv && .venv/bin/pip install boto3
.venv/bin/python3 functions/<function_name>/handler.py

# Test with mocked boto3
.venv/bin/python3 -c "
import unittest.mock as mock
# ... mock boto3.client, then import and call handler
"
```

**Important**: Do not run `terraform apply` without first getting team sign-off on `DESIGN_DOC.md`. All Lambda API calls are currently mocked — real API calls must be uncommented before deployment.

## Architecture

The platform has three layers:

**1. Terraform (`main.tf` + `modules/`)** — Infrastructure definition only. The four modules are:
- `modules/dynamodb/` — Two tables: `provisioning_state` (user×system access tracking) and `drift_events` (detected mismatches)
- `modules/iam/` — One IAM execution role per Lambda (least privilege), plus the `developers` group replacing `AdministratorAccess`
- `modules/step_functions/` — Step Functions infrastructure (roles, logging)
- `modules/eventbridge/` — Scheduled rule triggering drift detection daily at 9am UTC

**2. Lambda Functions (`functions/`)** — Python business logic, never deployed via Terraform yet. All boto3 API calls to external SaaS systems are mocked with comments showing the real implementation. Functions are grouped by workflow:

- *Onboarding*: `validate_input` → `create_record` → `provision_{github,slack,aws,jira}` (parallel) → `finalize_execution`
- *Offboarding*: `validate_offboarding` → `mark_deprovisioning` → `deprovision_{github,slack,aws,jira}` (parallel) → `finalize_offboarding`
- *Ongoing*: `detect_drift` (called by EventBridge), `normalize_telemetry` (called after every event)

**3. State Machines (`state_machines/`)** — JSON definitions for Step Functions. Both use `Type: Parallel` to run all system branches simultaneously. The offboarding machine uses `Type: Choice` to skip systems where the user was never provisioned.

## Key Design Decisions

**DynamoDB schema**: `user_id` (partition) + `system` (sort) composite key. Status flow: `pending → active → deprovisioning → deprovisioned`. The `mark_deprovisioning` Lambda uses a conditional update (`ConditionExpression='status = :active'`) to prevent concurrent offboarding workflows.

**Deprovisioning order**: Teams/groups are removed before account deactivation. If the final account removal fails, the user has already lost resource access. This is intentional (defense in depth).

**Idempotency**: Every Lambda checks current state before acting. If the user is already in the desired state, it returns success without calling external APIs. This makes Step Functions automatic retries safe.

**GitHub access model**: `provision_github` adds users to the org with `team_ids=[]` — zero repo access by default. Repo access requires a separate team assignment step (not yet implemented).

**Telemetry normalization**: `normalize_telemetry` maps all internal events to ECS schema (`event.action`, `event.severity` 0-100, `event.outcome`). Severity ≥90 = page on-call; 50-89 = ticket; <50 = log only.

**Mock vs real API calls**: Real GitHub, Jira, and Slack API calls are commented out in every Lambda with `# In production:` blocks showing the actual `requests` library implementation. Unmock before deploying.

## Existing IAM Users (Pre-Automation)

`isaac`, `nicole`, `tf-user-isaac`, `tf-user-nicole` are defined directly in `main.tf` (not in a module) for migration safety. They are assigned to `module.iam.developers_group_name` — the new least-privilege group — not the legacy `administrators` group.

The `administrators` group resource is kept in `main.tf` but has no policy attachment and no members.
