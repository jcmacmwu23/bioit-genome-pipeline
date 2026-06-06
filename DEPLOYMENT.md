# Deployment Guide — BioAPI Genome Pipeline

**Last Updated:** 2026-02-10

---

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Pre-Deployment Checklist](#pre-deployment-checklist)
3. [Option A — Manual Deployment (Terraform)](#option-a--manual-deployment)
4. [Option B — CI/CD Deployment (GitHub Actions)](#option-b--cicd-deployment)
5. [Post-Deployment Verification](#post-deployment-verification)
6. [AWS Resources Created](#aws-resources-created)
7. [Configuration Reference](#configuration-reference)
8. [Teardown](#teardown)
9. [Troubleshooting](#troubleshooting)

---

## Prerequisites

| Tool | Version | Install |
|---|---|---|
| AWS CLI | v2+ | `brew install awscli` |
| Terraform | 1.5+ | `brew install terraform` |
| Docker | any | [docker.com](https://www.docker.com/get-started/) |
| Python | 3.11 | `brew install python@3.11` |

### AWS IAM Permissions Required

The deploying user/role needs these permissions:

```
lambda:*
s3:*
sqs:*
states:*
iam:CreateRole, iam:AttachRolePolicy, iam:PassRole
logs:*
events:*
```

> Easiest approach: attach `AdministratorAccess` for initial deployment, then scope down after.

---

## Pre-Deployment Checklist

- [ ] Docker is running (`docker ps`)
- [ ] AWS CLI configured (`aws sts get-caller-identity`)
- [ ] Terraform installed (`terraform version`)
- [ ] Set your NCBI email in [main.tf](main.tf) line 182:
  ```hcl
  NCBI_EMAIL = "your_real_email@example.com"
  ```
- [ ] Confirm target AWS region (default: `us-east-1`)

---

## Option A — Manual Deployment

### Step 1 — Configure AWS credentials

Run in your terminal (credentials never go in code):

```bash
aws configure
# AWS Access Key ID: <your key>
# AWS Secret Access Key: <your secret>
# Default region: us-east-1
# Default output format: json
```

Verify:
```bash
aws sts get-caller-identity
```

### Step 2 — Build all artifacts

Requires Docker to be running (compiles C++ inside Amazon Linux 2):

```bash
cd /path/to/bioit-genome-pipeline
bash build.sh
```

This produces:
```
dist/
├── lambda_layer.zip       # C++ binary + Python deps (~50-100MB)
├── lambda_function.zip    # lambda_handler.py (~5KB)
└── terraform/             # Terraform configs + ZIPs
```

### Step 3 — Deploy infrastructure

```bash
cd dist/terraform
terraform init
terraform plan -var="project_name=genome-pipeline"
terraform apply -var="project_name=genome-pipeline"
```

Type `yes` when prompted. Terraform will output:

```
lambda_function_name = "genome-pipeline-processor"
output_bucket_name   = "genome-pipeline-output-<account_id>"
sqs_queue_url        = "https://sqs.us-east-1.amazonaws.com/..."
state_machine_arn    = "arn:aws:states:us-east-1:..."
```

Save these values — you'll need them for testing.

---

## Option B — CI/CD Deployment

### Step 1 — Push code to GitHub

```bash
git init
git add .
git commit -m "Initial BioAPI pipeline"
git remote add origin https://github.com/<your-org>/<your-repo>.git
git push -u origin main
```

### Step 2 — Add GitHub Secrets

In your repo: **Settings → Secrets and variables → Actions → New repository secret**

| Secret Name | Value |
|---|---|
| `AWS_ACCESS_KEY_ID` | Your AWS access key |
| `AWS_SECRET_ACCESS_KEY` | Your AWS secret key |

### Step 3 — Trigger deployment

- Push to `develop` branch → deploys to **development** (`genome-pipeline-dev`)
- Push to `main` branch → deploys to **production** (`genome-pipeline-prod`)

The [ci-cd.yml](ci-cd.yml) workflow runs automatically:
1. Runs tests + linting
2. Builds Lambda layer + function ZIPs
3. Runs `terraform apply` with the appropriate `project_name`

---

## Post-Deployment Verification

### 1 — Verify Lambda exists

```bash
aws lambda get-function --function-name genome-pipeline-processor
```

### 2 — Invoke with a small test (PhiX174 phage, ~5KB)

```bash
aws lambda invoke \
  --function-name genome-pipeline-processor \
  --payload '{"source":"ncbi","accession_id":"NC_001422.1","output_prefix":"test/phix174"}' \
  --cli-binary-format raw-in-base64-out \
  response.json

cat response.json
```

Expected response:
```json
{
  "statusCode": 200,
  "body": "{\"message\": \"Pipeline completed successfully\", \"output_location\": \"s3://genome-pipeline-output-<account>/test/phix174.parquet\"}"
}
```

### 3 — Verify Parquet file in S3

```bash
aws s3 ls s3://genome-pipeline-output-<account_id>/test/
```

### 4 — Check CloudWatch logs

```bash
aws logs tail /aws/lambda/genome-pipeline-processor --follow
```

### 5 — Run a human chromosome (Chr 22, smallest autosome)

```bash
aws lambda invoke \
  --function-name genome-pipeline-processor \
  --payload '{"source":"ncbi","accession_id":"NC_000022.11","output_prefix":"human/chr22"}' \
  --cli-binary-format raw-in-base64-out \
  chr22_response.json
```

> Note: Chr 22 is ~51MB and takes ~3-8 minutes. Lambda timeout is set to 15 min.

---

## AWS Resources Created

| Resource | Name Pattern | Purpose |
|---|---|---|
| Lambda Function | `<project_name>-processor` | Main pipeline runner |
| Lambda Layer | `<project_name>-parser-layer` | C++ binary + Python deps |
| S3 Bucket | `<project_name>-output-<account_id>` | Parquet output files (versioned) |
| S3 Bucket | `<project_name>-temp-<account_id>` | Temp files (auto-deleted after 7 days) |
| SQS Queue | `<project_name>-queue` | Batch job queue (14-day retention) |
| SQS DLQ | `<project_name>-dlq` | Failed jobs after 3 retries |
| Step Functions | `<project_name>-state-machine` | Workflow orchestration with retries |
| CloudWatch Log Group | `/aws/lambda/<function_name>` | Lambda logs (30-day retention) |
| EventBridge Rule | `<project_name>-daily` | Scheduled trigger (disabled by default) |
| IAM Role | `<project_name>-lambda-role` | Lambda execution role (least privilege) |
| IAM Role | `<project_name>-sfn-role` | Step Functions execution role |
| IAM Role | `<project_name>-eventbridge-role` | EventBridge execution role |

---

## Configuration Reference

### Terraform Variables ([main.tf](main.tf))

| Variable | Default | Description |
|---|---|---|
| `aws_region` | `us-east-1` | AWS region to deploy into |
| `project_name` | `genome-pipeline` | Prefix for all resource names |
| `lambda_timeout` | `900` (15 min) | Lambda max execution time in seconds |
| `lambda_memory` | `3008` MB | Lambda memory (also controls vCPU allocation) |

Override at deploy time:
```bash
terraform apply \
  -var="project_name=genome-pipeline-prod" \
  -var="aws_region=us-west-2" \
  -var="lambda_memory=2048"
```

### Lambda Environment Variables

| Variable | Set By | Description |
|---|---|---|
| `OUTPUT_BUCKET` | Terraform (auto) | S3 bucket name for output Parquet files |
| `TEMP_BUCKET` | Terraform (auto) | S3 bucket name for temp files |
| `NCBI_EMAIL` | [main.tf:182](main.tf#L182) | Your email for NCBI Entrez API rate limiting |

---

## Teardown

To destroy all AWS resources:

```bash
cd dist/terraform
terraform destroy -var="project_name=genome-pipeline"
```

> This will delete S3 buckets only if they are empty. Empty them first if needed:
> ```bash
> aws s3 rm s3://genome-pipeline-output-<account_id> --recursive
> aws s3 rm s3://genome-pipeline-temp-<account_id> --recursive
> ```

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| `fasta_parser: not found` in Lambda | macOS binary in layer | Run `build.sh` with Docker (Amazon Linux 2 compilation) |
| `Task timed out after 900.00 seconds` | Large chromosome file | Normal for chr1-5. Use SQS queue for batch jobs |
| `No space left on device` | Ephemeral storage exceeded | Lambda has 10GB `/tmp` — only affects files >10GB |
| `AccessDenied` on S3 | IAM policy missing | Check Lambda role has `s3:PutObject` on the output bucket |
| `errorMessage: Failed to download genome data` | NCBI rate limit | Set a real `NCBI_EMAIL` in [main.tf](main.tf) |
| `lambda_layer.zip not found` during `terraform apply` | Build not run | Run `bash build.sh` before Terraform |
| Terraform state conflict | Two deploys at same time | Use S3 remote backend for team deployments |

### Enable Terraform Remote State (recommended for teams)

Add to [main.tf](main.tf) inside the `terraform {}` block:

```hcl
backend "s3" {
  bucket = "your-terraform-state-bucket"
  key    = "genome-pipeline/terraform.tfstate"
  region = "us-east-1"
}
```

---

## Estimated Costs

| Component | Cost |
|---|---|
| Lambda (per chromosome) | ~$0.10 – $0.50 |
| Full human genome (24 chr) | ~$5 – $10 one-time |
| S3 storage | ~$0.023/GB/month |
| SQS | ~$0.40 per million messages |
| CloudWatch Logs | ~$0.50/GB ingested |

> Estimated monthly cost for light usage: **< $5/month**
