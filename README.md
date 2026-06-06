# BioIT Genome Pipeline

AWS-based genome ingestion, C++ sequence analysis, Athena-ready data lake outputs, and a live dashboard for chromosome-level review.

## What This Project Does

This project pulls human chromosome reference sequences, processes them with a high-performance C++ parser, stores partitioned Parquet datasets in S3, and exposes both:

- analytics tables in Athena
- operational status through a web API
- a CloudFront-hosted dashboard for chromosome monitoring and visualization

The current production workflow is optimized for full-chromosome analysis and routes full-analysis jobs through AWS Batch on Fargate.

## Current Architecture

```text
NCBI / Ensembl
      |
      v
  SQS job queue / dashboard API submit
      |
      v
AWS Lambda
  - sequence-only ingestion
  - lightweight orchestration
      |
      +--------------------------+
                                 |
                                 v
                      AWS Batch on Fargate
                      - full chromosome analysis
                      - C++ parser execution
                      - pattern detection
                      - region summaries
                                 |
                                 v
                           S3 data lake
                  genome_data / pattern_data / region_data
                                 |
                +----------------+----------------+
                |                                 |
                v                                 v
            Athena / Glue                  Dashboard API + DDB
                                           + CloudFront dashboard
```

## Main Outputs

The pipeline writes partitioned Parquet datasets to S3:

- `genome_data/`
  - sequence-level records
- `pattern_data/`
  - motif, repeat, and candidate ORF hits
- `region_data/`
  - windowed GC, ORF, motif, and repeat summaries
- `gene_annotation_data/`
  - annotation-derived gene features when loaded

Partition structure:

```text
source=<source>/species=<species>/chr=<chromosome>/year=<YYYY>/month=<MM>/
```

## AWS Components

- AWS Batch on Fargate for full-chromosome analysis
- Lambda for ingestion, queue handling, and web API routes
- SQS for job submission
- S3 for the data lake and frontend hosting
- Athena and Glue for analytics
- DynamoDB for operational job status tracking
- CloudFront for dashboard delivery
- CloudWatch Logs for runtime debugging

## Dashboard and API

Live environment in this project:

- Dashboard: [CloudFront dashboard](https://d11q5vlaasogkc.cloudfront.net/)
- API base: `https://7e4jzpfr2d.execute-api.us-east-1.amazonaws.com`

The dashboard supports:

- chromosome completion tracking
- Batch/Athena progress messaging
- ideogram-style chromosome atlas
- selected chromosome lens
- candidate ORF and CpG density views
- pattern and region summaries

## Athena Tables

Database:

- `genome_pipeline_db`

Core tables:

- `genome_sequences`
- `sequence_patterns`
- `sequence_regions`
- `gene_annotations`

Important:

- In Athena Query Editor, switch the database to `genome_pipeline_db`
- If partitions are newly written, run `MSCK REPAIR TABLE <table_name>` when needed

## Build and Deploy

### Prerequisites

- AWS CLI configured
- Terraform installed
- Python 3.11
- Docker or Colima for Batch image work when needed
- `make`

### Local build

```bash
make build-web-api
make build-function
make build-layer
make build
```

### Terraform deploy

```bash
cd dist/terraform
terraform init
terraform plan
terraform apply
```

## Common Workflows

### Sequence-only ingestion

Use sequence-only when you want to land the chromosome first without running the full region/pattern analysis path immediately.

### Full analysis

Full-chromosome analysis is Batch-first in the current system.

- dashboard button submits full analysis
- web API submits Batch jobs
- DynamoDB stores progress and completion state
- Athena-backed summary views load after outputs land

### Queue submission

Prefer the project client or JSON file submission over hand-built shell JSON.

See:

- [QUICKSTART.md](QUICKSTART.md)
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

## Project Docs

Recommended starting points:

- [README.md](README.md)
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- [PROJECT_WORK_SUMMARY.md](PROJECT_WORK_SUMMARY.md)

## Notes on Biological Scope

Current gene-like detection in the analysis path is based on candidate ORFs and pattern heuristics, not authoritative annotation alone.

For biologically stronger gene identification:

- ingest Ensembl or NCBI annotations
- keep `gene_annotations` queryable
- join annotations with `sequence_patterns` and `sequence_regions`

## Status

As of June 6, 2026, the project has:

- live AWS deployment
- full human chromosome sequence coverage
- completed Batch-first full-analysis path
- live dashboard with operational progress reporting
- chromosome atlas and lens visualizations
