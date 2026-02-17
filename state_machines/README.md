# Step Functions State Machine - Onboarding Workflow

## Visual Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                    START: Onboarding Request                         │
│  Input: {user_id, email, role, systems, github_username}            │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             ▼
                    ┌────────────────┐
                    │ ValidateInput  │ ◄── Validates email, role, systems
                    │    (Lambda)    │     Retries: 2x on failure
                    └────┬──────┬────┘
                         │      │
                   Success│      │ValidationError
                         │      │
                         │      └──────────┐
                         ▼                 ▼
               ┌──────────────────┐  ┌──────────────────┐
               │ CreateRecord     │  │ ValidationFailed │
               │   (Lambda)       │  │    (Error)       │
               │                  │  └──────────────────┘
               │ Creates DynamoDB │
               │ records (pending)│
               └────┬──────┬──────┘
                    │      │
              Success│      │DynamoDBError
                    │      │
                    │      └──────────┐
                    ▼                 ▼
          ┌────────────────────┐  ┌──────────────────┐
          │ CheckSystems       │  │ DynamoDBFailed   │
          │   (Choice)         │  │    (Error)       │
          └────────┬───────────┘  └──────────────────┘
                   │
                   ▼
          ┌──────────────────────────────────────────┐
          │    ProvisionSystemsParallel              │
          │    (Parallel State - ALL RUN AT ONCE)    │
          ├────────────┬──────────────┬──────────────┤
          │            │              │              │
          ▼            ▼              ▼              ▼
     ┌──────────┐ ┌──────────┐  ┌──────────┐  ┌──────────┐
     │ GitHub   │ │  Slack   │  │   AWS    │  │  Google  │
     │ Branch   │ │  Branch  │  │  Branch  │  │  Branch  │
     └─────┬────┘ └─────┬────┘  └─────┬────┘  └─────┬────┘
           │            │              │              │
           │            │              │              │
  ┌────────▼────────────▼──────────────▼──────────────▼────────┐
  │                 All Branches Complete                       │
  │         (Even if some fail, workflow continues)             │
  └────────────────────────┬────────────────────────────────────┘
                           │
                           ▼
                  ┌──────────────────┐
                  │ AggregateResults │
                  │     (Pass)       │
                  │                  │
                  │ Combines outputs │
                  │ from all systems │
                  └────────┬─────────┘
                           │
                           ▼
                  ┌──────────────────┐
                  │ FinalizeExecution│ ◄── Updates DynamoDB
                  │    (Lambda)      │     status: pending → active
                  └────────┬─────────┘
                           │
                           ▼
                    ┌──────────────┐
                    │   SUCCESS    │
                    │   END        │
                    └──────────────┘
```

---

## Key Features

### 1. **Parallel Execution (Performance)**
```
Sequential (BAD):
GitHub (60s) → Slack (60s) → AWS (30s) = 150 seconds total

Parallel (GOOD):
GitHub (60s) ┐
Slack  (60s) ├─ ALL AT ONCE = 60 seconds total
AWS    (30s) ┘
```

**Performance benefit:** 2.5x faster provisioning

---

### 2. **Error Handling (Resilience)**

Each Lambda has **automatic retries**:
```json
{
  "Retry": [
    {
      "ErrorEquals": ["States.TaskFailed"],
      "IntervalSeconds": 2,
      "MaxAttempts": 2,
      "BackoffRate": 2.0
    }
  ]
}
```

**Retry pattern:**
- Attempt 1: Fails (network timeout)
- Wait 2 seconds
- Attempt 2: Fails
- Wait 4 seconds (2 × 2.0 backoff)
- Attempt 3: Success ✅

**Why this matters:**
- Transient errors (API rate limits, network hiccups) auto-recover
- Reduces manual intervention
- Improves success rate

---

### 3. **Graceful Degradation (Availability)**

```
Scenario: GitHub API is down

Traditional approach:
GitHub fails → Entire workflow fails → User not provisioned in ANY system 🚨

Our approach (Parallel with Catch):
GitHub fails → Catch error → Mark as failed
Slack succeeds → User provisioned ✅
AWS succeeds → User provisioned ✅

Result:
- User partially provisioned (2/3 systems)
- Workflow completes successfully
- Operator can retry GitHub provisioning later
- No data loss (DynamoDB tracks failure)
```

**Catch block:**
```json
{
  "Catch": [
    {
      "ErrorEquals": ["States.ALL"],
      "ResultPath": "$.github_error",
      "Next": "GitHubProvisioningFailed"
    }
  ]
}
```

---

### 4. **Audit Trail (Compliance)**

Step Functions automatically logs:
- ✅ **Input:** What was requested
- ✅ **Output:** What was provisioned
- ✅ **Duration:** How long each step took
- ✅ **Errors:** Exactly where failures occurred
- ✅ **Retries:** How many retries were needed

**CloudWatch Logs example:**
```json
{
  "execution_arn": "arn:aws:states:...:execution:onboarding:isaac-20260217",
  "name": "isaac-20260217",
  "status": "SUCCEEDED",
  "start_date": "2026-02-17T10:00:00Z",
  "stop_date": "2026-02-17T10:01:30Z",
  "input": {
    "user_id": "isaac",
    "email": "isaac@brightwings.io",
    "systems": ["github", "slack"]
  },
  "output": {
    "systems_activated": ["github", "slack"],
    "github_username": "isaacbryant"
  }
}
```

**Compliance benefit:** Complete audit trail for SOC2, ISO 27001

---

## State Machine States Explained

### Task States (Lambda Invocations)

**ValidateInput:**
- **Purpose:** Validate user input
- **Timeout:** 30 seconds
- **Retries:** 2 attempts
- **Catch:** ValidationError → ValidationFailed state

**CreateProvisioningRecord:**
- **Purpose:** Create DynamoDB records (status=pending)
- **Timeout:** 30 seconds
- **Retries:** 3 attempts
- **Catch:** All errors → DynamoDBWriteFailed state

**ProvisionGitHub:**
- **Purpose:** Add user to GitHub org
- **Timeout:** 60 seconds (GitHub API can be slow)
- **Retries:** 2 attempts
- **Catch:** All errors → GitHubProvisioningFailed (graceful degradation)

**FinalizeExecution:**
- **Purpose:** Update DynamoDB status=active
- **Timeout:** 30 seconds
- **Retries:** 3 attempts
- **Catch:** All errors → FinalizationFailed state

---

### Choice States (Conditional Logic)

**CheckSystemsToProvision:**
```json
{
  "Type": "Choice",
  "Choices": [
    {
      "Variable": "$.systems",
      "IsPresent": true,
      "Next": "ProvisionSystemsParallel"
    }
  ],
  "Default": "NoSystemsSpecified"
}
```

**Purpose:** Validates that at least one system is specified

**CheckIfGitHubRequested:**
- Checks if "github" is in systems array
- If yes → ProvisionGitHub
- If no → GitHubSkipped (no-op)

---

### Parallel State (Concurrent Execution)

```json
{
  "Type": "Parallel",
  "Branches": [
    {"StartAt": "CheckIfGitHubRequested", ...},
    {"StartAt": "SlackPlaceholder", ...},
    {"StartAt": "AWSPlaceholder", ...}
  ],
  "ResultPath": "$.provisioning_results"
}
```

**How it works:**
1. Step Functions spawns 3 parallel branches
2. Each branch runs independently
3. Step Functions waits for ALL branches to complete
4. Results are aggregated into `$.provisioning_results`

**Example output:**
```json
{
  "provisioning_results": [
    {"github_result": {"status": "success"}},
    {"slack_result": {"status": "not_implemented"}},
    {"aws_result": {"status": "not_implemented"}}
  ]
}
```

---

### Pass States (Data Transformation)

**AggregateResults:**
```json
{
  "Type": "Pass",
  "Parameters": {
    "user_id.$": "$.user_id",
    "results.$": "$.provisioning_results",
    "workflow_status": "completed",
    "completed_at.$": "$$.State.EnteredTime"
  }
}
```

**Purpose:** Transform data into clean format for FinalizeExecution Lambda

**Input (messy):**
```json
{
  "user_id": "isaac",
  "validated_at": "...",
  "provisioned_at": "...",
  "provisioning_results": [...]
}
```

**Output (clean):**
```json
{
  "user_id": "isaac",
  "results": [...],
  "workflow_status": "completed",
  "completed_at": "2026-02-17T10:01:30Z"
}
```

---

### Fail States (Error Handling)

**WorkflowFailed:**
```json
{
  "Type": "Fail",
  "Error": "WorkflowExecutionFailed",
  "Cause": "The onboarding workflow failed. Check CloudWatch Logs for details."
}
```

**Purpose:** Mark workflow as failed (for monitoring/alerting)

**Monitoring:** Can set CloudWatch alarm when executions enter Fail state

---

## Testing the State Machine

### Local Testing with Step Functions Local (Docker)

```bash
# 1. Install Step Functions Local
docker pull amazon/aws-stepfunctions-local

# 2. Start Step Functions Local
docker run -p 8083:8083 \
  --env-file aws-credentials.env \
  amazon/aws-stepfunctions-local

# 3. Create state machine
aws stepfunctions create-state-machine \
  --endpoint-url http://localhost:8083 \
  --name onboarding-workflow \
  --definition file://onboarding_workflow.json \
  --role-arn arn:aws:iam::123456789012:role/DummyRole

# 4. Start execution
aws stepfunctions start-execution \
  --endpoint-url http://localhost:8083 \
  --state-machine-arn arn:aws:states:us-east-1:123456789012:stateMachine:onboarding-workflow \
  --input '{"user_id": "test", "email": "test@brightwings.io", "role": "developer", "systems": ["github"], "github_username": "testuser"}'

# 5. View execution history
aws stepfunctions get-execution-history \
  --endpoint-url http://localhost:8083 \
  --execution-arn arn:aws:states:...
```

---

### Testing with Mock Lambdas

For testing without Lambda deployment, use **Pass states** as placeholders:

```json
{
  "ValidateInput": {
    "Type": "Pass",
    "Result": {
      "user_id": "test",
      "email": "test@brightwings.io",
      "validated_at": "2026-02-17T10:00:00Z"
    },
    "Next": "CreateRecord"
  }
}
```

---

## Cost Optimization

### Step Functions Pricing (Standard Workflows)

```
First 4,000 state transitions: FREE
After 4,000: $0.025 per 1,000 transitions

Example: 1,000 user onboardings/month
- Each workflow: ~8 state transitions
- Total: 8,000 transitions/month
- Cost: (8,000 - 4,000) × $0.025 / 1,000 = $0.10/month

CloudWatch Logs:
- 1GB/month: ~$0.50/month

Total: ~$0.60/month for 1,000 onboardings
```

**Cost per onboarding:** $0.0006 (negligible)

---

## Security Considerations

### 1. **IAM Execution Role**
Step Functions execution role needs:
```hcl
{
  "Effect": "Allow",
  "Action": "lambda:InvokeFunction",
  "Resource": [
    "arn:aws:lambda:*:*:function:saas-automation-dev-validate-input",
    "arn:aws:lambda:*:*:function:saas-automation-dev-create-record",
    "arn:aws:lambda:*:*:function:saas-automation-dev-provision-github",
    "arn:aws:lambda:*:*:function:saas-automation-dev-finalize-execution"
  ]
}
```

**Security:** Scoped to SPECIFIC Lambda functions (not `lambda:*`)

### 2. **Input Validation**
```
Step Functions does NOT validate input!
→ Must validate in Lambda (ValidateInput)
→ Prevents injection attacks
```

### 3. **Sensitive Data in Logs**
```
CloudWatch Logs stores execution input/output
→ Do NOT pass secrets in Step Functions input
→ Retrieve secrets in Lambda from Secrets Manager
```

---

## Next Steps

1. **Deploy Step Functions state machine** (Terraform apply)
2. **Test with single user** (manual execution)
3. **Add CloudWatch alarms** (alert on failed executions)
4. **Implement remaining systems** (Slack, AWS, Google Workspace)
5. **Add SNS notifications** (alert on errors)
6. **Create API Gateway trigger** (HTTP API for onboarding)
