"""
Lambda Function: normalize_telemetry
Purpose: Normalizes raw security events from multiple SaaS systems into a
         unified ECS-inspired schema for SIEM consumption

Why Normalization Matters:
- GitHub, AWS, Jira, Slack all use different field names
- SIEMs need consistent schemas to write detection rules
- Without normalization: 4 detection rules for "user removed" (one per system)
- With normalization: 1 detection rule that works across all systems

ECS (Elastic Common Schema) Key Fields Used:
- @timestamp      - When the event occurred
- event.action    - What happened (user.provisioned, user.deprovisioned, drift.detected)
- event.category  - IAM, authentication, configuration, etc.
- event.outcome   - success, failure, unknown
- user.id         - Canonical user identifier
- user.email      - User email
- labels          - Custom key-value metadata

Input Sources:
1. Our own workflow events (provisioning/deprovisioning)
2. Drift detection events
3. (Future) Raw webhook events from GitHub, Slack, Jira

Input (via SNS or direct invocation):
{
    "source": "saas-automation.offboarding",
    "raw_event": {
        "event": "github_deprovisioning_success",
        "user_id": "nicole",
        "github_username": "nmaulino",
        "reason": "termination",
        "actions_taken": ["removed_from_all_teams", "removed_from_org"]
    }
}

Output (ECS-normalized):
{
    "@timestamp": "2026-02-17T16:00:00Z",
    "event": {
        "id": "uuid",
        "action": "user.deprovisioned",
        "category": ["iam"],
        "type": ["deletion"],
        "outcome": "success",
        "severity": 50,
        "dataset": "saas.automation",
        "provider": "github"
    },
    "user": {
        "id": "nicole",
        "name": "nmaulino",
        "email": "nicole@brightwings.io"
    },
    "labels": {
        "reason": "termination",
        "system": "github",
        "actions_taken": "removed_from_all_teams,removed_from_org"
    }
}
"""

import json
import logging
import os
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# AWS clients
dynamodb = boto3.client('dynamodb')
firehose = boto3.client('firehose')

# Environment variables
NORMALIZED_EVENTS_TABLE = os.environ.get(
    'NORMALIZED_EVENTS_TABLE',
    'saas-automation-dev-security-events'
)
FIREHOSE_STREAM_NAME = os.environ.get(
    'FIREHOSE_STREAM_NAME',
    ''  # When configured, streams to S3/Splunk/Datadog
)


# =============================================================================
# ECS Event Schema
#
# ECS standardizes field names across all security tools.
# Key principle: same field name means the same thing everywhere.
#
# event.action examples:
#   "user.provisioned"    - User granted access to a system
#   "user.deprovisioned"  - User access revoked
#   "drift.detected"      - Config drift found
#   "user.modified"       - User properties changed
#
# event.category (array, can have multiple):
#   "iam"             - Identity and access management
#   "authentication"  - Login/logout events
#   "configuration"   - Config changes
#
# event.type (array, describes the operation):
#   "creation"  - Something was created
#   "deletion"  - Something was deleted
#   "change"    - Something was modified
#   "info"      - Informational (no state change)
# =============================================================================

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler - normalizes incoming security events

    Can be triggered by:
    - SNS topic (from provisioning/deprovisioning Lambdas)
    - EventBridge (from drift detection)
    - Direct invocation (for testing)
    """

    logger.info(json.dumps({
        "event": "normalization_started",
        "request_id": context.request_id,
        "source": event.get("source", "unknown")
    }))

    normalized_events = []

    # Handle SNS wrapper (events come batched in Records array)
    if "Records" in event:
        for record in event["Records"]:
            if record.get("EventSource") == "aws:sns":
                raw_payload = json.loads(record["Sns"]["Message"])
                normalized = normalize_event(raw_payload)
                if normalized:
                    normalized_events.append(normalized)
    else:
        # Direct invocation (testing or EventBridge)
        normalized = normalize_event(event)
        if normalized:
            normalized_events.append(normalized)

    # Write normalized events to DynamoDB and/or Firehose
    for normalized_event in normalized_events:
        write_normalized_event(normalized_event)

    result = {
        "events_normalized": len(normalized_events),
        "processed_at": datetime.now().isoformat() + "Z"
    }

    logger.info(json.dumps({
        "event": "normalization_complete",
        **result
    }))

    return result


def normalize_event(raw_event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Route raw event to the correct normalizer based on source

    Args:
        raw_event: Raw event from any source system

    Returns:
        ECS-normalized event dict, or None if unrecognized
    """

    event_name = raw_event.get("event", "")
    source = raw_event.get("source", "")

    # Route to correct normalizer
    # NOTE: Check "deprovisioning" before "provisioning" - substring match order matters
    if "deprovisioning_success" in event_name:
        return normalize_deprovisioning_event(raw_event)

    elif "provisioning_success" in event_name or "provisioning_started" in event_name:
        return normalize_provisioning_event(raw_event)

    elif "drift_detected" in event_name or raw_event.get("drift_type"):
        return normalize_drift_event(raw_event)

    elif "offboarding_completed" in event_name:
        return normalize_offboarding_event(raw_event)

    elif "critical_security_alert" in event_name:
        return normalize_security_alert(raw_event)

    else:
        logger.warning(json.dumps({
            "event": "unrecognized_event_type",
            "event_name": event_name,
            "source": source
        }))
        return None


def normalize_provisioning_event(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize provisioning events to ECS schema

    Example input:
    {
        "event": "aws_provisioning_success",
        "user_id": "isaac",
        "aws_username": "isaac",
        "aws_groups": ["saas-automation-dev-developers"]
    }

    Example output (ECS):
    {
        "event": {
            "action": "user.provisioned",
            "category": ["iam"],
            "type": ["creation"],
            "outcome": "success"
        },
        "user": {"id": "isaac"},
        "labels": {"system": "aws", "groups": "saas-automation-dev-developers"}
    }
    """

    system = _extract_system(raw)

    return _build_ecs_event(
        action="user.provisioned",
        category=["iam"],
        event_type=["creation"],
        outcome="success",
        severity=25,  # Low: expected operation
        user_id=raw.get("user_id"),
        email=raw.get("email"),
        labels={
            "system": system,
            "execution_arn": raw.get("execution_arn", ""),
            # System-specific fields flattened into labels
            **_extract_system_labels(raw, system)
        }
    )


def normalize_deprovisioning_event(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize deprovisioning events to ECS schema

    Example input:
    {
        "event": "github_deprovisioning_success",
        "user_id": "nicole",
        "github_username": "nmaulino",
        "reason": "termination",
        "actions_taken": ["removed_from_all_teams", "removed_from_org"]
    }
    """

    system = _extract_system(raw)
    actions = raw.get("actions_taken", [])

    return _build_ecs_event(
        action="user.deprovisioned",
        category=["iam"],
        event_type=["deletion"],
        outcome="success",
        severity=50,  # Medium: security-relevant operation
        user_id=raw.get("user_id"),
        labels={
            "system": system,
            "reason": raw.get("reason", "unknown"),
            "actions_taken": ",".join(actions) if isinstance(actions, list) else str(actions),
            **_extract_system_labels(raw, system)
        }
    )


def normalize_drift_event(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize drift detection events to ECS schema

    Example input:
    {
        "event": "drift_detected",
        "drift_type": "PRIVILEGE_ESCALATION",
        "system": "aws",
        "user_id": "isaac",
        "aws_username": "isaac",
        "severity": "CRITICAL"
    }
    """

    drift_type = raw.get("drift_type", "UNKNOWN")
    system = raw.get("system", raw.get("source_system", "unknown"))

    # Map drift type to ECS severity score (0-100)
    # Critical drift gets highest scores for SIEM alerting priority
    severity_map = {
        "PRIVILEGE_ESCALATION": 90,  # Near-critical: active unauthorized access
        "ACCESS_REMOVED": 70,        # High: compliance gap, audit failure
        "UNAUTHORIZED_ACCESS": 90,   # Near-critical: unknown actor with access
        "ROLE_MISMATCH": 60,         # Medium-high: config deviation
    }
    severity = severity_map.get(drift_type, 75)

    # Map drift type to ECS event type
    type_map = {
        "PRIVILEGE_ESCALATION": ["change"],
        "ACCESS_REMOVED": ["deletion"],
        "UNAUTHORIZED_ACCESS": ["creation"],
        "ROLE_MISMATCH": ["change"],
    }
    event_type = type_map.get(drift_type, ["info"])

    return _build_ecs_event(
        action=f"drift.{drift_type.lower()}",
        category=["iam", "configuration"],
        event_type=event_type,
        outcome="unknown",  # Drift is a state, not a success/failure
        severity=severity,
        user_id=raw.get("user_id"),
        labels={
            "drift_type": drift_type,
            "system": system,
            "expected_state": raw.get("expected_state", ""),
            "actual_state": raw.get("actual_state", ""),
            "remediation": raw.get("details", {}).get("remediation", "") if isinstance(raw.get("details"), dict) else ""
        }
    )


def normalize_offboarding_event(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize workflow-level offboarding completion events

    Example input:
    {
        "event": "offboarding_completed",
        "user_id": "nicole",
        "reason": "termination",
        "systems_deprovisioned": ["github", "aws", "jira"],
        "systems_failed": []
    }
    """

    systems_failed = raw.get("systems_failed", [])
    outcome = "failure" if systems_failed else "success"
    severity = 75 if systems_failed else 50  # Higher if partial failure

    return _build_ecs_event(
        action="user.offboarded",
        category=["iam"],
        event_type=["deletion"],
        outcome=outcome,
        severity=severity,
        user_id=raw.get("user_id"),
        labels={
            "reason": raw.get("reason", "unknown"),
            "systems_deprovisioned": ",".join(raw.get("systems_deprovisioned", [])),
            "systems_failed": ",".join(systems_failed),
            "partial_failure": str(len(systems_failed) > 0).lower()
        }
    )


def normalize_security_alert(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize critical security alerts (deprovisioning failures, etc.)
    """

    return _build_ecs_event(
        action="alert.critical",
        category=["iam"],
        event_type=["info"],
        outcome="failure",
        severity=100,  # Maximum: security incident
        user_id=raw.get("user_id"),
        labels={
            "alert_type": raw.get("alert_type", "UNKNOWN"),
            "system": raw.get("system", "unknown"),
            "error": raw.get("error", ""),
            "action_required": raw.get("action_required", "")
        }
    )


# =============================================================================
# Helper Functions
# =============================================================================

def _build_ecs_event(
    action: str,
    category: List[str],
    event_type: List[str],
    outcome: str,
    severity: int,
    user_id: Optional[str],
    labels: Dict[str, str],
    email: Optional[str] = None
) -> Dict[str, Any]:
    """
    Build a normalized ECS event

    ECS Severity Scale (0-100):
    0-24:  Informational (routine operations)
    25-49: Low (expected security events)
    50-74: Medium (notable events, review later)
    75-89: High (investigate today)
    90-99: Critical (immediate response)
    100:   Emergency (security incident in progress)
    """

    return {
        "@timestamp": datetime.now().isoformat() + "Z",
        "event": {
            "id": str(uuid.uuid4()),
            "action": action,
            "category": category,
            "type": event_type,
            "outcome": outcome,
            "severity": severity,
            "dataset": "saas.automation",
            "module": "saas-security-automation"
        },
        "user": {
            "id": user_id or "unknown",
            "email": email or f"{user_id}@brightwings.io" if user_id else "unknown"
        },
        "labels": {k: str(v) for k, v in labels.items() if v is not None},
        "tags": ["saas-automation", "brightwings"]
    }


def _extract_system(raw: Dict[str, Any]) -> str:
    """Extract system name from raw event"""

    event_name = raw.get("event", "")
    system = raw.get("system", "")

    if system:
        return system

    # Infer from event name prefix
    for s in ["github", "slack", "aws", "jira"]:
        if event_name.startswith(s):
            return s

    return "unknown"


def _extract_system_labels(raw: Dict[str, Any], system: str) -> Dict[str, str]:
    """Extract system-specific fields as ECS labels"""

    labels = {}

    if system == "github":
        if raw.get("github_username"):
            labels["system_username"] = raw["github_username"]
        if raw.get("github_org"):
            labels["github_org"] = raw["github_org"]

    elif system == "aws":
        if raw.get("aws_username"):
            labels["system_username"] = raw["aws_username"]
        if raw.get("aws_groups"):
            groups = raw["aws_groups"]
            labels["aws_groups"] = ",".join(groups) if isinstance(groups, list) else str(groups)

    elif system == "jira":
        if raw.get("jira_account_id"):
            labels["system_username"] = raw["jira_account_id"]
        if raw.get("jira_workspace"):
            labels["jira_workspace"] = raw["jira_workspace"]

    elif system == "slack":
        if raw.get("slack_user_id"):
            labels["system_username"] = raw["slack_user_id"]

    return labels


def write_normalized_event(normalized_event: Dict[str, Any]) -> None:
    """
    Write normalized event to storage

    Options:
    1. DynamoDB - queryable, fast lookup
    2. Kinesis Firehose → S3 - cheap long-term storage, feeds Splunk/Datadog
    3. CloudWatch Logs - already happening via logger.info()

    For now: CloudWatch Logs (free, always-on)
    Future: Add Firehose for SIEM integration
    """

    logger.info(json.dumps({
        "event": "normalized_security_event",
        **normalized_event
    }))

    # Future: Stream to Firehose for S3/SIEM delivery
    if FIREHOSE_STREAM_NAME:
        try:
            firehose.put_record(
                DeliveryStreamName=FIREHOSE_STREAM_NAME,
                Record={
                    'Data': (json.dumps(normalized_event) + "\n").encode('utf-8')
                }
            )
        except ClientError as e:
            logger.error(json.dumps({
                "event": "firehose_write_failed",
                "error": str(e)
            }))


# =============================================================================
# Local Testing
# =============================================================================

if __name__ == "__main__":
    class MockContext:
        request_id = "local-test-12345"
        function_name = "normalize_telemetry"

    # Simulate the 4 event types our platform generates
    test_events = [
        {
            "name": "GitHub deprovisioning",
            "event": "github_deprovisioning_success",
            "user_id": "nicole",
            "github_username": "nmaulino",
            "reason": "termination",
            "actions_taken": ["removed_from_all_teams", "removed_from_org"],
            "system": "github"
        },
        {
            "name": "AWS provisioning",
            "event": "aws_provisioning_success",
            "user_id": "isaac",
            "aws_username": "isaac",
            "aws_groups": ["saas-automation-dev-developers"],
            "email": "isaac@brightwings.io",
            "system": "aws"
        },
        {
            "name": "Privilege escalation drift",
            "event": "drift_detected",
            "drift_type": "PRIVILEGE_ESCALATION",
            "system": "aws",
            "user_id": "isaac",
            "aws_username": "isaac",
            "expected_state": "developer_access",
            "actual_state": "admin_access",
            "details": {
                "remediation": "Remove from administrators group immediately"
            }
        },
        {
            "name": "Critical deprovisioning failure",
            "event": "critical_security_alert",
            "alert_type": "CRITICAL_DEPROVISIONING_FAILURE",
            "user_id": "nicole",
            "system": "github",
            "error": "GitHub API rate limit exceeded",
            "action_required": "Manually remove nmaulino from brightwings org"
        }
    ]

    print("=" * 60)
    print("Telemetry Normalization Output")
    print("=" * 60)

    normalizer_map = {
        "github_deprovisioning_success": normalize_deprovisioning_event,
        "aws_provisioning_success": normalize_provisioning_event,
        "drift_detected": normalize_drift_event,
        "critical_security_alert": normalize_security_alert,
    }

    for raw in test_events:
        print(f"\n--- {raw.pop('name')} ---")
        print(f"INPUT:  event={raw['event']}, user={raw.get('user_id')}")
        normalized = normalize_event(raw)
        if normalized:
            print(f"OUTPUT: action={normalized['event']['action']}, "
                  f"severity={normalized['event']['severity']}, "
                  f"outcome={normalized['event']['outcome']}")
            print(f"        labels={normalized['labels']}")
