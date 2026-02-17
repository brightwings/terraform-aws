"""
Lambda Function: finalize_execution
Purpose: Finalizes onboarding workflow by updating DynamoDB records to 'active' status

Input (from AggregateResults state):
{
    "user_id": "isaac",
    "email": "isaac@brightwings.io",
    "systems": ["github", "slack"],
    "results": [
        {"github_result": {"status": "success", "github_username": "isaacbryant"}},
        {"slack_result": {"status": "not_implemented"}}
    ],
    "workflow_status": "completed",
    "completed_at": "2026-02-17T10:10:00Z"
}

Output:
{
    ...input...,
    "finalization_status": "success",
    "systems_activated": ["github"],
    "systems_failed": [],
    "systems_skipped": ["slack"]
}

Actions:
1. Parse provisioning results from parallel branches
2. Update DynamoDB records:
   - status="active" for successful provisions
   - status="error" for failed provisions
3. Log completion for audit trail
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

# AWS client
dynamodb = boto3.client('dynamodb')

# Environment variable
PROVISIONING_STATE_TABLE = os.environ.get(
    'PROVISIONING_STATE_TABLE',
    'saas-automation-dev-provisioning-state'
)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler - finalizes onboarding workflow

    Args:
        event: Aggregated results from Step Functions
        context: Lambda runtime context

    Returns:
        Finalization summary

    Raises:
        Exception: If DynamoDB updates fail
    """

    logger.info(json.dumps({
        "event": "finalize_execution_started",
        "request_id": context.request_id,
        "user_id": event.get('user_id')
    }))

    try:
        user_id = event['user_id']
        results = event.get('results', [])

        # Parse provisioning results
        systems_activated = []
        systems_failed = []
        systems_skipped = []

        for result_set in results:
            for system_key, system_result in result_set.items():
                # Extract system name from key (e.g., "github_result" → "github")
                system = system_key.replace('_result', '')
                status = system_result.get('status', 'unknown')

                if status == 'success':
                    systems_activated.append(system)
                    # Update DynamoDB record to 'active'
                    update_system_status(
                        user_id=user_id,
                        system=system,
                        status='active',
                        metadata=system_result
                    )

                elif status in ['failed', 'error']:
                    systems_failed.append(system)
                    # Update DynamoDB record to 'error'
                    update_system_status(
                        user_id=user_id,
                        system=system,
                        status='error',
                        metadata=system_result
                    )

                else:
                    # Status: 'skipped', 'not_implemented', etc.
                    systems_skipped.append(system)

        # Build finalization summary
        result = {
            **event,
            "finalization_status": "success",
            "systems_activated": systems_activated,
            "systems_failed": systems_failed,
            "systems_skipped": systems_skipped,
            "finalized_at": datetime.utcnow().isoformat() + "Z"
        }

        logger.info(json.dumps({
            "event": "finalize_execution_success",
            "request_id": context.request_id,
            "user_id": user_id,
            "systems_activated": systems_activated,
            "systems_failed": systems_failed
        }))

        return result

    except Exception as e:
        logger.error(json.dumps({
            "event": "finalize_execution_failed",
            "request_id": context.request_id,
            "error": str(e),
            "error_type": type(e).__name__
        }))
        raise


def update_system_status(
    user_id: str,
    system: str,
    status: str,
    metadata: Dict[str, Any]
) -> None:
    """
    Update DynamoDB provisioning record status

    Args:
        user_id: Canonical user ID
        system: System name (github, slack, etc.)
        status: New status ('active' or 'error')
        metadata: Additional metadata from provisioning result
    """

    try:
        update_expression = 'SET #status = :status, finalized_at = :finalized_at'
        expression_values = {
            ':status': {'S': status},
            ':finalized_at': {'S': datetime.utcnow().isoformat() + "Z"}
        }

        # Add system-specific metadata
        if system == 'github' and 'github_username' in metadata:
            update_expression += ', system_username = :username'
            expression_values[':username'] = {'S': metadata['github_username']}

        if 'error' in metadata:
            update_expression += ', error_message = :error'
            expression_values[':error'] = {'S': str(metadata.get('error'))}

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
            "event": "system_status_updated",
            "user_id": user_id,
            "system": system,
            "status": status
        }))

    except ClientError as e:
        logger.error(json.dumps({
            "event": "dynamodb_update_failed",
            "user_id": user_id,
            "system": system,
            "error": str(e)
        }))
        raise


# For local testing
if __name__ == "__main__":
    test_event = {
        "user_id": "test-user",
        "email": "test@brightwings.io",
        "systems": ["github"],
        "results": [
            {
                "github_result": {
                    "status": "success",
                    "github_username": "testuser",
                    "github_org": "brightwings"
                }
            },
            {
                "slack_result": {
                    "status": "not_implemented"
                }
            }
        ],
        "workflow_status": "completed",
        "completed_at": "2026-02-17T10:10:00Z"
    }

    class MockContext:
        request_id = "local-test-12345"
        function_name = "finalize_execution"

    print("Note: This will fail without AWS credentials/LocalStack\n")

    try:
        result = lambda_handler(test_event, MockContext())
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Expected error (no AWS): {e}")
