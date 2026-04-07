# modules/iam/gmail_pipeline_roles.tf
# IAM resources for the Gmail Pipeline Tracker project.
# Kept separate from saas-automation roles — this project has no overlap with that system.
#
# Resources defined here:
#   1. Lambda execution role — assumed by the gmail-pipeline-tracker Lambda at runtime
#   2. Deployer group + policy — attached to your IAM user to run `terraform apply` on infra/main.tf

# ── 1. Lambda Execution Role ─────────────────────────────────────────────────
# This is the role the Lambda function assumes when it runs.
# Scoped to exactly what the function needs: read SSM secrets + write CloudWatch logs.

resource "aws_iam_role" "lambda_gmail_pipeline" {
  name               = "gmail-pipeline-lambda-execution"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json

  tags = merge(var.tags, {
    Name      = "gmail-pipeline-lambda-execution"
    ManagedBy = "terraform"
    Purpose   = "Gmail pipeline tracker Lambda runtime role"
  })
}

resource "aws_iam_role_policy" "lambda_gmail_pipeline" {
  name = "gmail-pipeline-lambda-permissions"
  role = aws_iam_role.lambda_gmail_pipeline.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadSSMSecrets"
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters"
        ]
        # Scoped to only the parameters this function needs — nothing else in SSM is accessible
        Resource = "arn:aws:ssm:${var.aws_region}:${var.aws_account_id}:parameter/gmail-pipeline/*"
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        # Scoped to this Lambda's log group only
        Resource = "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/lambda/gmail-pipeline-tracker:*"
      }
    ]
  })
}

# ── 2. Deployer Group + Policy ────────────────────────────────────────────────
# Attach your IAM user to this group to get permission to deploy the gmail pipeline
# via `terraform apply` in gmail-pipeline-lambda/infra/.
#
# To use: add your IAM user to this group in main.tf:
#   resource "aws_iam_user_group_membership" "alice_gmail_pipeline" {
#     user   = aws_iam_user.alice.name
#     groups = [aws_iam_group.gmail_pipeline_deployer.name]
#   }

resource "aws_iam_group" "gmail_pipeline_deployer" {
  name = "gmail-pipeline-deployer"
}

resource "aws_iam_group_policy" "gmail_pipeline_deployer" {
  name  = "gmail-pipeline-deploy-permissions"
  group = aws_iam_group.gmail_pipeline_deployer.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "LambdaDeploy"
        Effect = "Allow"
        Action = [
          "lambda:CreateFunction",
          "lambda:DeleteFunction",
          "lambda:GetFunction",
          "lambda:GetFunctionConfiguration",
          "lambda:UpdateFunctionCode",
          "lambda:UpdateFunctionConfiguration",
          "lambda:AddPermission",
          "lambda:RemovePermission",
          "lambda:InvokeFunction"
        ]
        # Scoped to this project's function only
        Resource = "arn:aws:lambda:${var.aws_region}:${var.aws_account_id}:function:gmail-pipeline-tracker"
      },
      {
        Sid    = "IAMRoleManagement"
        Effect = "Allow"
        Action = [
          "iam:CreateRole",
          "iam:DeleteRole",
          "iam:GetRole",
          "iam:PutRolePolicy",
          "iam:DeleteRolePolicy",
          "iam:GetRolePolicy",
          "iam:AttachRolePolicy",
          "iam:DetachRolePolicy",
          "iam:PassRole"
        ]
        # Scoped to roles prefixed with gmail-pipeline — cannot touch saas-automation roles
        Resource = "arn:aws:iam::${var.aws_account_id}:role/gmail-pipeline-*"
      },
      {
        Sid    = "SSMParameters"
        Effect = "Allow"
        Action = [
          "ssm:PutParameter",
          "ssm:GetParameter",
          "ssm:GetParameters",
          "ssm:DeleteParameter",
          "ssm:DescribeParameters"
        ]
        # Scoped to this project's parameter path only
        Resource = "arn:aws:ssm:${var.aws_region}:${var.aws_account_id}:parameter/gmail-pipeline/*"
      },
      {
        Sid    = "EventBridge"
        Effect = "Allow"
        Action = [
          "events:PutRule",
          "events:DeleteRule",
          "events:DescribeRule",
          "events:PutTargets",
          "events:RemoveTargets",
          "events:ListTargetsByRule"
        ]
        # Scoped to this project's EventBridge rule only
        Resource = "arn:aws:events:${var.aws_region}:${var.aws_account_id}:rule/gmail-pipeline-tracker-*"
      },
      {
        Sid    = "CloudWatchLogs"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:DeleteLogGroup",
          "logs:DescribeLogGroups",
          "logs:PutRetentionPolicy"
        ]
        # Scoped to this project's log group only
        Resource = "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/lambda/gmail-pipeline-tracker:*"
      }
    ]
  })
}
