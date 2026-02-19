"""
Lambda Function: deprovision_jira
Purpose: Removes user from Jira workspace (offboarding)

Input (from Step Functions parallel state):
{
    "user_id": "nicole",
    "reason": "termination",  # or "resignation", "end_of_contract"
    "system": "jira",
    "jira_account_id": "5d8e7f4abc123def",  # Retrieved from DynamoDB
    "execution_arn": "..."
}

Output:
{
    ...input fields...,
    "deprovisioning_status": "success",
    "actions_taken": [
        "removed_from_all_groups",
        "deactivated_account",
        "logged_security_event"
    ],
    "deprovisioned_at": "2026-02-17T16:00:00Z"
}

Security Actions:
1. Remove from all Jira groups (revokes project access)
2. Deactivate account (user cannot log in)
3. Log security event for audit trail
"""

import json
import logging
import os
from datetime import datetime
from typing import Dict, Any, List
import boto3
from botocore.exceptions import ClientError

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# AWS clients
secrets_manager = boto3.client('secretsmanager')
dynamodb = boto3.client('dynamodb')

# Environment variables
JIRA_SECRET_ARN = os.environ.get(
    'JIRA_SECRET_ARN',
    'arn:aws:secretsmanager:us-east-1:YOUR_AWS_ACCOUNT_ID:secret:jira-api-token-xyz123'
)
PROVISIONING_STATE_TABLE = os.environ.get(
    'PROVISIONING_STATE_TABLE',
    'saas-automation-dev-provisioning-state'
)
JIRA_SITE = os.environ.get('JIRA_SITE', 'brightwings')
JIRA_BASE_URL = f"https://{JIRA_SITE}.atlassian.net"


class JiraDeprovisioningError(Exception):
    """Custom exception for Jira deprovisioning failures"""
    pass


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler - deprovisions user from Jira workspace

    Args:
        event: Input from Step Functions
        context: Lambda runtime context

    Returns:
        Deprovisioning result

    Raises:
        JiraDeprovisioningError: If Jira API call fails (CRITICAL!)
    """

    logger.info(json.dumps({
        "event": "jira_deprovisioning_started",
        "request_id": context.aws_request_id,
        "user_id": event.get('user_id'),
        "reason": event.get('reason')
    }))

    try:
        user_id = event['user_id']
        jira_account_id = event.get('jira_account_id')
        reason = event.get('reason', 'unknown')

        if not jira_account_id:
            raise JiraDeprovisioningError(
                "jira_account_id not provided. Cannot deprovision without account ID."
            )

        # Step 1: Get Jira API credentials
        jira_credentials = get_jira_credentials()

        # Step 2: Check if user is still active (idempotency)
        if not check_user_is_active(jira_account_id, jira_credentials):
            logger.info(json.dumps({
                "event": "user_not_active",
                "jira_account_id": jira_account_id,
                "message": "User already deactivated (idempotent)"
            }))

            actions_taken = ["already_deactivated"]

        else:
            actions_taken = []

            # Step 3: Remove from all groups first (revokes project access)
            remove_from_all_groups(jira_account_id, jira_credentials)
            actions_taken.append("removed_from_all_groups")

            # Step 4: Deactivate account
            deactivate_account(jira_account_id, jira_credentials)
            actions_taken.append("deactivated_account")

            # Step 5: Log security event
            log_security_event(
                user_id=user_id,
                jira_account_id=jira_account_id,
                reason=reason,
                actions=actions_taken
            )
            actions_taken.append("logged_security_event")

        # Step 6: Update DynamoDB record to 'deprovisioned'
        update_dynamodb_record(
            user_id=user_id,
            system='jira',
            status='deprovisioned',
            deprovisioned_reason=reason
        )

        # Step 7: Return success
        result = {
            **event,
            "deprovisioning_status": "success",
            "actions_taken": actions_taken,
            "deprovisioned_at": datetime.now().isoformat() + "Z"
        }

        logger.info(json.dumps({
            "event": "jira_deprovisioning_success",
            "request_id": context.aws_request_id,
            "user_id": user_id,
            "jira_account_id": jira_account_id,
            "actions_taken": actions_taken
        }))

        return result

    except Exception as e:
        logger.error(json.dumps({
            "event": "jira_deprovisioning_failed",
            "request_id": context.aws_request_id,
            "user_id": event.get('user_id'),
            "error": str(e),
            "error_type": type(e).__name__,
            "severity": "CRITICAL"
        }))

        # CRITICAL: Deprovisioning failure is a security incident
        # Alert security team immediately
        alert_security_team_critical_failure(
            user_id=event.get('user_id'),
            system='jira',
            error=str(e)
        )

        raise JiraDeprovisioningError(str(e))


def get_jira_credentials() -> Dict[str, str]:
    """Retrieve Jira API credentials from Secrets Manager"""

    try:
        response = secrets_manager.get_secret_value(SecretId=JIRA_SECRET_ARN)

        if 'SecretString' in response:
            secret = response['SecretString']
            try:
                secret_dict = json.loads(secret)
                return {
                    'email': secret_dict.get('jira_email') or secret_dict.get('email'),
                    'api_token': secret_dict.get('jira_api_token') or secret_dict.get('api_token')
                }
            except json.JSONDecodeError:
                raise JiraDeprovisioningError("Invalid secret format")
        else:
            raise JiraDeprovisioningError("Secret not found")

    except ClientError as e:
        raise JiraDeprovisioningError(f"Failed to retrieve Jira credentials: {e}")


def check_user_is_active(account_id: str, credentials: Dict[str, str]) -> bool:
    """
    Check if user is still active in Jira (idempotency)

    Jira API: GET /rest/api/3/user?accountId={accountId}
    Returns: User object with 'active' field

    Args:
        account_id: Jira account ID
        credentials: Jira API credentials

    Returns:
        True if user is active, False otherwise
    """

    # MOCK: For demo
    # In production:
    # import requests
    # from requests.auth import HTTPBasicAuth
    #
    # response = requests.get(
    #     f'{JIRA_BASE_URL}/rest/api/3/user',
    #     auth=HTTPBasicAuth(credentials['email'], credentials['api_token']),
    #     params={'accountId': account_id}
    # )
    #
    # if response.status_code == 200:
    #     user = response.json()
    #     return user.get('active', False)
    # return False

    logger.info(json.dumps({
        "event": "checking_user_is_active",
        "account_id": account_id,
        "note": "MOCK: Assuming user is active"
    }))

    return True  # Mock: User is active


def remove_from_all_groups(account_id: str, credentials: Dict[str, str]) -> None:
    """
    Remove user from all Jira groups (revokes project access)

    Security Principle: Remove project access BEFORE deactivating account
    Why: If deactivation fails, user still loses project access

    Jira API:
    1. GET /rest/api/3/user/groups?accountId={accountId} (list groups)
    2. DELETE /rest/api/3/group/user?groupname={name}&accountId={accountId} (remove)

    Args:
        account_id: Jira account ID
        credentials: Jira API credentials

    Raises:
        JiraDeprovisioningError: If group removal fails
    """

    # MOCK: For demo
    # In production:
    # import requests
    # from requests.auth import HTTPBasicAuth
    #
    # # Get all groups user belongs to
    # groups_response = requests.get(
    #     f'{JIRA_BASE_URL}/rest/api/3/user/groups',
    #     auth=HTTPBasicAuth(credentials['email'], credentials['api_token']),
    #     params={'accountId': account_id}
    # )
    #
    # groups = groups_response.json()
    #
    # for group in groups:
    #     # Remove from group
    #     requests.delete(
    #         f'{JIRA_BASE_URL}/rest/api/3/group/user',
    #         auth=HTTPBasicAuth(credentials['email'], credentials['api_token']),
    #         params={
    #             'groupname': group['name'],
    #             'accountId': account_id
    #         }
    #     )

    logger.info(json.dumps({
        "event": "removed_from_all_groups",
        "account_id": account_id,
        "note": "MOCK: User removed from all Jira groups (loses all project access)"
    }))


def deactivate_account(account_id: str, credentials: Dict[str, str]) -> None:
    """
    Deactivate Jira user account

    Jira API: DELETE /rest/api/3/user?accountId={accountId}
    Note: This deactivates (not deletes) the user - preserves audit history

    Args:
        account_id: Jira account ID
        credentials: Jira API credentials

    Raises:
        JiraDeprovisioningError: If deactivation fails (CRITICAL!)
    """

    # MOCK: For demo
    # In production:
    # import requests
    # from requests.auth import HTTPBasicAuth
    #
    # response = requests.delete(
    #     f'{JIRA_BASE_URL}/rest/api/3/user',
    #     auth=HTTPBasicAuth(credentials['email'], credentials['api_token']),
    #     params={'accountId': account_id}
    # )
    #
    # if response.status_code != 204:
    #     raise JiraDeprovisioningError(
    #         f"Failed to deactivate user: {response.json()['errorMessages']}"
    #     )

    logger.info(json.dumps({
        "event": "account_deactivated",
        "account_id": account_id,
        "jira_site": JIRA_SITE,
        "note": "MOCK: User account deactivated in Jira"
    }))


def log_security_event(
    user_id: str,
    jira_account_id: str,
    reason: str,
    actions: List[str]
) -> None:
    """
    Log security event for offboarding (audit trail)

    Security Event Schema:
    {
        "event_type": "user_deprovisioned",
        "system": "jira",
        "user_id": "nicole",
        "reason": "termination",
        "actions_taken": [...],
        "timestamp": "2026-02-17T16:00:00Z"
    }

    This would be stored in:
    - CloudWatch Logs (immediate)
    - DynamoDB security_events table (queryable)
    - S3 for long-term retention (compliance)
    - SIEM (Splunk, Datadog, etc.)

    Args:
        user_id: Canonical user ID
        jira_account_id: Jira account ID
        reason: Offboarding reason
        actions: Actions taken during deprovisioning
    """

    security_event = {
        "event_type": "user_deprovisioned",
        "system": "jira",
        "user_id": user_id,
        "jira_account_id": jira_account_id,
        "reason": reason,
        "actions_taken": actions,
        "timestamp": datetime.now().isoformat() + "Z",
        "severity": "high"
    }

    logger.info(json.dumps({
        "event": "security_event_logged",
        **security_event
    }))

    # TODO: Write to DynamoDB security_events table
    # TODO: Send to SIEM


def update_dynamodb_record(
    user_id: str,
    system: str,
    status: str,
    deprovisioned_reason: str
) -> None:
    """
    Update DynamoDB record to 'deprovisioned' status

    Args:
        user_id: Canonical user ID
        system: System name ('jira')
        status: New status ('deprovisioned')
        deprovisioned_reason: Reason for deprovisioning
    """

    try:
        dynamodb.update_item(
            TableName=PROVISIONING_STATE_TABLE,
            Key={
                'user_id': {'S': user_id},
                'system': {'S': system}
            },
            UpdateExpression='SET #status = :status, deprovisioned_at = :deprovisioned_at, deprovisioned_reason = :reason',
            ExpressionAttributeNames={
                '#status': 'status'
            },
            ExpressionAttributeValues={
                ':status': {'S': status},
                ':deprovisioned_at': {'S': datetime.now().isoformat() + "Z"},
                ':reason': {'S': deprovisioned_reason}
            }
        )

        logger.info(json.dumps({
            "event": "dynamodb_record_updated",
            "user_id": user_id,
            "system": system,
            "status": status
        }))

    except ClientError as e:
        logger.error(json.dumps({
            "event": "dynamodb_update_failed",
            "error": str(e)
        }))
        raise


def alert_security_team_critical_failure(
    user_id: str,
    system: str,
    error: str
) -> None:
    """
    Alert security team of CRITICAL deprovisioning failure

    Deprovisioning failures are security incidents!
    User may still have access to sensitive resources.

    Alert channels:
    - PagerDuty (P1 incident)
    - Slack #security-alerts
    - Email to security@brightwings.io
    - SNS topic for security events

    Args:
        user_id: User who should be deprovisioned
        system: System where deprovisioning failed
        error: Error message
    """

    alert = {
        "alert_type": "CRITICAL_DEPROVISIONING_FAILURE",
        "severity": "P1",
        "user_id": user_id,
        "system": system,
        "error": error,
        "action_required": f"Manually remove user {user_id} from {system} IMMEDIATELY",
        "timestamp": datetime.now().isoformat() + "Z"
    }

    logger.error(json.dumps({
        "event": "critical_security_alert",
        **alert
    }))

    # TODO: Send to PagerDuty
    # TODO: Send to Slack
    # TODO: Send SNS notification


# For local testing
if __name__ == "__main__":
    test_event = {
        "user_id": "test-user",
        "jira_account_id": "5d8e7f4abc",
        "reason": "termination",
        "system": "jira"
    }

    class MockContext:
        request_id = "local-test-12345"
        function_name = "deprovision_jira"

    print("Note: This uses MOCK Jira API calls\n")

    try:
        result = lambda_handler(test_event, MockContext())
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Error: {e}")
