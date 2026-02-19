"""
Lambda Function: provision_slack
Purpose: Provisions user in Slack workspace using Slack API

Input (from Step Functions parallel state):
{
    "user_id": "isaac",
    "email": "isaac@brightwings.io",
    "role": "developer",
    "system": "slack",
    "execution_arn": "..."
}

Note: Slack uses email-based invitations (no custom username needed)

Output:
{
    ...input fields...,
    "slack_user_id": "U01ABC123",
    "slack_email": "isaac@brightwings.io",
    "slack_workspace": "brightwings",
    "provisioning_status": "success",
    "provisioned_at": "2026-02-17T15:00:00Z"
}

Slack API Actions:
1. Check if user already exists (by email)
2. If not exists: Send workspace invitation
3. User receives email, accepts invitation
4. Update DynamoDB with Slack user ID
"""

import json
import logging
import os
from datetime import datetime
from typing import Dict, Any, Optional
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


class SlackProvisioningError(Exception):
    """Custom exception for Slack provisioning failures"""
    pass


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler - provisions user in Slack workspace

    Args:
        event: Input from Step Functions
        context: Lambda runtime context

    Returns:
        Provisioning result with Slack user ID

    Raises:
        SlackProvisioningError: If Slack API call fails
    """

    logger.info(json.dumps({
        "event": "slack_provisioning_started",
        "request_id": context.aws_request_id,
        "user_id": event.get('user_id')
    }))

    try:
        user_id = event['user_id']
        email = event['email']
        role = event.get('role', 'developer')

        # Step 1: Get Slack API token from Secrets Manager
        slack_token = get_slack_token()

        # Step 2: Check if user already exists in workspace (idempotency)
        slack_user = check_user_exists_in_workspace(email, slack_token)

        if slack_user:
            logger.info(json.dumps({
                "event": "user_already_exists",
                "email": email,
                "slack_user_id": slack_user['id'],
                "message": "User already in Slack workspace (idempotent)"
            }))
            slack_user_id = slack_user['id']
        else:
            # Step 3: Invite user to Slack workspace
            slack_user_id = invite_user_to_workspace(
                email=email,
                slack_token=slack_token
            )

        # Step 4: Update DynamoDB record
        update_dynamodb_record(
            user_id=user_id,
            system='slack',
            slack_user_id=slack_user_id,
            slack_email=email,
            status='active'
        )

        # Step 5: Return success
        result = {
            **event,
            "slack_user_id": slack_user_id,
            "slack_email": email,
            "slack_workspace": SLACK_WORKSPACE,
            "provisioning_status": "success",
            "provisioned_at": datetime.now().isoformat() + "Z"
        }

        logger.info(json.dumps({
            "event": "slack_provisioning_success",
            "request_id": context.aws_request_id,
            "user_id": user_id,
            "slack_user_id": slack_user_id
        }))

        return result

    except Exception as e:
        logger.error(json.dumps({
            "event": "slack_provisioning_failed",
            "request_id": context.aws_request_id,
            "error": str(e),
            "error_type": type(e).__name__
        }))

        # Update DynamoDB with error status
        try:
            update_dynamodb_record(
                user_id=event['user_id'],
                system='slack',
                slack_user_id=None,
                slack_email=None,
                status='error',
                error_message=str(e)
            )
        except Exception as db_error:
            logger.error(f"Failed to update DynamoDB error status: {db_error}")

        raise SlackProvisioningError(str(e))


def get_slack_token() -> str:
    """
    Retrieve Slack API token from AWS Secrets Manager

    Slack Token Types:
    - User Token (xoxp-*): Acts on behalf of user (not recommended for automation)
    - Bot Token (xoxb-*): Acts on behalf of app (recommended)
    - Admin Token: Required for user.admin.invite

    Required Scopes:
    - admin.invites:write (send workspace invitations)
    - users:read (check if user exists)
    - users:read.email (get user by email)

    Returns:
        Slack API token

    Raises:
        SlackProvisioningError: If secret retrieval fails
    """

    try:
        response = secrets_manager.get_secret_value(SecretId=SLACK_SECRET_ARN)

        if 'SecretString' in response:
            secret = response['SecretString']
            try:
                secret_dict = json.loads(secret)
                token = secret_dict.get('slack_token') or secret_dict.get('token')
            except json.JSONDecodeError:
                token = secret

            logger.info(json.dumps({
                "event": "slack_token_retrieved",
                "secret_arn": SLACK_SECRET_ARN
            }))

            return token
        else:
            raise SlackProvisioningError("Secret not found in SecretString")

    except ClientError as e:
        logger.error(json.dumps({
            "event": "secrets_manager_error",
            "error_code": e.response['Error']['Code'],
            "error_message": e.response['Error']['Message']
        }))
        raise SlackProvisioningError(f"Failed to retrieve Slack token: {e}")


def check_user_exists_in_workspace(email: str, slack_token: str) -> Optional[Dict[str, Any]]:
    """
    Check if user already exists in Slack workspace (idempotency)

    Slack API: GET https://slack.com/api/users.lookupByEmail
    Returns: User object if exists, None if not found

    Args:
        email: User email address
        slack_token: Slack API token

    Returns:
        User object if exists, None otherwise
    """

    # MOCK: For this demo, simulate API call
    # In production, use:
    # import requests
    # response = requests.get(
    #     'https://slack.com/api/users.lookupByEmail',
    #     headers={'Authorization': f'Bearer {slack_token}'},
    #     params={'email': email}
    # )
    # if response.json()['ok']:
    #     return response.json()['user']
    # return None

    logger.info(json.dumps({
        "event": "checking_slack_user_exists",
        "email": email,
        "note": "MOCK: Simulating API call - assuming user doesn't exist"
    }))

    # Mock: Return None (user doesn't exist, need to invite)
    return None


def invite_user_to_workspace(email: str, slack_token: str) -> str:
    """
    Invite user to Slack workspace

    Slack API: POST https://slack.com/api/admin.users.invite
    Body: {
        "email": "isaac@brightwings.io",
        "team_id": "T01ABC123",  # Workspace ID
        "channel_ids": "C01ABC123,C01DEF456",  # Default channels
        "is_restricted": false,  # Regular member (not guest)
        "is_ultra_restricted": false  # Regular member (not single-channel guest)
    }

    Security Note: User invited as REGULAR MEMBER
    - Can see all public channels
    - Can join any public channel
    - Cannot see private channels (unless invited)

    Args:
        email: User email address
        slack_token: Slack API token

    Returns:
        Slack user ID (e.g., "U01ABC123")

    Raises:
        SlackProvisioningError: If invitation fails
    """

    # MOCK: For this demo, simulate API call
    # In production, use:
    # import requests
    # response = requests.post(
    #     'https://slack.com/api/admin.users.invite',
    #     headers={'Authorization': f'Bearer {slack_token}'},
    #     json={
    #         'email': email,
    #         'team_id': SLACK_TEAM_ID,
    #         'channel_ids': 'C01GENERAL',  # Add to #general channel
    #         'is_restricted': False,  # Regular member
    #         'is_ultra_restricted': False
    #     }
    # )
    # if not response.json()['ok']:
    #     raise SlackProvisioningError(f"Slack API error: {response.json()['error']}")
    # return response.json()['user']['id']

    logger.info(json.dumps({
        "event": "slack_invitation_sent",
        "email": email,
        "workspace": SLACK_WORKSPACE,
        "note": "MOCK: Simulating API call"
    }))

    # Mock: Return fake Slack user ID
    mock_user_id = f"U{hash(email) % 1000000:06d}"
    logger.info(f"Mock Slack user ID generated: {mock_user_id}")

    return mock_user_id


def update_dynamodb_record(
    user_id: str,
    system: str,
    slack_user_id: Optional[str],
    slack_email: Optional[str],
    status: str,
    error_message: Optional[str] = None
) -> None:
    """
    Update DynamoDB record with Slack provisioning result

    Args:
        user_id: Canonical user ID
        system: System name ('slack')
        slack_user_id: Slack user ID (or None if error)
        slack_email: Slack email
        status: Provisioning status ('active' or 'error')
        error_message: Error message if status='error'
    """

    try:
        update_expression = 'SET #status = :status, updated_at = :updated_at'
        expression_values = {
            ':status': {'S': status},
            ':updated_at': {'S': datetime.now().isoformat() + "Z"}
        }

        if slack_user_id:
            update_expression += ', system_username = :username, system_metadata = :metadata'
            expression_values[':username'] = {'S': slack_user_id}
            expression_values[':metadata'] = {
                'M': {
                    'slack_email': {'S': slack_email},
                    'slack_workspace': {'S': SLACK_WORKSPACE}
                }
            }

        if error_message:
            update_expression += ', error_message = :error'
            expression_values[':error'] = {'S': error_message}

        dynamodb.update_item(
            TableName=PROVISIONING_STATE_TABLE,
            Key={
                'user_id': {'S': user_id},
                'system': {'S': system}
            },
            UpdateExpression=update_expression,
            ExpressionAttributeNames={
                '#status': 'status'
            },
            ExpressionAttributeValues=expression_values
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


# For local testing
if __name__ == "__main__":
    test_event = {
        "user_id": "test-user",
        "email": "test@brightwings.io",
        "role": "developer",
        "system": "slack"
    }

    class MockContext:
        request_id = "local-test-12345"
        function_name = "provision_slack"

    print("Note: This uses MOCK Slack API calls (no real API requests)\n")

    try:
        result = lambda_handler(test_event, MockContext())
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Error: {e}")
