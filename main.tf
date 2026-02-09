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

resource "aws_iam_user" "tf_user_nicole" {
  name = "tf-user-nicole"
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

# AWS IAM User Group Memberships #
resource "aws_iam_user_group_membership" "isaac_groups" {
  user = aws_iam_user.isaac.name
  groups = [
    aws_iam_group.administrators.name,
  ]
}

resource "aws_iam_user_group_membership" "nicole_groups" {
  user = aws_iam_user.nicole.name
  groups = [
    aws_iam_group.administrators.name,
  ]
}
