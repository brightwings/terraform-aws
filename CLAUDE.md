# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This repository manages AWS IAM infrastructure using Terraform for the Brightwings organization. It provisions IAM users, groups, and associated policies using infrastructure as code.

## Terraform Configuration

**Provider Profile**: `tf-user-isaac`
- The AWS provider is configured to use the `tf-user-isaac` profile from AWS credentials
- This profile must exist in `~/.aws/credentials` or be configured via AWS CLI

**Terraform Version**: Uses AWS provider version 6.31.0

## Common Commands

### Initialize and Plan
```bash
terraform init          # Initialize Terraform (required after cloning or adding providers)
terraform plan          # Preview changes before applying
terraform apply         # Apply infrastructure changes
```

### State Management
```bash
terraform state list                           # List all resources in state
terraform state show <resource>                # Show detailed state of a resource
terraform import <resource_type>.<name> <id>   # Import existing AWS resources
```

### Importing Existing IAM Resources
When importing existing IAM users or groups:
```bash
terraform import aws_iam_user.<resource_name> <aws_username>
terraform import aws_iam_group.<resource_name> <aws_groupname>
```

After importing, run `terraform plan` to identify any configuration drift (tags, paths, etc.) that need to be added to the Terraform configuration.

## Infrastructure Architecture

### IAM Users
- **isaac**: Developer user
- **tf-user-isaac**: Terraform automation user (used by the provider)
- **nicole**: Developer user

All users are tagged with `ManagedBy = "terraform"` to indicate they are managed by this Terraform configuration.

### IAM Groups
- **administrators**: Group with AdministratorAccess policy attached
- **developers**: Group for developer-level access (no policies attached yet)

### Policy Attachments
- The `administrators` group has the AWS managed policy `AdministratorAccess` attached via `aws_iam_group_policy_attachment`

## File Structure

- `main.tf`: Primary infrastructure definitions (users, groups, policy attachments)
- `variables.tf`: Variable definitions (currently empty)
- `output.tf`: Output definitions (currently empty)
- `terraform.tfstate`: Current state (DO NOT manually edit)
- `terraform.tfstate.backup`: Previous state backup

## Working with This Repository

### Adding New Users
1. Add a new `aws_iam_user` resource in `main.tf`
2. Include the `ManagedBy = "terraform"` tag for consistency
3. Run `terraform plan` to preview
4. Run `terraform apply` to create

### Adding Users to Groups
Use `aws_iam_user_group_membership` resources to assign users to groups:
```hcl
resource "aws_iam_user_group_membership" "user_groups" {
  user = aws_iam_user.<username>.name
  groups = [
    aws_iam_group.<groupname>.name,
  ]
}
```

### Attaching Policies to Groups
Use `aws_iam_group_policy_attachment` for AWS managed policies, or `aws_iam_group_policy` for inline policies.

## Important Notes

- This repository manages IAM access control - changes directly affect AWS account security
- Always run `terraform plan` before `terraform apply` to review changes
- The `tf-user-isaac` user must have sufficient IAM permissions to manage users, groups, and policies
- Imported resources may have tags or configurations not reflected in Terraform - check `terraform plan` output carefully