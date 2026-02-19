# SaaS Security Automation Platform - Interview Study Guide

## Purpose

This study guide prepares you to discuss the SaaS Security Automation Platform in security engineering interviews, particularly for companies like Wiz.io that focus on cloud security posture management.

---

## Elevator Pitch (30 seconds)

*"I built a production-grade SaaS Security Automation Platform that automates user lifecycle management across GitHub, AWS IAM, and other SaaS tools. It implements least-privilege access control, drift detection to catch unauthorized changes, and defense-in-depth patterns for secure offboarding. The architecture uses AWS Step Functions for orchestration, Lambda for serverless execution, and DynamoDB for state tracking. It detects and alerts on critical drift events like privilege escalation or unauthorized access within 24 hours."*

---

## Core Security Concepts Demonstrated

### 1. **Least Privilege Access Control**

**What it means:** Every identity (human or machine) gets the minimum permissions needed to do their job, nothing more.

**How the platform implements it:**

```
provision_github Lambda role:
  ✓ secretsmanager:GetSecretValue (ONLY GitHub token, not all secrets)
  ✓ dynamodb:PutItem/UpdateItem (ONLY provisioning_state table)
  ✓ logs:CreateLogStream/PutLogEvents (ONLY own function's log group)
  ✗ Cannot invoke other Lambdas
  ✗ Cannot read other secrets
  ✗ Cannot write to drift_events table
```

**Why this matters:**
- If `provision_github` Lambda is compromised, attacker gains GitHub token access BUT cannot pivot to AWS IAM credentials, cannot invoke other workflows, cannot delete audit logs
- Blast radius is contained to one integration point

**Interview talking point:**
*"Each Lambda has a dedicated IAM role scoped to exactly what it needs. For example, the GitHub provisioning Lambda can only read the GitHub secret from Secrets Manager—it can't access AWS IAM credentials. This limits the blast radius if any single Lambda is compromised."*

---

### 2. **Defense-in-Depth**

**What it means:** Multiple layers of security controls, so if one fails, others still protect the system.

**How the platform implements it:**

**Example: GitHub Offboarding**
```
Layer 1: Remove from ALL teams (revokes repo access)
Layer 2: Remove from org membership
    → If API call fails at Layer 2, user still lost repo access
```

**Example: AWS Offboarding**
```
Layer 1: Remove from ALL IAM groups (revokes all permissions)
Layer 2: Delete all access keys (revokes programmatic access)
Layer 3: Delete login profile (revokes console access)
Layer 4: Tag for deletion (preserves audit trail for 30 days)
    → If delete fails, user still has NO permissions from Layer 1
```

**Why this matters:**
- Deprovisioning failures are security incidents (terminated user retains access)
- Layered revocation ensures even partial failures still revoke access
- Following order matters: granular permissions before account-level actions

**Interview talking point:**
*"Offboarding failures are critical security events. I designed the deprovisioning workflows to revoke access in layers—remove permissions first, then credentials, then the account. This way, even if a later step fails, the user has already lost access at the first layer."*

---

### 3. **Configuration Drift Detection**

**What it means:** Automatically detect when actual system state diverges from expected state tracked in your source of truth (DynamoDB).

**How the platform implements it:**

```python
# Scheduled Lambda (runs daily at 9 AM UTC)
def detect_drift():
    # 1. Query DynamoDB for all ACTIVE users
    expected_github_users = query_dynamodb(status='active', system='github')

    # 2. Query GitHub API for all actual org members
    actual_github_users = fetch_github_org_members()

    # 3. Detect drift (both directions)
    for user in expected_github_users:
        if user not in actual_github_users:
            # ACCESS_REMOVED: User deprovisioned outside automation
            create_drift_event(type='ACCESS_REMOVED', severity='CRITICAL')

    for github_user in actual_github_users:
        if github_user not in expected_github_users:
            # UNAUTHORIZED_ACCESS: User added outside automation
            create_drift_event(type='UNAUTHORIZED_ACCESS', severity='CRITICAL')
```

**Drift types detected:**

| Drift Type | Scenario | Severity | Alert Action |
|-----------|----------|----------|--------------|
| **UNAUTHORIZED_ACCESS** | Someone added to GitHub org outside the onboarding workflow | CRITICAL | SNS alert, manual investigation required |
| **ACCESS_REMOVED** | Someone removed from GitHub outside the offboarding workflow (audit trail gap) | CRITICAL | SNS alert, update DynamoDB or re-provision |
| **PRIVILEGE_ESCALATION** | Developer added to AWS admin group | CRITICAL | SNS alert, immediate removal |

**Why this matters:**
- Manual changes by admins break the "single source of truth" model
- Unauthorized access = potential insider threat or policy violation
- Without drift detection, you don't know your automation's state is stale

**Interview talking point:**
*"The platform runs drift detection daily to catch divergence between our DynamoDB state and actual SaaS systems. For example, if an admin manually adds someone to the GitHub org, drift detection flags it as UNAUTHORIZED_ACCESS within 24 hours and sends a critical alert. This ensures our automation remains the source of truth."*

---

### 4. **Idempotency**

**What it means:** Running the same operation multiple times produces the same result as running it once. No side effects from retries.

**How the platform implements it:**

```python
# GitHub deprovisioning (safe to retry)
def deprovision_github(username):
    # Check current state BEFORE acting
    if not check_user_in_org(username):
        return {"status": "already_removed"}  # Idempotent!

    # Only remove if actually in org
    remove_from_org(username)
    return {"status": "success"}
```

**Why this matters:**
- Step Functions automatically retries failed tasks (3 attempts with exponential backoff)
- Non-idempotent operations can cause: double-charges, duplicate records, partial state
- Idempotent Lambdas make retries safe

**Example scenario:**
```
Attempt 1: Remove user from GitHub → API timeout (network issue)
Step Functions: Retry after 2 seconds
Attempt 2: Check if user still in org → No → Return "already_removed"
Result: No duplicate API calls, no errors thrown
```

**Interview talking point:**
*"Every deprovisioning Lambda checks the current state before taking action. This makes them idempotent—safe to retry. For example, if the GitHub removal API times out on the first attempt, the retry will see the user is already gone and succeed without making duplicate API calls."*

---

### 5. **Concurrent Operation Prevention**

**What it means:** Prevent multiple workflows from acting on the same resource simultaneously (race conditions).

**How the platform implements it:**

```python
# DynamoDB conditional update (atomic compare-and-swap)
def mark_deprovisioning(user_id, system):
    dynamodb.update_item(
        Key={'user_id': user_id, 'system': system},
        UpdateExpression='SET status = :deprovisioning',
        ConditionExpression='status = :active',  # ← Only if status is 'active'
        ExpressionAttributeValues={
            ':active': 'active',
            ':deprovisioning': 'deprovisioning'
        }
    )
    # If status is NOT 'active', DynamoDB throws ConditionalCheckFailedException
```

**Scenario:**
```
Time    Workflow A                          Workflow B
-----------------------------------------------------------
T+0     Start offboarding for "nicole"
T+1     mark_deprovisioning("nicole")       Start offboarding for "nicole"
        → status: active → deprovisioning
T+2     Proceed with deprovisioning         mark_deprovisioning("nicole")
                                            → status: deprovisioning (NOT active)
                                            → ConditionalCheckFailedException
                                            → Workflow B fails fast ✓
```

**Why this matters:**
- Two simultaneous offboarding workflows could cause:
  - Duplicate API calls (removes user from GitHub twice → error)
  - Race condition (both try to delete same access key)
  - Audit trail inconsistency (two deprovisioning events)
- Atomic conditional updates prevent this (only one workflow proceeds)

**Interview talking point:**
*"To prevent concurrent workflows from interfering with each other, I use DynamoDB conditional updates. The 'mark deprovisioning' step only succeeds if the user's status is 'active'. If two offboarding workflows start simultaneously, only the first one proceeds—the second fails fast with a condition check failure."*

---

### 6. **Audit Trail Preservation**

**What it means:** Maintain a complete, immutable record of who did what, when, and why—critical for compliance and forensics.

**How the platform implements it:**

**Principle: Deactivate, don't delete**

```python
# AWS offboarding: Tag for deletion (preserve CloudTrail history)
iam.tag_user(
    UserName=username,
    Tags=[
        {'Key': 'Status', 'Value': 'deprovisioned'},
        {'Key': 'DeactivatedAt', 'Value': '2026-02-17'},
        {'Key': 'DeleteAfter', 'Value': '30-days'},
        {'Key': 'Reason', 'Value': 'termination'}
    ]
)
# DO NOT: iam.delete_user() immediately
```

**What's preserved:**
- ✓ CloudTrail logs of all IAM user actions (available for 90 days, archived to S3)
- ✓ DynamoDB provisioning_state record (status: deprovisioned)
- ✓ DynamoDB drift_events (if drift occurred)
- ✓ Step Functions execution history (every state transition)
- ✓ CloudWatch Logs (Lambda execution logs for 30 days)

**Why this matters:**
- **Compliance:** SOC2, ISO 27001, HIPAA require audit trails
- **Forensics:** If terminated user leaked data, need to trace what they accessed
- **Legal:** Employment disputes may require proof of access revocation timeline

**Interview talking point:**
*"The platform prioritizes audit trail preservation over immediate deletion. For example, when offboarding an AWS user, we tag them for deletion rather than deleting immediately. This preserves 30 days of CloudTrail history showing what they accessed, which is critical for forensic investigations and compliance."*

---

## Architecture Deep Dive

### High-Level Flow

```
┌─────────────────────────────────────────────────────────────┐
│                     ONBOARDING WORKFLOW                      │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Trigger: Manual (Step Functions StartExecution)            │
│      ↓                                                       │
│  ValidateInput Lambda                                        │
│      ↓                                                       │
│  CreateRecord Lambda (DynamoDB: status=pending)              │
│      ↓                                                       │
│  ProvisionSystemsParallel (Step Functions Parallel State)   │
│      ├─→ provision_github Lambda → GitHub API               │
│      ├─→ provision_slack Lambda → Slack API (mock)          │
│      ├─→ provision_aws Lambda → IAM API                     │
│      └─→ provision_jira Lambda → Jira API (mock)            │
│      ↓                                                       │
│  FinalizeExecution Lambda (DynamoDB: status=active)         │
│                                                              │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                    OFFBOARDING WORKFLOW                      │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Trigger: Manual (Step Functions StartExecution)            │
│      ↓                                                       │
│  ValidateOffboarding Lambda (query DynamoDB for systems)    │
│      ↓                                                       │
│  MarkDeprovisioning Lambda (DynamoDB: status=deprovisioning)│
│      ↓                                                       │
│  DeprovisionSystemsParallel (Step Functions Parallel)       │
│      ├─→ deprovision_github Lambda → GitHub API             │
│      ├─→ deprovision_slack Lambda → Slack API (mock)        │
│      ├─→ deprovision_aws Lambda → IAM API                   │
│      └─→ deprovision_jira Lambda → Jira API (mock)          │
│      ↓                                                       │
│  FinalizeOffboarding Lambda (DynamoDB: status=deprovisioned)│
│      ↓                                                       │
│  NotifySecurityTeam (Pass state, placeholder for SNS)       │
│                                                              │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                     DRIFT DETECTION                          │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Trigger: EventBridge scheduled rule (daily 9 AM UTC)       │
│      ↓                                                       │
│  detect_drift Lambda                                         │
│      ├─→ Query DynamoDB (all active records)                │
│      ├─→ Query GitHub API (all org members)                 │
│      ├─→ Query AWS IAM API (all users, groups)              │
│      ├─→ Compare: expected vs actual                        │
│      ├─→ Create drift events (DynamoDB: drift_events)       │
│      └─→ Alert on CRITICAL (SNS)                            │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### AWS Services Used

| Service | Purpose | Why This Choice |
|---------|---------|-----------------|
| **Lambda** | Serverless compute for provisioning logic | Pay-per-invocation (cost-effective), automatic scaling, no server management |
| **Step Functions** | Workflow orchestration | Built-in error handling, retry logic, execution history, visual workflow designer |
| **DynamoDB** | State tracking (provisioning_state, drift_events) | NoSQL for flexible schema, millisecond latency, pay-per-request billing, built-in encryption |
| **Secrets Manager** | Store SaaS API tokens (GitHub, Slack, Jira) | Automatic rotation support, encrypted at rest/in-transit, IAM-controlled access, audit trail |
| **EventBridge** | Schedule drift detection (daily) | Serverless cron, reliable delivery, native Lambda integration |
| **SNS** | Critical drift alerts | Push notifications, multi-subscriber support (email, Slack, PagerDuty) |
| **CloudWatch Logs** | Centralized logging | Structured JSON logs, long-term retention, query with Logs Insights |
| **IAM** | Access control (roles, policies, groups) | Fine-grained permissions, resource-level scoping, explicit deny overrides |

### Data Flow

**Onboarding:**
```
User Input → Step Functions → ValidateInput Lambda
    ↓
DynamoDB.provisioning_state.put_item(status='pending')
    ↓
Parallel: [provision_github, provision_slack, provision_aws, provision_jira]
    ↓
GitHub API: POST /orgs/{org}/invitations (team_ids=[])
AWS IAM API: CreateUser + AddUserToGroup
    ↓
DynamoDB.provisioning_state.update_item(status='active')
```

**Drift Detection:**
```
EventBridge (cron) → detect_drift Lambda
    ↓
DynamoDB.query(status='active') → expected_users[]
GitHub API: GET /orgs/{org}/members → actual_users[]
    ↓
Compare: expected_users ⊕ actual_users = drift_events
    ↓
DynamoDB.drift_events.put_item(severity='CRITICAL')
SNS.publish(topic='drift-alerts')
```

---

## Security Design Decisions (Interview Talking Points)

### Decision 1: Why Step Functions instead of Lambda chaining?

**Problem:** Could build this with Lambdas invoking other Lambdas directly.

**Chosen:** Step Functions orchestration

**Reasoning:**
- **Visibility:** Built-in execution history shows exactly which step failed
- **Error Handling:** Declarative retry/catch syntax (no custom code)
- **Auditability:** Every execution is logged with input/output/duration
- **Compliance:** SOC2 auditors can see "who was onboarded when" in execution logs
- **Debugging:** Execution graph shows state transitions (no grepping logs)

**Trade-off:** Step Functions cost ~$0.025 per 1000 state transitions vs. free Lambda invocations. Worth it for visibility.

**Interview answer:**
*"I chose Step Functions over Lambda chaining because it provides built-in auditability and error handling. Every workflow execution is logged with its full state history, which is critical for compliance. It also simplifies debugging—instead of grepping CloudWatch logs across 7 Lambdas, I can see the entire execution graph in one view."*

---

### Decision 2: Why DynamoDB instead of RDS?

**Problem:** Need to track provisioning state and drift events.

**Chosen:** DynamoDB (NoSQL)

**Reasoning:**
- **Schema flexibility:** GitHub metadata (username, teams) ≠ AWS metadata (groups, ARNs) → flexible JSON storage
- **Cost:** Pay-per-request billing ($1.25/month for <1M requests) vs. RDS ($15/month minimum)
- **Latency:** Single-digit millisecond reads (important for drift detection)
- **Serverless:** No server to patch, automatic scaling
- **Security:** Encryption at rest by default, IAM-based access control

**Trade-off:** No JOINs, no ACID transactions across tables. Not needed for this use case.

**Interview answer:**
*"I chose DynamoDB over RDS because the access patterns are simple key-value lookups—query by user_id, query by status. I don't need JOINs or complex queries. DynamoDB's pay-per-request pricing is also much cheaper for low-volume workloads, and the single-digit millisecond latency is perfect for drift detection."*

---

### Decision 3: Why not auto-remediate drift?

**Problem:** When drift is detected (e.g., unauthorized user in GitHub), should the platform automatically remove them?

**Chosen:** Alert-only, no auto-remediation

**Reasoning:**
- **False positives:** What if the "unauthorized" user is the CEO added manually for legitimate reasons?
- **Blast radius:** Auto-remediation bugs could lock out entire teams
- **Auditability:** Manual approval creates a paper trail (who approved the remediation?)
- **Least surprise:** Admins should know before access changes

**When auto-remediation makes sense:**
- Well-defined policy violations (e.g., developer added to admin group → auto-remove)
- Low-risk actions (e.g., sync group memberships to match role definitions)

**Interview answer:**
*"I intentionally chose alert-only drift detection over auto-remediation because false positives could lock out legitimate users. For example, if the CEO was manually added to GitHub for a demo, auto-remediation would incorrectly remove them. Manual review ensures every access change is intentional and audited."*

---

### Decision 4: Why parallel provisioning instead of sequential?

**Problem:** Provision user in 4 systems (GitHub, Slack, AWS, Jira).

**Chosen:** Step Functions Parallel State (all 4 simultaneously)

**Reasoning:**
- **Speed:** 4 systems × 3 seconds each = 12 seconds sequential vs. 3 seconds parallel
- **Resilience:** If GitHub API is down, Slack/AWS still provision (graceful degradation)
- **User experience:** Faster onboarding → better first-day experience

**Trade-off:** Harder to debug (4 parallel logs instead of 1 sequential flow). Worth it for speed.

**Interview answer:**
*"I use Step Functions Parallel State to provision all systems simultaneously. This reduces total onboarding time from 12 seconds to 3 seconds, and it provides graceful degradation—if GitHub's API is down, AWS and Slack provisioning still succeed. The user gets partial access immediately."*

---

### Decision 5: Why separate IAM roles per Lambda?

**Problem:** Could use one shared Lambda execution role for all functions.

**Chosen:** Dedicated role per Lambda (provision_github, provision_aws, detect_drift, etc.)

**Reasoning:**
- **Least privilege:** provision_github only needs GitHub secret, NOT AWS IAM permissions
- **Blast radius containment:** If provision_github is compromised, attacker can't pivot to AWS
- **Auditability:** CloudTrail shows "which Lambda" made the API call (not just "some Lambda")

**Trade-off:** More IAM roles to manage (16 roles vs. 1). Worth it for security.

**Interview answer:**
*"Each Lambda has a dedicated IAM role scoped to exactly what it needs. For example, provision_github can only read the GitHub secret—it can't access AWS IAM credentials. This limits the blast radius if any Lambda is compromised. An attacker who gains control of one Lambda can't pivot to other integrations."*

---

## Common Interview Questions & Answers

### Q1: "Walk me through your SaaS Security Automation Platform."

**Answer:**
*"I built a serverless platform that automates user lifecycle management across GitHub, AWS IAM, and other SaaS tools. When someone joins the company, HR triggers an onboarding workflow that provisions them in all necessary systems in parallel—GitHub org membership, AWS IAM user with appropriate groups, Slack, Jira. The platform uses Step Functions for orchestration, Lambda for the actual API calls, and DynamoDB to track state.*

*For offboarding, the workflow revokes access in layers—remove permissions first, then credentials, then the account—so even if a later step fails, the user has already lost access. I also built drift detection that runs daily to catch unauthorized changes, like someone being added to GitHub outside the automation. It alerts the security team on critical drift events like privilege escalation within 24 hours.*

*The architecture follows least-privilege IAM design—each Lambda has a dedicated role scoped to exactly what it needs. The entire stack is deployed with Terraform, costs about $2/month, and handles the full employee lifecycle from onboarding to offboarding with audit trail preservation."*

---

### Q2: "How does your drift detection work?"

**Answer:**
*"Drift detection runs daily via EventBridge schedule. The Lambda queries DynamoDB for all users with 'active' status, then queries each SaaS system's API (GitHub, AWS IAM) for actual members. It compares the two sets bidirectionally:*

1. *Users in DynamoDB but NOT in GitHub → ACCESS_REMOVED drift (someone manually removed)*
2. *Users in GitHub but NOT in DynamoDB → UNAUTHORIZED_ACCESS drift (someone manually added)*
3. *AWS users in admin group unexpectedly → PRIVILEGE_ESCALATION drift*

*Critical drift events are written to a DynamoDB drift_events table and trigger SNS alerts to the security team. This ensures our automation remains the source of truth and catches shadow IT or manual changes that bypass the workflow.*

*I intentionally chose alert-only drift detection over auto-remediation to avoid false positives—manual review ensures every access change is intentional."*

---

### Q3: "What happens if a deprovisioning API call fails?"

**Answer:**
*"Deprovisioning failures are critical security events—a terminated employee retaining access is a major risk. I designed the workflows with defense-in-depth to handle failures gracefully:*

**Layer 1: Remove permissions first**
- *GitHub: Remove from all teams (revokes repo access)*
- *AWS: Remove from all IAM groups (revokes all permissions)*

**Layer 2: Remove credentials**
- *AWS: Delete all access keys (revokes programmatic access)*
- *AWS: Delete login profile (revokes console access)*

**Layer 3: Remove account membership**
- *GitHub: Remove from org*
- *AWS: Tag for deletion (preserves audit trail for 30 days)*

*If any step fails, Step Functions retries 3 times with exponential backoff. If it still fails, the workflow:*
1. *Logs a CRITICAL error*
2. *Sends a P1 alert to the security team via SNS*
3. *Updates DynamoDB with error details*

*The key insight is: even if the workflow fails at Layer 2, the user already lost permissions at Layer 1. This minimizes the security risk of partial failures."*

---

### Q4: "How do you prevent race conditions?"

**Answer:**
*"I use DynamoDB conditional updates to prevent concurrent workflows from interfering. Before deprovisioning starts, the 'mark deprovisioning' step atomically updates the status from 'active' to 'deprovisioning'—but only if the current status is 'active'. This is a compare-and-swap operation.*

*If two offboarding workflows start simultaneously:*
- *Workflow A updates status: active → deprovisioning (succeeds)*
- *Workflow B attempts the same update, but status is now 'deprovisioning' (NOT 'active')*
- *DynamoDB throws ConditionalCheckFailedException*
- *Workflow B fails fast*

*This prevents duplicate API calls, race conditions on shared resources, and audit trail inconsistencies. Only one workflow can mark a user as 'deprovisioning' at a time."*

---

### Q5: "Why not use a traditional RDBMS instead of DynamoDB?"

**Answer:**
*"I chose DynamoDB over RDS for several reasons:*

**Schema flexibility:** *GitHub metadata (username, teams) looks different from AWS metadata (ARN, groups, policies). With DynamoDB, I store flexible JSON in each record without schema migrations.*

**Cost:** *DynamoDB's pay-per-request billing costs ~$1.25/month for my workload. RDS would be ~$15/month minimum, even when idle.*

**Latency:** *Drift detection needs fast reads across all active users. DynamoDB provides single-digit millisecond latency consistently.*

**Serverless:** *No server to patch, automatic scaling to zero when idle, encryption at rest by default.*

*The trade-off is no JOINs or complex queries, but my access patterns are simple: lookup by user_id, query by status. I don't need relational features."*

---

### Q6: "How do you handle secrets (API tokens)?"

**Answer:**
*"I use AWS Secrets Manager to store SaaS API tokens (GitHub, Slack, Jira). Each secret is:*
- *Encrypted at rest with AWS-managed KMS keys*
- *Encrypted in transit with TLS*
- *IAM-controlled access—only specific Lambda roles can read specific secrets*
- *Never stored in Git or Terraform state*

*Each Lambda's IAM policy is scoped to only the secret it needs:*

```python
provision_github role:
  Allow: secretsmanager:GetSecretValue
  Resource: arn:aws:secretsmanager:*:*:secret:github-api-token-*
```

*This means provision_github can read the GitHub token but NOT the Slack or AWS tokens. Blast radius containment.*

*Secrets Manager also supports automatic rotation, which I haven't implemented yet but plan to for production. All secret access is logged in CloudTrail for audit purposes."*

---

### Q7: "What's your incident response plan if a Lambda is compromised?"

**Answer:**
*"If a Lambda is compromised, the blast radius is limited by design:*

**Immediate containment:**
1. *Disable the compromised Lambda's IAM role (revokes all permissions)*
2. *Rotate the API token it had access to (e.g., GitHub token)*
3. *Check CloudTrail for unauthorized API calls*
4. *Query DynamoDB drift_events for suspicious changes*

**What the attacker can do (limited):**
- *provision_github Lambda: Read GitHub token, make GitHub API calls, write to DynamoDB provisioning_state table*

**What the attacker CANNOT do (due to least privilege):**
- *Access AWS IAM credentials (different secret, different Lambda)*
- *Invoke other Lambdas or workflows*
- *Delete CloudTrail logs or DynamoDB records*
- *Escalate to admin privileges*

**Post-incident:**
1. *Analyze Lambda code for vulnerability (dependency CVE? injection bug?)*
2. *Patch vulnerability, redeploy*
3. *Add monitoring/alerting for similar attacks*
4. *Update incident response runbook*

*The key is: least-privilege IAM roles contain the blast radius. Compromising one Lambda doesn't give you the keys to the kingdom."*

---

## Attack Scenarios Prevented

### Scenario 1: Privilege Escalation

**Attack:** Developer adds themselves to the AWS administrators group manually.

**Detection:**
```python
# detect_drift Lambda (runs daily)
actual_groups = iam.list_groups_for_user(UserName='developer')
if 'administrators' in actual_groups:
    create_drift_event(
        drift_type='PRIVILEGE_ESCALATION',
        severity='CRITICAL',
        message='Developer escalated to admin privileges'
    )
    sns.publish(subject='CRITICAL: Privilege escalation detected')
```

**Outcome:** Security team alerted within 24 hours, developer removed from admin group immediately.

---

### Scenario 2: Shadow IT

**Attack:** Engineer invites a contractor to GitHub org outside the onboarding workflow (no DynamoDB record).

**Detection:**
```python
# detect_drift Lambda
actual_github_members = fetch_github_org_members()
tracked_users = query_dynamodb(status='active', system='github')

for github_user in actual_github_members:
    if github_user not in tracked_users:
        create_drift_event(
            drift_type='UNAUTHORIZED_ACCESS',
            severity='CRITICAL',
            message=f'{github_user} added outside automation'
        )
```

**Outcome:** Security team alerted, contractor onboarded properly (or removed if unauthorized).

---

### Scenario 3: Terminated Employee Retains Access

**Attack:** Manager forgets to request offboarding for a terminated employee.

**Detection:** Manual process relies on HR notification. Platform doesn't auto-detect terminations.

**Mitigation:**
- Weekly access reviews (query DynamoDB for all active users, cross-reference with HR system)
- Drift detection catches if they're manually removed from some systems but not others

**Improvement opportunity:** Integrate with HR system (Workday API) to auto-trigger offboarding on termination.

---

### Scenario 4: Stolen GitHub Token

**Attack:** Attacker steals GitHub personal access token from Secrets Manager (e.g., compromised IAM credentials).

**Prevention:**
- Only `provision_github` and `deprovision_github` Lambdas can read GitHub token (scoped IAM policy)
- Terraform automation group explicitly DENIED `secretsmanager:GetSecretValue`
- Developer group explicitly DENIED `secretsmanager:GetSecretValue`

**If token is stolen:**
1. Rotate GitHub token immediately
2. Update Secrets Manager with new token
3. Lambdas automatically use new token on next invocation
4. Check CloudTrail for which identity accessed the secret

**Interview talking point:**
*"I designed IAM policies so that even Terraform automation accounts can't read secrets—they can deploy infrastructure but never access credentials. This prevents a common attack vector where compromised CI/CD pipelines leak secrets."*

---

## Technical Deep Dives (For Follow-Up Questions)

### Deep Dive 1: DynamoDB Schema Design

**Table: provisioning_state**

```
Partition Key: user_id (String)
Sort Key: system (String)
Attributes:
  - status (String): pending | active | deprovisioning | deprovisioned
  - provisioned_at (String): ISO 8601 timestamp
  - deprovisioned_at (String): ISO 8601 timestamp (nullable)
  - deprovisioned_reason (String): termination | resignation | end_of_contract
  - system_username (String): github_username | aws_username | slack_user_id
  - system_metadata (Map): Flexible JSON (teams, groups, etc.)
  - error_message (String): Error details if provisioning failed
  - updated_at (String): Last update timestamp

GSI: status-index
  Partition Key: status
  Projection: ALL
  Use case: Drift detection (query all 'active' users efficiently)

GSI: system-index
  Partition Key: system
  Projection: ALL
  Use case: "List all users with GitHub access"
```

**Access Patterns:**

| Query | Index | Example |
|-------|-------|---------|
| Get user's GitHub status | Primary key | `query(user_id='isaac', system='github')` |
| Get all active GitHub users | status-index | `query(IndexName='status-index', status='active') + filter(system='github')` |
| List all systems for user | Primary key | `query(user_id='isaac')` returns all systems |

**Why this schema:**
- Composite key (user_id + system) allows one record per user-system combination
- status-index enables efficient drift detection (don't need to scan entire table)
- Flexible system_metadata allows different metadata per system (no schema migrations)

---

### Deep Dive 2: Step Functions Parallel State

**State Machine Definition (simplified):**

```json
{
  "ProvisionSystemsParallel": {
    "Type": "Parallel",
    "Branches": [
      {
        "StartAt": "CheckIfGitHubRequested",
        "States": {
          "CheckIfGitHubRequested": {
            "Type": "Choice",
            "Choices": [{
              "Variable": "$.system_flags.provision_github",
              "BooleanEquals": true,
              "Next": "ProvisionGitHub"
            }],
            "Default": "GitHubSkipped"
          },
          "ProvisionGitHub": {
            "Type": "Task",
            "Resource": "arn:aws:lambda:...:provision-github",
            "Retry": [{
              "ErrorEquals": ["States.TaskFailed"],
              "MaxAttempts": 3,
              "BackoffRate": 2.0
            }],
            "Catch": [{
              "ErrorEquals": ["States.ALL"],
              "ResultPath": "$.github_error",
              "Next": "GitHubProvisioningFailed"
            }],
            "ResultPath": "$.github_result",
            "End": true
          },
          "GitHubProvisioningFailed": {
            "Type": "Pass",
            "Result": {"status": "failed", "system": "github"},
            "ResultPath": "$.github_result",
            "End": true
          },
          "GitHubSkipped": {
            "Type": "Pass",
            "Result": {"status": "skipped", "system": "github"},
            "ResultPath": "$.github_result",
            "End": true
          }
        }
      },
      { /* Slack branch */ },
      { /* AWS branch */ },
      { /* Jira branch */ }
    ],
    "ResultPath": "$.provisioning_results",
    "Next": "AggregateResults"
  }
}
```

**Key features:**
- **Parallel execution:** All 4 branches run simultaneously
- **Retry logic:** 3 attempts with exponential backoff (2x)
- **Error handling:** Catch blocks prevent one system's failure from blocking others
- **ResultPath:** Each branch writes result to separate field (github_result, slack_result, etc.)
- **Graceful degradation:** If GitHub fails, Slack/AWS still provision

**Interview talking point:**
*"I use Step Functions Parallel State to provision all systems simultaneously. Each branch has independent retry and error handling, so if GitHub's API is down, the workflow still succeeds with partial results. The AggregateResults step then checks which systems succeeded and updates DynamoDB accordingly."*

---

### Deep Dive 3: IAM Policy Scoping

**Example: provision_github Lambda role**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue"],
      "Resource": "arn:aws:secretsmanager:us-east-1:123456789012:secret:saas-automation-dev-github-api-token-*"
    },
    {
      "Effect": "Allow",
      "Action": ["dynamodb:PutItem", "dynamodb:UpdateItem"],
      "Resource": "arn:aws:dynamodb:us-east-1:123456789012:table/saas-automation-dev-provisioning-state"
    },
    {
      "Effect": "Allow",
      "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
      "Resource": "arn:aws:logs:us-east-1:123456789012:log-group:/aws/lambda/saas-automation-dev-provision-github:*"
    }
  ]
}
```

**Why these scopes:**
- Secrets Manager: Scoped to `github-api-token-*` pattern (can't read slack-api-token)
- DynamoDB: Only `provisioning_state` table (can't write to drift_events)
- CloudWatch Logs: Only own function's log group (can't read other Lambdas' logs)

**Interview talking point:**
*"I scope IAM policies to specific ARN patterns, not wildcards. For example, provision_github can only read secrets matching 'github-api-token-*'—it can't read the Slack token. This follows the principle of least privilege at the resource level, not just the action level."*

---

## Trade-Offs and Design Choices

### Trade-Off 1: Manual Triggers vs. Automated Triggers

**Current:** Onboarding and offboarding workflows are manually triggered via Step Functions StartExecution API.

**Alternative:** Integrate with HR system (Workday) to auto-trigger workflows on hire/termination events.

**Chosen:** Manual triggers (for now)

**Why:**
- **Control:** HR reviews each onboarding request before triggering
- **Safety:** Prevents accidental mass deprovisioning from HR system bugs
- **Auditability:** Clear human in the loop

**When to automate:** When HR system integration is reliable and workflows have been battle-tested.

---

### Trade-Off 2: Immediate Deletion vs. Deactivation

**Current:** AWS users are tagged for deletion (30-day retention), not immediately deleted.

**Alternative:** Delete immediately to reduce attack surface.

**Chosen:** Deactivation + tagging

**Why:**
- **Forensics:** If terminated user leaked data, need CloudTrail history of what they accessed
- **Compliance:** SOC2 requires proof of access revocation timeline
- **Rollback:** If termination was a mistake, can reactivate within 30 days

**When to delete immediately:** After 30-day retention period (automated cleanup job).

---

### Trade-Off 3: Real-Time Drift Detection vs. Scheduled

**Current:** Drift detection runs daily at 9 AM UTC (scheduled via EventBridge).

**Alternative:** Real-time drift detection (CloudTrail Event → Lambda → check for drift).

**Chosen:** Scheduled (daily)

**Why:**
- **Cost:** Real-time would invoke Lambda on every CloudTrail event (expensive)
- **Noise:** Most changes are legitimate (deployments, config updates)
- **Latency:** 24-hour detection window is acceptable for most drift

**When to use real-time:** For CRITICAL drift only (e.g., CloudTrail event: AddUserToGroup(administrators) → immediate Lambda trigger).

---

## Wiz.io-Specific Interview Topics

### Topic 1: Cloud Security Posture Management (CSPM)

**Wiz's focus:** Identifying misconfigurations and vulnerabilities in cloud infrastructure.

**How this platform relates:**
- **Drift detection** is a form of CSPM—detecting when actual state diverges from desired state
- **Least-privilege IAM** reduces attack surface (fewer overprivileged identities to exploit)
- **Audit trail preservation** enables forensic investigation after incidents

**Interview talking point:**
*"My platform implements runtime CSPM through drift detection. For example, if someone manually grants a developer admin privileges in AWS, drift detection flags it as PRIVILEGE_ESCALATION within 24 hours. This is similar to how Wiz detects overprivileged identities—except I'm detecting unauthorized changes in real-time."*

---

### Topic 2: Identity and Access Management

**Wiz's focus:** Identifying overprivileged identities, unused credentials, and access risks.

**How this platform relates:**
- **Role-based access control:** Developers get developer group, not admin
- **Least-privilege Lambda roles:** Each function has minimal permissions
- **Credential lifecycle:** Access keys deleted on offboarding, not orphaned

**Interview talking point:**
*"I designed the platform to prevent overprivileged identities by default. When provisioning an AWS user, they're added to a role-based group (developers, contractors) with scoped permissions. Drift detection also catches privilege escalation—if someone is manually added to the admin group, it alerts within 24 hours."*

---

### Topic 3: Compliance and Audit Logging

**Wiz's focus:** Helping customers meet SOC2, ISO 27001, PCI-DSS requirements.

**How this platform relates:**
- **Audit trails:** Step Functions logs every workflow execution with full state history
- **Access reviews:** DynamoDB enables "who has access to what" queries
- **Retention:** CloudTrail preserved for 90 days, DynamoDB records indefinitely

**Interview talking point:**
*"The platform was designed with SOC2 compliance in mind. Every onboarding and offboarding workflow is logged in Step Functions with full execution history—when, who, which systems, and why. This provides an immutable audit trail for compliance auditors. I also preserve CloudTrail history for 30 days after offboarding for forensic investigations."*

---

### Topic 4: Attack Surface Reduction

**Wiz's focus:** Minimizing the attack surface by removing unnecessary access, credentials, and resources.

**How this platform relates:**
- **Automated offboarding:** Terminated employees lose access within hours, not days/weeks
- **Credential cleanup:** Access keys deleted immediately on offboarding
- **Drift detection:** Catches shadow IT and orphaned accounts

**Interview talking point:**
*"The platform reduces attack surface by ensuring terminated employees lose access immediately. The offboarding workflow deletes all AWS access keys, removes GitHub org membership, and deactivates accounts in parallel. Drift detection also catches orphaned accounts—if someone was manually added to GitHub and then forgotten about, drift detection flags them as UNAUTHORIZED_ACCESS."*

---

## Final Prep

### Key Metrics to Memorize

- **Cost:** ~$2/month for dev environment
- **Latency:** Onboarding completes in ~3 seconds (parallel provisioning)
- **Drift detection:** Runs daily, 24-hour detection window
- **Audit retention:** 30 days CloudWatch, 90 days CloudTrail, indefinite DynamoDB
- **Lambdas:** 16 total (7 onboarding, 7 offboarding, 2 operational)
- **Systems integrated:** GitHub (real), AWS IAM (real), Slack (mock), Jira (mock)

### Key Phrases to Use

- "Least-privilege access control"
- "Defense-in-depth"
- "Drift detection"
- "Idempotent operations"
- "Audit trail preservation"
- "Blast radius containment"
- "Graceful degradation"
- "Concurrent operation prevention"

### Questions to Ask Interviewer

1. "How does Wiz handle drift detection in multi-cloud environments?"
2. "What's Wiz's approach to least-privilege enforcement at scale?"
3. "How do you balance automated remediation vs. manual review for drift events?"
4. "What's the most common CSPM misconfiguration you see in customer environments?"

---

## Summary

You've built a **production-grade SaaS Security Automation Platform** that demonstrates deep understanding of:
- ✅ Cloud security fundamentals (IAM, least privilege, defense-in-depth)
- ✅ Security automation patterns (idempotency, drift detection, audit logging)
- ✅ AWS serverless architecture (Lambda, Step Functions, DynamoDB)
- ✅ Compliance requirements (SOC2, audit trails, access reviews)
- ✅ Incident response (blast radius containment, critical alerting)

This platform is a **portfolio-grade project** that shows you can design, build, and deploy security automation at scale—exactly what companies like Wiz look for in security engineers.
