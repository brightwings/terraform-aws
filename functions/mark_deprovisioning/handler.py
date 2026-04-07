"""
Lambda Function: mark_deprovisioning
Purpose: Updates DynamoDB records to 'deprovisioning' status (prevents concurrent operations)

Input (from Step Functions):
{
    "user_id": "bob",
    "reason": "termination",
    "active_systems": ["github", "slack", "aws", "jira"],
    "github_username": "bjones",
    ...
}

Output:
{
    ...input fields...,
    "marking_status": "success",
    "marked_systems": ["github", "slack", "aws", "jira"],
    "marked_at": "2026-02-17T15:00:00Z"
}

Security Design: Prevent Concurrent Operations
- Status flow: active → deprovisioning → deprovisioned
- Multiple offboarding workflows cannot run concurrently for same user
- If status is already 'deprovisioning', workflow fails fast

Why This Matters:
1. Prevents duplicate API calls (e.g., removing from GitHub org twice)
2. Prevents race conditions (two workflows trying to delete same access key)
3. Ensures audit trail consistency (one deprovisioning event, not multiple)
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


class MarkingError(Exception):
    """Custom exception for marking failures"""
    pass


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler - marks provisioning records as 'deprovisioning'

    Args:
        event: Input from Step Functions (includes active_systems)
        context: Lambda runtime context

    Returns:
        Marking result

    Raises:
        MarkingError: If DynamoDB update fails or concurrent operation detected
    """

    logger.info(json.dumps({
        "event": "marking_deprovisioning_started",
        "request_id": context.aws_request_id,
        "user_id": event.get('user_id')
    }))

    try:
        user_id = event['user_id']
        active_systems = event['active_systems']
        reason = event.get('reason', 'unknown')

        if not active_systems or len(active_systems) == 0:
            raise MarkingError("active_systems list is empty")

        # Update status for each system
        marked_systems = []
        for system in active_systems:
            try:
                mark_system_deprovisioning(
                    user_id=user_id,
                    system=system,
                    reason=reason
                )
                marked_systems.append(system)
            except ConditionalCheckFailedException as e:
                # System is already being deprovisioned or is no longer active
                logger.warning(json.dumps({
                    "event": "concurrent_operation_detected",
                    "user_id": user_id,
                    "system": system,
                    "message": "Another workflow is already deprovisioning this system"
                }))
                # Continue with other systems, but log warning
                continue

        if len(marked_systems) == 0:
            raise MarkingError(
                "No systems could be marked (all already being deprovisioned or no longer active)"
            )

        # Return success
        result = {
            **event,
            "marking_status": "success",
            "marked_systems": marked_systems,
            "marked_at": datetime.now().isoformat() + "Z"
        }

        logger.info(json.dumps({
            "event": "marking_deprovisioning_success",
            "request_id": context.aws_request_id,
            "user_id": user_id,
            "marked_systems": marked_systems
        }))

        return result

    except Exception as e:
        logger.error(json.dumps({
            "event": "marking_deprovisioning_failed",
            "request_id": context.aws_request_id,
            "user_id": event.get('user_id'),
            "error": str(e),
            "error_type": type(e).__name__
        }))
        raise MarkingError(str(e))


class ConditionalCheckFailedException(Exception):
    """Exception for DynamoDB conditional check failures"""
    pass


def mark_system_deprovisioning(user_id: str, system: str, reason: str) -> None:
    """
    Update DynamoDB record status to 'deprovisioning'

    Conditional Update:
    - Only update if current status = 'active'
    - Prevents race condition with concurrent workflows
    - Atomic operation (no partial updates)

    Args:
        user_id: Canonical user ID
        system: System name (e.g., 'github', 'slack')
        reason: Offboarding reason

    Raises:
        ConditionalCheckFailedException: If status is not 'active'
        MarkingError: If update fails
    """

    try:
        dynamodb.update_item(
            TableName=PROVISIONING_STATE_TABLE,
            Key={
                'user_id': {'S': user_id},
                'system': {'S': system}
            },
            UpdateExpression='SET #status = :deprovisioning_status, deprovisioning_started_at = :timestamp, deprovisioning_reason = :reason',
            ConditionExpression='#status IN (:active_status, :deprovisioning_status)',
            ExpressionAttributeNames={
                '#status': 'status'
            },
            ExpressionAttributeValues={
                ':active_status': {'S': 'active'},
                ':deprovisioning_status': {'S': 'deprovisioning'},
                ':timestamp': {'S': datetime.now().isoformat() + "Z"},
                ':reason': {'S': reason}
            }
        )

        logger.info(json.dumps({
            "event": "system_marked_deprovisioning",
            "user_id": user_id,
            "system": system
        }))

    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            # Record is not in 'active' status (already being deprovisioned or deprovisioned)
            logger.warning(json.dumps({
                "event": "conditional_check_failed",
                "user_id": user_id,
                "system": system,
                "message": "Record is not in 'active' status"
            }))
            raise ConditionalCheckFailedException(
                f"System {system} is not in 'active' status"
            )
        else:
            logger.error(json.dumps({
                "event": "dynamodb_update_failed",
                "user_id": user_id,
                "system": system,
                "error": str(e)
            }))
            raise MarkingError(f"Failed to update DynamoDB: {e}")


# For local testing
if __name__ == "__main__":
    test_event = {
        "user_id": "test-user",
        "reason": "termination",
        "active_systems": ["github", "slack"]
    }

    class MockContext:
        request_id = "local-test-12345"
        function_name = "mark_deprovisioning"

    print("Note: This will update DynamoDB (requires AWS credentials and table to exist)\n")

    try:
        result = lambda_handler(test_event, MockContext())
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Error: {e}")
