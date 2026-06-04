# AWS Batch on Fargate Process

This document explains how the BioIT pipeline uses AWS Batch on Fargate for large-chromosome full analysis, why it exists alongside Lambda, and how to build, deploy, and verify the path end to end.

## Why Batch Exists

The project now has two analysis paths:

- `sequence_only` on Lambda for bulk ingestion of all human chromosomes
- `full` analysis on Lambda for smaller chromosomes that fit the current Lambda memory/runtime ceiling
- `full` analysis on AWS Batch on Fargate for larger chromosomes that are too large for Lambda

This split exists because large chromosomes such as `1`, `2`, `3`, and `X` exceeded the current Lambda full-analysis memory limit during the C++ parser + Parquet conversion flow.

## High-Level Flow

1. A chromosome is downloaded from NCBI and landed into sequence-level S3/Parquet outputs.
2. The dashboard or API checks whether full analysis should run on Lambda or Batch.
3. If the chromosome length is above the Lambda threshold, the API submits an AWS Batch job instead of enqueueing the Lambda path.
4. The Batch container runs the same orchestration logic as `lambda_handler.py`.
5. Results are stored in the same S3 prefixes and remain queryable through Glue/Athena and the dashboard.

The key design goal is reuse: Batch is not a separate analytics pipeline. It is a heavier compute backend for the same `full` analysis contract.

## Routing Rule

The API decides the backend in [web_api_handler.py](/Users/turtlemasterflex/Documents/Misc/ Job Search/ BioIT Project/BioAPI pipeline/bioproject files/web_api_handler.py).

- If `patterns_ready` and `regions_ready` already exist, full analysis is treated as complete.
- If `sequence_ready` is false, full analysis is blocked.
- If `sequence_length` is above `FULL_ANALYSIS_MAX_BASES`, the API returns `full_analysis_status = "batch_required"` and `full_analysis_backend = "batch"` when Batch is configured.
- Otherwise, the chromosome remains eligible for Lambda full analysis.

The dashboard reflects that status and changes the button label to `Run Full Analysis on Batch` for oversized chromosomes.

## Current Runtime Thresholds

The important defaults currently in Terraform are:

- Lambda memory: `3008 MB`
- Batch vCPU: `8`
- Batch memory: `32768 MiB`
- Batch max vCPUs in compute environment: `256`
- Batch image tag: `latest`

These values live in [main.tf](/Users/turtlemasterflex/Documents/Misc/ Job Search/ BioIT Project/BioAPI pipeline/bioproject files/main.tf).

## AWS Resources Involved

The Batch-on-Fargate path uses these resources:

- ECR repository for the Batch runner image
- AWS Batch compute environment on Fargate
- AWS Batch job queue
- AWS Batch job definition
- IAM role for the Batch service
- IAM execution role for ECS task startup
- IAM job role for the analysis container
- CloudWatch log group for Batch jobs
- Existing S3 temp and output buckets

The Terraform resources are defined in [main.tf](/Users/turtlemasterflex/Documents/Misc/ Job Search/ BioIT Project/BioAPI pipeline/bioproject files/main.tf).

## Container Build

The Batch container is defined in [Dockerfile.batch](/Users/turtlemasterflex/Documents/Misc/ Job Search/ BioIT Project/BioAPI pipeline/bioproject files/Dockerfile.batch).

It currently does the following:

1. Uses `public.ecr.aws/docker/library/python:3.11-slim`
2. Installs `ca-certificates`, `curl`, and `g++`
3. Copies:
   - `requirements.txt`
   - `lambda_handler.py`
   - `batch_entrypoint.py`
   - `fasta_parser.cpp`
4. Downloads `nlohmann/json.hpp`
5. Compiles the Linux `fasta_parser` binary into `/opt/bin/fasta_parser`
6. Installs Python dependencies plus `boto3`
7. Starts with:

```bash
python /app/batch_entrypoint.py
```

## Batch Entrypoint Contract

The Batch job entrypoint is [batch_entrypoint.py](/Users/turtlemasterflex/Documents/Misc/ Job Search/ BioIT Project/BioAPI pipeline/bioproject files/batch_entrypoint.py).

Important behavior:

- It prefers `JOB_PAYLOAD` from the environment.
- It falls back to a CLI JSON argument if needed.
- It calls `lambda_handler(event, None)` directly.
- It exits nonzero when the underlying handler reports an error.

This means Batch and Lambda share the same orchestration logic and output layout.

## What Gets Submitted to Batch

The API submits the same job payload shape used elsewhere in the project, for example:

```json
{
  "source": "ncbi",
  "accession_id": "NC_000001.11",
  "chromosome": "1",
  "species": "homo_sapiens",
  "output_prefix": "human_genome/chr1",
  "analysis_mode": "full"
}
```

The payload is injected into the Batch task as:

```text
JOB_PAYLOAD=<json>
```

The Batch submission logic lives in [web_api_handler.py](/Users/turtlemasterflex/Documents/Misc/ Job Search/ BioIT Project/BioAPI pipeline/bioproject files/web_api_handler.py).

## Dashboard Behavior

The dashboard behavior for large chromosomes is implemented in [webapp/app.js](/Users/turtlemasterflex/Documents/Misc/ Job Search/ BioIT Project/BioAPI pipeline/bioproject files/webapp/app.js).

Current UX:

- If a chromosome is sequence-ready but too large for Lambda, the dashboard says:
  `Sequence landed. This chromosome will use AWS Batch on Fargate for full analysis.`
- The detail panel marks full-analysis status as `Batch`
- The action button changes to `Run Full Analysis on Batch`
- The button stays disabled if:
  - sequence data has not landed
  - full outputs already exist
  - the chromosome is otherwise blocked

## Deployment Process

There are two separate deployment concerns:

1. Terraform infrastructure and web/API config
2. The Batch container image in ECR

### 1. Terraform Infrastructure

When `main.tf` or the web API changes:

1. Build the web API zip:

```bash
make build-web-api
```

2. Make sure `dist/web_api_function.zip` is copied into `dist/terraform/web_api_function.zip`

3. Apply Terraform from the staged bundle:

```bash
cd "dist/terraform"
terraform init
terraform apply -var='ncbi_email=meiwu123@gmail.com'
```

Important note:

- The staged `dist/terraform/web_api_function.zip` can become stale.
- If the live API does not reflect recent code changes, compare the staged zip hash with the freshly built zip before applying.

### 2. Batch Container Image

When `Dockerfile.batch`, `batch_entrypoint.py`, `lambda_handler.py`, `fasta_parser.cpp`, or runtime dependencies change, the container image must be rebuilt and pushed to ECR.

There are two ways to do this:

- local Docker build + push
- remote CodeBuild build + push

Local Docker is simpler when available. Remote CodeBuild is a good fallback when the machine cannot run a compatible Linux container toolchain.

## Recommended Image Rebuild Sequence

1. Zip the container build context or otherwise make the source available to the build system.
2. Build the image from `Dockerfile.batch`
3. Tag it to the ECR repository
4. Push it to ECR using the configured `batch_image_tag`
5. Submit a test chromosome through the Batch route
6. Verify CloudWatch logs and S3 outputs

Because the Batch job definition points at `:<batch_image_tag>`, pushing a new image to `latest` updates future jobs without requiring a new job definition revision unless the Terraform config itself changed.

## Verification Checklist

After any Batch deployment change:

1. Confirm the API reports the large chromosome as `batch_required`
2. Submit a test chromosome from the dashboard or API
3. Check the Batch job reaches `RUNNING` and then `SUCCEEDED`
4. Inspect CloudWatch logs for:
   - payload load
   - NCBI download
   - parser start
   - Parquet conversion
   - S3 upload
5. Verify the expected S3 outputs exist:
   - `genome_data/...`
   - `pattern_data/...`
   - `region_data/...`
6. Refresh Athena metadata if needed
7. Confirm the dashboard now shows `patterns_ready` and `regions_ready`

## Example Operational Checks

Useful checks include:

```bash
aws batch describe-jobs --jobs <job-id>
```

```bash
aws logs tail "/aws/batch/job/genome-pipeline-full-analysis" --follow
```

```bash
aws s3 ls "s3://genome-pipeline-output-443568785165/pattern_data/source=ncbi/species=homo_sapiens/chr=1/"
```

```bash
aws s3 ls "s3://genome-pipeline-output-443568785165/region_data/source=ncbi/species=homo_sapiens/chr=1/"
```

## Known Gotchas

### 1. Lambda-safe does not mean Batch-safe

Batch fixes the runtime size problem, but the container still has to include:

- Linux-compatible `fasta_parser`
- all Python dependencies needed at runtime
- a working payload handoff into `batch_entrypoint.py`

### 2. Stale staged artifacts

The staged Terraform bundle in `dist/terraform/` can lag behind current source edits. If the API or dashboard seems unchanged after apply, verify the staged zip and staged `main.tf` really include the intended edits.

### 3. Sequence-only and full analysis are intentionally different

Bulk `1-22 + X + Y` ingestion should stay on `sequence_only` so the system can land all chromosomes cheaply and reliably.

Full analysis should be promoted intentionally:

- one chromosome at a time for heavier work
- Lambda for smaller chromosomes
- Batch on Fargate for larger ones

### 4. Full analysis is a build-once path

In normal operation, a chromosome should only need heavy full analysis once per code version / reference version / parameter set. After that, the stored S3 Parquet outputs become the source for Athena queries and dashboard reads.

## Recommended Workflow Going Forward

1. Use `sequence_only` to land all chromosomes.
2. Use the dashboard to identify which chromosomes need full analysis.
3. Let smaller chromosomes run on Lambda.
4. Let larger chromosomes run on AWS Batch on Fargate.
5. Keep results in S3 and query them repeatedly rather than recomputing them.

## Relevant Files

- [main.tf](/Users/turtlemasterflex/Documents/Misc/ Job Search/ BioIT Project/BioAPI pipeline/bioproject files/main.tf)
- [Dockerfile.batch](/Users/turtlemasterflex/Documents/Misc/ Job Search/ BioIT Project/BioAPI pipeline/bioproject files/Dockerfile.batch)
- [batch_entrypoint.py](/Users/turtlemasterflex/Documents/Misc/ Job Search/ BioIT Project/BioAPI pipeline/bioproject files/batch_entrypoint.py)
- [web_api_handler.py](/Users/turtlemasterflex/Documents/Misc/ Job Search/ BioIT Project/BioAPI pipeline/bioproject files/web_api_handler.py)
- [webapp/app.js](/Users/turtlemasterflex/Documents/Misc/ Job Search/ BioIT Project/BioAPI pipeline/bioproject files/webapp/app.js)
- [DASHBOARD_BUILD_STEPS.md](/Users/turtlemasterflex/Documents/Misc/ Job Search/ BioIT Project/BioAPI pipeline/bioproject files/DASHBOARD_BUILD_STEPS.md)
- [FULLSTACK_BLUEPRINT.md](/Users/turtlemasterflex/Documents/Misc/ Job Search/ BioIT Project/BioAPI pipeline/bioproject files/FULLSTACK_BLUEPRINT.md)
