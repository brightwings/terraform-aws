"""
Lambda Function: deprovision_slack
Purpose: Deactivates user in Slack workspace (offboarding)

Input (from Step Functions parallel state):
{
    "user_id": "nicole",
    "reason": "termination",
    "system": "slack",
    "slack_user_id": "U01ABC123",  # Retrieved from DynamoDB
    "execution_arn": "..."
}

Output:
{
    ...input fields...,
    "deprovisioning_status": "success",
    "actions_taken": [
        "deactivated_account",
        "logged_security_event"
    ],
    "deprovisioned_at": "2026-02-17T16:00:00Z"
}

Security Actions:
1. Deactivate Slack account (user cannot log in)
2. Log security event for audit trail
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
SLACK_SECRET_ARN = os.environ.get(
    'SLACK_SECRET_ARN',
    'arn:aws:secretsmanager:us-east-1:YOUR_AWS_ACCOUNT_ID:secret:slack-api-token-xyz123'
)
PROVISIONING_STATE_TABLE = os.environ.get(
    'PROVISIONING_STATE_TABLE',
    'saas-automation-dev-provisioning-state'
)
SLACK_WORKSPACE = os.environ.get('SLACK_WORKSPACE', 'brightwings')


class SlackDeprovisioningError(Exception):
    """Custom exception for Slack deprovisioning failures"""
    pass


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler - deactivates user in Slack workspace

    Args:
        event: Input from Step Functions
        context: Lambda runtime context

    Returns:
        Deprovisioning result

    Raises:
        SlackDeprovisioningError: If Slack API call fails (CRITICAL!)
    """

    logger.info(json.dumps({
        "event": "slack_deprovisioning_started",
        "request_id": context.aws_request_id,
        "user_id": event.get('user_id'),
        "reason": event.get('reason')
    }))

    try:
        user_id = event['user_id']
        slack_user_id = event.get('slack_user_id')
        reason = event.get('reason', 'unknown')

        if not slack_user_id:
            raise SlackDeprovisioningError(
                "slack_user_id not provided. Cannot deprovision without user ID."
            )

        # Step 1: Get Slack API token
        slack_token = get_slack_token()

        # Step 2: Check if user is still active (idempotency)
        if not check_user_is_active(slack_user_id, slack_token):
            logger.info(json.dumps({
                "event": "user_not_active",
                "slack_user_id": slack_user_id,
                "message": "User already deactivated (idempotent)"
            }))

            actions_taken = ["already_deactivated"]

        else:
            actions_taken = []

            # Step 3: Deactivate Slack account
            deactivate_account(slack_user_id, slack_token)
            actions_taken.append("deactivated_account")

            # Step 4: Log security event
            log_security_event(
                user_id=user_id,
                slack_user_id=slack_user_id,
                reason=reason,
                actions=actions_taken
            )
            actions_taken.append("logged_security_event")

        # Step 5: Update DynamoDB record to 'deprovisioned'
        update_dynamodb_record(
            user_id=user_id,
            system='slack',
            status='deprovisioned',
            deprovisioned_reason=reason
        )

        # Step 6: Return success
        result = {
            **event,
            "deprovisioning_status": "success",
            "actions_taken": actions_taken,
            "deprovisioned_at": datetime.now().isoformat() + "Z"
        }

        logger.info(json.dumps({
            "event": "slack_deprovisioning_success",
            "request_id": context.aws_request_id,
            "user_id": user_id,
            "slack_user_id": slack_user_id,
            "actions_taken": actions_taken
        }))

        return result

    except Exception as e:
        logger.error(json.dumps({
            "event": "slack_deprovisioning_failed",
            "request_id": context.aws_request_id,
            "user_id": event.get('user_id'),
            "error": str(e),
            "error_type": type(e).__name__,
            "severity": "HIGH"
        }))

        # Alert security team of failure
        alert_security_team_critical_failure(
            user_id=event.get('user_id'),
            system='slack',
            error=str(e)
        )

        raise SlackDeprovisioningError(str(e))


def get_slack_token() -> str:
    """Retrieve Slack API token from Secrets Manager"""

    try:
        response = secrets_manager.get_secret_value(SecretId=SLACK_SECRET_ARN)

        if 'SecretString' in response:
            secret = response['SecretString']
            try:
                secret_dict = json.loads(secret)
                return secret_dict.get('slack_token') or secret_dict.get('token')
            except json.JSONDecodeError:
                return secret
        else:
            raise SlackDeprovisioningError("Secret not found")

    except ClientError as e:
        raise SlackDeprovisioningError(f"Failed to retrieve Slack token: {e}")


def check_user_is_active(user_id: str, slack_token: str) -> bool:
    """
    Check if user is still active in Slack (idempotency)

    Slack API: GET /api/users.info?user={user_id}
    Returns: User object with 'deleted' field

    Args:
        user_id: Slack user ID
        slack_token: Slack API token

    Returns:
        True if user is active, False otherwise
    """

    # MOCK: For demo
    # In production:
    # import requests
    # response = requests.get(
    #     'https://slack.com/api/users.info',
    #     headers={'Authorization': f'Bearer {slack_token}'},
    #     params={'user': user_id}
    # )
    # if response.json()['ok']:
    #     return not response.json()['user'].get('deleted', False)
    # return False

    logger.info(json.dumps({
        "event": "checking_user_is_active",
        "user_id": user_id,
        "note": "MOCK: Assuming user is active"
    }))

    return True  # Mock: User is active


def deactivate_account(user_id: str, slack_token: str) -> None:
    """
    Deactivate Slack user account

    Slack API: POST /api/admin.users.remove
    Body: {"user_id": "U01ABC123", "team_id": "T01ABC123"}

    Security Note: Deactivation (not deletion) preserves message history

    Args:
        user_id: Slack user ID
        slack_token: Slack API token

    Raises:
        SlackDeprovisioningError: If deactivation fails (CRITICAL!)
    """

    # MOCK: For demo
    # In production:
    # import requests
    # response = requests.post(
    #     'https://slack.com/api/admin.users.remove',
    #     headers={'Authorization': f'Bearer {slack_token}'},
    #     json={'user_id': user_id, 'team_id': SLACK_TEAM_ID}
    # )
    # if not response.json()['ok']:
    #     raise SlackDeprovisioningError(
    #         f"Failed to deactivate user: {response.json()['error']}"
    #     )

    logger.info(json.dumps({
        "event": "account_deactivated",
        "user_id": user_id,
        "slack_workspace": SLACK_WORKSPACE,
        "note": "MOCK: User account deactivated in Slack"
    }))


def log_security_event(
    user_id: str,
    slack_user_id: str,
    reason: str,
    actions: List[str]
) -> None:
    """Log security event for offboarding (audit trail)"""

    security_event = {
        "event_type": "user_deprovisioned",
        "system": "slack",
        "user_id": user_id,
        "slack_user_id": slack_user_id,
        "reason": reason,
        "actions_taken": actions,
        "timestamp": datetime.now().isoformat() + "Z",
        "severity": "high"
    }

    logger.info(json.dumps({
        "event": "security_event_logged",
        **security_event
    }))


def update_dynamodb_record(
    user_id: str,
    system: str,
    status: str,
    deprovisioned_reason: str
) -> None:
    """Update DynamoDB record to 'deprovisioned' status"""

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
    """Alert security team of CRITICAL deprovisioning failure"""

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


# For local testing
if __name__ == "__main__":
    test_event = {
        "user_id": "test-user",
        "slack_user_id": "U01ABC123",
        "reason": "termination",
        "system": "slack"
    }

    class MockContext:
        request_id = "local-test-12345"
        function_name = "deprovision_slack"

    print("Note: This uses MOCK Slack API calls\n")

    try:
        result = lambda_handler(test_event, MockContext())
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Error: {e}")
