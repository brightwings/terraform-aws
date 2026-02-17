"""
Lambda Function: deprovision_github
Purpose: Removes user from GitHub organization (offboarding)

Input (from Step Functions parallel state):
{
    "user_id": "nicole",
    "reason": "termination",  # or "resignation", "end_of_contract"
    "system": "github",
    "github_username": "nmaulino",  # Retrieved from DynamoDB
    "execution_arn": "..."
}

Output:
{
    ...input fields...,
    "deprovisioning_status": "success",
    "actions_taken": [
        "removed_from_org",
        "revoked_all_tokens",
        "logged_security_event"
    ],
    "deprovisioned_at": "2026-02-17T16:00:00Z"
}

Security Actions:
1. Remove user from GitHub organization
2. Remove from all teams (loses all repo access)
3. Revoke personal access tokens (if possible)
4. Log security event for audit trail
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
GITHUB_SECRET_ARN = os.environ.get(
    'GITHUB_SECRET_ARN',
    'arn:aws:secretsmanager:us-east-1:YOUR_AWS_ACCOUNT_ID:secret:github-api-token-xyz123'
)
PROVISIONING_STATE_TABLE = os.environ.get(
    'PROVISIONING_STATE_TABLE',
    'saas-automation-dev-provisioning-state'
)
GITHUB_ORG = os.environ.get('GITHUB_ORG', 'brightwings')


class GitHubDeprovisioningError(Exception):
    """Custom exception for GitHub deprovisioning failures"""
    pass


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler - deprovisions user from GitHub org

    Args:
        event: Input from Step Functions
        context: Lambda runtime context

    Returns:
        Deprovisioning result

    Raises:
        GitHubDeprovisioningError: If GitHub API call fails (CRITICAL!)
    """

    logger.info(json.dumps({
        "event": "github_deprovisioning_started",
        "request_id": context.request_id,
        "user_id": event.get('user_id'),
        "reason": event.get('reason')
    }))

    try:
        user_id = event['user_id']
        github_username = event.get('github_username')
        reason = event.get('reason', 'unknown')

        if not github_username:
            raise GitHubDeprovisioningError(
                "github_username not provided. Cannot deprovision without username."
            )

        # Step 1: Get GitHub API token
        github_token = get_github_token()

        # Step 2: Check if user is still in org (idempotency)
        if not check_user_in_org(github_username, github_token):
            logger.info(json.dumps({
                "event": "user_not_in_org",
                "github_username": github_username,
                "message": "User already removed from org (idempotent)"
            }))

            actions_taken = ["already_removed"]

        else:
            actions_taken = []

            # Step 3: Remove from all teams first (revokes repo access)
            remove_from_all_teams(github_username, github_token)
            actions_taken.append("removed_from_all_teams")

            # Step 4: Remove from organization
            remove_from_org(github_username, github_token)
            actions_taken.append("removed_from_org")

            # Step 5: Log security event
            log_security_event(
                user_id=user_id,
                github_username=github_username,
                reason=reason,
                actions=actions_taken
            )
            actions_taken.append("logged_security_event")

        # Step 6: Update DynamoDB record to 'deprovisioned'
        update_dynamodb_record(
            user_id=user_id,
            system='github',
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
            "event": "github_deprovisioning_success",
            "request_id": context.request_id,
            "user_id": user_id,
            "github_username": github_username,
            "actions_taken": actions_taken
        }))

        return result

    except Exception as e:
        logger.error(json.dumps({
            "event": "github_deprovisioning_failed",
            "request_id": context.request_id,
            "user_id": event.get('user_id'),
            "error": str(e),
            "error_type": type(e).__name__,
            "severity": "CRITICAL"
        }))

        # CRITICAL: Deprovisioning failure is a security incident
        # Alert security team immediately
        alert_security_team_critical_failure(
            user_id=event.get('user_id'),
            system='github',
            error=str(e)
        )

        raise GitHubDeprovisioningError(str(e))


def get_github_token() -> str:
    """Retrieve GitHub API token from Secrets Manager"""

    try:
        response = secrets_manager.get_secret_value(SecretId=GITHUB_SECRET_ARN)

        if 'SecretString' in response:
            secret = response['SecretString']
            try:
                secret_dict = json.loads(secret)
                return secret_dict.get('github_token') or secret_dict.get('token')
            except json.JSONDecodeError:
                return secret
        else:
            raise GitHubDeprovisioningError("Secret not found")

    except ClientError as e:
        raise GitHubDeprovisioningError(f"Failed to retrieve GitHub token: {e}")


def check_user_in_org(github_username: str, github_token: str) -> bool:
    """
    Check if user is still in GitHub org (idempotency)

    GitHub API: GET /orgs/{org}/members/{username}
    Returns: 204 if member, 404 if not

    Args:
        github_username: GitHub username
        github_token: GitHub API token

    Returns:
        True if user is in org, False otherwise
    """

    # MOCK: For demo
    # In production:
    # response = requests.get(
    #     f'https://api.github.com/orgs/{GITHUB_ORG}/members/{github_username}',
    #     headers={'Authorization': f'token {github_token}'}
    # )
    # return response.status_code == 204

    logger.info(json.dumps({
        "event": "checking_user_in_org",
        "github_username": github_username,
        "note": "MOCK: Assuming user is in org"
    }))

    return True  # Mock: User is in org


def remove_from_all_teams(github_username: str, github_token: str) -> None:
    """
    Remove user from all GitHub teams (revokes repo access)

    Security Principle: Remove repo access BEFORE org membership
    Why: If org removal fails, user still loses repo access

    GitHub API:
    1. GET /orgs/{org}/teams/{team_slug}/memberships/{username} (list teams)
    2. DELETE /orgs/{org}/teams/{team_slug}/memberships/{username} (remove)

    Args:
        github_username: GitHub username
        github_token: GitHub API token

    Raises:
        GitHubDeprovisioningError: If team removal fails
    """

    # MOCK: For demo
    # In production:
    # # Get all teams user belongs to
    # teams_response = requests.get(
    #     f'https://api.github.com/orgs/{GITHUB_ORG}/teams',
    #     headers={'Authorization': f'token {github_token}'}
    # )
    # teams = teams_response.json()
    #
    # for team in teams:
    #     # Check if user is in team
    #     member_response = requests.get(
    #         f'https://api.github.com/orgs/{GITHUB_ORG}/teams/{team['slug']}/memberships/{github_username}',
    #         headers={'Authorization': f'token {github_token}'}
    #     )
    #
    #     if member_response.status_code == 200:
    #         # Remove from team
    #         requests.delete(
    #             f'https://api.github.com/orgs/{GITHUB_ORG}/teams/{team['slug']}/memberships/{github_username}',
    #             headers={'Authorization': f'token {github_token}'}
    #         )

    logger.info(json.dumps({
        "event": "removed_from_all_teams",
        "github_username": github_username,
        "note": "MOCK: User removed from all teams (loses all repo access)"
    }))


def remove_from_org(github_username: str, github_token: str) -> None:
    """
    Remove user from GitHub organization

    GitHub API: DELETE /orgs/{org}/members/{username}

    Args:
        github_username: GitHub username
        github_token: GitHub API token

    Raises:
        GitHubDeprovisioningError: If org removal fails (CRITICAL!)
    """

    # MOCK: For demo
    # In production:
    # response = requests.delete(
    #     f'https://api.github.com/orgs/{GITHUB_ORG}/members/{github_username}',
    #     headers={'Authorization': f'token {github_token}'}
    # )
    #
    # if response.status_code != 204:
    #     raise GitHubDeprovisioningError(
    #         f"Failed to remove user from org: {response.text}"
    #     )

    logger.info(json.dumps({
        "event": "removed_from_org",
        "github_username": github_username,
        "github_org": GITHUB_ORG,
        "note": "MOCK: User removed from GitHub organization"
    }))


def log_security_event(
    user_id: str,
    github_username: str,
    reason: str,
    actions: List[str]
) -> None:
    """
    Log security event for offboarding (audit trail)

    Security Event Schema:
    {
        "event_type": "user_deprovisioned",
        "system": "github",
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
        github_username: GitHub username
        reason: Offboarding reason
        actions: Actions taken during deprovisioning
    """

    security_event = {
        "event_type": "user_deprovisioned",
        "system": "github",
        "user_id": user_id,
        "github_username": github_username,
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
        system: System name ('github')
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
        "github_username": "testuser",
        "reason": "termination",
        "system": "github"
    }

    class MockContext:
        request_id = "local-test-12345"
        function_name = "deprovision_github"

    print("Note: This uses MOCK GitHub API calls\n")

    try:
        result = lambda_handler(test_event, MockContext())
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Error: {e}")
