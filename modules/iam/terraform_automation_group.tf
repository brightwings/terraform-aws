# modules/iam/terraform_automation_group.tf
# Purpose: Scoped IAM group for Terraform automation accounts (tf-user-saas-automation)
# These accounts run `terraform apply` and need infrastructure lifecycle permissions,
# but should NEVER read secret values or manage user credentials (access keys, passwords).

resource "aws_iam_group" "terraform_automation" {
  name = "${var.name_prefix}-terraform-automation"
  path = "/"
}

resource "aws_iam_group_policy" "terraform_automation" {
  name  = "terraform-automation-permissions"
  group = aws_iam_group.terraform_automation.name

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
          "iam:UpdateUser",
          "iam:ListUsers",
          "iam:TagUser",
          "iam:UntagUser",
          "iam:AddUserToGroup",
          "iam:RemoveUserFromGroup",
          "iam:ListGroupsForUser",
          "iam:ListUserTags"
        ]
        Resource = "arn:aws:iam::${var.aws_account_id}:user/*"
        # Manages human users declared in Terraform config
        # Does NOT include CreateAccessKey/DeleteAccessKey - Lambda handles credential lifecycle
      },
      {
        Sid    = "IAMGroupManagement"
        Effect = "Allow"
        Action = [
          "iam:CreateGroup",
          "iam:DeleteGroup",
          "iam:GetGroup",
          "iam:ListGroups",
          "iam:UpdateGroup",
          "iam:AttachGroupPolicy",
          "iam:DetachGroupPolicy",
          "iam:PutGroupPolicy",
          "iam:DeleteGroupPolicy",
          "iam:GetGroupPolicy",
          "iam:ListGroupPolicies",
          "iam:ListAttachedGroupPolicies"
        ]
        Resource = "arn:aws:iam::${var.aws_account_id}:group/*"
      },
      {
        Sid    = "IAMRoleManagement"
        Effect = "Allow"
        Action = [
          "iam:CreateRole",
          "iam:DeleteRole",
          "iam:GetRole",
          "iam:ListRoles",
          "iam:UpdateRole",
          "iam:TagRole",
          "iam:UntagRole",
          "iam:UpdateAssumeRolePolicy",
          "iam:AttachRolePolicy",
          "iam:DetachRolePolicy",
          "iam:PutRolePolicy",
          "iam:DeleteRolePolicy",
          "iam:GetRolePolicy",
          "iam:ListRolePolicies",
          "iam:ListAttachedRolePolicies"
        ]
        Resource = "arn:aws:iam::${var.aws_account_id}:role/${var.name_prefix}-*"
        # Scoped to platform roles only
      },
      {
        Sid    = "IAMPassRole"
        Effect = "Allow"
        Action = ["iam:PassRole"]
        Resource = "arn:aws:iam::${var.aws_account_id}:role/${var.name_prefix}-*"
        Condition = {
          StringEquals = {
            "iam:PassedToService" = [
              "lambda.amazonaws.com",
              "states.amazonaws.com"
            ]
          }
        }
        # Required for Terraform to attach IAM roles to Lambda and Step Functions
      },
      {
        Sid    = "IAMReadOnly"
        Effect = "Allow"
        Action = [
          "iam:GetPolicy",
          "iam:GetPolicyVersion",
          "iam:ListPolicies",
          "iam:ListPolicyVersions",
          "iam:ListInstanceProfilesForRole"
        ]
        Resource = "*"
        # Read-only access needed for Terraform plan to detect drift
      },
      {
        Sid    = "LambdaManagement"
        Effect = "Allow"
        Action = [
          "lambda:CreateFunction",
          "lambda:DeleteFunction",
          "lambda:GetFunction",
          "lambda:GetFunctionConfiguration",
          "lambda:UpdateFunctionCode",
          "lambda:UpdateFunctionConfiguration",
          "lambda:ListFunctions",
          "lambda:AddPermission",
          "lambda:RemovePermission",
          "lambda:GetPolicy",
          "lambda:TagResource",
          "lambda:ListTags",
          "lambda:GetFunctionCodeSigningConfig"
        ]
        Resource = "arn:aws:lambda:${var.aws_region}:${var.aws_account_id}:function:${var.name_prefix}-*"
        # Full Lambda lifecycle, scoped to platform functions only
        # Does NOT include InvokeFunction - tf bots deploy code, don't run it
      },
      {
        Sid    = "DynamoDBManagement"
        Effect = "Allow"
        Action = [
          "dynamodb:CreateTable",
          "dynamodb:DeleteTable",
          "dynamodb:DescribeTable",
          "dynamodb:ListTables",
          "dynamodb:UpdateTable",
          "dynamodb:UpdateContinuousBackups",
          "dynamodb:DescribeContinuousBackups",
          "dynamodb:DescribeTimeToLive",
          "dynamodb:UpdateTimeToLive",
          "dynamodb:TagResource",
          "dynamodb:ListTagsOfResource"
        ]
        Resource = "arn:aws:dynamodb:${var.aws_region}:${var.aws_account_id}:table/${var.name_prefix}-*"
        # Infrastructure management only - does NOT include data operations (GetItem, PutItem, etc.)
      },
      {
        Sid    = "StepFunctionsManagement"
        Effect = "Allow"
        Action = [
          "states:CreateStateMachine",
          "states:DeleteStateMachine",
          "states:DescribeStateMachine",
          "states:ListStateMachines",
          "states:UpdateStateMachine",
          "states:TagResource",
          "states:ListTagsForResource"
        ]
        Resource = "arn:aws:states:${var.aws_region}:${var.aws_account_id}:stateMachine:${var.name_prefix}-*"
        # Does NOT include StartExecution - tf bots don't trigger workflows
      },
      {
        Sid    = "SecretsManagerManagement"
        Effect = "Allow"
        Action = [
          "secretsmanager:CreateSecret",
          "secretsmanager:DeleteSecret",
          "secretsmanager:DescribeSecret",
          "secretsmanager:ListSecrets",
          "secretsmanager:TagResource",
          "secretsmanager:PutResourcePolicy",
          "secretsmanager:GetResourcePolicy"
        ]
        Resource = "arn:aws:secretsmanager:${var.aws_region}:${var.aws_account_id}:secret:${var.name_prefix}-*"
        # Can create and manage secrets, but NOT read values (GetSecretValue excluded)
      },
      {
        Sid    = "SNSManagement"
        Effect = "Allow"
        Action = [
          "sns:CreateTopic",
          "sns:DeleteTopic",
          "sns:GetTopicAttributes",
          "sns:SetTopicAttributes",
          "sns:ListTopics",
          "sns:TagResource",
          "sns:ListTagsForResource"
        ]
        Resource = "arn:aws:sns:${var.aws_region}:${var.aws_account_id}:${var.name_prefix}-*"
      },
      {
        Sid    = "EventBridgeManagement"
        Effect = "Allow"
        Action = [
          "events:PutRule",
          "events:DeleteRule",
          "events:DescribeRule",
          "events:ListRules",
          "events:PutTargets",
          "events:RemoveTargets",
          "events:ListTargetsByRule",
          "events:TagResource",
          "events:ListTagsForResource"
        ]
        Resource = "arn:aws:events:${var.aws_region}:${var.aws_account_id}:rule/${var.name_prefix}-*"
      },
      {
        Sid    = "CloudWatchLogsManagement"
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:DeleteLogGroup",
          "logs:DescribeLogGroups",
          "logs:PutRetentionPolicy",
          "logs:DeleteRetentionPolicy",
          "logs:TagLogGroup",
          "logs:ListTagsLogGroup"
        ]
        Resource = [
          "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/lambda/${var.name_prefix}-*",
          "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/states/${var.name_prefix}*"
        ]
      },
      {
        Sid    = "STSReadOnly"
        Effect = "Allow"
        Action = ["sts:GetCallerIdentity"]
        Resource = "*"
        # Required for Terraform data sources (aws_caller_identity)
      }
    ]
  })
}

# Explicit DENY - overrides any Allow, even from other groups
resource "aws_iam_group_policy" "terraform_automation_deny" {
  name  = "terraform-automation-deny"
  group = aws_iam_group.terraform_automation.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DenySecretValues"
        Effect = "Deny"
        Action = ["secretsmanager:GetSecretValue"]
        Resource = "*"
        # tf bots deploy infrastructure, never read API tokens or credentials
      },
      {
        Sid    = "DenyCredentialManagement"
        Effect = "Deny"
        Action = [
          "iam:CreateAccessKey",
          "iam:DeleteAccessKey",
          "iam:UpdateAccessKey",
          "iam:CreateLoginProfile",
          "iam:DeleteLoginProfile",
          "iam:UpdateLoginProfile"
        ]
        Resource = "*"
        # Credential lifecycle belongs to lambda_provision_aws, not tf bots
      },
      {
        Sid    = "DenyPrivilegeEscalation"
        Effect = "Deny"
        Action = [
          "iam:CreatePolicy",
          "iam:DeletePolicy",
          "iam:CreatePolicyVersion",
          "iam:SetDefaultPolicyVersion",
          "iam:AttachUserPolicy",
          "iam:DetachUserPolicy",
          "iam:PutUserPolicy",
          "iam:DeleteUserPolicy"
        ]
        Resource = "*"
        # Cannot create arbitrary policies or attach policies directly to users
        # All permissions flow through groups and named roles only
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
      },
      {
        Sid    = "DenyDataAccess"
        Effect = "Deny"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
          "dynamodb:Scan",
          "lambda:InvokeFunction",
          "states:StartExecution",
          "states:StopExecution"
        ]
        Resource = "*"
        # tf bots manage infrastructure only, never read/write application data
        # or trigger workflows
      }
    ]
  })
}
