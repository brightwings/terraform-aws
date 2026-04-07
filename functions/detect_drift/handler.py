"""
Lambda Function: detect_drift
Purpose: Detects configuration drift between expected state (DynamoDB) and
         actual state (GitHub, Slack, AWS, Jira APIs)

Triggered by: EventBridge scheduled rule (runs hourly)

Input (from EventBridge):
{
    "source": "aws.events",
    "detail-type": "Scheduled Event",
    "detail": {}
}

Output:
{
    "drift_events_found": 3,
    "drift_by_system": {
        "github": 1,
        "slack": 0,
        "aws": 2,
        "jira": 0
    },
    "critical_drift_count": 2,
    "checked_at": "2026-02-17T18:00:00Z"
}

Drift Types Detected:
1. ACCESS_REMOVED: User in DynamoDB as 'active' but no longer in system
   → Severity: CRITICAL (access revoked outside automation)
2. UNAUTHORIZED_ACCESS: User in system but no DynamoDB record
   → Severity: CRITICAL (access granted outside automation)

Why Drift Detection Matters:
- Your automation is only as good as its awareness of reality
- Manual changes by admins break the expected state model
- Unauthorized access = potential insider threat or policy violation
- Removed access = user deprovisioned outside workflow (audit gap)
"""

import json
import logging
import os
import uuid
import urllib.request
import urllib.error
from datetime import datetime
from typing import Dict, Any, List, Optional
import boto3
from botocore.exceptions import ClientError

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# AWS clients
dynamodb = boto3.client('dynamodb')
secrets_manager = boto3.client('secretsmanager')
sns = boto3.client('sns')

# Environment variables
PROVISIONING_STATE_TABLE = os.environ.get(
    'PROVISIONING_STATE_TABLE',
    'saas-automation-dev-provisioning-state'
)
DRIFT_EVENTS_TABLE = os.environ.get(
    'DRIFT_EVENTS_TABLE',
    'saas-automation-dev-drift-events'
)
GITHUB_SECRET_ARN = os.environ.get(
    'GITHUB_SECRET_ARN',
    'arn:aws:secretsmanager:us-east-1:YOUR_AWS_ACCOUNT_ID:secret:github-api-token-xyz123'
)
GITHUB_ORG = os.environ.get('GITHUB_ORG', 'example-corp')
JIRA_SECRET_ARN = os.environ.get(
    'JIRA_SECRET_ARN',
    'arn:aws:secretsmanager:us-east-1:YOUR_AWS_ACCOUNT_ID:secret:jira-api-token-xyz123'
)
JIRA_SITE = os.environ.get('JIRA_SITE', 'example-corp')
DRIFT_ALERT_SNS_ARN = os.environ.get('DRIFT_ALERT_SNS_ARN', '')
NAME_PREFIX = os.environ.get('NAME_PREFIX', 'saas-automation-dev')


def _github_api(method: str, path: str, token: str) -> tuple:
    """Make a GitHub API call using stdlib urllib."""
    url = f'https://api.github.com{path}'
    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
    }
    req = urllib.request.Request(url, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode('utf-8')
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8')
        return e.code, json.loads(raw) if raw else {}


def get_github_token() -> str:
    """Retrieve GitHub API token from Secrets Manager."""
    response = secrets_manager.get_secret_value(SecretId=GITHUB_SECRET_ARN)
    secret = response['SecretString']
    try:
        d = json.loads(secret)
        return d.get('github_token') or d.get('token')
    except json.JSONDecodeError:
        return secret


class DriftEvent:
    """Represents a single drift event"""

    def __init__(
        self,
        user_id: str,
        system: str,
        drift_type: str,
        expected_state: str,
        actual_state: str,
        severity: str,
        details: Dict[str, Any]
    ):
        self.event_id = str(uuid.uuid4())
        self.user_id = user_id
        self.system = system
        self.drift_type = drift_type
        self.expected_state = expected_state
        self.actual_state = actual_state
        self.severity = severity
        self.details = details
        self.detected_at = datetime.now().isoformat() + "Z"
        self.status = "open"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "user_id": self.user_id,
            "system": self.system,
            "drift_type": self.drift_type,
            "expected_state": self.expected_state,
            "actual_state": self.actual_state,
            "severity": self.severity,
            "details": self.details,
            "detected_at": self.detected_at,
            "status": self.status
        }


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler - detects drift across all systems

    Args:
        event: EventBridge scheduled event
        context: Lambda runtime context

    Returns:
        Drift detection summary
    """

    logger.info(json.dumps({
        "event": "drift_detection_started",
        "request_id": context.aws_request_id,
        "trigger": event.get('source', 'manual')
    }))

    all_drift_events = []
    drift_by_system = {
        "github": 0,
        "slack": 0,
        "aws": 0,
        "jira": 0
    }

    try:
        # Step 1: Get all active provisioning records from DynamoDB
        active_records = get_all_active_records()

        logger.info(json.dumps({
            "event": "active_records_retrieved",
            "count": len(active_records)
        }))

        # Step 2: Group records by system for efficient API calls
        records_by_system = group_by_system(active_records)

        # Step 3: Check each system for drift
        github_drift = check_github_drift(records_by_system.get('github', []))
        all_drift_events.extend(github_drift)
        drift_by_system['github'] = len(github_drift)

        aws_drift = check_aws_drift(records_by_system.get('aws', []))
        all_drift_events.extend(aws_drift)
        drift_by_system['aws'] = len(aws_drift)

        jira_drift = check_jira_drift(records_by_system.get('jira', []))
        all_drift_events.extend(jira_drift)
        drift_by_system['jira'] = len(jira_drift)

        # Slack: MOCK only (user not using yet)
        slack_drift = check_slack_drift(records_by_system.get('slack', []))
        all_drift_events.extend(slack_drift)
        drift_by_system['slack'] = len(slack_drift)

        # Step 4: Write drift events to DynamoDB
        if all_drift_events:
            for drift_event in all_drift_events:
                write_drift_event(drift_event)

            # Step 5: Alert on critical drift
            critical_events = [e for e in all_drift_events if e.severity == 'CRITICAL']
            if critical_events:
                send_drift_alert(critical_events)

        # Step 6: Return summary
        critical_count = sum(1 for e in all_drift_events if e.severity == 'CRITICAL')
        warning_count = sum(1 for e in all_drift_events if e.severity == 'WARNING')

        result = {
            "drift_events_found": len(all_drift_events),
            "critical_drift_count": critical_count,
            "warning_drift_count": warning_count,
            "drift_by_system": drift_by_system,
            "checked_records": len(active_records),
            "checked_at": datetime.now().isoformat() + "Z"
        }

        logger.info(json.dumps({
            "event": "drift_detection_complete",
            "request_id": context.aws_request_id,
            **result
        }))

        return result

    except Exception as e:
        logger.error(json.dumps({
            "event": "drift_detection_failed",
            "request_id": context.aws_request_id,
            "error": str(e),
            "error_type": type(e).__name__
        }))
        raise


def get_all_active_records() -> List[Dict[str, Any]]:
    """
    Query DynamoDB for ALL active provisioning records (across all users)

    Access Pattern: Scan with filter (or use status GSI)
    - In production: Use status-index GSI for efficiency
    - GSI: status (hash key) → list all 'active' records in one query

    Returns:
        List of active provisioning records
    """

    try:
        # Using GSI: query by status='active' across all users
        # More efficient than full scan on large tables
        response = dynamodb.query(
            TableName=PROVISIONING_STATE_TABLE,
            IndexName='status-index',
            KeyConditionExpression='#status = :active',
            ExpressionAttributeNames={
                '#status': 'status'
            },
            ExpressionAttributeValues={
                ':active': {'S': 'active'}
            }
        )

        return response.get('Items', [])

    except ClientError as e:
        logger.error(json.dumps({
            "event": "dynamodb_query_failed",
            "error": str(e)
        }))
        raise


def group_by_system(records: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Group records by system for efficient API calls

    Why: One API call can check all GitHub users at once
    vs. one API call per user (N+1 problem)

    Args:
        records: List of DynamoDB records

    Returns:
        Records grouped by system name
    """

    groups: Dict[str, List] = {}

    for record in records:
        system = record.get('system', {}).get('S', '')
        if system not in groups:
            groups[system] = []
        groups[system].append(record)

    return groups


def check_github_drift(records: List[Dict[str, Any]]) -> List[DriftEvent]:
    """
    Check GitHub org for drift against DynamoDB records

    Checks:
    1. ACCESS_REMOVED: User in DynamoDB as 'active' but not in GitHub org
       → Someone was removed from the org outside the offboarding workflow
    2. UNAUTHORIZED_ACCESS: User in GitHub org but no DynamoDB record
       → Someone was added to the org outside the onboarding workflow

    Args:
        records: DynamoDB records for GitHub system

    Returns:
        List of drift events detected
    """

    drift_events = []

    try:
        github_token = get_github_token()
    except Exception as e:
        logger.error(json.dumps({
            "event": "github_token_retrieval_failed",
            "error": str(e)
        }))
        return drift_events

    # Fetch all current org members from GitHub (paginate to handle large orgs)
    actual_members = {}  # login -> user dict
    page = 1
    while True:
        status, page_members = _github_api(
            'GET',
            f'/orgs/{GITHUB_ORG}/members?per_page=100&page={page}',
            github_token
        )
        if status != 200 or not page_members:
            break
        for m in page_members:
            actual_members[m['login']] = m
        if len(page_members) < 100:
            break
        page += 1

    logger.info(json.dumps({
        "event": "github_members_fetched",
        "github_org": GITHUB_ORG,
        "actual_member_count": len(actual_members)
    }))

    # Build set of usernames tracked in DynamoDB
    tracked_usernames = {}  # github_login -> user_id
    for record in records:
        user_id = record.get('user_id', {}).get('S', '')
        github_username = record.get('system_username', {}).get('S', '')
        if not github_username:
            logger.warning(json.dumps({
                "event": "missing_github_username",
                "user_id": user_id,
                "message": "No GitHub username in DynamoDB record — cannot check drift"
            }))
            continue
        tracked_usernames[github_username] = user_id

    # Check 1: ACCESS_REMOVED — tracked in DynamoDB but not in org
    for github_username, user_id in tracked_usernames.items():
        if github_username not in actual_members:
            drift_event = DriftEvent(
                user_id=user_id,
                system="github",
                drift_type="ACCESS_REMOVED",
                expected_state="active",
                actual_state="not_member",
                severity="CRITICAL",
                details={
                    "github_username": github_username,
                    "github_org": GITHUB_ORG,
                    "message": f"{github_username} removed from {GITHUB_ORG} org outside the offboarding workflow",
                    "remediation": "Verify intentional removal and run offboarding workflow to update DynamoDB"
                }
            )
            drift_events.append(drift_event)
            logger.warning(json.dumps({
                "event": "drift_detected",
                "drift_type": "ACCESS_REMOVED",
                "system": "github",
                "user_id": user_id,
                "github_username": github_username,
                "severity": "CRITICAL"
            }))

    # Check 2: UNAUTHORIZED_ACCESS — in org but not tracked in DynamoDB
    for github_login in actual_members:
        if github_login not in tracked_usernames:
            drift_event = DriftEvent(
                user_id=github_login,  # no canonical user_id — use github login
                system="github",
                drift_type="UNAUTHORIZED_ACCESS",
                expected_state="not_provisioned",
                actual_state="org_member",
                severity="CRITICAL",
                details={
                    "github_username": github_login,
                    "github_org": GITHUB_ORG,
                    "message": f"{github_login} is in the {GITHUB_ORG} org but was never onboarded through the provisioning workflow",
                    "remediation": "Verify this person should have access. If not, remove immediately. If yes, run onboarding to track them."
                }
            )
            drift_events.append(drift_event)
            logger.warning(json.dumps({
                "event": "drift_detected",
                "drift_type": "UNAUTHORIZED_ACCESS",
                "system": "github",
                "github_username": github_login,
                "severity": "CRITICAL"
            }))

    return drift_events


def check_aws_drift(records: List[Dict[str, Any]]) -> List[DriftEvent]:
    """
    Check AWS IAM for drift against DynamoDB records

    Checks:
    1. ACCESS_REMOVED: User in DynamoDB but IAM user deleted/disabled
    2. PRIVILEGE_ESCALATION: User in group not authorized in DynamoDB

    Args:
        records: DynamoDB records for AWS system

    Returns:
        List of drift events detected
    """

    if not records:
        return []

    drift_events = []
    iam_client = boto3.client('iam')

    for record in records:
        user_id = record.get('user_id', {}).get('S', '')
        aws_username = record.get('system_username', {}).get('S', '')

        if not aws_username:
            continue

        try:
            # Check if IAM user still exists
            iam_client.get_user(UserName=aws_username)

            # Check group memberships for privilege escalation
            groups_response = iam_client.list_groups_for_user(UserName=aws_username)
            actual_groups = {g['GroupName'] for g in groups_response['Groups']}

            # Flag if user is in administrators group (unexpected escalation)
            admin_group = f"{NAME_PREFIX}-administrators"
            if admin_group in actual_groups:
                drift_event = DriftEvent(
                    user_id=user_id,
                    system="aws",
                    drift_type="PRIVILEGE_ESCALATION",
                    expected_state="developer_access",
                    actual_state="admin_access",
                    severity="CRITICAL",
                    details={
                        "aws_username": aws_username,
                        "actual_groups": list(actual_groups),
                        "message": f"User {aws_username} is in administrators group (not authorized)",
                        "remediation": "Remove from administrators group immediately"
                    }
                )
                drift_events.append(drift_event)

                logger.warning(json.dumps({
                    "event": "drift_detected",
                    "drift_type": "PRIVILEGE_ESCALATION",
                    "system": "aws",
                    "user_id": user_id,
                    "aws_username": aws_username,
                    "severity": "CRITICAL"
                }))

        except iam_client.exceptions.NoSuchEntityException:
            # IAM user was deleted outside automation
            drift_event = DriftEvent(
                user_id=user_id,
                system="aws",
                drift_type="ACCESS_REMOVED",
                expected_state="active",
                actual_state="user_deleted",
                severity="CRITICAL",
                details={
                    "aws_username": aws_username,
                    "message": f"IAM user {aws_username} deleted outside automation",
                    "remediation": "Verify intentional deletion and update DynamoDB"
                }
            )
            drift_events.append(drift_event)

            logger.warning(json.dumps({
                "event": "drift_detected",
                "drift_type": "ACCESS_REMOVED",
                "system": "aws",
                "user_id": user_id,
                "aws_username": aws_username,
                "severity": "CRITICAL"
            }))

        except ClientError as e:
            logger.error(json.dumps({
                "event": "iam_check_failed",
                "user_id": user_id,
                "error": str(e)
            }))

    return drift_events


def check_jira_drift(records: List[Dict[str, Any]]) -> List[DriftEvent]:
    """
    Check Jira workspace for drift against DynamoDB records

    Checks:
    1. ACCESS_REMOVED: User in DynamoDB but deactivated in Jira

    Args:
        records: DynamoDB records for Jira system

    Returns:
        List of drift events detected
    """

    if not records:
        return []

    drift_events = []

    # MOCK: For demo
    # In production:
    # for record in records:
    #     account_id = record['system_username']['S']
    #     response = requests.get(
    #         f'{JIRA_BASE_URL}/rest/api/3/user',
    #         auth=HTTPBasicAuth(credentials['email'], credentials['api_token']),
    #         params={'accountId': account_id}
    #     )
    #     user = response.json()
    #     if not user.get('active', False):
    #         # Drift: user deactivated outside automation
    #         ...

    logger.info(json.dumps({
        "event": "jira_drift_check_complete",
        "records_checked": len(records),
        "note": "MOCK: No drift detected (simulated)"
    }))

    return drift_events


def check_slack_drift(records: List[Dict[str, Any]]) -> List[DriftEvent]:
    """
    Check Slack workspace for drift against DynamoDB records

    PLACEHOLDER: Slack not in use yet

    Args:
        records: DynamoDB records for Slack system

    Returns:
        Empty list (placeholder)
    """

    if not records:
        return []

    logger.info(json.dumps({
        "event": "slack_drift_check_skipped",
        "note": "Slack not in active use - skipping drift check"
    }))

    return []


def write_drift_event(drift_event: DriftEvent) -> None:
    """
    Write drift event to DynamoDB drift_events table

    Schema matches drift_events table design:
    - Partition key: event_id
    - Sort key: detected_at
    - GSIs: severity-index, user-index, system-index

    Args:
        drift_event: DriftEvent to persist
    """

    try:
        event_dict = drift_event.to_dict()

        dynamodb.put_item(
            TableName=DRIFT_EVENTS_TABLE,
            Item={
                'event_id': {'S': event_dict['event_id']},
                'detected_at': {'S': event_dict['detected_at']},
                'user_id': {'S': event_dict['user_id']},
                'system': {'S': event_dict['system']},
                'drift_type': {'S': event_dict['drift_type']},
                'expected_state': {'S': event_dict['expected_state']},
                'actual_state': {'S': event_dict['actual_state']},
                'severity': {'S': event_dict['severity']},
                'status': {'S': event_dict['status']},
                'details': {'S': json.dumps(event_dict['details'])}
            }
        )

        logger.info(json.dumps({
            "event": "drift_event_written",
            "event_id": event_dict['event_id'],
            "user_id": event_dict['user_id'],
            "system": event_dict['system'],
            "drift_type": event_dict['drift_type']
        }))

    except ClientError as e:
        logger.error(json.dumps({
            "event": "drift_event_write_failed",
            "error": str(e)
        }))
        raise


def send_drift_alert(critical_events: List[DriftEvent]) -> None:
    """
    Send SNS alert for critical drift events

    Alert Schema:
    {
        "alert_type": "CRITICAL_DRIFT_DETECTED",
        "drift_count": 3,
        "events": [
            {
                "user_id": "bob",
                "system": "github",
                "drift_type": "ACCESS_REMOVED",
                "details": {...}
            }
        ],
        "action_required": "Investigate immediately",
        "detected_at": "2026-02-17T18:00:00Z"
    }

    Args:
        critical_events: List of CRITICAL severity drift events
    """

    alert = {
        "alert_type": "CRITICAL_DRIFT_DETECTED",
        "severity": "CRITICAL",
        "drift_count": len(critical_events),
        "events": [
            {
                "user_id": e.user_id,
                "system": e.system,
                "drift_type": e.drift_type,
                "details": e.details
            }
            for e in critical_events
        ],
        "action_required": "Investigate access discrepancies immediately",
        "detected_at": datetime.now().isoformat() + "Z"
    }

    logger.error(json.dumps({
        "event": "critical_drift_alert",
        **alert
    }))

    # Send to SNS if configured
    if DRIFT_ALERT_SNS_ARN:
        try:
            sns.publish(
                TopicArn=DRIFT_ALERT_SNS_ARN,
                Subject=f"CRITICAL: {len(critical_events)} drift events detected",
                Message=json.dumps(alert, indent=2)
            )
            logger.info(json.dumps({
                "event": "sns_alert_sent",
                "topic_arn": DRIFT_ALERT_SNS_ARN,
                "events_count": len(critical_events)
            }))
        except ClientError as e:
            logger.error(json.dumps({
                "event": "sns_alert_failed",
                "error": str(e)
            }))
    else:
        logger.info(json.dumps({
            "event": "sns_alert_skipped",
            "reason": "DRIFT_ALERT_SNS_ARN not configured"
        }))


# For local testing
if __name__ == "__main__":
    class MockContext:
        request_id = "local-test-12345"
        function_name = "detect_drift"

    mock_event = {
        "source": "aws.events",
        "detail-type": "Scheduled Event",
        "detail": {}
    }

    print("Note: This will query DynamoDB and IAM (requires AWS credentials)\n")

    try:
        result = lambda_handler(mock_event, MockContext())
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Error: {e}")
