# Terraform configuration for Genome Data Pipeline
# Provider configuration
terraform {
  required_version = ">= 1.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# Variables
variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project name for resource naming"
  type        = string
  default     = "genome-pipeline"
}

variable "lambda_timeout" {
  description = "Lambda timeout in seconds"
  type        = number
  default     = 900 # 15 minutes
}

variable "lambda_memory" {
  description = "Lambda memory in MB"
  type        = number
  default     = 3008 # Account/runtime limit observed during deploy
}

variable "ncbi_email" {
  description = "Contact email used for NCBI Entrez requests"
  type        = string
  default     = "your_email@example.com"
}

variable "batch_image_tag" {
  description = "Container image tag used for the Batch full-analysis runner"
  type        = string
  default     = "latest"
}

variable "batch_job_vcpus" {
  description = "vCPU allocation for large-chromosome full-analysis jobs on AWS Batch"
  type        = number
  default     = 8
}

variable "batch_job_memory" {
  description = "Memory in MiB for large-chromosome full-analysis jobs on AWS Batch"
  type        = number
  default     = 32768
}

variable "batch_max_vcpus" {
  description = "Maximum vCPUs AWS Batch can scale to in the Fargate compute environment"
  type        = number
  default     = 256
}

# S3 Buckets
resource "aws_s3_bucket" "genome_output" {
  bucket = "${var.project_name}-output-${data.aws_caller_identity.current.account_id}"

  tags = {
    Name        = "Genome Pipeline Output"
    Environment = "production"
  }
}

resource "aws_s3_bucket_versioning" "genome_output" {
  bucket = aws_s3_bucket.genome_output.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket" "genome_temp" {
  bucket = "${var.project_name}-temp-${data.aws_caller_identity.current.account_id}"

  tags = {
    Name        = "Genome Pipeline Temp Storage"
    Environment = "production"
  }
}

# Lifecycle policy for temp bucket
resource "aws_s3_bucket_lifecycle_configuration" "genome_temp" {
  bucket = aws_s3_bucket.genome_temp.id

  rule {
    id     = "delete-after-7-days"
    status = "Enabled"

    filter {}

    expiration {
      days = 7
    }
  }
}

resource "aws_s3_object" "lambda_layer_zip" {
  bucket = aws_s3_bucket.genome_temp.id
  key    = "artifacts/lambda_layer.zip"
  source = "lambda_layer.zip"
  etag   = filemd5("lambda_layer.zip")
}

resource "aws_s3_object" "lambda_function_zip" {
  bucket = aws_s3_bucket.genome_temp.id
  key    = "artifacts/lambda_function.zip"
  source = "lambda_function.zip"
  etag   = filemd5("lambda_function.zip")
}

resource "aws_s3_object" "web_api_function_zip" {
  bucket = aws_s3_bucket.genome_temp.id
  key    = "artifacts/web_api_function.zip"
  source = "web_api_function.zip"
  etag   = filemd5("web_api_function.zip")
}

resource "aws_ecr_repository" "batch_runner" {
  name                 = "${var.project_name}-batch-runner"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }
}

# IAM Role for Lambda
resource "aws_iam_role" "lambda_role" {
  name = "${var.project_name}-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

# IAM Policy for Lambda
resource "aws_iam_role_policy" "lambda_policy" {
  name = "${var.project_name}-lambda-policy"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject"
        ]
        Resource = [
          "${aws_s3_bucket.genome_output.arn}/*",
          "${aws_s3_bucket.genome_temp.arn}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.genome_output.arn,
          aws_s3_bucket.genome_temp.arn
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:SendMessage"
        ]
        Resource = aws_sqs_queue.genome_queue.arn
      },
      {
        Effect = "Allow"
        Action = [
          "athena:StartQueryExecution",
          "athena:GetQueryExecution"
        ]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["cloudfront:CreateInvalidation"]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject"
        ]
        Resource = "${aws_s3_bucket.athena_results.arn}/*"
      },
      {
        Effect = "Allow"
        Action = [
          "glue:GetTable",
          "glue:GetPartitions",
          "glue:BatchCreatePartition"
        ]
        Resource = "*"
      }
    ]
  })
}

# Lambda Layer for C++ binary and dependencies
resource "aws_lambda_layer_version" "genome_parser_layer" {
  s3_bucket        = aws_s3_object.lambda_layer_zip.bucket
  s3_key           = aws_s3_object.lambda_layer_zip.key
  layer_name       = "${var.project_name}-parser-layer"
  source_code_hash = filebase64sha256("lambda_layer.zip")

  compatible_runtimes = ["python3.11", "python3.12"]

  description = "C++ FASTA/FASTQ parser and Python dependencies"
}

# Lambda Function
resource "aws_lambda_function" "genome_processor" {
  s3_bucket        = aws_s3_object.lambda_function_zip.bucket
  s3_key           = aws_s3_object.lambda_function_zip.key
  function_name    = "${var.project_name}-processor"
  role             = aws_iam_role.lambda_role.arn
  handler          = "lambda_handler.lambda_handler"
  source_code_hash = filebase64sha256("lambda_function.zip")
  runtime          = "python3.11"
  timeout          = var.lambda_timeout
  memory_size      = var.lambda_memory

  layers = [aws_lambda_layer_version.genome_parser_layer.arn]

  environment {
    variables = {
      OUTPUT_BUCKET          = aws_s3_bucket.genome_output.id
      TEMP_BUCKET            = aws_s3_bucket.genome_temp.id
      NCBI_EMAIL             = var.ncbi_email
      ATHENA_DATABASE        = aws_glue_catalog_database.genome_db.name
      ATHENA_WORKGROUP       = aws_athena_workgroup.genome_workgroup.name
      ATHENA_RESULTS_BUCKET  = aws_s3_bucket.athena_results.id
      API_CF_DISTRIBUTION_ID = aws_cloudfront_distribution.api.id
    }
  }

  ephemeral_storage {
    size = 10240 # 10 GB - maximum for Lambda
  }

  tags = {
    Name        = "Genome Processor Lambda"
    Environment = "production"
  }
}

# CloudWatch Log Group
resource "aws_cloudwatch_log_group" "lambda_logs" {
  name              = "/aws/lambda/${aws_lambda_function.genome_processor.function_name}"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "batch_job_logs" {
  name              = "/aws/batch/job/${var.project_name}-full-analysis"
  retention_in_days = 30
}

resource "aws_iam_role" "batch_service_role" {
  name = "${var.project_name}-batch-service-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "batch.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "batch_service" {
  role       = aws_iam_role.batch_service_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBatchServiceRole"
}

resource "aws_iam_role" "batch_execution_role" {
  name = "${var.project_name}-batch-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "batch_execution_policy" {
  role       = aws_iam_role.batch_execution_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "batch_job_role" {
  name = "${var.project_name}-batch-job-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "batch_job_policy" {
  name = "${var.project_name}-batch-job-policy"
  role = aws_iam_role.batch_job_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject"
        ]
        Resource = [
          "${aws_s3_bucket.genome_output.arn}/*",
          "${aws_s3_bucket.genome_temp.arn}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.genome_output.arn,
          aws_s3_bucket.genome_temp.arn
        ]
      }
    ]
  })
}

resource "aws_security_group" "batch_jobs" {
  name        = "${var.project_name}-batch-fargate"
  description = "Outbound internet access for genome full-analysis Batch jobs"
  vpc_id      = data.aws_vpc.default.id

  egress {
    from_port        = 0
    to_port          = 0
    protocol         = "-1"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }

  tags = {
    Name        = "Genome Batch Fargate"
    Environment = "production"
  }
}

resource "aws_batch_compute_environment" "full_analysis_fargate" {
  compute_environment_name = "${var.project_name}-full-analysis-fargate"
  service_role             = aws_iam_role.batch_service_role.arn
  type                     = "MANAGED"
  state                    = "ENABLED"

  compute_resources {
    type               = "FARGATE"
    max_vcpus          = var.batch_max_vcpus
    security_group_ids = [aws_security_group.batch_jobs.id]
    subnets            = data.aws_subnets.default.ids
  }

  depends_on = [aws_iam_role_policy_attachment.batch_service]
}

resource "aws_batch_job_queue" "full_analysis" {
  name     = "${var.project_name}-full-analysis"
  state    = "ENABLED"
  priority = 1

  compute_environment_order {
    order               = 1
    compute_environment = aws_batch_compute_environment.full_analysis_fargate.arn
  }
}

resource "aws_batch_job_definition" "full_analysis" {
  name                  = "${var.project_name}-full-analysis"
  type                  = "container"
  platform_capabilities = ["FARGATE"]

  container_properties = jsonencode({
    image            = "${aws_ecr_repository.batch_runner.repository_url}:${var.batch_image_tag}"
    command          = ["python", "/app/batch_entrypoint.py"]
    executionRoleArn = aws_iam_role.batch_execution_role.arn
    jobRoleArn       = aws_iam_role.batch_job_role.arn
    resourceRequirements = [
      { type = "VCPU", value = tostring(var.batch_job_vcpus) },
      { type = "MEMORY", value = tostring(var.batch_job_memory) }
    ]
    fargatePlatformConfiguration = {
      platformVersion = "LATEST"
    }
    networkConfiguration = {
      assignPublicIp = "ENABLED"
    }
    runtimePlatform = {
      operatingSystemFamily = "LINUX"
      cpuArchitecture       = "X86_64"
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.batch_job_logs.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "genome-batch"
      }
    }
    environment = [
      { name = "OUTPUT_BUCKET", value = aws_s3_bucket.genome_output.id },
      { name = "TEMP_BUCKET", value = aws_s3_bucket.genome_temp.id },
      { name = "NCBI_EMAIL", value = var.ncbi_email }
    ]
  })
}

# IAM Role for Dashboard API Lambda
resource "aws_iam_role" "web_api_role" {
  name = "${var.project_name}-web-api-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "web_api_policy" {
  name = "${var.project_name}-web-api-policy"
  role = aws_iam_role.web_api_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Effect = "Allow"
        Action = [
          "sqs:SendMessage",
          "sqs:GetQueueAttributes"
        ]
        Resource = [
          aws_sqs_queue.genome_queue.arn,
          aws_sqs_queue.genome_dlq.arn
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject"
        ]
        Resource = "${aws_s3_bucket.genome_output.arn}/*"
      },
      {
        Effect = "Allow"
        Action = [
          "s3:ListBucket"
        ]
        Resource = aws_s3_bucket.genome_output.arn
      },
      {
        Effect = "Allow"
        Action = [
          "s3:ListBucket",
          "s3:GetBucketLocation"
        ]
        Resource = aws_s3_bucket.athena_results.arn
      },
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:AbortMultipartUpload",
          "s3:ListMultipartUploadParts"
        ]
        Resource = "${aws_s3_bucket.athena_results.arn}/*"
      },
      {
        Effect = "Allow"
        Action = [
          "logs:FilterLogEvents"
        ]
        Resource = "${aws_cloudwatch_log_group.lambda_logs.arn}:*"
      },
      {
        Effect = "Allow"
        Action = [
          "athena:StartQueryExecution",
          "athena:GetQueryExecution",
          "athena:GetQueryResults"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "batch:SubmitJob",
          "batch:DescribeJobs",
          "batch:DescribeJobDefinitions",
          "batch:DescribeJobQueues",
          "batch:ListJobs",
          "batch:TerminateJob",
          "batch:CancelJob"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "glue:GetDatabase",
          "glue:GetDatabases",
          "glue:GetTable",
          "glue:GetTables",
          "glue:GetPartition",
          "glue:GetPartitions"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "lakeformation:GetDataAccess"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_lambda_function" "web_api" {
  s3_bucket        = aws_s3_object.web_api_function_zip.bucket
  s3_key           = aws_s3_object.web_api_function_zip.key
  function_name    = "${var.project_name}-web-api"
  role             = aws_iam_role.web_api_role.arn
  handler          = "web_api_handler.lambda_handler"
  source_code_hash = filebase64sha256("web_api_function.zip")
  runtime          = "python3.11"
  timeout          = 60
  memory_size      = 1024

  environment {
    variables = {
      OUTPUT_BUCKET         = aws_s3_bucket.genome_output.id
      QUEUE_URL             = aws_sqs_queue.genome_queue.url
      DLQ_URL               = aws_sqs_queue.genome_dlq.url
      PROJECT_NAME          = var.project_name
      PIPELINE_LOG_GROUP    = aws_cloudwatch_log_group.lambda_logs.name
      ATHENA_DATABASE       = aws_glue_catalog_database.genome_db.name
      ATHENA_WORKGROUP      = aws_athena_workgroup.genome_workgroup.name
      ATHENA_RESULTS_BUCKET = aws_s3_bucket.athena_results.id
      BATCH_JOB_QUEUE       = aws_batch_job_queue.full_analysis.arn
      BATCH_JOB_DEFINITION  = aws_batch_job_definition.full_analysis.arn
    }
  }

  tags = {
    Name        = "Genome Dashboard API Lambda"
    Environment = "production"
  }
}

resource "aws_cloudwatch_log_group" "web_api_logs" {
  name              = "/aws/lambda/${aws_lambda_function.web_api.function_name}"
  retention_in_days = 30
}

# SQS Queue for processing jobs
resource "aws_sqs_queue" "genome_queue" {
  name                       = "${var.project_name}-queue"
  delay_seconds              = 0
  max_message_size           = 262144
  message_retention_seconds  = 1209600 # 14 days
  visibility_timeout_seconds = var.lambda_timeout + 60

  tags = {
    Name        = "Genome Processing Queue"
    Environment = "production"
  }
}

# SQS Dead Letter Queue
resource "aws_sqs_queue" "genome_dlq" {
  name = "${var.project_name}-dlq"

  tags = {
    Name        = "Genome Processing DLQ"
    Environment = "production"
  }
}

# Configure DLQ for main queue
resource "aws_sqs_queue_redrive_policy" "genome_queue_redrive" {
  queue_url = aws_sqs_queue.genome_queue.id

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.genome_dlq.arn
    maxReceiveCount     = 3
  })
}

# HTTP API for dashboard backend
resource "aws_apigatewayv2_api" "dashboard_api" {
  name          = "${var.project_name}-dashboard-api"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "dashboard_api_lambda" {
  api_id                 = aws_apigatewayv2_api.dashboard_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.web_api.invoke_arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "dashboard_api_default" {
  api_id    = aws_apigatewayv2_api.dashboard_api.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.dashboard_api_lambda.id}"
}

resource "aws_apigatewayv2_stage" "dashboard_api_default" {
  api_id      = aws_apigatewayv2_api.dashboard_api.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "allow_apigw_dashboard_api" {
  statement_id  = "AllowExecutionFromAPIGatewayDashboardApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.web_api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.dashboard_api.execution_arn}/*/*"
}

# Static website hosting for dashboard
resource "aws_s3_bucket" "dashboard_site" {
  bucket = "${var.project_name}-dashboard-${data.aws_caller_identity.current.account_id}"

  tags = {
    Name        = "Genome Dashboard Website"
    Environment = "production"
  }
}

resource "aws_s3_bucket_public_access_block" "dashboard_site" {
  bucket                  = aws_s3_bucket.dashboard_site.id
  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_website_configuration" "dashboard_site" {
  bucket = aws_s3_bucket.dashboard_site.id

  index_document {
    suffix = "index.html"
  }

  error_document {
    key = "index.html"
  }
}

resource "aws_s3_bucket_policy" "dashboard_site_public_read" {
  bucket = aws_s3_bucket.dashboard_site.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "PublicReadGetObject"
        Effect    = "Allow"
        Principal = "*"
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.dashboard_site.arn}/*"
      }
    ]
  })

  depends_on = [aws_s3_bucket_public_access_block.dashboard_site]
}

resource "aws_s3_object" "dashboard_site_files" {
  for_each = fileset("${path.module}/webapp", "**")

  bucket = aws_s3_bucket.dashboard_site.id
  key    = each.value
  source = "${path.module}/webapp/${each.value}"
  etag   = filemd5("${path.module}/webapp/${each.value}")
  content_type = lookup(
    {
      "html" = "text/html"
      "css"  = "text/css"
      "js"   = "application/javascript"
      "json" = "application/json"
      "svg"  = "image/svg+xml"
      "png"  = "image/png"
    },
    reverse(split(".", each.value))[0],
    "application/octet-stream"
  )
}

# ── CloudFront in front of API Gateway ──────────────────────────────────────
# Custom cache policies — forward query strings so ?start=&end= reach the origin

resource "aws_cloudfront_cache_policy" "api_short" {
  name        = "${var.project_name}-api-2min"
  default_ttl = 120
  max_ttl     = 300
  min_ttl     = 0
  parameters_in_cache_key_and_forwarded_to_origin {
    cookies_config      { cookie_behavior       = "none" }
    headers_config      { header_behavior       = "none" }
    query_strings_config { query_string_behavior = "all" }
  }
}

resource "aws_cloudfront_cache_policy" "api_long" {
  name        = "${var.project_name}-api-1hr"
  default_ttl = 3600
  max_ttl     = 86400
  min_ttl     = 0
  parameters_in_cache_key_and_forwarded_to_origin {
    cookies_config      { cookie_behavior       = "none" }
    headers_config      { header_behavior       = "none" }
    query_strings_config { query_string_behavior = "all" }
  }
}

resource "aws_cloudfront_distribution" "api" {
  enabled     = true
  price_class = "PriceClass_100"

  origin {
    domain_name = trimprefix(aws_apigatewayv2_api.dashboard_api.api_endpoint, "https://")
    origin_id   = "apigw"
    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "https-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  # Default — no cache (POST / PUT / DELETE, or anything not matched below)
  default_cache_behavior {
    allowed_methods          = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods           = ["GET", "HEAD"]
    target_origin_id         = "apigw"
    viewer_protocol_policy   = "https-only"
    cache_policy_id          = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" # AWS CachingDisabled
    origin_request_policy_id = "b689b0a8-53d0-40ab-baf2-68738e2966ac" # AllViewerExceptHostHeader
    compress                 = true
  }

  # /api/chromosomes — 2-min cache (changes when a new analysis completes)
  ordered_cache_behavior {
    path_pattern             = "/api/chromosomes"
    allowed_methods          = ["GET", "HEAD", "OPTIONS"]
    cached_methods           = ["GET", "HEAD"]
    target_origin_id         = "apigw"
    viewer_protocol_policy   = "https-only"
    cache_policy_id          = aws_cloudfront_cache_policy.api_short.id
    origin_request_policy_id = "b689b0a8-53d0-40ab-baf2-68738e2966ac"
    compress                 = true
  }

  # /api/chromosomes/*/summary — 5-min cache
  ordered_cache_behavior {
    path_pattern             = "/api/chromosomes/*/summary"
    allowed_methods          = ["GET", "HEAD", "OPTIONS"]
    cached_methods           = ["GET", "HEAD"]
    target_origin_id         = "apigw"
    viewer_protocol_policy   = "https-only"
    cache_policy_id          = aws_cloudfront_cache_policy.api_short.id
    origin_request_policy_id = "b689b0a8-53d0-40ab-baf2-68738e2966ac"
    compress                 = true
  }

  # /api/chromosomes/*/patterns — 1-hour cache (stable after analysis)
  ordered_cache_behavior {
    path_pattern             = "/api/chromosomes/*/patterns"
    allowed_methods          = ["GET", "HEAD", "OPTIONS"]
    cached_methods           = ["GET", "HEAD"]
    target_origin_id         = "apigw"
    viewer_protocol_policy   = "https-only"
    cache_policy_id          = aws_cloudfront_cache_policy.api_long.id
    origin_request_policy_id = "b689b0a8-53d0-40ab-baf2-68738e2966ac"
    compress                 = true
  }

  # /api/chromosomes/*/regions — 1-hour cache
  ordered_cache_behavior {
    path_pattern             = "/api/chromosomes/*/regions"
    allowed_methods          = ["GET", "HEAD", "OPTIONS"]
    cached_methods           = ["GET", "HEAD"]
    target_origin_id         = "apigw"
    viewer_protocol_policy   = "https-only"
    cache_policy_id          = aws_cloudfront_cache_policy.api_long.id
    origin_request_policy_id = "b689b0a8-53d0-40ab-baf2-68738e2966ac"
    compress                 = true
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }
  viewer_certificate {
    cloudfront_default_certificate = true
  }
  tags = { Name = "${var.project_name}-api-cdn" }
}

# ── Lambda keep-warm (EventBridge ping every 5 min — eliminates cold starts) ─

resource "aws_cloudwatch_event_rule" "api_warmup" {
  name                = "${var.project_name}-api-warmup"
  description         = "Keep web API Lambda warm to avoid cold starts"
  schedule_expression = "rate(5 minutes)"
}

resource "aws_cloudwatch_event_target" "api_warmup" {
  rule      = aws_cloudwatch_event_rule.api_warmup.name
  target_id = "warm-web-api"
  arn       = aws_lambda_function.web_api.arn
  input     = jsonencode({ "source" = "warmup" })
}

resource "aws_lambda_permission" "allow_eventbridge_warmup" {
  statement_id  = "AllowEventBridgeWarmup"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.web_api.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.api_warmup.arn
}

# ── config.js now points at CloudFront API URL (HTTPS, cached) ───────────────

resource "aws_s3_object" "dashboard_site_config" {
  bucket       = aws_s3_bucket.dashboard_site.id
  key          = "config.js"
  content      = "window.BIOIT_API_BASE_URL = 'https://${aws_cloudfront_distribution.api.domain_name}';\n"
  content_type = "application/javascript"
  etag         = md5("window.BIOIT_API_BASE_URL = 'https://${aws_cloudfront_distribution.api.domain_name}';\n")
}

# Lambda trigger from SQS
resource "aws_lambda_event_source_mapping" "sqs_trigger" {
  event_source_arn = aws_sqs_queue.genome_queue.arn
  function_name    = aws_lambda_function.genome_processor.arn
  batch_size       = 1
  enabled          = true
}

# Step Functions State Machine for orchestration
resource "aws_iam_role" "sfn_role" {
  name = "${var.project_name}-sfn-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "states.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "sfn_policy" {
  name = "${var.project_name}-sfn-policy"
  role = aws_iam_role.sfn_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "lambda:InvokeFunction"
        ]
        Resource = aws_lambda_function.genome_processor.arn
      },
      {
        Effect = "Allow"
        Action = [
          "sqs:SendMessage"
        ]
        Resource = aws_sqs_queue.genome_queue.arn
      }
    ]
  })
}

resource "aws_sfn_state_machine" "genome_pipeline" {
  name     = "${var.project_name}-state-machine"
  role_arn = aws_iam_role.sfn_role.arn

  definition = jsonencode({
    Comment = "Genome Data Pipeline State Machine"
    StartAt = "ProcessGenome"
    States = {
      ProcessGenome = {
        Type     = "Task"
        Resource = aws_lambda_function.genome_processor.arn
        Retry = [
          {
            ErrorEquals     = ["States.TaskFailed"]
            IntervalSeconds = 60
            MaxAttempts     = 2
            BackoffRate     = 2
          }
        ]
        Catch = [
          {
            ErrorEquals = ["States.ALL"]
            Next        = "ProcessingFailed"
          }
        ]
        Next = "ProcessingSucceeded"
      }
      ProcessingSucceeded = {
        Type = "Succeed"
      }
      ProcessingFailed = {
        Type = "Fail"
      }
    }
  })
}

# EventBridge rule for scheduled processing (optional)
resource "aws_cloudwatch_event_rule" "daily_processing" {
  name                = "${var.project_name}-daily"
  description         = "Trigger genome pipeline daily"
  schedule_expression = "cron(0 2 * * ? *)" # 2 AM UTC daily

  state = "DISABLED" # Set to ENABLED to turn on the schedule
}

resource "aws_cloudwatch_event_target" "sfn_target" {
  rule      = aws_cloudwatch_event_rule.daily_processing.name
  target_id = "GenomePipelineStateMachine"
  arn       = aws_sfn_state_machine.genome_pipeline.arn
  role_arn  = aws_iam_role.eventbridge_role.arn

  input = jsonencode({
    source        = "ncbi"
    accession_id  = "NC_000001.11"
    output_prefix = "scheduled/chr1"
  })
}

resource "aws_iam_role" "eventbridge_role" {
  name = "${var.project_name}-eventbridge-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "events.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "eventbridge_policy" {
  name = "${var.project_name}-eventbridge-policy"
  role = aws_iam_role.eventbridge_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "states:StartExecution"
        ]
        Resource = aws_sfn_state_machine.genome_pipeline.arn
      }
    ]
  })
}

# ============================================
# Data Lake — S3 Partitioned Output
# ============================================
# Partitioned path written by Lambda:
# s3://<output_bucket>/genome_data/source=ncbi/species=homo_sapiens/chr=22/year=2026/month=02/<file>.parquet

# ============================================
# AWS Glue — Data Catalog
# ============================================
resource "aws_glue_catalog_database" "genome_db" {
  name        = "${replace(var.project_name, "-", "_")}_db"
  description = "Genome sequence data lake catalog"
}

resource "aws_iam_role" "glue_crawler_role" {
  name = "${var.project_name}-glue-crawler-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "glue.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "glue_service" {
  role       = aws_iam_role.glue_crawler_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
}

resource "aws_iam_role_policy" "glue_s3_access" {
  name = "${var.project_name}-glue-s3-policy"
  role = aws_iam_role.glue_crawler_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["s3:GetObject", "s3:ListBucket"]
      Resource = [
        aws_s3_bucket.genome_output.arn,
        "${aws_s3_bucket.genome_output.arn}/*"
      ]
    }]
  })
}

# Explicit table definition — schema tracked in git (schemas/genome_sequences.json)
# Crawler below keeps partition metadata up to date, but schema is NOT inferred from it
resource "aws_glue_catalog_table" "genome_sequences" {
  name          = "genome_sequences"
  database_name = aws_glue_catalog_database.genome_db.name
  description   = "FASTA/FASTQ genome sequences parsed by C++ parser"

  table_type = "EXTERNAL_TABLE"

  parameters = {
    "classification"   = "parquet"
    "compressionType"  = "snappy"
    "typeOfData"       = "file"
    "parquet.compress" = "SNAPPY"
    "EXTERNAL"         = "TRUE"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.genome_output.id}/genome_data/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      name                  = "parquet-serde"
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
      parameters = {
        "serialization.format" = "1"
      }
    }

    # Column definitions — mirrors schemas/genome_sequences.json
    columns {
      name    = "id"
      type    = "string"
      comment = "Sequence identifier from FASTA/FASTQ header"
    }
    columns {
      name    = "description"
      type    = "string"
      comment = "Full header description text"
    }
    columns {
      name    = "sequence"
      type    = "string"
      comment = "DNA/RNA sequence string"
    }
    columns {
      name    = "length"
      type    = "bigint"
      comment = "Sequence length in base pairs"
    }
    columns {
      name    = "gc_content"
      type    = "double"
      comment = "GC percentage (0.0 - 100.0)"
    }
    columns {
      name    = "base_composition"
      type    = "struct<A:int,T:int,G:int,C:int,N:int>"
      comment = "Per-base nucleotide counts"
    }
    columns {
      name    = "quality"
      type    = "string"
      comment = "Phred quality scores (FASTQ only, null for FASTA)"
    }
  }

  # Partition keys — must match Lambda S3 path structure
  partition_keys {
    name = "source"
    type = "string"
  }
  partition_keys {
    name = "species"
    type = "string"
  }
  partition_keys {
    name = "chr"
    type = "string"
  }
  partition_keys {
    name = "year"
    type = "string"
  }
  partition_keys {
    name = "month"
    type = "string"
  }
}

resource "aws_glue_catalog_table" "sequence_patterns" {
  name          = "sequence_patterns"
  database_name = aws_glue_catalog_database.genome_db.name
  description   = "Motif, repeat, and candidate ORF hits derived from chromosome sequences"

  table_type = "EXTERNAL_TABLE"

  parameters = {
    "classification"   = "parquet"
    "compressionType"  = "snappy"
    "typeOfData"       = "file"
    "parquet.compress" = "SNAPPY"
    "EXTERNAL"         = "TRUE"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.genome_output.id}/pattern_data/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      name                  = "parquet-serde"
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
      parameters = {
        "serialization.format" = "1"
      }
    }

    columns {
      name    = "sequence_id"
      type    = "string"
      comment = "Parent FASTA/FASTQ record identifier"
    }
    columns {
      name    = "pattern_type"
      type    = "string"
      comment = "Pattern family: motif, repeat, or orf"
    }
    columns {
      name    = "pattern_name"
      type    = "string"
      comment = "Specific pattern label"
    }
    columns {
      name    = "start"
      type    = "bigint"
      comment = "Zero-based inclusive start coordinate"
    }
    columns {
      name    = "end"
      type    = "bigint"
      comment = "Zero-based exclusive end coordinate"
    }
    columns {
      name    = "length"
      type    = "bigint"
      comment = "Hit length in base pairs"
    }
    columns {
      name    = "strand"
      type    = "string"
      comment = "Strand orientation used for the hit"
    }
    columns {
      name    = "score"
      type    = "double"
      comment = "Simple numeric score for ranking hits"
    }
    columns {
      name    = "matched_sequence"
      type    = "string"
      comment = "Matched nucleotide sequence snippet"
    }
  }

  partition_keys {
    name = "source"
    type = "string"
  }
  partition_keys {
    name = "species"
    type = "string"
  }
  partition_keys {
    name = "chr"
    type = "string"
  }
  partition_keys {
    name = "year"
    type = "string"
  }
  partition_keys {
    name = "month"
    type = "string"
  }
}

resource "aws_glue_catalog_table" "sequence_regions" {
  name          = "sequence_regions"
  database_name = aws_glue_catalog_database.genome_db.name
  description   = "Sliding-window chromosome region summaries for visualization and hotspot analysis"

  table_type = "EXTERNAL_TABLE"

  parameters = {
    "classification"   = "parquet"
    "compressionType"  = "snappy"
    "typeOfData"       = "file"
    "parquet.compress" = "SNAPPY"
    "EXTERNAL"         = "TRUE"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.genome_output.id}/region_data/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      name                  = "parquet-serde"
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
      parameters = {
        "serialization.format" = "1"
      }
    }

    columns {
      name    = "sequence_id"
      type    = "string"
      comment = "Parent FASTA/FASTQ record identifier"
    }
    columns {
      name    = "region_type"
      type    = "string"
      comment = "Region summary type"
    }
    columns {
      name    = "window_start"
      type    = "bigint"
      comment = "Zero-based inclusive window start"
    }
    columns {
      name    = "window_end"
      type    = "bigint"
      comment = "Zero-based exclusive window end"
    }
    columns {
      name    = "length"
      type    = "bigint"
      comment = "Window length in base pairs"
    }
    columns {
      name    = "gc_content"
      type    = "double"
      comment = "Window GC percentage"
    }
    columns {
      name    = "n_content"
      type    = "double"
      comment = "Window ambiguous-base percentage"
    }
    columns {
      name    = "gc_skew"
      type    = "double"
      comment = "Window GC skew"
    }
    columns {
      name    = "motif_hits"
      type    = "bigint"
      comment = "Count of motif hits overlapping the window"
    }
    columns {
      name    = "orf_count"
      type    = "bigint"
      comment = "Count of candidate ORFs overlapping the window"
    }
    columns {
      name    = "repeat_bases"
      type    = "bigint"
      comment = "Total repeat bases overlapping the window"
    }
    columns {
      name    = "max_homopolymer_run"
      type    = "bigint"
      comment = "Longest homopolymer run inside the window"
    }
  }

  partition_keys {
    name = "source"
    type = "string"
  }
  partition_keys {
    name = "species"
    type = "string"
  }
  partition_keys {
    name = "chr"
    type = "string"
  }
  partition_keys {
    name = "year"
    type = "string"
  }
  partition_keys {
    name = "month"
    type = "string"
  }
}

resource "aws_glue_catalog_table" "gene_annotations" {
  name          = "gene_annotations"
  database_name = aws_glue_catalog_database.genome_db.name
  description   = "Known gene annotations fetched from Ensembl for chromosome overlap and tooltip lookups"

  table_type = "EXTERNAL_TABLE"

  parameters = {
    "classification"   = "parquet"
    "compressionType"  = "snappy"
    "typeOfData"       = "file"
    "parquet.compress" = "SNAPPY"
    "EXTERNAL"         = "TRUE"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.genome_output.id}/gene_annotation_data/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      name                  = "parquet-serde"
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
      parameters = {
        "serialization.format" = "1"
      }
    }

    columns {
      name    = "gene_id"
      type    = "string"
      comment = "Stable gene identifier from Ensembl"
    }
    columns {
      name    = "gene_symbol"
      type    = "string"
      comment = "Primary display symbol for the gene"
    }
    columns {
      name    = "gene_name"
      type    = "string"
      comment = "Long-form gene or protein name"
    }
    columns {
      name    = "feature_type"
      type    = "string"
      comment = "Annotation feature type, typically gene"
    }
    columns {
      name    = "biotype"
      type    = "string"
      comment = "Ensembl biotype such as protein_coding or lncRNA"
    }
    columns {
      name    = "start"
      type    = "bigint"
      comment = "One-based inclusive gene start coordinate"
    }
    columns {
      name    = "end"
      type    = "bigint"
      comment = "One-based inclusive gene end coordinate"
    }
    columns {
      name    = "length"
      type    = "bigint"
      comment = "Gene span in base pairs"
    }
    columns {
      name    = "strand"
      type    = "string"
      comment = "Gene strand orientation"
    }
    columns {
      name    = "assembly_name"
      type    = "string"
      comment = "Assembly name returned by Ensembl"
    }
    columns {
      name    = "source_name"
      type    = "string"
      comment = "Annotation source used to fetch the record"
    }
    columns {
      name    = "version"
      type    = "string"
      comment = "Annotation version if supplied by the source"
    }
  }

  partition_keys {
    name = "source"
    type = "string"
  }
  partition_keys {
    name = "species"
    type = "string"
  }
  partition_keys {
    name = "chr"
    type = "string"
  }
  partition_keys {
    name = "year"
    type = "string"
  }
  partition_keys {
    name = "month"
    type = "string"
  }
}

resource "aws_glue_crawler" "genome_crawler" {
  name          = "${var.project_name}-crawler"
  role          = aws_iam_role.glue_crawler_role.arn
  database_name = aws_glue_catalog_database.genome_db.name
  description   = "Crawls partitioned Parquet genome, pattern, and region data in S3"

  s3_target {
    path = "s3://${aws_s3_bucket.genome_output.id}/genome_data/"
  }

  s3_target {
    path = "s3://${aws_s3_bucket.genome_output.id}/pattern_data/"
  }

  s3_target {
    path = "s3://${aws_s3_bucket.genome_output.id}/region_data/"
  }

  s3_target {
    path = "s3://${aws_s3_bucket.genome_output.id}/gene_annotation_data/"
  }

  schema_change_policy {
    update_behavior = "UPDATE_IN_DATABASE"
    delete_behavior = "LOG"
  }

  # Run crawler daily at 3 AM UTC (after pipeline may have added new data)
  schedule = "cron(0 3 * * ? *)"

  configuration = jsonencode({
    Version = 1.0
    CrawlerOutput = {
      Partitions = { AddOrUpdateBehavior = "InheritFromTable" }
    }
  })
}

# ============================================
# Amazon Athena — Query Engine
# ============================================
resource "aws_s3_bucket" "athena_results" {
  bucket = "${var.project_name}-athena-results-${data.aws_caller_identity.current.account_id}"

  tags = {
    Name        = "Athena Query Results"
    Environment = "production"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "athena_results" {
  bucket = aws_s3_bucket.athena_results.id

  rule {
    id     = "expire-query-results"
    status = "Enabled"

    filter {}

    expiration {
      days = 30
    }
  }
}

resource "aws_athena_workgroup" "genome_workgroup" {
  name        = "${var.project_name}-workgroup"
  description = "Athena workgroup for genome data lake queries"

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true

    result_configuration {
      output_location = "s3://${aws_s3_bucket.athena_results.id}/query-results/"

      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }

    bytes_scanned_cutoff_per_query = 10737418240 # 10 GB safety limit per query
  }
}

# Athena named query — example: find high GC content sequences
resource "aws_athena_named_query" "high_gc" {
  name        = "high-gc-sequences"
  workgroup   = aws_athena_workgroup.genome_workgroup.id
  database    = aws_glue_catalog_database.genome_db.name
  description = "Find sequences with GC content above 60%"

  query = <<-SQL
    SELECT id, description, chr, source, species, gc_content, length
    FROM genome_sequences
    WHERE gc_content > 60.0
    ORDER BY gc_content DESC
    LIMIT 100;
  SQL
}

resource "aws_athena_named_query" "summary_by_chr" {
  name        = "summary-by-chromosome"
  workgroup   = aws_athena_workgroup.genome_workgroup.id
  database    = aws_glue_catalog_database.genome_db.name
  description = "Average GC content and total sequences per chromosome"

  query = <<-SQL
    SELECT
      chr,
      source,
      COUNT(*)          AS sequence_count,
      AVG(gc_content)   AS avg_gc_content,
      SUM(length)       AS total_bases
    FROM genome_sequences
    GROUP BY chr, source
    ORDER BY chr;
  SQL
}

resource "aws_athena_named_query" "top_patterns" {
  name        = "top-patterns-by-chromosome"
  workgroup   = aws_athena_workgroup.genome_workgroup.id
  database    = aws_glue_catalog_database.genome_db.name
  description = "Most frequent motifs, repeats, and candidate ORFs per chromosome partition"

  query = <<-SQL
    SELECT
      chr,
      pattern_type,
      pattern_name,
      COUNT(*) AS hit_count,
      AVG(length) AS avg_hit_length
    FROM sequence_patterns
    WHERE year = '2026'
      AND month = '05'
    GROUP BY chr, pattern_type, pattern_name
    ORDER BY hit_count DESC
    LIMIT 100;
  SQL
}

resource "aws_athena_named_query" "orf_rich_regions" {
  name        = "orf-rich-regions"
  workgroup   = aws_athena_workgroup.genome_workgroup.id
  database    = aws_glue_catalog_database.genome_db.name
  description = "Windows with the largest number of candidate ORFs"

  query = <<-SQL
    SELECT
      chr,
      window_start,
      window_end,
      orf_count,
      gc_content,
      motif_hits
    FROM sequence_regions
    WHERE year = '2026'
      AND month = '05'
      AND orf_count > 0
    ORDER BY orf_count DESC, gc_content DESC
    LIMIT 100;
  SQL
}

resource "aws_athena_named_query" "gc_hotspots" {
  name        = "gc-hotspot-windows"
  workgroup   = aws_athena_workgroup.genome_workgroup.id
  database    = aws_glue_catalog_database.genome_db.name
  description = "Highest-GC windows for hotspot exploration"

  query = <<-SQL
    SELECT
      chr,
      window_start,
      window_end,
      gc_content,
      gc_skew,
      motif_hits,
      repeat_bases
    FROM sequence_regions
    WHERE year = '2026'
      AND month = '05'
    ORDER BY gc_content DESC, motif_hits DESC
    LIMIT 100;
  SQL
}

resource "aws_athena_named_query" "repeat_dense_windows" {
  name        = "repeat-dense-windows"
  workgroup   = aws_athena_workgroup.genome_workgroup.id
  database    = aws_glue_catalog_database.genome_db.name
  description = "Windows with the greatest repeat density and longest homopolymers"

  query = <<-SQL
    SELECT
      chr,
      window_start,
      window_end,
      repeat_bases,
      max_homopolymer_run,
      n_content,
      gc_content
    FROM sequence_regions
    WHERE year = '2026'
      AND month = '05'
      AND repeat_bases > 0
    ORDER BY repeat_bases DESC, max_homopolymer_run DESC
    LIMIT 100;
  SQL
}

resource "aws_athena_named_query" "motif_dense_gc_orf_windows" {
  name        = "motif-dense-gc-orf-windows"
  workgroup   = aws_athena_workgroup.genome_workgroup.id
  database    = aws_glue_catalog_database.genome_db.name
  description = "Join motif hits to region windows to find GC-rich or ORF-rich hotspots"

  query = <<-SQL
    WITH motif_windows AS (
      SELECT
        r.chr,
        r.source,
        r.species,
        r.year,
        r.month,
        r.window_start,
        r.window_end,
        r.gc_content,
        r.gc_skew,
        r.orf_count,
        r.repeat_bases,
        r.max_homopolymer_run,
        COUNT(p.pattern_name) AS overlapping_pattern_hits,
        COUNT(DISTINCT p.pattern_name) AS distinct_pattern_names,
        ARRAY_JOIN(
          ARRAY_SLICE(
            ARRAY_SORT(ARRAY_DISTINCT(ARRAY_AGG(p.pattern_name))),
            1,
            10
          ),
          ', '
        ) AS example_patterns
      FROM sequence_regions r
      LEFT JOIN sequence_patterns p
        ON r.source = p.source
       AND r.species = p.species
       AND r.chr = p.chr
       AND r.year = p.year
       AND r.month = p.month
       AND r.sequence_id = p.sequence_id
       AND p.start < r.window_end
       AND p.end > r.window_start
      WHERE r.year = '2026'
        AND r.month = '05'
      GROUP BY
        r.chr,
        r.source,
        r.species,
        r.year,
        r.month,
        r.window_start,
        r.window_end,
        r.gc_content,
        r.gc_skew,
        r.orf_count,
        r.repeat_bases,
        r.max_homopolymer_run
    )
    SELECT
      chr,
      source,
      species,
      window_start,
      window_end,
      gc_content,
      gc_skew,
      orf_count,
      overlapping_pattern_hits,
      distinct_pattern_names,
      repeat_bases,
      max_homopolymer_run,
      example_patterns
    FROM motif_windows
    WHERE overlapping_pattern_hits > 0
      AND (gc_content >= 50.0 OR orf_count > 0)
    ORDER BY
      overlapping_pattern_hits DESC,
      orf_count DESC,
      gc_content DESC
    LIMIT 100;
  SQL
}

# ============================================
# AWS Lake Formation — Governance
# ============================================
resource "aws_lakeformation_data_lake_settings" "settings" {
  admins = [data.aws_caller_identity.current.arn]
}

resource "aws_lakeformation_resource" "genome_output_bucket" {
  arn      = aws_s3_bucket.genome_output.arn
  role_arn = aws_iam_role.glue_crawler_role.arn
}

resource "aws_lakeformation_permissions" "glue_crawler_db" {
  principal   = aws_iam_role.glue_crawler_role.arn
  permissions = ["CREATE_TABLE", "ALTER", "DROP"]

  database {
    name = aws_glue_catalog_database.genome_db.name
  }
}

resource "aws_lakeformation_permissions" "lambda_s3_data" {
  principal   = aws_iam_role.lambda_role.arn
  permissions = ["DATA_LOCATION_ACCESS"]

  data_location {
    arn = aws_s3_bucket.genome_output.arn
  }
}

# ============================================
# Data source for current AWS account
# ============================================
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

data "aws_caller_identity" "current" {}

# Outputs
output "lambda_function_name" {
  value       = aws_lambda_function.genome_processor.function_name
  description = "Name of the Lambda function"
}

output "output_bucket_name" {
  value       = aws_s3_bucket.genome_output.id
  description = "S3 bucket for pipeline outputs"
}

output "sqs_queue_url" {
  value       = aws_sqs_queue.genome_queue.url
  description = "URL of the SQS queue"
}

output "state_machine_arn" {
  value       = aws_sfn_state_machine.genome_pipeline.arn
  description = "ARN of the Step Functions state machine"
}

output "glue_database_name" {
  value       = aws_glue_catalog_database.genome_db.name
  description = "Glue catalog database name"
}

output "athena_workgroup" {
  value       = aws_athena_workgroup.genome_workgroup.name
  description = "Athena workgroup name"
}

output "athena_results_bucket" {
  value       = aws_s3_bucket.athena_results.id
  description = "S3 bucket for Athena query results"
}

output "dashboard_api_endpoint" {
  value       = aws_apigatewayv2_api.dashboard_api.api_endpoint
  description = "HTTP API endpoint for the BioIT dashboard backend"
}

# CloudFront distribution — HTTPS termination in front of the S3 dashboard bucket
resource "aws_cloudfront_distribution" "dashboard" {
  enabled             = true
  default_root_object = "index.html"
  price_class         = "PriceClass_100" # North America + Europe only (cheapest)

  origin {
    domain_name = aws_s3_bucket_website_configuration.dashboard_site.website_endpoint
    origin_id   = "dashboard-s3-website"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only" # S3 website endpoints are HTTP
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "dashboard-s3-website"
    viewer_protocol_policy = "redirect-to-https"

    forwarded_values {
      query_string = false
      cookies { forward = "none" }
    }

    min_ttl     = 0
    default_ttl = 0   # Honour no-cache headers from S3 objects
    max_ttl     = 31536000
    compress    = true
  }

  # Return index.html for unknown paths (SPA-style)
  custom_error_response {
    error_code         = 403
    response_code      = 200
    response_page_path = "/index.html"
  }
  custom_error_response {
    error_code         = 404
    response_code      = 200
    response_page_path = "/index.html"
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    cloudfront_default_certificate = true # Free *.cloudfront.net HTTPS cert
  }

  tags = { Name = "${var.project_name}-dashboard-cdn" }
}

output "dashboard_website_url" {
  value       = aws_s3_bucket_website_configuration.dashboard_site.website_endpoint
  description = "S3 static website endpoint (HTTP)"
}

output "dashboard_https_url" {
  value       = "https://${aws_cloudfront_distribution.dashboard.domain_name}"
  description = "CloudFront HTTPS URL for the BioIT dashboard (share this one)"
}

output "api_cdn_url" {
  value       = "https://${aws_cloudfront_distribution.api.domain_name}"
  description = "CloudFront HTTPS URL for the API (cached, no cold starts)"
}

output "batch_job_queue_arn" {
  value       = aws_batch_job_queue.full_analysis.arn
  description = "ARN of the AWS Batch job queue for large-chromosome full analysis"
}

output "batch_job_definition_arn" {
  value       = aws_batch_job_definition.full_analysis.arn
  description = "ARN of the AWS Batch job definition for large-chromosome full analysis"
}

output "batch_runner_repository_url" {
  value       = aws_ecr_repository.batch_runner.repository_url
  description = "ECR repository URL for the Batch full-analysis container image"
}
