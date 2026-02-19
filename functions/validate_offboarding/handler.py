"""
Lambda Function: validate_offboarding
Purpose: Validates offboarding request and retrieves user's active systems from DynamoDB

Input (from Step Functions):
{
    "user_id": "nicole",
    "reason": "termination"  # or "resignation", "end_of_contract"
}

Output:
{
    ...input fields...,
    "active_systems": ["github", "slack", "aws", "jira"],
    "github_username": "nmaulino",
    "slack_user_id": "U01ABC123",
    "aws_username": "nicole",
    "jira_account_id": "5d8e7f4abc",
    "validated_at": "2026-02-17T15:00:00Z"
}

Validation Rules:
1. user_id must be non-empty string
2. reason must be one of: termination, resignation, end_of_contract
3. User must have at least one active provisioning record in DynamoDB
4. Retrieve system-specific usernames for deprovisioning
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
dynamodb = boto3.client('dynamodb')

# Environment variables
PROVISIONING_STATE_TABLE = os.environ.get(
    'PROVISIONING_STATE_TABLE',
    'saas-automation-dev-provisioning-state'
)

# Valid offboarding reasons
VALID_REASONS = ['termination', 'resignation', 'end_of_contract']


class ValidationError(Exception):
    """Custom exception for validation failures"""
    pass


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler - validates offboarding request

    Args:
        event: Input from Step Functions
        context: Lambda runtime context

    Returns:
        Validated input with active systems list

    Raises:
        ValidationError: If validation fails
    """

    logger.info(json.dumps({
        "event": "offboarding_validation_started",
        "request_id": context.aws_request_id,
        "input": event
    }))

    try:
        # Step 1: Validate required fields
        user_id = event.get('user_id')
        reason = event.get('reason')

        if not user_id or not isinstance(user_id, str) or len(user_id) == 0:
            raise ValidationError("user_id is required and must be non-empty string")

        if not reason or reason not in VALID_REASONS:
            raise ValidationError(
                f"reason must be one of: {', '.join(VALID_REASONS)}"
            )

        # Step 2: Query DynamoDB for active provisioning records
        active_records = get_active_provisioning_records(user_id)

        if not active_records:
            raise ValidationError(
                f"User {user_id} has no active provisioning in any system. Nothing to deprovision."
            )

        # Step 3: Extract system list and system-specific usernames
        active_systems = []
        system_usernames = {}

        for record in active_records:
            system = record['system']['S']
            active_systems.append(system)

            # Extract system-specific username from metadata
            if 'system_username' in record:
                system_key = f"{system}_username" if system == 'github' else f"{system}_user_id" if system in ['slack', 'jira'] else f"{system}_username"
                system_usernames[system_key] = record['system_username']['S']

        # Step 4: Return validated input with active systems
        result = {
            **event,
            "active_systems": active_systems,
            **system_usernames,
            "validated_at": datetime.now().isoformat() + "Z"
        }

        logger.info(json.dumps({
            "event": "offboarding_validation_success",
            "request_id": context.aws_request_id,
            "user_id": user_id,
            "active_systems": active_systems,
            "systems_count": len(active_systems)
        }))

        return result

    except ValidationError as e:
        logger.error(json.dumps({
            "event": "validation_failed",
            "request_id": context.aws_request_id,
            "error": str(e),
            "error_type": "ValidationError"
        }))
        raise

    except Exception as e:
        logger.error(json.dumps({
            "event": "validation_error",
            "request_id": context.aws_request_id,
            "error": str(e),
            "error_type": type(e).__name__
        }))
        raise ValidationError(f"Validation failed: {str(e)}")


def get_active_provisioning_records(user_id: str) -> List[Dict[str, Any]]:
    """
    Query DynamoDB for all active provisioning records for user

    Query Pattern:
    - Partition key: user_id
    - Filter: status = 'active'

    Args:
        user_id: Canonical user ID

    Returns:
        List of active provisioning records

    Raises:
        ValidationError: If DynamoDB query fails
    """

    try:
        response = dynamodb.query(
            TableName=PROVISIONING_STATE_TABLE,
            KeyConditionExpression='user_id = :user_id',
            FilterExpression='#status = :status',
            ExpressionAttributeNames={
                '#status': 'status'
            },
            ExpressionAttributeValues={
                ':user_id': {'S': user_id},
                ':status': {'S': 'active'}
            }
        )

        items = response.get('Items', [])

        logger.info(json.dumps({
            "event": "active_records_retrieved",
            "user_id": user_id,
            "records_count": len(items)
        }))

        return items

    except ClientError as e:
        logger.error(json.dumps({
            "event": "dynamodb_query_failed",
            "user_id": user_id,
            "error": str(e)
        }))
        raise ValidationError(f"Failed to query provisioning records: {e}")


# For local testing
if __name__ == "__main__":
    test_event = {
        "user_id": "test-user",
        "reason": "termination"
    }

    class MockContext:
        request_id = "local-test-12345"
        function_name = "validate_offboarding"

    print("Note: This will query DynamoDB (requires AWS credentials and table to exist)\n")

    try:
        result = lambda_handler(test_event, MockContext())
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Error: {e}")
