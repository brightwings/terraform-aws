"""
Lambda Function: provision_jira
Purpose: Provisions user in Jira workspace using Jira REST API

Input (from Step Functions parallel state):
{
    "user_id": "isaac",
    "email": "isaac@brightwings.io",
    "role": "developer",
    "system": "jira",
    "execution_arn": "..."
}

Output:
{
    ...input fields...,
    "jira_account_id": "5d8e7f4abc123def",
    "jira_email": "isaac@brightwings.io",
    "jira_workspace": "brightwings",
    "provisioning_status": "success",
    "provisioned_at": "2026-02-17T15:00:00Z"
}

Jira API Actions:
1. Check if user already exists (by email)
2. If not exists: Create user account
3. Assign to appropriate groups based on role
4. Update DynamoDB with Jira account ID
"""

import json
import logging
import os
from datetime import datetime
from typing import Dict, Any, Optional, List
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

# Role-to-Groups mapping for Jira
ROLE_TO_GROUPS = {
    "developer": ["jira-developers", "jira-software-users"],
    "admin": ["jira-administrators", "jira-software-users"],
    "contractor": ["jira-contractors", "jira-software-users"]
}


class JiraProvisioningError(Exception):
    """Custom exception for Jira provisioning failures"""
    pass


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler - provisions user in Jira workspace

    Args:
        event: Input from Step Functions
        context: Lambda runtime context

    Returns:
        Provisioning result with Jira account ID

    Raises:
        JiraProvisioningError: If Jira API call fails
    """

    logger.info(json.dumps({
        "event": "jira_provisioning_started",
        "request_id": context.request_id,
        "user_id": event.get('user_id')
    }))

    try:
        user_id = event['user_id']
        email = event['email']
        role = event.get('role', 'developer')

        # Step 1: Get Jira API credentials from Secrets Manager
        jira_credentials = get_jira_credentials()

        # Step 2: Check if user already exists in Jira (idempotency)
        jira_user = check_user_exists_in_jira(email, jira_credentials)

        if jira_user:
            logger.info(json.dumps({
                "event": "user_already_exists",
                "email": email,
                "jira_account_id": jira_user['accountId'],
                "message": "User already in Jira workspace (idempotent)"
            }))
            jira_account_id = jira_user['accountId']
            jira_groups = get_user_groups(jira_account_id, jira_credentials)
        else:
            # Step 3: Create user in Jira
            jira_account_id = create_jira_user(
                email=email,
                jira_credentials=jira_credentials
            )

            # Step 4: Assign to groups based on role
            jira_groups = ROLE_TO_GROUPS.get(role, [])
            for group_name in jira_groups:
                add_user_to_group(
                    account_id=jira_account_id,
                    group_name=group_name,
                    jira_credentials=jira_credentials
                )

        # Step 5: Update DynamoDB record
        update_dynamodb_record(
            user_id=user_id,
            system='jira',
            jira_account_id=jira_account_id,
            jira_email=email,
            jira_groups=jira_groups,
            status='active'
        )

        # Step 6: Return success
        result = {
            **event,
            "jira_account_id": jira_account_id,
            "jira_email": email,
            "jira_workspace": JIRA_SITE,
            "jira_groups": jira_groups,
            "provisioning_status": "success",
            "provisioned_at": datetime.now().isoformat() + "Z"
        }

        logger.info(json.dumps({
            "event": "jira_provisioning_success",
            "request_id": context.request_id,
            "user_id": user_id,
            "jira_account_id": jira_account_id,
            "jira_groups": jira_groups
        }))

        return result

    except Exception as e:
        logger.error(json.dumps({
            "event": "jira_provisioning_failed",
            "request_id": context.request_id,
            "error": str(e),
            "error_type": type(e).__name__
        }))

        # Update DynamoDB with error status
        try:
            update_dynamodb_record(
                user_id=event['user_id'],
                system='jira',
                jira_account_id=None,
                jira_email=None,
                jira_groups=[],
                status='error',
                error_message=str(e)
            )
        except Exception as db_error:
            logger.error(f"Failed to update DynamoDB error status: {db_error}")

        raise JiraProvisioningError(str(e))


def get_jira_credentials() -> Dict[str, str]:
    """
    Retrieve Jira API credentials from AWS Secrets Manager

    Jira Authentication:
    - Email: Admin user email (e.g., admin@brightwings.io)
    - API Token: Generated from https://id.atlassian.com/manage-profile/security/api-tokens

    Required Permissions:
    - User Management: Create users, assign groups
    - Site Administration: Manage users in organization

    Returns:
        Dict with 'email' and 'api_token'

    Raises:
        JiraProvisioningError: If secret retrieval fails
    """

    try:
        response = secrets_manager.get_secret_value(SecretId=JIRA_SECRET_ARN)

        if 'SecretString' in response:
            secret = response['SecretString']
            try:
                secret_dict = json.loads(secret)
                email = secret_dict.get('jira_email') or secret_dict.get('email')
                api_token = secret_dict.get('jira_api_token') or secret_dict.get('api_token')
            except json.JSONDecodeError:
                raise JiraProvisioningError("Invalid secret format")

            logger.info(json.dumps({
                "event": "jira_credentials_retrieved",
                "secret_arn": JIRA_SECRET_ARN
            }))

            return {
                'email': email,
                'api_token': api_token
            }
        else:
            raise JiraProvisioningError("Secret not found in SecretString")

    except ClientError as e:
        logger.error(json.dumps({
            "event": "secrets_manager_error",
            "error_code": e.response['Error']['Code'],
            "error_message": e.response['Error']['Message']
        }))
        raise JiraProvisioningError(f"Failed to retrieve Jira credentials: {e}")


def check_user_exists_in_jira(email: str, credentials: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """
    Check if user already exists in Jira workspace (idempotency)

    Jira API: GET /rest/api/3/user/search?query={email}
    Returns: User object if exists, None if not found

    Args:
        email: User email address
        credentials: Jira API credentials

    Returns:
        User object if exists, None otherwise
    """

    # MOCK: For this demo, simulate API call
    # In production, use:
    # import requests
    # from requests.auth import HTTPBasicAuth
    #
    # response = requests.get(
    #     f'{JIRA_BASE_URL}/rest/api/3/user/search',
    #     auth=HTTPBasicAuth(credentials['email'], credentials['api_token']),
    #     params={'query': email}
    # )
    #
    # users = response.json()
    # if users and len(users) > 0:
    #     return users[0]
    # return None

    logger.info(json.dumps({
        "event": "checking_jira_user_exists",
        "email": email,
        "note": "MOCK: Simulating API call - assuming user doesn't exist"
    }))

    # Mock: Return None (user doesn't exist, need to create)
    return None


def create_jira_user(email: str, credentials: Dict[str, str]) -> str:
    """
    Create user in Jira workspace

    Jira API: POST /rest/api/3/user
    Body: {
        "emailAddress": "isaac@brightwings.io",
        "products": ["jira-software"]
    }

    Security Note: User created with basic access
    - Can view projects they're assigned to
    - Cannot create projects or manage users (unless admin group)
    - Access controlled via groups and project permissions

    Args:
        email: User email address
        credentials: Jira API credentials

    Returns:
        Jira account ID (e.g., "5d8e7f4abc123def")

    Raises:
        JiraProvisioningError: If user creation fails
    """

    # MOCK: For this demo, simulate API call
    # In production, use:
    # import requests
    # from requests.auth import HTTPBasicAuth
    #
    # response = requests.post(
    #     f'{JIRA_BASE_URL}/rest/api/3/user',
    #     auth=HTTPBasicAuth(credentials['email'], credentials['api_token']),
    #     json={
    #         'emailAddress': email,
    #         'products': ['jira-software']
    #     }
    # )
    #
    # if response.status_code != 201:
    #     raise JiraProvisioningError(f"Jira API error: {response.json()['errorMessages']}")
    #
    # return response.json()['accountId']

    logger.info(json.dumps({
        "event": "jira_user_created",
        "email": email,
        "jira_site": JIRA_SITE,
        "note": "MOCK: Simulating API call"
    }))

    # Mock: Return fake Jira account ID
    mock_account_id = f"{hash(email) % 100000000:08x}"
    logger.info(f"Mock Jira account ID generated: {mock_account_id}")

    return mock_account_id


def add_user_to_group(account_id: str, group_name: str, credentials: Dict[str, str]) -> None:
    """
    Add user to Jira group

    Jira API: POST /rest/api/3/group/user
    Body: {
        "accountId": "5d8e7f4abc123def"
    }
    Query: groupname={group_name}

    Args:
        account_id: Jira account ID
        group_name: Jira group name (e.g., "jira-developers")
        credentials: Jira API credentials

    Raises:
        JiraProvisioningError: If group assignment fails
    """

    # MOCK: For this demo, simulate API call
    # In production, use:
    # import requests
    # from requests.auth import HTTPBasicAuth
    #
    # response = requests.post(
    #     f'{JIRA_BASE_URL}/rest/api/3/group/user',
    #     auth=HTTPBasicAuth(credentials['email'], credentials['api_token']),
    #     params={'groupname': group_name},
    #     json={'accountId': account_id}
    # )
    #
    # if response.status_code != 201:
    #     raise JiraProvisioningError(f"Failed to add user to group: {response.json()['errorMessages']}")

    logger.info(json.dumps({
        "event": "user_added_to_jira_group",
        "account_id": account_id,
        "group_name": group_name,
        "note": "MOCK: User added to Jira group"
    }))


def get_user_groups(account_id: str, credentials: Dict[str, str]) -> List[str]:
    """
    Get list of groups user belongs to

    Args:
        account_id: Jira account ID
        credentials: Jira API credentials

    Returns:
        List of group names
    """

    # MOCK: Return default groups
    logger.info(json.dumps({
        "event": "fetching_user_groups",
        "account_id": account_id,
        "note": "MOCK: Returning default groups"
    }))

    return ["jira-software-users"]


def update_dynamodb_record(
    user_id: str,
    system: str,
    jira_account_id: Optional[str],
    jira_email: Optional[str],
    jira_groups: List[str],
    status: str,
    error_message: Optional[str] = None
) -> None:
    """
    Update DynamoDB record with Jira provisioning result

    Args:
        user_id: Canonical user ID
        system: System name ('jira')
        jira_account_id: Jira account ID (or None if error)
        jira_email: Jira email
        jira_groups: List of Jira groups
        status: Provisioning status ('active' or 'error')
        error_message: Error message if status='error'
    """

    try:
        update_expression = 'SET #status = :status, updated_at = :updated_at'
        expression_values = {
            ':status': {'S': status},
            ':updated_at': {'S': datetime.now().isoformat() + "Z"}
        }

        if jira_account_id:
            update_expression += ', system_username = :username, system_metadata = :metadata'
            expression_values[':username'] = {'S': jira_account_id}
            expression_values[':metadata'] = {
                'M': {
                    'jira_email': {'S': jira_email},
                    'jira_workspace': {'S': JIRA_SITE},
                    'jira_groups': {'L': [{'S': g} for g in jira_groups]}
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
        "system": "jira"
    }

    class MockContext:
        request_id = "local-test-12345"
        function_name = "provision_jira"

    print("Note: This uses MOCK Jira API calls (no real API requests)\n")

    try:
        result = lambda_handler(test_event, MockContext())
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Error: {e}")
