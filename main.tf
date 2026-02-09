terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.31.0"
    }
  }
}

provider "aws" {
  profile = "tf-user-isaac"
}

# AWS IAM Users #
resource "aws_iam_user" "isaac" {
  name = "isaac"
  path = "/"

  tags = {
    ManagedBy = "terraform"
  }
}

resource "aws_iam_user" "tf_user_isaac" {
  name = "tf-user-isaac"
  path = "/"

  tags = {
    ManagedBy = "terraform"
  }
}

resource "aws_iam_user" "nicole" {
  name = "nicole"
  path = "/"

  tags = {
    ManagedBy = "terraform"
  }
}

# AWS IAM Groups #
resource "aws_iam_group" "administrators" {
  name = "administrators"
  path = "/"
}

resource "aws_iam_group" "developers" {
  name = "developers"
  path = "/"
}

# AWS IAM Group Policy Attachments #
resource "aws_iam_group_policy_attachment" "administrators_admin_access" {
  group      = aws_iam_group.administrators.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}
