# Design Doc: SaaS Security Automation Platform
**Status:** Draft — Pending Team Review
**Author:** Isaac
**Last Updated:** 2026-02-17
**Review Required Before:** `terraform apply`

---

## Problem Statement

Brightwings currently provisions and deprovisions SaaS access manually. This creates three risks:

1. **Orphaned access** — Offboarded employees may retain access to GitHub, Jira, or AWS if any step is missed. There is no centralized record of who has access to what.
2. **No drift detection** — If someone manually modifies access outside of process (adds themselves to an admin group, removes a user from GitHub), there is no mechanism to detect or alert on it.
3. **Audit gaps** — SOC2 and ISO 27001 require evidence that access was revoked promptly upon termination. Manual processes produce inconsistent records.

**This platform automates the full user access lifecycle** across GitHub, Slack, AWS, and Jira with centralized state tracking, drift detection, and normalized security telemetry.

---

## Architecture Overview

```
                    ┌─────────────────────────────────────┐
                    │         Trigger (Manual/API)         │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │         AWS Step Functions           │
                    │   Onboarding / Offboarding Workflow  │
                    └──┬──────┬──────┬──────┬─────────────┘
                       │      │      │      │
              ┌────────▼─┐ ┌──▼───┐ ┌▼───┐ ┌▼────┐
              │  GitHub  │ │Slack │ │ AWS│ │Jira │  ← Lambda per system
              └────────┬─┘ └──┬───┘ └┬───┘ └┬────┘
                       │      │      │      │
                    ┌──▼──────▼──────▼──────▼──────┐
                    │         DynamoDB              │
                    │   provisioning_state table    │
                    │   drift_events table          │
                    └──────────────┬────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │  EventBridge (daily 9am UTC)         │
                    │         detect_drift Lambda          │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │      normalize_telemetry Lambda      │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │     CloudWatch Logs → (future SIEM)  │
                    └─────────────────────────────────────┘
```

---

## AWS Services Used

| Service | Purpose | Why This Service |
|---------|---------|-----------------|
| **Step Functions** | Workflow orchestration | Built-in retry, parallel execution, audit trail, no custom state management code |
| **Lambda** | Business logic | Serverless, pay-per-use, scales to zero (we run infrequently) |
| **DynamoDB** | State tracking | Serverless, fast key-value lookups, TTL for auto-expiring old drift events |
| **EventBridge** | Scheduled drift check | Managed cron, no server to maintain, $0 cost for one scheduled rule |
| **Secrets Manager** | API tokens (GitHub, Jira) | Encrypted at rest, automatic rotation support, IAM-controlled access |
| **SNS** | Alert delivery | Simple fan-out to email/PagerDuty/Slack; decouples alert logic from Lambda code |
| **CloudWatch Logs** | Telemetry + debugging | Already included with Lambda, structured JSON logs queryable via Logs Insights |
| **IAM** | Least privilege roles | One role per Lambda, resource-level scoping, no shared credentials |

---

## What Gets Deployed

### Lambda Functions (11)

**Onboarding workflow:**
- `validate-input` — validates user_id, email domain, role, systems list
- `create-record` — writes pending provisioning records to DynamoDB
- `provision-github` — adds user to GitHub org (no repo access by default)
- `provision-slack` — invites user to Slack workspace
- `provision-aws` — creates IAM user, assigns to role-appropriate group
- `provision-jira` — creates user in Jira, assigns to groups
- `finalize-execution` — updates DynamoDB status to active, logs completion

**Offboarding workflow:**
- `validate-offboarding` — validates request, queries DynamoDB for active systems
- `mark-deprovisioning` — atomic status update to prevent concurrent workflows
- `deprovision-github` — removes from teams then org (defense-in-depth ordering)
- `deprovision-slack` — deactivates account (preserves message history)
- `deprovision-aws` — removes from groups, deletes access keys, deletes login profile
- `deprovision-jira` — removes from groups, deactivates account
- `finalize-offboarding` — aggregates results, logs security event

**Ongoing:**
- `detect-drift` — daily comparison of DynamoDB state vs actual API state
- `normalize-telemetry` — maps all events to ECS schema for SIEM consumption

### DynamoDB Tables (2)

**`saas-automation-dev-provisioning-state`**
- Tracks user access per system (`user_id` + `system` composite key)
- Status flow: `pending` → `active` → `deprovisioning` → `deprovisioned`
- GSIs: status-index (for drift detection bulk queries)

**`saas-automation-dev-drift-events`**
- Records every detected configuration drift
- Fields: drift_type, severity, expected_state, actual_state, remediation
- TTL: auto-delete resolved events after 90 days

### Step Functions State Machines (2)

**Onboarding:** 8-state machine, parallel provisioning across all systems
**Offboarding:** 13-state machine, parallel deprovisioning with concurrent-operation guard

### Secrets Manager Secrets (2)
- `github-api-token` — GitHub personal access token with org admin scope
- `jira-api-token` — Jira API token with user management permissions

### EventBridge Rule (1)
- Schedule: `cron(0 9 * * ? *)` — daily at 9am UTC
- Target: `detect-drift` Lambda

### SNS Topic (1)
- `saas-automation-dev-security-alerts`
- Subscribers: security team email (to be configured post-deploy)

---

## Security Model

### Principle: Least Privilege Per Lambda

Each Lambda has its own IAM execution role scoped to only what it needs. No shared roles.

| Lambda | Secrets Manager | DynamoDB | IAM | SNS |
|--------|----------------|----------|-----|-----|
| provision-github | `github-api-token` only | PutItem, UpdateItem | — | — |
| provision-aws | — | PutItem, UpdateItem | CreateUser, AddUserToGroup | — |
| detect-drift | `github-api-token` | Query (read-only) | GetUser, ListGroupsForUser | Publish |
| normalize-telemetry | — | PutItem | — | — |

### Principle: No Wildcard Resources

```hcl
# Bad (what we're NOT doing)
Resource = "*"

# Good (what we ARE doing)
Resource = "arn:aws:secretsmanager:us-east-1:YOUR_AWS_ACCOUNT_ID:secret:github-api-token-*"
Resource = "arn:aws:dynamodb:us-east-1:YOUR_AWS_ACCOUNT_ID:table/saas-automation-dev-*"
```

### Principle: Defense-in-Depth for Offboarding

Deprovisioning order removes access at the most granular level first:

```
GitHub:  teams → org membership
Jira:    groups → account deactivation
AWS:     IAM groups → access keys → login profile → tagged for deletion
```

If the final step fails (API error), the user has already lost access at every intermediate level.

### Principle: Concurrent Operation Prevention

`mark-deprovisioning` uses a DynamoDB conditional update that only succeeds if `status = active`. This ensures two simultaneous offboarding workflows for the same user cannot both proceed — one wins, one fails fast.

---

## Cost Estimate

All estimates based on ~50 users, low provisioning volume (dev/learning environment).

| Service | Usage | Monthly Cost |
|---------|-------|-------------|
| Lambda | ~200 invocations/month, 128MB, avg 3s | ~$0.00 (free tier) |
| Step Functions | ~20 executions/month (Standard) | ~$0.00 (free tier) |
| DynamoDB | On-demand, <1GB storage, ~500 reads/writes/month | ~$1.25 |
| EventBridge | 1 rule, 30 invocations/month | ~$0.00 |
| Secrets Manager | 2 secrets | **$0.80** |
| CloudWatch Logs | ~100MB/month | ~$0.05 |
| SNS | <1000 notifications/month | ~$0.00 |
| **TOTAL** | | **~$2.10/month** |

**One-time setup costs:** $0 (all infrastructure is pay-per-use)

> Note: Costs scale linearly with provisioning volume. At 500 users with weekly changes, estimate ~$5-8/month. Secrets Manager is the only fixed cost.

---

## Deployment Plan

### Prerequisites (Complete Before Apply)
- [ ] GitHub personal access token created with `admin:org` scope
- [ ] Jira API token created with user management permissions
- [ ] Secrets created in AWS Secrets Manager:
  - `github-api-token` → `{"token": "<token>"}`
  - `jira-api-token` → `{"email": "admin@brightwings.io", "api_token": "<token>"}`
- [ ] AWS account budget alert set at $20/month
- [ ] Team has reviewed this doc and approved

### Step 1: Deploy Infrastructure
```bash
terraform init
terraform plan    # Review — expected: ~25 resources created
terraform apply
```

**Expected resources created:**
- 2 DynamoDB tables
- 16 Lambda functions
- 2 Step Functions state machines
- 1 EventBridge rule
- 1 SNS topic
- ~12 IAM roles and policies

### Step 2: Smoke Test (Single User)
```bash
# Start an onboarding execution manually
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:YOUR_AWS_ACCOUNT_ID:stateMachine:saas-automation-dev-onboarding \
  --input '{
    "user_id": "test-deploy",
    "email": "test@brightwings.io",
    "role": "developer",
    "systems": ["github"],
    "github_username": "test-deploy-user"
  }'

# Verify in DynamoDB
aws dynamodb get-item \
  --table-name saas-automation-dev-provisioning-state \
  --key '{"user_id": {"S": "test-deploy"}, "system": {"S": "github"}}'
```

### Step 3: Verify Drift Detection
```bash
# Invoke drift detection manually
aws lambda invoke \
  --function-name saas-automation-dev-detect-drift \
  --payload '{"source": "manual-test"}' \
  /tmp/drift-output.json

cat /tmp/drift-output.json
```

### Step 4: Subscribe to Alerts
```bash
aws sns subscribe \
  --topic-arn arn:aws:sns:us-east-1:YOUR_AWS_ACCOUNT_ID:saas-automation-dev-security-alerts \
  --protocol email \
  --notification-endpoint security@brightwings.io
```

---

## Rollback Plan

If deployment causes issues, full teardown:

```bash
terraform destroy
```

**What `terraform destroy` removes:** All Lambda functions, DynamoDB tables, Step Functions, EventBridge rules, SNS topics, IAM roles.

**What it does NOT touch:** Existing IAM users (isaac, nicole, tf-user-isaac, tf-user-nicole), the administrators group, or any manually created resources.

**Data loss consideration:** `terraform destroy` deletes DynamoDB tables including provisioning state. Before destroying in production, export table data:
```bash
aws dynamodb scan --table-name saas-automation-dev-provisioning-state > backup.json
```

---

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| GitHub API token expires | Medium | Onboarding/offboarding fails | Secrets Manager rotation reminder, drift detection catches failures |
| DynamoDB throttling | Low | Workflow delays (not failures) | On-demand billing mode auto-scales, retries handle transient errors |
| Lambda cold start on offboarding | Low | 1-3s delay | Acceptable for non-real-time offboarding; provisioned concurrency if needed |
| Step Functions execution limit | Very Low | Workflows queue | Default 1M executions/month limit far exceeds our usage |
| Accidental `terraform destroy` | Low | Data loss | State file in version control, DynamoDB backup before any destroy |

---

## Open Questions for Team Review

1. **Slack deployment timing** — Slack provisioning is implemented but we're not actively using it. Should we enable the Slack branch in the state machine now, or keep it as a skip?

2. **GitHub real API tokens** — The current Lambda code uses MOCK API calls. Before deploying, the real GitHub API calls need to be uncommented. Who owns the GitHub org admin token?

3. **Offboarding trigger** — Currently the workflow must be triggered manually via Step Functions. Should we add an API Gateway endpoint so HR can trigger offboarding directly? Adds ~$0/month cost.

4. **Drift remediation** — Current drift detection alerts but does not auto-remediate. For `ACCESS_REMOVED` drift, should the system automatically re-run the provisioning workflow? Risk: could re-provision intentional manual removals.

5. **Retention period** — Drift events auto-delete after 90 days TTL. Is this sufficient for your compliance requirements, or do we need longer retention (SOC2 typically requires 1 year)?

---

## Approval

| Reviewer | Role | Status | Date |
|----------|------|--------|------|
| Isaac | Author | ✅ | 2026-02-17 |
| | | ⏳ Pending | |
| | | ⏳ Pending | |

**Deployment is blocked until at least one additional reviewer approves.**

---

*Questions? Reach out in #security-engineering or open a PR comment.*
