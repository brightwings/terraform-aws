# SaaS Security Automation Platform

Automates the full user access lifecycle — onboarding, offboarding, and drift detection — across GitHub, Slack, AWS IAM, and Jira. Built with Terraform, AWS Step Functions, and Python Lambda functions.

## Overview

Manual SaaS provisioning creates three security risks: orphaned access when people leave, configuration drift when access is changed outside the system, and no centralized audit trail across systems. This platform eliminates all three.

When a new user joins, a single trigger provisions all four systems in parallel and records state in DynamoDB. When they leave, the same state is used to deprovision only the systems they actually had access to. A daily EventBridge job detects drift between expected and actual state and raises alerts.

## Architecture

```
                        ┌─────────────────────────────────┐
                        │         EventBridge              │
                        │     (daily drift check)          │
                        └────────────┬────────────────────┘
                                     │
                                     ▼
┌──────────────────────────────────────────────────────────────────┐
│                        Step Functions                             │
│                                                                   │
│  Onboarding:                        Offboarding:                 │
│  validate_input                     validate_offboarding          │
│       ↓                                  ↓                       │
│  create_record                      mark_deprovisioning           │
│       ↓                             (conditional, atomic)         │
│  ┌────┴────────────────────┐             ↓                       │
│  │ provision_github        │   ┌─────────┴──────────────┐        │
│  │ provision_slack     (∥) │   │ deprovision_github      │       │
│  │ provision_aws           │   │ deprovision_slack   (∥) │       │
│  │ provision_jira          │   │ deprovision_aws         │       │
│  └────────────────────┬────┘   │ deprovision_jira        │       │
│                       ↓        └──────────┬─────────────┘        │
│                 finalize_execution         ↓                      │
│                       ↓           finalize_offboarding            │
│                 normalize_telemetry ←──────┘                      │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                 ┌────────────────────────┐
                 │        DynamoDB         │
                 │  provisioning_state     │  user_id (PK) + system (SK)
                 │  drift_events           │  event_id (PK) + detected_at (SK)
                 └────────────────────────┘
```

## Tech Stack

- **Infrastructure**: Terraform (modular, local state)
- **Orchestration**: AWS Step Functions (parallel branches, automatic retries)
- **Compute**: AWS Lambda (Python 3)
- **State**: DynamoDB (composite keys, conditional writes, TTL)
- **Scheduling**: EventBridge (daily drift detection at 9am UTC)
- **IAM**: Least-privilege execution roles per Lambda, explicit DENY policies

## Project Structure

```
├── main.tf                          # Root module — wires everything together
├── variables.tf / outputs.tf
├── modules/
│   ├── dynamodb/                    # State tracking tables
│   ├── iam/                         # Lambda execution roles, developer group
│   ├── lambda/                      # Lambda packaging + deployment resources
│   ├── step_functions/              # State machine infrastructure
│   └── eventbridge/                 # Drift detection schedule
├── functions/
│   ├── validate_input/              # Onboarding: input validation
│   ├── create_record/               # Onboarding: DynamoDB write
│   ├── provision_{github,slack,aws,jira}/   # Onboarding: parallel provisioning
│   ├── finalize_execution/          # Onboarding: result aggregation
│   ├── validate_offboarding/        # Offboarding: query active systems
│   ├── mark_deprovisioning/         # Offboarding: atomic status lock
│   ├── deprovision_{github,slack,aws,jira}/ # Offboarding: parallel removal
│   ├── finalize_offboarding/        # Offboarding: audit + alerting
│   ├── detect_drift/                # Ongoing: compare expected vs actual state
│   └── normalize_telemetry/         # Ongoing: ECS schema normalization
└── state_machines/
    ├── onboarding_workflow.json
    └── offboarding_workflow.json
```

## Key Design Decisions

**Idempotency** — Every Lambda checks current state before calling external APIs. If the user is already in the target state, it returns success immediately. This makes Step Functions automatic retries safe with no side effects.

**Atomic status transitions** — `mark_deprovisioning` uses a DynamoDB `ConditionExpression` (`status = :active`) to prevent two concurrent offboarding workflows from racing on the same user.

**Defense in depth deprovisioning** — Team/group membership is removed before account deactivation. If the final account removal fails, the user has already lost access to all resources. This is intentional.

**Deactivate, don't delete** — Accounts are deactivated rather than deleted to preserve audit history (CloudTrail, Jira issues, Slack messages) for compliance and forensic investigation.

**Telemetry normalization** — All events are mapped to ECS schema (`event.action`, `event.severity` 0–100, `event.outcome`). Severity ≥90 pages on-call; 50–89 creates a ticket; <50 logs only.

**Drift detection** — EventBridge triggers a daily comparison of DynamoDB state (expected) against live API state (actual) for each system. Mismatches are written to the `drift_events` table with TTL-based cleanup after 90 days.

**Scoped IAM** — Each Lambda has its own execution role with resource-level restrictions (e.g., `arn:aws:secretsmanager:*:*:secret:github-api-token-*`). A separate `developers` group replaces `AdministratorAccess` with explicit DENY policies to prevent privilege escalation.

## Running Locally

```bash
# Validate infrastructure (no AWS calls)
terraform init
terraform validate
terraform plan

# Test a Lambda function directly
python3 -m venv .venv && .venv/bin/pip install boto3
.venv/bin/python3 functions/validate_input/handler.py

# Run tests with mocked boto3
.venv/bin/python3 functions/validate_input/test_handler.py
```

> All external API calls (GitHub, Slack, Jira) are mocked in the Lambda handlers. Each has a `# In production:` comment block showing the real `requests` implementation.

## Status

Infrastructure is fully defined and validated. Lambda business logic is complete with mocked external calls. Not yet deployed — `terraform apply` requires sign-off on `DESIGN_DOC.md` and real API credentials wired in before running.
