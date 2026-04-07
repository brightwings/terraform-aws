"""
Lambda Function: deprovision_github
Purpose: Removes user from GitHub organization (offboarding)

Input (from Step Functions parallel state):
{
    "user_id": "bob",
    "reason": "termination",  # or "resignation", "end_of_contract"
    "system": "github",
    "github_username": "bjones",  # Retrieved from DynamoDB
    "execution_arn": "..."
}

Output:
{
    ...input fields...,
    "deprovisioning_status": "success",
    "actions_taken": [
        "removed_from_all_teams",
        "removed_from_org",
        "logged_security_event"
    ],
    "deprovisioned_at": "2026-02-17T16:00:00Z"
}

Security Actions:
1. Remove user from all teams (revokes all repo access)
2. Remove user from GitHub organization
3. Log security event for audit trail
"""

import json
import logging
import os
import urllib.request
import urllib.error
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
GITHUB_ORG = os.environ.get('GITHUB_ORG', 'example-corp')
GITHUB_API = 'https://api.github.com'


class GitHubDeprovisioningError(Exception):
    """Custom exception for GitHub deprovisioning failures"""
    pass


def _github_api(method: str, path: str, token: str, body: dict = None) -> tuple:
    """
    Make a GitHub API call using urllib (stdlib - no extra packages needed).

    Args:
        method: HTTP method (GET, DELETE, etc.)
        path: API path (e.g. /orgs/example-corp/members/username)
        token: GitHub personal access token
        body: Optional request body dict (JSON-encoded automatically)

    Returns:
        Tuple of (status_code, response_dict)
    """
    url = f'{GITHUB_API}{path}'
    data = json.dumps(body).encode('utf-8') if body is not None else None
    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
    }
    if data:
        headers['Content-Type'] = 'application/json'

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read().decode('utf-8')
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8')
        return e.code, json.loads(raw) if raw else {}


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
        "request_id": context.aws_request_id,
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
            # Security principle: remove repo access BEFORE org membership.
            # If org removal later fails, user still loses repo access.
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
            "request_id": context.aws_request_id,
            "user_id": user_id,
            "github_username": github_username,
            "actions_taken": actions_taken
        }))

        return result

    except Exception as e:
        logger.error(json.dumps({
            "event": "github_deprovisioning_failed",
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
    Returns: 204 if member, 302/404 if not

    Args:
        github_username: GitHub username
        github_token: GitHub API token

    Returns:
        True if user is in org, False otherwise
    """
    status, _ = _github_api(
        'GET',
        f'/orgs/{GITHUB_ORG}/members/{github_username}',
        github_token
    )

    is_member = status == 204
    logger.info(json.dumps({
        "event": "org_membership_check",
        "github_username": github_username,
        "github_org": GITHUB_ORG,
        "status_code": status,
        "is_member": is_member
    }))

    return is_member


def remove_from_all_teams(github_username: str, github_token: str) -> None:
    """
    Remove user from all GitHub teams (revokes all repo access)

    Security Principle: Remove repo access BEFORE org membership.
    Why: If org removal fails, user still loses repo access.

    GitHub API:
    1. GET /orgs/{org}/teams?per_page=100 - list all teams
    2. GET /orgs/{org}/teams/{team_slug}/memberships/{username} - check membership
    3. DELETE /orgs/{org}/teams/{team_slug}/memberships/{username} - remove

    Args:
        github_username: GitHub username
        github_token: GitHub API token

    Raises:
        GitHubDeprovisioningError: If team listing fails
    """
    status, teams = _github_api(
        'GET',
        f'/orgs/{GITHUB_ORG}/teams?per_page=100',
        github_token
    )

    if status != 200:
        raise GitHubDeprovisioningError(
            f"Failed to list org teams (HTTP {status})"
        )

    removed_teams = []
    for team in teams:
        team_slug = team['slug']

        # Check if user is in this team
        membership_status, _ = _github_api(
            'GET',
            f'/orgs/{GITHUB_ORG}/teams/{team_slug}/memberships/{github_username}',
            github_token
        )

        if membership_status == 200:
            # User is in this team — remove them
            del_status, _ = _github_api(
                'DELETE',
                f'/orgs/{GITHUB_ORG}/teams/{team_slug}/memberships/{github_username}',
                github_token
            )

            if del_status == 204:
                removed_teams.append(team_slug)
                logger.info(json.dumps({
                    "event": "removed_from_team",
                    "github_username": github_username,
                    "team_slug": team_slug
                }))
            else:
                logger.warning(json.dumps({
                    "event": "team_removal_failed",
                    "github_username": github_username,
                    "team_slug": team_slug,
                    "status_code": del_status
                }))

    logger.info(json.dumps({
        "event": "removed_from_all_teams",
        "github_username": github_username,
        "teams_removed": removed_teams,
        "teams_checked": len(teams)
    }))


def remove_from_org(github_username: str, github_token: str) -> None:
    """
    Remove user from GitHub organization

    GitHub API: DELETE /orgs/{org}/members/{username}
    Returns: 204 on success

    Args:
        github_username: GitHub username
        github_token: GitHub API token

    Raises:
        GitHubDeprovisioningError: If org removal fails (CRITICAL!)
    """
    status, response = _github_api(
        'DELETE',
        f'/orgs/{GITHUB_ORG}/members/{github_username}',
        github_token
    )

    if status != 204:
        raise GitHubDeprovisioningError(
            f"Failed to remove user from org (HTTP {status}): {response.get('message', response)}"
        )

    logger.info(json.dumps({
        "event": "removed_from_org",
        "github_username": github_username,
        "github_org": GITHUB_ORG
    }))


def log_security_event(
    user_id: str,
    github_username: str,
    reason: str,
    actions: List[str]
) -> None:
    """
    Log security event for offboarding (audit trail)

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
        aws_request_id = "local-test-12345"
        function_name = "deprovision_github"

    print("Note: This calls the real GitHub API (requires AWS credentials for Secrets Manager)\n")

    try:
        result = lambda_handler(test_event, MockContext())
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Error: {e}")
