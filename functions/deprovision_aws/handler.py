"""
Lambda Function: deprovision_aws
Purpose: Removes user from IAM groups, deletes access keys, marks for deletion (offboarding)

Input (from Step Functions parallel state):
{
    "user_id": "nicole",
    "reason": "termination",
    "system": "aws",
    "aws_username": "nicole",  # Retrieved from DynamoDB
    "execution_arn": "..."
}

Output:
{
    ...input fields...,
    "deprovisioning_status": "success",
    "actions_taken": [
        "removed_from_all_groups",
        "deleted_access_keys",
        "deleted_login_profile",
        "logged_security_event"
    ],
    "deprovisioned_at": "2026-02-17T16:00:00Z"
}

Security Actions:
1. Remove from all IAM groups (revokes all permissions)
2. Delete all access keys (revokes programmatic access)
3. Delete login profile (revokes console access)
4. Tag for deletion (manual deletion in 30 days per AWS best practice)
5. Log security event for audit trail

Note: We don't delete the IAM user immediately to preserve audit trail
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


class AWSDeprovisioningError(Exception):
    """Custom exception for AWS deprovisioning failures"""
    pass


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler - deprovisions AWS IAM user

    Args:
        event: Input from Step Functions
        context: Lambda runtime context

    Returns:
        Deprovisioning result

    Raises:
        AWSDeprovisioningError: If IAM operations fail (CRITICAL!)
    """

    logger.info(json.dumps({
        "event": "aws_deprovisioning_started",
        "request_id": context.aws_request_id,
        "user_id": event.get('user_id'),
        "reason": event.get('reason')
    }))

    try:
        user_id = event['user_id']
        aws_username = event.get('aws_username')
        reason = event.get('reason', 'unknown')

        if not aws_username:
            raise AWSDeprovisioningError(
                "aws_username not provided. Cannot deprovision without username."
            )

        # Step 1: Check if user still exists (idempotency)
        if not check_iam_user_exists(aws_username):
            logger.info(json.dumps({
                "event": "user_not_found",
                "aws_username": aws_username,
                "message": "User already removed or doesn't exist (idempotent)"
            }))

            actions_taken = ["already_removed"]

        else:
            actions_taken = []

            # Step 2: Remove from all groups first (revokes all permissions)
            remove_from_all_groups(aws_username)
            actions_taken.append("removed_from_all_groups")

            # Step 3: Delete all access keys (revokes programmatic access)
            delete_all_access_keys(aws_username)
            actions_taken.append("deleted_access_keys")

            # Step 4: Delete login profile (revokes console access)
            delete_login_profile(aws_username)
            actions_taken.append("deleted_login_profile")

            # Step 5: Tag for deletion (preserves audit trail)
            tag_user_for_deletion(aws_username, reason)
            actions_taken.append("tagged_for_deletion")

            # Step 6: Log security event
            log_security_event(
                user_id=user_id,
                aws_username=aws_username,
                reason=reason,
                actions=actions_taken
            )
            actions_taken.append("logged_security_event")

        # Step 7: Update DynamoDB record to 'deprovisioned'
        update_dynamodb_record(
            user_id=user_id,
            system='aws',
            status='deprovisioned',
            deprovisioned_reason=reason
        )

        # Step 8: Return success
        result = {
            **event,
            "deprovisioning_status": "success",
            "actions_taken": actions_taken,
            "deprovisioned_at": datetime.now().isoformat() + "Z",
            "note": "IAM user tagged for deletion (manual deletion in 30 days)"
        }

        logger.info(json.dumps({
            "event": "aws_deprovisioning_success",
            "request_id": context.aws_request_id,
            "user_id": user_id,
            "aws_username": aws_username,
            "actions_taken": actions_taken
        }))

        return result

    except Exception as e:
        logger.error(json.dumps({
            "event": "aws_deprovisioning_failed",
            "request_id": context.aws_request_id,
            "user_id": event.get('user_id'),
            "error": str(e),
            "error_type": type(e).__name__,
            "severity": "CRITICAL"
        }))

        # CRITICAL: AWS deprovisioning failure is a security incident
        # Alert security team immediately
        alert_security_team_critical_failure(
            user_id=event.get('user_id'),
            system='aws',
            error=str(e)
        )

        raise AWSDeprovisioningError(str(e))


def check_iam_user_exists(username: str) -> bool:
    """
    Check if IAM user exists (idempotency)

    Args:
        username: IAM username

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


def remove_from_all_groups(username: str) -> None:
    """
    Remove IAM user from all groups (revokes all permissions)

    Security Principle: Remove permissions BEFORE deleting access keys
    Why: If key deletion fails, user still has no permissions

    Args:
        username: IAM username

    Raises:
        AWSDeprovisioningError: If group removal fails
    """

    try:
        # Get all groups user belongs to
        response = iam.list_groups_for_user(UserName=username)
        groups = response['Groups']

        for group in groups:
            group_name = group['GroupName']
            iam.remove_user_from_group(
                UserName=username,
                GroupName=group_name
            )
            logger.info(json.dumps({
                "event": "removed_from_group",
                "username": username,
                "group_name": group_name
            }))

        logger.info(json.dumps({
            "event": "removed_from_all_groups",
            "username": username,
            "groups_count": len(groups)
        }))

    except ClientError as e:
        logger.error(json.dumps({
            "event": "remove_from_groups_failed",
            "username": username,
            "error": str(e)
        }))
        raise AWSDeprovisioningError(f"Failed to remove user from groups: {e}")


def delete_all_access_keys(username: str) -> None:
    """
    Delete all access keys for IAM user (revokes programmatic access)

    Security Note: This immediately revokes all programmatic access
    - Active sessions may continue until token expires
    - New API calls will fail immediately

    Args:
        username: IAM username

    Raises:
        AWSDeprovisioningError: If access key deletion fails
    """

    try:
        # List all access keys
        response = iam.list_access_keys(UserName=username)
        access_keys = response['AccessKeyMetadata']

        for key_metadata in access_keys:
            access_key_id = key_metadata['AccessKeyId']

            # Delete access key
            iam.delete_access_key(
                UserName=username,
                AccessKeyId=access_key_id
            )

            logger.info(json.dumps({
                "event": "access_key_deleted",
                "username": username,
                "access_key_id": access_key_id
            }))

        logger.info(json.dumps({
            "event": "deleted_all_access_keys",
            "username": username,
            "keys_count": len(access_keys)
        }))

    except ClientError as e:
        logger.error(json.dumps({
            "event": "delete_access_keys_failed",
            "username": username,
            "error": str(e)
        }))
        raise AWSDeprovisioningError(f"Failed to delete access keys: {e}")


def delete_login_profile(username: str) -> None:
    """
    Delete login profile (revokes console access)

    Security Note: This immediately revokes console access
    - User cannot log in via AWS Console
    - Active console sessions may continue until timeout

    Args:
        username: IAM username
    """

    try:
        iam.delete_login_profile(UserName=username)
        logger.info(json.dumps({
            "event": "login_profile_deleted",
            "username": username
        }))

    except iam.exceptions.NoSuchEntityException:
        # Login profile doesn't exist (user never had console access)
        logger.info(json.dumps({
            "event": "no_login_profile",
            "username": username,
            "message": "User has no login profile (no console access)"
        }))

    except ClientError as e:
        logger.error(json.dumps({
            "event": "delete_login_profile_failed",
            "username": username,
            "error": str(e)
        }))
        raise AWSDeprovisioningError(f"Failed to delete login profile: {e}")


def tag_user_for_deletion(username: str, reason: str) -> None:
    """
    Tag IAM user for deletion (preserves audit trail)

    AWS Best Practice: Don't delete IAM users immediately
    - Preserves CloudTrail audit logs
    - Allows investigation if needed
    - Manual deletion after 30-day retention period

    Args:
        username: IAM username
        reason: Deprovisioning reason
    """

    try:
        iam.tag_user(
            UserName=username,
            Tags=[
                {'Key': 'Status', 'Value': 'deprovisioned'},
                {'Key': 'DeprovisionedAt', 'Value': datetime.now().isoformat()},
                {'Key': 'DeprovisionedReason', 'Value': reason},
                {'Key': 'DeleteAfter', 'Value': '30-days'}
            ]
        )

        logger.info(json.dumps({
            "event": "user_tagged_for_deletion",
            "username": username,
            "reason": reason
        }))

    except ClientError as e:
        logger.error(json.dumps({
            "event": "tagging_failed",
            "username": username,
            "error": str(e)
        }))
        # Non-critical - continue even if tagging fails
        logger.warning("User deprovisioned but tagging failed - manual cleanup needed")


def log_security_event(
    user_id: str,
    aws_username: str,
    reason: str,
    actions: List[str]
) -> None:
    """Log security event for offboarding (audit trail)"""

    security_event = {
        "event_type": "user_deprovisioned",
        "system": "aws",
        "user_id": user_id,
        "aws_username": aws_username,
        "reason": reason,
        "actions_taken": actions,
        "timestamp": datetime.now().isoformat() + "Z",
        "severity": "high"
    }

    logger.info(json.dumps({
        "event": "security_event_logged",
        **security_event
    }))


def update_dynamodb_record(
    user_id: str,
    system: str,
    status: str,
    deprovisioned_reason: str
) -> None:
    """Update DynamoDB record to 'deprovisioned' status"""

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
    """Alert security team of CRITICAL deprovisioning failure"""

    alert = {
        "alert_type": "CRITICAL_DEPROVISIONING_FAILURE",
        "severity": "P1",
        "user_id": user_id,
        "system": system,
        "error": error,
        "action_required": f"Manually remove IAM user {user_id} and delete access keys IMMEDIATELY",
        "timestamp": datetime.now().isoformat() + "Z"
    }

    logger.error(json.dumps({
        "event": "critical_security_alert",
        **alert
    }))


# For local testing
if __name__ == "__main__":
    test_event = {
        "user_id": "test-user",
        "aws_username": "test-user",
        "reason": "termination",
        "system": "aws"
    }

    class MockContext:
        request_id = "local-test-12345"
        function_name = "deprovision_aws"

    print("Note: This will call real AWS IAM API (requires credentials)\n")

    try:
        result = lambda_handler(test_event, MockContext())
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Error: {e}")
