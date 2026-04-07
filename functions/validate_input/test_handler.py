"""
Unit tests for validate_input Lambda function
Run with: python3 -m pytest test_handler.py
"""

import pytest
import json
from handler import lambda_handler, ValidationError


class MockContext:
    """Mock Lambda context for testing"""
    request_id = "test-request-12345"
    function_name = "validate_input"
    memory_limit_in_mb = 128
    invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:validate_input"


def test_valid_input():
    """Test with valid input - should succeed"""
    event = {
        "user_id": "alice",
        "email": "alice@example.com",
        "role": "developer",
        "systems": ["aws", "github"]
    }

    result = lambda_handler(event, MockContext())

    assert result["user_id"] == "alice"
    assert result["validation_status"] == "success"
    assert "validated_at" in result
    print("✅ Test passed: valid_input")


def test_missing_user_id():
    """Test with missing user_id - should fail"""
    event = {
        "email": "alice@example.com",
        "role": "developer",
        "systems": ["aws"]
    }

    with pytest.raises(ValidationError, match="Missing required field: user_id"):
        lambda_handler(event, MockContext())

    print("✅ Test passed: missing_user_id raises ValidationError")


def test_invalid_email_format():
    """Test with invalid email format - should fail"""
    event = {
        "user_id": "alice",
        "email": "not-an-email",
        "role": "developer",
        "systems": ["aws"]
    }

    with pytest.raises(ValidationError, match="Invalid email format"):
        lambda_handler(event, MockContext())

    print("✅ Test passed: invalid_email_format raises ValidationError")


def test_wrong_email_domain():
    """Test with wrong email domain - should fail (security check)"""
    event = {
        "user_id": "hacker",
        "email": "hacker@evil.com",
        "role": "developer",
        "systems": ["aws"]
    }

    with pytest.raises(ValidationError, match="Email must be @example.com domain"):
        lambda_handler(event, MockContext())

    print("✅ Test passed: wrong_email_domain raises ValidationError")


def test_invalid_role():
    """Test with invalid role - should fail"""
    event = {
        "user_id": "alice",
        "email": "alice@example.com",
        "role": "superadmin",  # Not in allowed_roles
        "systems": ["aws"]
    }

    with pytest.raises(ValidationError, match="Invalid role"):
        lambda_handler(event, MockContext())

    print("✅ Test passed: invalid_role raises ValidationError")


def test_unsupported_system():
    """Test with unsupported system - should fail"""
    event = {
        "user_id": "alice",
        "email": "alice@example.com",
        "role": "developer",
        "systems": ["aws", "jira", "unknown-system"]
    }

    with pytest.raises(ValidationError, match="Unsupported system"):
        lambda_handler(event, MockContext())

    print("✅ Test passed: unsupported_system raises ValidationError")


def test_empty_systems_list():
    """Test with empty systems list - should fail"""
    event = {
        "user_id": "alice",
        "email": "alice@example.com",
        "role": "developer",
        "systems": []
    }

    with pytest.raises(ValidationError, match="systems list cannot be empty"):
        lambda_handler(event, MockContext())

    print("✅ Test passed: empty_systems_list raises ValidationError")


if __name__ == "__main__":
    # Run tests manually (without pytest)
    print("Running unit tests for validate_input Lambda...\n")

    try:
        test_valid_input()
        test_missing_user_id()
        test_invalid_email_format()
        test_wrong_email_domain()
        test_invalid_role()
        test_unsupported_system()
        test_empty_systems_list()

        print("\n🎉 All tests passed!")

    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
