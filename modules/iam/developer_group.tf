# modules/iam/developer_group.tf
# Purpose: Least privilege IAM group for human developers (replaces AdministratorAccess anti-pattern)

# Developer Group (for alice, bob)
resource "aws_iam_group" "developers" {
  name = "${var.name_prefix}-developers"
  path = "/"
}

# Developer Policy (Scoped Permissions)
resource "aws_iam_group_policy" "developers" {
  name  = "developer-permissions"
  group = aws_iam_group.developers.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ManageIAMUsers"
        Effect = "Allow"
        Action = [
          "iam:CreateUser",
          "iam:GetUser",
          "iam:ListUsers",
          "iam:TagUser",
          "iam:AddUserToGroup",
          "iam:RemoveUserFromGroup",
          "iam:ListGroupsForUser"
        ]
        Resource = [
          "arn:aws:iam::${var.aws_account_id}:user/*",
          "arn:aws:iam::${var.aws_account_id}:group/${var.name_prefix}-developers"
        ]
        # Can manage users, but NOT delete them
      },
      {
        Sid    = "ReadIAMGroups"
        Effect = "Allow"
        Action = [
          "iam:GetGroup",
          "iam:ListGroups",
          "iam:GetGroupPolicy"
        ]
        Resource = "*"
        # Read-only access to groups (for Terraform planning)
      },
      {
        Sid    = "ManageLambdaFunctions"
        Effect = "Allow"
        Action = [
          "lambda:CreateFunction",
          "lambda:UpdateFunctionCode",
          "lambda:UpdateFunctionConfiguration",
          "lambda:GetFunction",
          "lambda:ListFunctions",
          "lambda:InvokeFunction",
          "lambda:TagResource"
        ]
        Resource = "arn:aws:lambda:${var.aws_region}:${var.aws_account_id}:function:${var.name_prefix}-*"
        # Can manage Lambdas with specific prefix, but NOT delete
      },
      {
        Sid    = "ManageDynamoDB"
        Effect = "Allow"
        Action = [
          "dynamodb:CreateTable",
          "dynamodb:DescribeTable",
          "dynamodb:ListTables",
          "dynamodb:UpdateTable",
          "dynamodb:PutItem",
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:Scan",
          "dynamodb:UpdateItem",
          "dynamodb:TagResource"
        ]
        Resource = "arn:aws:dynamodb:${var.aws_region}:${var.aws_account_id}:table/${var.name_prefix}-*"
        # Can manage DynamoDB tables with prefix, but NOT delete
      },
      {
        Sid    = "ManageStepFunctions"
        Effect = "Allow"
        Action = [
          "states:CreateStateMachine",
          "states:UpdateStateMachine",
          "states:DescribeStateMachine",
          "states:ListStateMachines",
          "states:StartExecution",
          "states:DescribeExecution",
          "states:GetExecutionHistory"
        ]
        Resource = "arn:aws:states:${var.aws_region}:${var.aws_account_id}:stateMachine:${var.name_prefix}-*"
      },
      {
        Sid    = "ReadOnlySecrets"
        Effect = "Allow"
        Action = [
          "secretsmanager:ListSecrets",
          "secretsmanager:DescribeSecret"
        ]
        Resource = "*"
        # Can see secrets exist, but NOT read values (security!)
      },
      {
        Sid    = "ViewCloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams",
          "logs:GetLogEvents",
          "logs:FilterLogEvents"
        ]
        Resource = "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/lambda/${var.name_prefix}-*"
        # Read logs for debugging, but NOT delete log groups
      },
      {
        Sid    = "PassRoleToLambda"
        Effect = "Allow"
        Action = [
          "iam:PassRole"
        ]
        Resource = [
          aws_iam_role.lambda_provision_github.arn,
          aws_iam_role.lambda_detect_drift.arn
        ]
        Condition = {
          StringEquals = {
            "iam:PassedToService" = "lambda.amazonaws.com"
          }
        }
        # Allows attaching IAM roles to Lambda functions (required for Terraform)
        # But scoped to specific roles and Lambda service only
      }
    ]
  })
}

# Explicit DENY for Destructive Operations (Prevents Privilege Escalation)
resource "aws_iam_group_policy" "developers_deny_destructive" {
  name  = "deny-destructive-operations"
  group = aws_iam_group.developers.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DenyDeleteOperations"
        Effect = "Deny"
        Action = [
          "iam:DeleteUser",
          "iam:DeleteGroup",
          "iam:DeleteRole",
          "iam:DeleteRolePolicy",
          "lambda:DeleteFunction",
          "dynamodb:DeleteTable",
          "states:DeleteStateMachine",
          "logs:DeleteLogGroup",
          "secretsmanager:DeleteSecret"
        ]
        Resource = "*"
        # Explicit DENY overrides ANY ALLOW (even if added to another group)
      },
      {
        Sid    = "DenyBillingAccess"
        Effect = "Deny"
        Action = [
          "aws-portal:*",
          "account:*",
          "billing:*",
          "budgets:*"
        ]
        Resource = "*"
        # Developers don't need billing access
      },
      {
        Sid    = "DenySecretsAccess"
        Effect = "Deny"
        Action = [
          "secretsmanager:GetSecretValue",
          "secretsmanager:GetResourcePolicy"
        ]
        Resource = "*"
        # Developers can LIST secrets, but NOT read values
        # Only Lambda functions should read secrets
      }
    ]
  })
}
