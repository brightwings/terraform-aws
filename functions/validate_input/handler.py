"""
Lambda Function: validate_input
Purpose: Validates onboarding request input before provisioning

Input (from Step Functions):
{
    "user_id": "alice",
    "email": "alice@example.com",
    "role": "developer",
    "systems": ["aws", "github", "google_workspace"]
}

Output:
{
    "user_id": "alice",
    "email": "alice@example.com",
    "role": "developer",
    "systems": ["aws", "github", "google_workspace"],
    "validated_at": "2026-02-17T10:00:00Z",
    "validation_status": "success"
}

Error handling:
- Missing required fields → Raise ValidationError
- Invalid email format → Raise ValidationError
- Unknown role → Raise ValidationError
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Any
import re

# Configure structured logging (JSON format for CloudWatch Logs Insights)
logger = logging.getLogger()
logger.setLevel(logging.INFO)


class ValidationError(Exception):
    """Custom exception for validation failures"""
    pass


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler function

    Args:
        event: Input from Step Functions (JSON)
        context: Lambda runtime context (request_id, function_name, etc.)

    Returns:
        Validated input with metadata

    Raises:
        ValidationError: If input validation fails
    """

    # Log incoming request (for debugging and audit trail)
    logger.info(json.dumps({
        "event": "validation_started",
        "request_id": context.aws_request_id,
        "function_name": context.function_name,
        "input": event
    }))

    try:
        # Step 1: Validate required fields
        validate_required_fields(event)

        # Step 2: Validate email format
        validate_email(event.get('email'))

        # Step 3: Validate role
        validate_role(event.get('role'))

        # Step 4: Validate systems list
        validate_systems(event.get('systems'))

        # Step 5: Add metadata
        validated_input = {
            **event,  # Spread operator: include all original fields
            "validated_at": datetime.utcnow().isoformat() + "Z",
            "validation_status": "success"
        }

        # Log success
        logger.info(json.dumps({
            "event": "validation_success",
            "request_id": context.aws_request_id,
            "user_id": event.get('user_id'),
            "systems": event.get('systems')
        }))

        return validated_input

    except ValidationError as e:
        # Log validation error
        logger.error(json.dumps({
            "event": "validation_failed",
            "request_id": context.aws_request_id,
            "error": str(e),
            "input": event
        }))

        # Re-raise for Step Functions error handling
        raise

    except Exception as e:
        # Log unexpected error
        logger.error(json.dumps({
            "event": "unexpected_error",
            "request_id": context.aws_request_id,
            "error": str(e),
            "error_type": type(e).__name__
        }))

        # Re-raise for Step Functions error handling
        raise


def validate_required_fields(event: Dict[str, Any]) -> None:
    """
    Validate that all required fields are present

    Required fields:
    - user_id: Canonical user identifier
    - email: User email address
    - role: User role (developer, admin, contractor)
    - systems: List of systems to provision

    Raises:
        ValidationError: If any required field is missing
    """
    required_fields = ['user_id', 'email', 'role', 'systems']

    for field in required_fields:
        if field not in event or not event[field]:
            raise ValidationError(f"Missing required field: {field}")

    logger.info(json.dumps({
        "event": "required_fields_validated",
        "user_id": event.get('user_id')
    }))


def validate_email(email: str) -> None:
    """
    Validate email format

    Args:
        email: Email address to validate

    Raises:
        ValidationError: If email format is invalid
    """
    # Basic email regex (not perfect, but good enough for our use case)
    email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'

    if not re.match(email_pattern, email):
        raise ValidationError(f"Invalid email format: {email}")

    # Security check: Only allow @example.com emails
    if not email.endswith('@example.com'):
        raise ValidationError(f"Email must be @example.com domain: {email}")

    logger.info(json.dumps({
        "event": "email_validated",
        "email": email
    }))


def validate_role(role: str) -> None:
    """
    Validate user role

    Allowed roles:
    - developer: Standard developer access
    - admin: Administrative access (requires additional approval)
    - contractor: Limited-term contractor access

    Args:
        role: User role to validate

    Raises:
        ValidationError: If role is not in allowed list
    """
    allowed_roles = ['developer', 'admin', 'contractor']

    if role not in allowed_roles:
        raise ValidationError(
            f"Invalid role: {role}. Allowed roles: {', '.join(allowed_roles)}"
        )

    logger.info(json.dumps({
        "event": "role_validated",
        "role": role
    }))


def validate_systems(systems: List[str]) -> None:
    """
    Validate systems list

    Supported systems:
    - aws: AWS IAM user provisioning
    - github: GitHub organization membership
    - google_workspace: Google Workspace account
    - slack: Slack workspace membership

    Args:
        systems: List of systems to provision

    Raises:
        ValidationError: If systems list is empty or contains unknown systems
    """
    if not isinstance(systems, list):
        raise ValidationError("systems must be a list")

    if len(systems) == 0:
        raise ValidationError("systems list cannot be empty")

    supported_systems = ['aws', 'github', 'google_workspace', 'slack']

    for system in systems:
        if system not in supported_systems:
            raise ValidationError(
                f"Unsupported system: {system}. "
                f"Supported systems: {', '.join(supported_systems)}"
            )

    logger.info(json.dumps({
        "event": "systems_validated",
        "systems": systems,
        "count": len(systems)
    }))


# For local testing
if __name__ == "__main__":
    # Mock event for local testing
    test_event = {
        "user_id": "test-user",
        "email": "test@example.com",
        "role": "developer",
        "systems": ["aws", "github"]
    }

    # Mock context
    class MockContext:
        request_id = "local-test-12345"
        function_name = "validate_input"

    # Run handler
    try:
        result = lambda_handler(test_event, MockContext())
        print(json.dumps(result, indent=2))
    except ValidationError as e:
        print(f"Validation failed: {e}")
