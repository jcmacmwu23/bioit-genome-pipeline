# Detailed Troubleshooting Playbook

This is the operator-facing troubleshooting reference for the BioIT Genome Pipeline. It is intentionally more detailed than the interview-facing summary.

## Scope

Use this guide when any of the following happen:

- queue submission succeeds but no useful processing starts
- Lambda or Batch jobs stall, fail, or look stuck in the dashboard
- Athena or dashboard summaries disagree with the underlying data
- frontend changes appear locally but not at the live CloudFront URL
- lens/chart/table behavior looks inconsistent

---

## 1. Confirm Which System Is Actually Wrong

Before changing anything, identify whether the problem is in:

- the execution layer
  - Lambda
  - AWS Batch
- the data lake layer
  - S3
  - Glue / Athena
- the operations/status layer
  - DynamoDB
  - dashboard API
- the frontend delivery layer
  - S3 website assets
  - CloudFront cache

### Fast triage questions

1. Did the sequence Parquet land in S3?
2. Did pattern and region Parquet land in S3?
3. Did Athena reflect those outputs yet?
4. Did DynamoDB operational state update?
5. Is the dashboard reading live API data or stale frontend assets?

---

## 2. Queue Submission Problems

## Symptom

- `aws sqs send-message` succeeds
- dashboard still says no sequence data
- Lambda or DLQ errors appear later

## Likely Root Cause

- malformed JSON body
- Lambda not reading `Records[*].body`
- missing `chromosome`, `accession_id`, or `analysis_mode`

## Checks

### Verify the queue body shape

Preferred payload:

```json
{
  "source": "ncbi",
  "accession_id": "NC_000022.11",
  "chromosome": "22",
  "species": "homo_sapiens",
  "analysis_mode": "sequence_only"
}
```

### Preferred submission patterns

- Python client
- Python script with `json.dumps(...)`
- `aws sqs send-message --message-body file://payload.json`

## Fix

- avoid hand-built inline shell JSON in loops
- ensure the Lambda handler supports SQS `Records[*].body`
- resubmit after validating payload shape

---

## 3. Lambda Appears Stuck at 96%

## Symptom

- dashboard shows `96%`
- elapsed time keeps increasing
- no pattern or region outputs appear

## What It Usually Means

The job is not meaningfully progressing anymore. In this project that typically meant:

- Lambda retry loops
- runtime timeout pressure
- memory exhaustion
- stale operational state

## Checks

### CloudWatch logs

Look for:

- `Runtime.OutOfMemory`
- task timeout
- repeated retries

### DynamoDB status

Check whether the item still says `running` even after the execution really died.

## Fix

- route full-analysis to AWS Batch on Fargate
- reset stale operational state on re-submission
- avoid using Lambda for large full-chromosome analysis

---

## 4. Batch Completed but Dashboard Still Says Pending

## Symptom

- Batch job is `SUCCEEDED`
- dashboard still says pending, syncing, or loading

## Root Cause

One of these is usually lagging:

- Athena partition visibility
- dashboard API summary aggregation
- DynamoDB status cleanup

## Checks

### S3 outputs

Confirm all expected files exist:

- `genome_data/...`
- `pattern_data/...`
- `region_data/...`

### Athena

If files exist but queries do not:

```sql
MSCK REPAIR TABLE genome_sequences;
MSCK REPAIR TABLE sequence_patterns;
MSCK REPAIR TABLE sequence_regions;
```

### API summary

Query:

```bash
curl -fsSL "https://7e4jzpfr2d.execute-api.us-east-1.amazonaws.com/api/chromosomes/21/summary"
```

If API is wrong while Athena/S3 are right, the problem is API aggregation, not the data lake.

## Fix

- repair Athena partitions if needed
- verify API uses full-chromosome aggregates, not preview rows
- correct stale DynamoDB operational fields on re-run

---

## 5. Summary Card Says `0 ORFs`, but Lens Shows ORF Bars

## Symptom

- summary card shows zero ORFs
- lower lens clearly shows ORF bars

## Root Cause

The summary endpoint was incorrectly summing only the first preview windows instead of the entire `sequence_regions` chromosome partition.

## Checks

### Sample regions directly

```bash
curl -fsSL "https://7e4jzpfr2d.execute-api.us-east-1.amazonaws.com/api/chromosomes/21/regions?limit=5000"
```

If later windows have nonzero `orf_count`, the summary card is wrong.

## Fix

- use dedicated chromosome-wide aggregate Athena queries for:
  - total pattern hits
  - total ORF count

---

## 6. Lens Shows Only Early Chromosome Windows

## Symptom

- lens appears to zoom only into the first region
- later windows never appear

## Root Cause

The regions API route was not honoring `limit` correctly, so the dashboard received only a truncated early slice.

## Checks

Compare:

```bash
curl -fsSL "https://7e4jzpfr2d.execute-api.us-east-1.amazonaws.com/api/chromosomes/13/regions?limit=20"
curl -fsSL "https://7e4jzpfr2d.execute-api.us-east-1.amazonaws.com/api/chromosomes/13/regions?limit=5000"
```

If the larger limit still returns only a tiny early segment, the API is wrong.

## Fix

- update the regions route to respect `limit`
- redeploy the web API Lambda

---

## 7. Dashboard Looks Right Locally but Wrong at CloudFront URL

## Symptom

- source file has the fix
- live dashboard still shows old UI

## Root Cause

Usually one of:

- local `webapp/` was changed but not copied into `dist/terraform/webapp`
- Terraform was not applied for dashboard assets
- CloudFront still has cached JavaScript/CSS

## Recovery Steps

### 1. Sync local frontend into deploy bundle

```bash
cp webapp/app.js dist/terraform/webapp/app.js
cp webapp/styles.css dist/terraform/webapp/styles.css
cp webapp/index.html dist/terraform/webapp/index.html
```

### 2. Apply dashboard asset changes

```bash
cd dist/terraform
terraform apply -auto-approve \
  -target=aws_s3_object.dashboard_site_files["app.js"] \
  -target=aws_s3_object.dashboard_site_files["styles.css"] \
  -target=aws_s3_object.dashboard_site_files["index.html"]
```

### 3. Invalidate CloudFront

```bash
AWS_PAGER='' aws cloudfront create-invalidation \
  --distribution-id E36E9FB9MPQQXK \
  --paths /app.js /styles.css /index.html /
```

---

## 8. Athena Query Returns Nothing Even Though Files Exist

## Symptom

- Parquet is in S3
- Athena table exists
- query still returns no rows

## Checks

1. Confirm the database in Athena is `genome_pipeline_db`
2. Confirm the table name is correct
3. Confirm partitions are repaired
4. Confirm the file path uses the expected partition structure

## Fix

- switch Query Editor database to `genome_pipeline_db`
- run `MSCK REPAIR TABLE`
- wait briefly for metadata refresh when needed

---

## 9. DDB Operational Status Looks Wrong After Re-Submission

## Symptom

- dashboard shows old progress
- attempt count looks inherited
- start/finish times do not match the new run

## Root Cause

Operational state was not fully reset on re-submission.

## Fix Implemented

Re-submission now resets stale fields including:

- `started_at`
- `attempt_count`
- `error_type`

## If Manual Cleanup Is Needed

Update the item directly with the AWS CLI or rerun the submission path that resets state.

---

## 10. Duplicate Scrollbars in Dashboard Tables

## Symptom

- two scroll rails appear on one table

## Root Cause

The UI had both:

- styled native scrollbar
- custom overlay scrollbar indicator

## Fix

- removed the overlay indicator
- kept a single native styled scrollbar

---

## 11. Recommended Live Checks

When a chromosome looks suspicious, check in this order:

### 1. Summary API

```bash
curl -fsSL "https://7e4jzpfr2d.execute-api.us-east-1.amazonaws.com/api/chromosomes/<CHR>/summary"
```

### 2. Regions API

```bash
curl -fsSL "https://7e4jzpfr2d.execute-api.us-east-1.amazonaws.com/api/chromosomes/<CHR>/regions?limit=5000"
```

### 3. Batch status

Check Batch job state and CloudWatch logs.

### 4. S3

Confirm expected sequence, pattern, and region outputs landed.

### 5. Athena

Confirm the database, partitions, and query results.

---

## 12. Operational Guidance Going Forward

For this project’s current state:

- use Lambda for lighter ingestion/orchestration paths
- use AWS Batch on Fargate for full chromosome analysis
- treat DynamoDB as the operational truth for in-flight job state
- treat Athena as the analytics truth after Parquet outputs land

That split gives the cleanest debugging model:

- execution truth: Batch / Lambda / CloudWatch
- data truth: S3 / Athena
- status truth: DynamoDB / dashboard API

---

## 13. Documentation and Repo Hygiene Notes

To keep the repository easier to share and review:

- markdown links should use repo-relative paths instead of machine-specific absolute paths
- repo docs should avoid laptop-specific folder references
- the core reading path should stay small, with deeper operational detail kept in companion reference files

Recommended core docs:

- `README.md`
- `TROUBLESHOOTING.md`
- `PROJECT_WORK_SUMMARY.md`

Extended reference docs:

- `TROUBLESHOOTING_DETAILED.md`
- `PROJECT_WORK_SUMMARY_DETAILED.md`
