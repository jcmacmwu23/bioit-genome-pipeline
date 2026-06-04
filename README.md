# BioIT Genome Intelligence Pipeline

An end-to-end AWS data engineering project that pulls human reference genome sequences from the NCBI API, processes them through a high-performance C++ FASTA/FASTQ parser, stores results in an S3 data lake as Parquet, and surfaces the analysis through an interactive web dashboard.

---

## Architecture

```
NCBI Entrez API
      │
      ▼
AWS Lambda / AWS Batch on Fargate
  ├── Download FASTA  (S3 reuse or NCBI stream)
  ├── C++ FASTA parser  (g++ -O3, deployed as Lambda layer or Docker image)
  │     ├── GC content & base composition per sequence
  │     ├── Motif detection  (start codons, CpG hotspots, GATA motifs, …)
  │     ├── Candidate ORF detection  (3 reading frames × both strands)
  │     └── Sliding-window region summaries (100 kb windows)
  └── Parquet → S3 data lake
            │
            ├── genome_data/      (sequence-level metrics)
            ├── pattern_data/     (per-hit motif & ORF positions)
            └── region_data/      (windowed GC, ORF density, CpG density)
                     │
              AWS Glue Catalog  ──  MSCK REPAIR auto-triggered post-job
                     │
              Amazon Athena
                     │
         API Gateway  →  Python Lambda (web_api_handler.py)
                     │
              S3 Static Website
                     │
         Interactive Dashboard  (vanilla JS + SVG, no framework)
```

### Compute routing by chromosome size

| Chromosome size | Backend | Reason |
|---|---|---|
| ≤ 60 Mb (chr19–22, Y) | AWS Lambda | Fits within 15-min / 3 GB ceiling |
| > 60 Mb (chr1–18, X) | AWS Batch on Fargate | No timeout, up to 32 GB RAM |

---

## Tech Stack

| Layer | Technology |
|---|---|
| Sequence source | NCBI Entrez API (streaming download) |
| FASTA parsing | C++17 with nlohmann/json, compiled per-platform |
| Compute | AWS Lambda (Python 3.11), AWS Batch on Fargate |
| Storage | Amazon S3 (Parquet + Snappy), S3 static website |
| Catalog | AWS Glue, Amazon Athena |
| Job queuing | Amazon SQS |
| Orchestration | AWS Step Functions |
| API | Amazon API Gateway HTTP → Python Lambda |
| Dashboard | Vanilla JS + inline SVG (no framework dependency) |
| Infrastructure | Terraform |

---

## Dashboard Features

- **24-chromosome completion map** — pill grid, clickable; green = full analysis complete
- **Chromosome atlas** — all 24 bars scaled by reference length, click any bar to select
- **Selected chromosome lens**
  - Ideogram colored by ORF density gradient (beige → purple)
  - Analysis window track: GC-colored 100 kb windows with motif dots and ORF flags
  - CpG motif density track across the full chromosome
  - Click-to-zoom: fetches individual ORF positions + CpG sites from Athena for the selected region
  - ← Zoom out to return to full view
- **Pattern leaderboard** — top motifs by total hit count per chromosome
- **Real-time Batch progress** — elapsed time, estimated % complete, and ETA while a job runs
- **Safe job submission** — single-chromosome and full 24-chromosome batch forms

---

## Prerequisites

- AWS account with IAM permissions for Lambda, Batch, S3, Glue, Athena, SQS, ECR, Step Functions
- AWS CLI configured (`aws configure`)
- Terraform ≥ 1.0
- Python 3.11+
- g++ with C++17 support (**Linux x86_64** required for Lambda/Batch)
- Docker (for rebuilding the Batch container image on macOS/ARM)

---

## Deployment

### 1. Clone and build

```bash
git clone <repo-url>
cd bioproject-files

# Build Lambda layer (Linux C++ binary + Python deps) and function zips
make build
```

> **macOS note:** The C++ parser must be a Linux ELF binary.
> Use Docker (`--platform linux/amd64`) or AWS CodeBuild (`buildspec.batch-image.yml`).

### 2. Deploy infrastructure

```bash
cd dist/terraform
terraform init
terraform apply -var='ncbi_email=your@email.com'
```

### 3. Configure the dashboard

```bash
cp webapp/config.js.example webapp/config.js
# Set window.BIOIT_API_BASE_URL to the API Gateway URL from Terraform output
```

### 4. Build and push the Batch container image

```bash
ECR_URI=$(terraform output -raw batch_runner_repository_url)

aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin $ECR_URI

docker build --platform linux/amd64 -t batch-runner -f Dockerfile.batch .
docker tag batch-runner:latest ${ECR_URI}:latest
docker push ${ECR_URI}:latest
```

---

## Running the Pipeline

### Ingest all 24 chromosomes (sequence-only, uses Lambda)

```bash
curl -X POST https://<api>/api/jobs/human-reference \
  -H "Content-Type: application/json" \
  -d '{"species": "homo_sapiens", "analysis_mode": "sequence_only"}'
```

### Promote a chromosome to full analysis (Batch for large chromosomes)

From the dashboard: select a chromosome → **Run Full Analysis on Batch**.

Or via API:

```bash
curl -X POST https://<api>/api/chromosomes/1/analyze \
  -H "Content-Type: application/json" \
  -d '{"species": "homo_sapiens"}'
```

The API automatically:
- Looks up the existing raw FASTA in S3 to avoid re-downloading from NCBI
- Routes to Lambda (small chromosomes) or Batch (large chromosomes)
- Triggers `MSCK REPAIR TABLE` on completion so Athena sees results immediately

---

## Key Files

| File | Purpose |
|---|---|
| `fasta_parser.cpp` | C++ parser — GC content, motifs, ORFs, sliding windows |
| `lambda_handler.py` | Pipeline core — download, parse, upload, repair partitions |
| `batch_entrypoint.py` | Batch container entry — reads `JOB_PAYLOAD` env, calls lambda_handler |
| `web_api_handler.py` | REST API — chromosome status, Athena queries, job submission |
| `main.tf` | Complete Terraform definition — all AWS resources |
| `Dockerfile.batch` | Batch image — compiles C++ for Linux, installs Python deps |
| `buildspec.batch-image.yml` | AWS CodeBuild spec for remote image builds |
| `webapp/` | Dashboard — HTML + CSS + vanilla JS + SVG |
| `Makefile` | Build, test, deploy targets |

---

## Human Genome Reference

All 24 chromosomes use GRCh38.p14 NCBI RefSeq accessions (NC_000001.11 – NC_000024.10).
See `web_api_handler.py → HUMAN_CHROMOSOME_ACCESSIONS` for the complete mapping.

---

## Estimated Cost (AWS Free Tier)

| Service | Typical one-time cost |
|---|---|
| Lambda processing (small chromosomes) | ~$0 (free tier) |
| Batch / Fargate (chr1–18, X) | ~$0.40–0.55 / chromosome |
| S3 storage (all 24 chromosomes) | ~$0 (< 5 GB) |
| Athena queries | Cents per query on Parquet |
| API Gateway + CloudFront | ~$0 (free tier) |
| **Full 24-chromosome run** | **~$8–12 total** |

Results persist in S3 — no cost to query after the initial analysis.

---

## License

MIT
