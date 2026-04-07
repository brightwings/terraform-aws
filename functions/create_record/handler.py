"""
Lambda Function: create_record
Purpose: Creates provisioning state record in DynamoDB

Input (from validate_input Lambda):
{
    "user_id": "alice",
    "email": "alice@example.com",
    "role": "developer",
    "systems": ["aws", "github"],
    "validated_at": "2026-02-17T10:00:00Z"
}

Output:
{
    ...input fields...,
    "dynamodb_records_created": ["alice-aws", "alice-github"],
    "execution_arn": "arn:aws:states:..."
}

DynamoDB Records Created:
- One record per system in the systems list
- Partition key: user_id
- Sort key: system
- Status: "pending" (will be updated to "active" after provisioning)
"""

import json
import logging
import os
from datetime import datetime
from typing import Dict, List, Any
import boto3
from botocore.exceptions import ClientError

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize DynamoDB client
# In AWS Lambda, boto3 automatically uses the Lambda execution role
# For local testing, uses ~/.aws/credentials
dynamodb = boto3.client('dynamodb')

# Get table name from environment variable (set by Terraform)
PROVISIONING_STATE_TABLE = os.environ.get(
    'PROVISIONING_STATE_TABLE',
    'saas-automation-dev-provisioning-state'  # Default for local testing
)


class DynamoDBError(Exception):
    """Custom exception for DynamoDB operations"""
    pass


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler - creates provisioning state records

    Args:
        event: Validated input from previous Lambda
        context: Lambda runtime context

    Returns:
        Input data with DynamoDB record IDs

    Raises:
        DynamoDBError: If DynamoDB write fails
    """

    logger.info(json.dumps({
        "event": "create_record_started",
        "request_id": context.aws_request_id,
        "user_id": event.get('user_id'),
        "systems": event.get('systems')
    }))

    try:
        user_id = event['user_id']
        systems = event['systems']
        execution_arn = context.invoked_function_arn  # Step Functions execution ARN

        # Create one DynamoDB record per system
        records_created = []

        for system in systems:
            record_id = create_provisioning_record(
                user_id=user_id,
                system=system,
                email=event.get('email'),
                role=event.get('role'),
                execution_arn=execution_arn
            )
            records_created.append(record_id)

            logger.info(json.dumps({
                "event": "record_created",
                "user_id": user_id,
                "system": system,
                "record_id": record_id
            }))

        # Return input with metadata
        result = {
            **event,
            "dynamodb_records_created": records_created,
            "execution_arn": execution_arn,
            "records_created_at": datetime.utcnow().isoformat() + "Z"
        }

        logger.info(json.dumps({
            "event": "create_record_success",
            "request_id": context.aws_request_id,
            "user_id": user_id,
            "records_count": len(records_created)
        }))

        return result

    except Exception as e:
        logger.error(json.dumps({
            "event": "create_record_failed",
            "request_id": context.aws_request_id,
            "error": str(e),
            "error_type": type(e).__name__
        }))
        raise


def create_provisioning_record(
    user_id: str,
    system: str,
    email: str,
    role: str,
    execution_arn: str
) -> str:
    """
    Create a provisioning state record in DynamoDB

    Record structure:
    {
        "user_id": "alice",           # Partition key
        "system": "github",            # Sort key
        "status": "pending",           # Provisioning status
        "email": "alice@example.com",
        "role": "developer",
        "provisioned_at": "2026-02-17T10:00:00Z",
        "execution_arn": "arn:aws:states:...",
        "system_username": null,       # Will be filled by provision_X Lambda
        "system_metadata": {}          # System-specific data
    }

    Args:
        user_id: Canonical user identifier
        system: System name (aws, github, google_workspace, slack)
        email: User email
        role: User role
        execution_arn: Step Functions execution ARN (for audit trail)

    Returns:
        Record ID (user_id-system)

    Raises:
        DynamoDBError: If DynamoDB write fails
    """

    record_id = f"{user_id}-{system}"
    timestamp = datetime.utcnow().isoformat() + "Z"

    try:
        # Use PutItem with condition expression for idempotency
        # If record already exists with status="active", don't overwrite
        response = dynamodb.put_item(
            TableName=PROVISIONING_STATE_TABLE,
            Item={
                'user_id': {'S': user_id},
                'system': {'S': system},
                'status': {'S': 'pending'},
                'email': {'S': email},
                'role': {'S': role},
                'provisioned_at': {'S': timestamp},
                'execution_arn': {'S': execution_arn},
                'system_username': {'NULL': True},  # Will be updated by provision_X Lambda
                'system_metadata': {'M': {}},  # Map (empty dict)
            },
            # Idempotency check: Only create if doesn't exist OR status != active
            # This prevents overwriting an active provisioning with pending
            ConditionExpression='attribute_not_exists(user_id) OR #status <> :active',
            ExpressionAttributeNames={
                '#status': 'status'
            },
            ExpressionAttributeValues={
                ':active': {'S': 'active'}
            }
        )

        logger.info(json.dumps({
            "event": "dynamodb_put_item_success",
            "record_id": record_id,
            "table": PROVISIONING_STATE_TABLE
        }))

        return record_id

    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            # Record already exists with status="active"
            # This is OK - it means user is already provisioned (idempotent)
            logger.info(json.dumps({
                "event": "record_already_exists",
                "record_id": record_id,
                "message": "User already provisioned in this system (idempotent)"
            }))
            return record_id
        else:
            # Other DynamoDB error
            logger.error(json.dumps({
                "event": "dynamodb_error",
                "error_code": e.response['Error']['Code'],
                "error_message": e.response['Error']['Message'],
                "record_id": record_id
            }))
            raise DynamoDBError(f"Failed to create record: {e}")


# For local testing
if __name__ == "__main__":
    # Mock event
    test_event = {
        "user_id": "test-user",
        "email": "test@example.com",
        "role": "developer",
        "systems": ["aws", "github"],
        "validated_at": "2026-02-17T10:00:00Z"
    }

    # Mock context
    class MockContext:
        request_id = "local-test-12345"
        function_name = "create_record"
        invoked_function_arn = "arn:aws:states:us-east-1:123456789012:execution:onboarding:test-exec"

    print("Note: This will fail locally without AWS credentials or LocalStack")
    print("To test locally, use LocalStack or mock boto3 calls\n")

    try:
        result = lambda_handler(test_event, MockContext())
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Expected error (no AWS/LocalStack): {e}")
