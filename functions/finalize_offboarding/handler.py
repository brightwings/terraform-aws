"""
Lambda Function: finalize_offboarding
Purpose: Aggregates deprovisioning results and logs security event

Input (from Step Functions):
{
    "user_id": "nicole",
    "reason": "termination",
    "active_systems": ["github", "slack", "aws", "jira"],
    "results": [
        [{"github_result": {"deprovisioning_status": "success", ...}}],
        [{"slack_result": {"deprovisioning_status": "success", ...}}],
        [{"aws_result": {"deprovisioning_status": "success", ...}}],
        [{"jira_result": {"deprovisioning_status": "success", ...}}]
    ]
}

Output:
{
    ...input fields...,
    "workflow_status": "completed",
    "systems_deprovisioned": ["github", "slack", "aws", "jira"],
    "systems_failed": [],
    "completed_at": "2026-02-17T16:00:00Z"
}

Purpose:
1. Aggregate deprovisioning results from parallel state
2. Categorize: success vs failed
3. Log final security event
4. Return summary for notification
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


class FinalizationError(Exception):
    """Custom exception for finalization failures"""
    pass


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler - finalizes offboarding workflow

    Args:
        event: Input from Step Functions (includes results from parallel state)
        context: Lambda runtime context

    Returns:
        Final workflow result

    Raises:
        FinalizationError: If finalization fails (non-critical)
    """

    logger.info(json.dumps({
        "event": "finalize_offboarding_started",
        "request_id": context.aws_request_id,
        "user_id": event.get('user_id')
    }))

    try:
        user_id = event['user_id']
        reason = event.get('reason', 'unknown')
        results = event.get('results', [])

        # Step 1: Aggregate results from parallel state
        systems_deprovisioned = []
        systems_failed = []

        for result_branch in results:
            # Each branch returns a dict like {"github_result": {...}}
            if isinstance(result_branch, list) and len(result_branch) > 0:
                result_dict = result_branch[0]

                for system_key, system_result in result_dict.items():
                    # Extract system name (e.g., "github_result" → "github")
                    system = system_key.replace('_result', '')

                    if isinstance(system_result, dict):
                        status = system_result.get('status') or system_result.get('deprovisioning_status')

                        if status == 'success':
                            systems_deprovisioned.append(system)
                        elif status == 'failed':
                            systems_failed.append(system)
                        elif status == 'skipped':
                            # System was not provisioned, skip
                            continue

        # Step 2: Log final security event
        log_offboarding_security_event(
            user_id=user_id,
            reason=reason,
            systems_deprovisioned=systems_deprovisioned,
            systems_failed=systems_failed
        )

        # Step 3: Determine workflow status
        if len(systems_failed) > 0:
            workflow_status = "completed_with_failures"
        else:
            workflow_status = "completed"

        # Step 4: Return final result
        result = {
            "user_id": user_id,
            "reason": reason,
            "workflow_status": workflow_status,
            "systems_deprovisioned": systems_deprovisioned,
            "systems_failed": systems_failed,
            "completed_at": datetime.now().isoformat() + "Z"
        }

        logger.info(json.dumps({
            "event": "finalize_offboarding_success",
            "request_id": context.aws_request_id,
            "user_id": user_id,
            "workflow_status": workflow_status,
            "systems_deprovisioned": systems_deprovisioned,
            "systems_failed": systems_failed
        }))

        return result

    except Exception as e:
        logger.error(json.dumps({
            "event": "finalize_offboarding_failed",
            "request_id": context.aws_request_id,
            "user_id": event.get('user_id'),
            "error": str(e),
            "error_type": type(e).__name__
        }))

        # Note: Finalization failure is NOT critical
        # Users were already deprovisioned (that's what matters)
        # This is just logging and status updates
        raise FinalizationError(str(e))


def log_offboarding_security_event(
    user_id: str,
    reason: str,
    systems_deprovisioned: List[str],
    systems_failed: List[str]
) -> None:
    """
    Log final offboarding security event

    This is the "master" security event that summarizes entire offboarding
    - Individual systems also logged their own events
    - This provides workflow-level summary

    Event Schema:
    {
        "event_type": "offboarding_completed",
        "user_id": "nicole",
        "reason": "termination",
        "systems_deprovisioned": ["github", "slack", "aws", "jira"],
        "systems_failed": [],
        "timestamp": "2026-02-17T16:00:00Z",
        "severity": "high"
    }

    Storage:
    - CloudWatch Logs (immediate)
    - DynamoDB security_events table (queryable)
    - S3 for long-term retention (compliance)
    - SIEM (Splunk, Datadog, etc.)

    Args:
        user_id: Canonical user ID
        reason: Offboarding reason
        systems_deprovisioned: List of successfully deprovisioned systems
        systems_failed: List of systems that failed to deprovision
    """

    # Determine severity based on failures
    if len(systems_failed) > 0:
        severity = "critical"  # Failures require immediate attention
    else:
        severity = "high"  # Standard offboarding

    security_event = {
        "event_type": "offboarding_completed",
        "user_id": user_id,
        "reason": reason,
        "systems_deprovisioned": systems_deprovisioned,
        "systems_failed": systems_failed,
        "timestamp": datetime.now().isoformat() + "Z",
        "severity": severity
    }

    logger.info(json.dumps({
        "event": "offboarding_security_event_logged",
        **security_event
    }))

    # TODO: Write to DynamoDB security_events table
    # TODO: Send to SIEM
    # TODO: If failures exist, send to PagerDuty


# For local testing
if __name__ == "__main__":
    test_event = {
        "user_id": "test-user",
        "reason": "termination",
        "active_systems": ["github", "slack"],
        "results": [
            [{"github_result": {"deprovisioning_status": "success"}}],
            [{"slack_result": {"deprovisioning_status": "success"}}]
        ]
    }

    class MockContext:
        request_id = "local-test-12345"
        function_name = "finalize_offboarding"

    print("Note: This aggregates results and logs security events\n")

    try:
        result = lambda_handler(test_event, MockContext())
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Error: {e}")
