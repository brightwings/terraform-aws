"""
Lambda Function: provision_aws
Purpose: Provisions AWS IAM user and assigns to appropriate groups

Input (from Step Functions parallel state):
{
    "user_id": "isaac",
    "email": "isaac@brightwings.io",
    "role": "developer",
    "system": "aws",
    "execution_arn": "..."
}

Output:
{
    ...input fields...,
    "aws_username": "isaac",
    "aws_groups": ["saas-automation-dev-developers"],
    "aws_user_arn": "arn:aws:iam::YOUR_AWS_ACCOUNT_ID:user/isaac",
    "provisioning_status": "success",
    "provisioned_at": "2026-02-17T15:00:00Z"
}

Security Notes:
- Creates IAM user with NO access keys (SSO/Console access only)
- Assigns to groups based on role (developers, contractors, etc.)
- Uses least privilege group policies (not AdministratorAccess)
- Tags user with ManagedBy=terraform for tracking
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
iam = boto3.client('iam')
dynamodb = boto3.client('dynamodb')

# Environment variables
PROVISIONING_STATE_TABLE = os.environ.get(
    'PROVISIONING_STATE_TABLE',
    'saas-automation-dev-provisioning-state'
)
NAME_PREFIX = os.environ.get('NAME_PREFIX', 'saas-automation-dev')


# Role-to-Groups mapping
ROLE_TO_GROUPS = {
    "developer": [f"{NAME_PREFIX}-developers"],
    "admin": [f"{NAME_PREFIX}-developers"],  # Admins start as developers, escalate via break-glass
    "contractor": [f"{NAME_PREFIX}-developers"],  # Contractors get same base permissions
}


class AWSProvisioningError(Exception):
    """Custom exception for AWS provisioning failures"""
    pass


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler - provisions AWS IAM user

    Args:
        event: Input from Step Functions
        context: Lambda runtime context

    Returns:
        Provisioning result with AWS username and groups

    Raises:
        AWSProvisioningError: If IAM operations fail
    """

    logger.info(json.dumps({
        "event": "aws_provisioning_started",
        "request_id": context.aws_request_id,
        "user_id": event.get('user_id')
    }))

    try:
        user_id = event['user_id']
        email = event['email']
        role = event.get('role', 'developer')

        # Step 1: Generate IAM username (use canonical user_id)
        aws_username = user_id  # Simple: user_id = IAM username

        # Step 2: Check if user already exists (idempotency)
        if check_iam_user_exists(aws_username):
            logger.info(json.dumps({
                "event": "user_already_exists",
                "aws_username": aws_username,
                "message": "IAM user already exists (idempotent)"
            }))

            # Get existing user details
            user_info = iam.get_user(UserName=aws_username)
            aws_user_arn = user_info['User']['Arn']

            # Get current group memberships
            groups_response = iam.list_groups_for_user(UserName=aws_username)
            current_groups = [g['GroupName'] for g in groups_response['Groups']]

        else:
            # Step 3: Create IAM user
            aws_user_arn = create_iam_user(
                username=aws_username,
                email=email
            )

            # Step 4: Assign to groups based on role
            groups = ROLE_TO_GROUPS.get(role, [])
            for group_name in groups:
                add_user_to_group(
                    username=aws_username,
                    group_name=group_name
                )

            current_groups = groups

        # Step 5: Update DynamoDB record
        update_dynamodb_record(
            user_id=user_id,
            system='aws',
            aws_username=aws_username,
            aws_user_arn=aws_user_arn,
            aws_groups=current_groups,
            status='active'
        )

        # Step 6: Return success
        result = {
            **event,
            "aws_username": aws_username,
            "aws_user_arn": aws_user_arn,
            "aws_groups": current_groups,
            "provisioning_status": "success",
            "provisioned_at": datetime.now().isoformat() + "Z",
            "note": "User created without access keys. Use AWS Console with SSO or generate keys manually."
        }

        logger.info(json.dumps({
            "event": "aws_provisioning_success",
            "request_id": context.aws_request_id,
            "user_id": user_id,
            "aws_username": aws_username,
            "aws_groups": current_groups
        }))

        return result

    except Exception as e:
        logger.error(json.dumps({
            "event": "aws_provisioning_failed",
            "request_id": context.aws_request_id,
            "error": str(e),
            "error_type": type(e).__name__
        }))

        # Update DynamoDB with error status
        try:
            update_dynamodb_record(
                user_id=event['user_id'],
                system='aws',
                aws_username=None,
                aws_user_arn=None,
                aws_groups=[],
                status='error',
                error_message=str(e)
            )
        except Exception as db_error:
            logger.error(f"Failed to update DynamoDB error status: {db_error}")

        raise AWSProvisioningError(str(e))


def check_iam_user_exists(username: str) -> bool:
    """
    Check if IAM user already exists (idempotency)

    Args:
        username: IAM username to check

    Returns:
        True if user exists, False otherwise
    """

    try:
        iam.get_user(UserName=username)
        logger.info(json.dumps({
            "event": "iam_user_exists",
            "username": username
        }))
        return True

    except iam.exceptions.NoSuchEntityException:
        logger.info(json.dumps({
            "event": "iam_user_not_found",
            "username": username
        }))
        return False

    except ClientError as e:
        logger.error(json.dumps({
            "event": "iam_check_failed",
            "error": str(e)
        }))
        raise


def create_iam_user(username: str, email: str) -> str:
    """
    Create AWS IAM user

    Security Design:
    - No access keys created (prevents credential leakage)
    - User must use AWS Console with SSO or request keys separately
    - Tagged with ManagedBy=terraform-automation for tracking
    - Tagged with email for identity correlation

    Args:
        username: IAM username
        email: User email (for tagging)

    Returns:
        User ARN (e.g., "arn:aws:iam::YOUR_AWS_ACCOUNT_ID:user/isaac")

    Raises:
        AWSProvisioningError: If user creation fails
    """

    try:
        response = iam.create_user(
            UserName=username,
            Tags=[
                {'Key': 'ManagedBy', 'Value': 'terraform-automation'},
                {'Key': 'Email', 'Value': email},
                {'Key': 'CreatedBy', 'Value': 'provision_aws_lambda'},
                {'Key': 'CreatedAt', 'Value': datetime.now().isoformat()}
            ]
        )

        user_arn = response['User']['Arn']

        logger.info(json.dumps({
            "event": "iam_user_created",
            "username": username,
            "user_arn": user_arn
        }))

        return user_arn

    except ClientError as e:
        if e.response['Error']['Code'] == 'EntityAlreadyExists':
            # User was created by another process (race condition)
            # This is OK - return existing user ARN (idempotent)
            user_info = iam.get_user(UserName=username)
            return user_info['User']['Arn']
        else:
            logger.error(json.dumps({
                "event": "iam_user_creation_failed",
                "error": str(e)
            }))
            raise AWSProvisioningError(f"Failed to create IAM user: {e}")


def add_user_to_group(username: str, group_name: str) -> None:
    """
    Add IAM user to group

    Args:
        username: IAM username
        group_name: IAM group name (e.g., "saas-automation-dev-developers")

    Raises:
        AWSProvisioningError: If group assignment fails
    """

    try:
        iam.add_user_to_group(
            UserName=username,
            GroupName=group_name
        )

        logger.info(json.dumps({
            "event": "user_added_to_group",
            "username": username,
            "group_name": group_name
        }))

    except iam.exceptions.NoSuchEntityException:
        # Group doesn't exist
        logger.error(json.dumps({
            "event": "group_not_found",
            "group_name": group_name,
            "message": f"IAM group '{group_name}' does not exist. Create it in Terraform first."
        }))
        raise AWSProvisioningError(f"IAM group not found: {group_name}")

    except ClientError as e:
        logger.error(json.dumps({
            "event": "add_to_group_failed",
            "username": username,
            "group_name": group_name,
            "error": str(e)
        }))
        raise AWSProvisioningError(f"Failed to add user to group: {e}")


def update_dynamodb_record(
    user_id: str,
    system: str,
    aws_username: str,
    aws_user_arn: str,
    aws_groups: List[str],
    status: str,
    error_message: str = None
) -> None:
    """
    Update DynamoDB record with AWS provisioning result

    Args:
        user_id: Canonical user ID
        system: System name ('aws')
        aws_username: AWS IAM username
        aws_user_arn: AWS user ARN
        aws_groups: List of IAM groups user belongs to
        status: Provisioning status ('active' or 'error')
        error_message: Error message if status='error'
    """

    try:
        update_expression = 'SET #status = :status, updated_at = :updated_at'
        expression_values = {
            ':status': {'S': status},
            ':updated_at': {'S': datetime.now().isoformat() + "Z"}
        }

        if aws_username:
            update_expression += ', system_username = :username, system_metadata = :metadata'
            expression_values[':username'] = {'S': aws_username}

            # Store AWS-specific metadata
            metadata = {
                'aws_user_arn': {'S': aws_user_arn},
                'aws_groups': {'L': [{'S': g} for g in aws_groups]}
            }
            expression_values[':metadata'] = {'M': metadata}

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
        "system": "aws"
    }

    class MockContext:
        request_id = "local-test-12345"
        function_name = "provision_aws"

    print("Note: This will call real AWS IAM API (requires credentials)\n")

    try:
        result = lambda_handler(test_event, MockContext())
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Error: {e}")
