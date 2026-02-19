# modules/iam/lambda_execution_roles.tf
# Purpose: Least privilege IAM roles for Lambda functions

# Base Lambda Execution Role (Reusable Trust Policy)
# This allows Lambda service to assume the role
data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

# Role 0: Basic Lambda execution role (DynamoDB + CloudWatch Logs only)
# Used by: most Lambdas that don't need Secrets Manager or IAM access
resource "aws_iam_role" "lambda_basic" {
  name               = "${var.name_prefix}-lambda-basic"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
  tags               = merge(var.tags, { Name = "${var.name_prefix}-lambda-basic" })
}

resource "aws_iam_role_policy" "lambda_basic" {
  name = "basic-permissions"
  role = aws_iam_role.lambda_basic.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBAccess"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:Query"
        ]
        Resource = [
          var.provisioning_state_table_arn,
          "${var.provisioning_state_table_arn}/index/*"
        ]
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/lambda/${var.name_prefix}-*"
      }
    ]
  })
}

# Role 0b: provision_aws / deprovision_aws Lambda
# Needs: IAM user management + DynamoDB + CloudWatch Logs
resource "aws_iam_role" "lambda_provision_aws" {
  name               = "${var.name_prefix}-lambda-provision-aws"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
  tags               = merge(var.tags, { Name = "${var.name_prefix}-lambda-provision-aws" })
}

resource "aws_iam_role_policy" "lambda_provision_aws" {
  name = "provision-aws-permissions"
  role = aws_iam_role.lambda_provision_aws.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "IAMUserManagement"
        Effect = "Allow"
        Action = [
          "iam:CreateUser",
          "iam:GetUser",
          "iam:DeleteUser",
          "iam:TagUser",
          "iam:AddUserToGroup",
          "iam:RemoveUserFromGroup",
          "iam:ListGroupsForUser",
          "iam:ListAccessKeys",
          "iam:DeleteAccessKey",
          "iam:CreateLoginProfile",
          "iam:DeleteLoginProfile"
        ]
        Resource = [
          "arn:aws:iam::${var.aws_account_id}:user/*",
          "arn:aws:iam::${var.aws_account_id}:group/${var.name_prefix}-*"
        ]
      },
      {
        Sid    = "DynamoDBAccess"
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
        Resource = var.provisioning_state_table_arn
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/lambda/${var.name_prefix}-*"
      }
    ]
  })
}

# Role 1: provision_github Lambda
# Needs: Secrets Manager (GitHub token) + DynamoDB (write state) + CloudWatch Logs
resource "aws_iam_role" "lambda_provision_github" {
  name               = "${var.name_prefix}-lambda-provision-github"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = merge(
    var.tags,
    {
      Name      = "${var.name_prefix}-lambda-provision-github"
      ManagedBy = "terraform"
      Purpose   = "GitHub provisioning automation"
    }
  )
}

# Policy for provision_github Lambda (Least Privilege)
resource "aws_iam_role_policy" "lambda_provision_github" {
  name = "provision-github-permissions"
  role = aws_iam_role.lambda_provision_github.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadGitHubAPIToken"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:${var.name_prefix}-github-api-token-*"
        # Scoped to specific secret prefix, NOT all secrets
      },
      {
        Sid    = "WriteProvisioningState"
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:GetItem"
        ]
        Resource = var.provisioning_state_table_arn
        # Scoped to specific table, NOT all tables
      },
      {
        Sid    = "WriteLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/lambda/${var.name_prefix}-provision-github:*"
        # Scoped to this Lambda's log group only
      }
    ]
  })
}

# Role 2: detect_drift Lambda
# Needs: Secrets Manager (SaaS API tokens) + DynamoDB (read state + write drift events) + CloudWatch Logs
resource "aws_iam_role" "lambda_detect_drift" {
  name               = "${var.name_prefix}-lambda-detect-drift"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = merge(
    var.tags,
    {
      Name      = "${var.name_prefix}-lambda-detect-drift"
      ManagedBy = "terraform"
      Purpose   = "Configuration drift detection"
    }
  )
}

resource "aws_iam_role_policy" "lambda_detect_drift" {
  name = "detect-drift-permissions"
  role = aws_iam_role.lambda_detect_drift.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadSaaSAPITokens"
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue"
        ]
        Resource = [
          "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:${var.name_prefix}-github-api-token-*",
          "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:${var.name_prefix}-slack-api-token-*",
          "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:${var.name_prefix}-google-api-credentials-*"
        ]
        # Can read multiple SaaS tokens for drift checks
      },
      {
        Sid    = "ReadProvisioningState"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:Scan"
        ]
        Resource = [
          var.provisioning_state_table_arn,
          "${var.provisioning_state_table_arn}/index/*" # Access to GSIs
        ]
      },
      {
        Sid    = "WriteDriftEvents"
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:UpdateItem"
        ]
        Resource = var.drift_events_table_arn
      },
      {
        Sid    = "WriteLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/lambda/${var.name_prefix}-detect-drift:*"
      }
    ]
  })
}

# Role 3: Step Functions Execution Role
# Needs: Lambda invoke permissions (scoped to specific functions)
resource "aws_iam_role" "step_functions_execution" {
  name = "${var.name_prefix}-step-functions-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "states.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = merge(
    var.tags,
    {
      Name      = "${var.name_prefix}-step-functions-execution"
      ManagedBy = "terraform"
      Purpose   = "Step Functions orchestration"
    }
  )
}

resource "aws_iam_role_policy" "step_functions_execution" {
  name = "step-functions-permissions"
  role = aws_iam_role.step_functions_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "InvokeLambdaFunctions"
        Effect = "Allow"
        Action = [
          "lambda:InvokeFunction"
        ]
        # Scoped to this account's functions with the name prefix only
        Resource = "arn:aws:lambda:${var.aws_region}:${var.aws_account_id}:function:${var.name_prefix}-*"
      },
      {
        Sid    = "WriteLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery",
          "logs:GetLogDelivery",
          "logs:UpdateLogDelivery",
          "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries",
          "logs:PutResourcePolicy",
          "logs:DescribeResourcePolicies",
          "logs:DescribeLogGroups"
        ]
        Resource = "*"
        # Step Functions logging requires broader permissions
      }
    ]
  })
}
