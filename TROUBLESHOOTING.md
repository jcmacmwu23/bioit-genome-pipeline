# Troubleshooting Guide

This guide captures the main issues encountered while building and operating the BioIT Genome Pipeline, along with the fixes used in the live AWS environment.

## 1. SQS Messages Were Accepted but Jobs Failed Immediately

### Symptom

- queue submissions appeared successful
- Lambda failed right away
- some jobs ended in the DLQ

### Root Cause

The Lambda handler originally expected direct payloads and did not correctly unwrap SQS `Records[*].body`.

### Fix

- updated the handler to support both direct invocation and SQS event shapes
- documented safer queue submission patterns
- preferred JSON file or Python-generated payloads over hand-built shell JSON

## 2. Athena Queries Worked but the Wrong Database Was Selected

### Symptom

- tables appeared to be missing in Athena
- saved queries returned no rows

### Root Cause

Athena Query Editor had reset to the default database instead of `genome_pipeline_db`.

### Fix

- confirmed the live tables existed in `genome_pipeline_db`
- documented the database switch in the README
- used `MSCK REPAIR TABLE` when partitions had not yet been registered

## 3. Lambda Full Analysis Stalled at 96%

### Symptom

- dashboard showed jobs stuck around `96%`
- elapsed time kept growing
- no final output landed

### Root Cause

Large or memory-heavy full-chromosome runs were not actually still progressing. Some Lambda jobs were retrying or dying from runtime limits, including memory pressure.

### Fix

- moved full-analysis routing to AWS Batch on Fargate
- removed the misleading Lambda cutoff logic from the dashboard and API
- kept Lambda focused on lighter ingestion and orchestration work

## 4. Batch Finished but Dashboard Still Looked Pending

### Symptom

- Batch job succeeded
- dashboard still showed pending or loading states

### Root Cause

Operational state and Athena-backed summaries were not always synchronized cleanly, especially after retries or re-submissions.

### Fix

- added DynamoDB-backed operational tracking
- stored submission, start, finish, backend, progress, and output paths
- reset stale status fields on re-submission
- improved dashboard messaging for Batch, Athena sync, and completion

## 5. Summary Card Showed `0 ORFs` Even When the Lens Had ORFs

### Symptom

- summary card displayed `0 window ORFs`
- lower visualization clearly showed ORF bars

### Root Cause

The summary API was summing only the first few preview windows instead of the full chromosome-wide region dataset.

### Fix

- replaced preview-based totals with dedicated aggregate Athena queries
- added a regression test for chromosomes whose early windows are zero but later windows contain ORFs

## 6. Lens Visualization Looked Empty or Incomplete

### Symptom

- lens showed only a narrow early-region slice
- tooltip values looked static
- later windows were missing

### Root Cause

The regions API route was not honoring the requested `limit` correctly, so the frontend only received a truncated early segment.

### Fix

- updated the regions route to respect `limit`
- redeployed the web API
- verified the lens could now render full chromosome span samples

## 7. Dashboard Looked Correct Locally but Not in the Live URL

### Symptom

- local source had the new UI
- CloudFront dashboard still showed the older version

### Root Cause

Frontend source changes had not yet been copied into the Terraform deployment bundle or CloudFront was still serving cached assets.

### Fix

- copied updated `webapp` files into `dist/terraform/webapp`
- applied the dashboard S3 object changes
- invalidated CloudFront for `app.js`, `styles.css`, and related assets

## 8. Full Analysis Needed Better Progress Visibility

### Symptom

- dashboard only showed generic pending states
- hard to tell whether work was starting, running, syncing, or done

### Root Cause

The original UI relied mostly on dataset presence instead of explicit operational status.

### Fix

- added progress-aware status cards
- surfaced Batch start/running/success states
- added Athena sync messaging after Batch completion
- used DynamoDB to back more of the dashboard’s live status

## 9. Candidate Coding Regions Tables Looked Like They Had Two Scrollbars

### Symptom

- each table appeared to have two vertical rails

### Root Cause

The UI had both:

- the native browser scrollbar
- a custom always-visible overlay scrollbar

### Fix

- removed the overlay scrollbar
- kept a single styled native scrollbar

## 10. Key Operational Lesson

For small demonstrations, Lambda can be useful. For full-chromosome processing with richer analysis and more consistent runtime behavior, AWS Batch on Fargate proved to be the better execution model in this project.

