"""
Lambda Function: provision_github
Purpose: Provisions user in GitHub organization using GitHub API

Input (from Step Functions parallel state):
{
    "user_id": "alice",
    "email": "alice@example.com",
    "role": "developer",
    "system": "github",  # Added by Step Functions
    "github_username": "alicejones",  # OPTIONAL: Existing GitHub username
    "execution_arn": "..."
}

If github_username is provided: Use it (user has existing GitHub account)
If github_username is NOT provided: Fail with clear error message

Output:
{
    ...input fields...,
    "github_username": "alicejones",
    "github_org": "example-corp",
    "github_role": "member",
    "provisioning_status": "success",
    "provisioned_at": "2026-02-17T10:05:00Z"
}

External Dependencies:
- GitHub API (requires github-api-token in Secrets Manager)
- Secrets Manager (for API token)
- DynamoDB (update record with github_username)
"""

import json
import logging
import os
import urllib.request
import urllib.error
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

# Environment variables (set by Terraform)
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


class GitHubProvisioningError(Exception):
    """Custom exception for GitHub provisioning failures"""
    pass


def _github_api(method: str, path: str, token: str, body: dict = None) -> tuple:
    """
    Make a GitHub API call using urllib (stdlib - no extra packages needed).

    Args:
        method: HTTP method (GET, POST, DELETE)
        path: API path (e.g. /users/alicejones)
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
    Main Lambda handler - provisions user in GitHub org

    Args:
        event: Input from Step Functions
        context: Lambda runtime context

    Returns:
        Provisioning result with GitHub username

    Raises:
        GitHubProvisioningError: If GitHub API call fails
    """

    logger.info(json.dumps({
        "event": "github_provisioning_started",
        "request_id": context.aws_request_id,
        "user_id": event.get('user_id')
    }))

    try:
        user_id = event['user_id']
        email = event['email']
        role = event.get('role', 'developer')

        # Step 1: Get GitHub username from input (REQUIRED)
        # Users must provide their existing GitHub username
        github_username = event.get('github_username')

        if not github_username:
            raise GitHubProvisioningError(
                "github_username is required. User must provide their existing GitHub account. "
                "Example: {'github_username': 'alicejones'}"
            )

        logger.info(json.dumps({
            "event": "using_provided_github_username",
            "user_id": user_id,
            "github_username": github_username
        }))

        # Step 2: Get GitHub API token from Secrets Manager
        github_token = get_github_token()

        # Step 3: Check if user already exists in org (idempotency)
        if check_user_exists_in_org(github_username, github_token):
            logger.info(json.dumps({
                "event": "user_already_in_org",
                "github_username": github_username,
                "message": "User already in GitHub org (idempotent)"
            }))
        else:
            # Step 4: Verify GitHub username exists (user has account)
            if not verify_github_user_exists(github_username, github_token):
                raise GitHubProvisioningError(
                    f"GitHub username '{github_username}' does not exist. "
                    f"User must create GitHub account first or provide correct username."
                )

            # Step 5: Invite user to GitHub org
            invite_user_to_org(
                github_username=github_username,
                email=email,
                github_token=github_token,
                role='member'  # or 'admin' based on user role
            )

        # Step 5: Update DynamoDB record
        update_dynamodb_record(
            user_id=user_id,
            system='github',
            github_username=github_username,
            status='active'
        )

        # Step 6: Return success
        result = {
            **event,
            "github_username": github_username,
            "github_org": GITHUB_ORG,
            "github_role": "member",
            "status": "success",           # read by finalize_execution to mark DynamoDB 'active'
            "provisioning_status": "success",
            "provisioned_at": datetime.utcnow().isoformat() + "Z"
        }

        logger.info(json.dumps({
            "event": "github_provisioning_success",
            "request_id": context.aws_request_id,
            "user_id": user_id,
            "github_username": github_username
        }))

        return result

    except Exception as e:
        logger.error(json.dumps({
            "event": "github_provisioning_failed",
            "request_id": context.aws_request_id,
            "error": str(e),
            "error_type": type(e).__name__
        }))

        # Update DynamoDB with error status
        try:
            update_dynamodb_record(
                user_id=event['user_id'],
                system='github',
                github_username=None,
                status='error',
                error_message=str(e)
            )
        except Exception as db_error:
            logger.error(f"Failed to update DynamoDB error status: {db_error}")

        raise GitHubProvisioningError(str(e))


def get_github_token() -> str:
    """
    Retrieve GitHub API token from AWS Secrets Manager

    Returns:
        GitHub personal access token

    Raises:
        GitHubProvisioningError: If secret retrieval fails
    """

    try:
        response = secrets_manager.get_secret_value(SecretId=GITHUB_SECRET_ARN)

        # Secret can be stored as string or JSON
        if 'SecretString' in response:
            secret = response['SecretString']
            try:
                # Try parsing as JSON first
                secret_dict = json.loads(secret)
                token = secret_dict.get('github_token') or secret_dict.get('token')
            except json.JSONDecodeError:
                # Secret is plain string
                token = secret

            logger.info(json.dumps({
                "event": "github_token_retrieved",
                "secret_arn": GITHUB_SECRET_ARN
            }))

            return token
        else:
            raise GitHubProvisioningError("Secret not found in SecretString")

    except ClientError as e:
        logger.error(json.dumps({
            "event": "secrets_manager_error",
            "error_code": e.response['Error']['Code'],
            "error_message": e.response['Error']['Message']
        }))
        raise GitHubProvisioningError(f"Failed to retrieve GitHub token: {e}")


def verify_github_user_exists(github_username: str, github_token: str) -> bool:
    """
    Verify that GitHub username exists (user has created their GitHub account)

    GitHub API: GET /users/{username}
    Returns: 200 if user exists, 404 if not

    Args:
        github_username: GitHub username to verify
        github_token: GitHub API token

    Returns:
        True if user exists, False otherwise
    """
    status, _ = _github_api('GET', f'/users/{github_username}', github_token)

    logger.info(json.dumps({
        "event": "github_user_exists_check",
        "github_username": github_username,
        "status_code": status,
        "exists": status == 200
    }))

    return status == 200


def check_user_exists_in_org(github_username: str, github_token: str) -> bool:
    """
    Check if user already exists in GitHub org (idempotency)

    GitHub API: GET /orgs/{org}/members/{username}
    Returns: 204 if user is member, 302/404 if not

    Args:
        github_username: GitHub username to check
        github_token: GitHub API token

    Returns:
        True if user is already an org member, False otherwise
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


def invite_user_to_org(
    github_username: str,
    email: str,
    github_token: str,
    role: str = 'member'
) -> None:
    """
    Invite user to GitHub organization as a MEMBER (no repo access by default)

    GitHub Organization Membership Levels:
    - member: Can see org, other members, public repos (SECURE DEFAULT)
    - admin: Full org access (dangerous - requires separate approval)

    IMPORTANT: Being an org member does NOT grant repository access!
    Repository access must be granted separately via:
    - Adding to GitHub teams (teams have repo permissions)
    - Direct collaborator access to specific repos
    - Base permissions (org-level setting, usually "none")

    Security Model:
    1. This Lambda: Add user as org member (no repo access)
    2. Separate workflow: Add to teams based on role
       - developers team → access to dev repos
       - contractors team → access to contractor-approved repos
    3. Principle of least privilege: Start with zero access, add as needed

    GitHub API: POST /orgs/{org}/invitations
    Body: {
        "invitee_id": 123456,     # GitHub user ID
        "role": "direct_member",   # Organization role
        "team_ids": []             # Empty = no team assignments (no repo access)
    }

    Args:
        github_username: GitHub username
        email: User email (for invitation)
        github_token: GitHub API token
        role: Organization role ('member' or 'admin')

    Raises:
        GitHubProvisioningError: If invitation fails
    """

    # Step 1: Get GitHub user ID from username
    status, user_data = _github_api('GET', f'/users/{github_username}', github_token)
    if status != 200:
        raise GitHubProvisioningError(
            f"Cannot get GitHub user ID for '{github_username}' (HTTP {status})"
        )
    github_user_id = user_data['id']

    logger.info(json.dumps({
        "event": "github_user_id_retrieved",
        "github_username": github_username,
        "github_user_id": github_user_id
    }))

    # Step 2: Send organization invitation (NO team assignments = no repo access)
    status, response = _github_api(
        'POST',
        f'/orgs/{GITHUB_ORG}/invitations',
        github_token,
        body={
            'invitee_id': github_user_id,
            'role': 'direct_member',  # Org member (not admin)
            'team_ids': []            # EMPTY: No team access = no repo access
        }
    )

    if status == 201:
        logger.info(json.dumps({
            "event": "github_invitation_sent",
            "github_username": github_username,
            "github_user_id": github_user_id,
            "github_org": GITHUB_ORG,
            "role": "direct_member",
            "team_ids": [],  # No teams = no repo access (secure default)
            "invitation_id": response.get('id')
        }))
    elif status == 422:
        # 422 can mean user is already a member or has a pending invitation
        message = response.get('message', '')
        errors = response.get('errors', [])
        logger.info(json.dumps({
            "event": "github_invitation_already_exists",
            "github_username": github_username,
            "message": message,
            "errors": errors
        }))
        # Treat as success — user will be (or already is) a member
    else:
        raise GitHubProvisioningError(
            f"GitHub invitation failed (HTTP {status}): {response.get('message', response)}"
        )


def update_dynamodb_record(
    user_id: str,
    system: str,
    github_username: Optional[str],
    status: str,
    error_message: Optional[str] = None
) -> None:
    """
    Update DynamoDB record with GitHub provisioning result

    Args:
        user_id: Canonical user ID
        system: System name ('github')
        github_username: GitHub username (or None if error)
        status: Provisioning status ('active' or 'error')
        error_message: Error message if status='error'
    """

    try:
        update_expression = 'SET #status = :status, updated_at = :updated_at'
        expression_values = {
            ':status': {'S': status},
            ':updated_at': {'S': datetime.utcnow().isoformat() + "Z"}
        }

        if github_username:
            update_expression += ', system_username = :username'
            expression_values[':username'] = {'S': github_username}

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
        "email": "test@example.com",
        "role": "developer",
        "system": "github"
    }

    class MockContext:
        aws_request_id = "local-test-12345"
        function_name = "provision_github"

    print("Note: This calls the real GitHub API (requires AWS credentials for Secrets Manager)\n")

    try:
        result = lambda_handler(test_event, MockContext())
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Error: {e}")
