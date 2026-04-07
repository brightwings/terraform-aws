# Reusable Lambda function module
# Creates a zip archive from source directory and deploys as Lambda function

data "archive_file" "function_zip" {
  type        = "zip"
  source_dir  = var.source_dir
  output_path = "${path.module}/../../.build/${var.function_name}.zip"
}

resource "aws_lambda_function" "this" {
  function_name    = var.function_name
  filename         = data.archive_file.function_zip.output_path
  source_code_hash = data.archive_file.function_zip.output_base64sha256
  role             = var.execution_role_arn
  handler          = "handler.lambda_handler"
  runtime          = "python3.12"
  timeout          = var.timeout
  memory_size      = var.memory_size

  environment {
    variables = var.environment_variables
  }

  tags = var.tags
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${var.function_name}"
  retention_in_days = 30
  tags              = var.tags
}
