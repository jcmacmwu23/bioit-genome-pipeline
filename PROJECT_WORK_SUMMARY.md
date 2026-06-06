# Project Work Summary

This document summarizes the engineering work completed on the BioIT Genome Pipeline project.

## Project Goal

Build an end-to-end genome data pipeline that:

- downloads human chromosome reference sequences
- runs chromosome-level analysis with C++
- stores queryable data lake outputs in AWS
- exposes results through Athena and a web dashboard

## What Was Delivered

## 1. Pipeline and Runtime Fixes

- fixed Lambda handling of SQS event payloads
- fixed packaging assumptions in the build path
- corrected Terraform Athena query/table mismatches
- aligned queue submission and processing behavior with one-chromosome-per-job design
- validated local build and deployment artifacts

## 2. Full-Chromosome Analysis Path

- evaluated Lambda limits for larger chromosomes
- identified runtime and memory failure patterns on full-analysis workloads
- moved full-analysis execution to AWS Batch on Fargate
- updated the API and dashboard so full-analysis requests route to Batch
- removed misleading Lambda-size cutoff messaging from the user-facing workflow

## 3. Data Lake and Analytics Outputs

- verified partitioned Parquet landing in S3
- validated Glue and Athena integration
- confirmed live queryability of:
  - `genome_sequences`
  - `sequence_patterns`
  - `sequence_regions`
  - `gene_annotations`
- added and verified saved Athena query support around analysis outputs

## 4. Operational Tracking

- introduced DynamoDB-backed operational status storage
- tracked submission, start, finish, backend, progress, errors, and output paths
- improved resubmission behavior by clearing stale state
- exposed this operational state to the dashboard and API

## 5. Dashboard and Web API

- built and iterated on the dashboard API
- added chromosome completion monitoring
- added Batch/Athena-aware status messaging
- improved summary cards and chromosome-level analytics presentation
- fixed summary aggregation bugs where preview windows were incorrectly treated as full totals
- improved live progress reporting for pending, running, syncing, and complete states

## 6. Chromosome Visualization Work

- built ideogram-style chromosome atlas
- highlighted selected chromosomes in the atlas
- added selected chromosome lens view
- supported refocus and zoom behavior in the lens
- improved ORF and CpG track readability
- added:
  - ORF color legend
  - consistent x-axis labeling
  - CpG density axes
- reduced layout crowding in the lens
- removed duplicate table scrollbar visuals

## 7. Documentation Work

- refreshed the main README to match the deployed architecture
- documented Batch-first execution and dashboard behavior
- documented safer queue-submission patterns
- created troubleshooting documentation for real operational issues
- created this project work summary for interview review

## Significant Problems Solved

## Runtime and Deployment

- Lambda jobs failing due to incorrect SQS event parsing
- Lambda layer packaging and deployment size issues
- full-analysis workloads stalling or failing under Lambda constraints
- frontend changes not appearing live due to bundle/deployment drift

## Data and Querying

- Athena tables appearing missing because of wrong database context
- partitions not visible until repaired
- summary counts showing incorrect values because only preview rows were aggregated

## UI and Workflow

- chromosome lens loading incomplete region slices
- status cards not reflecting real Batch/Athena state
- duplicate or confusing scroll affordances in data tables

## Final State of the Project

As of June 6, 2026, the project includes:

- deployed AWS infrastructure
- Batch-first full-analysis execution
- live operational dashboard
- full human chromosome sequence coverage
- completed chromosome full-analysis runs across the target set
- queryable Athena datasets
- production-style troubleshooting and operational instrumentation

## Technical Themes

This project demonstrates work across:

- cloud data engineering
- serverless and containerized execution design
- infrastructure as code
- operational debugging
- data lake architecture
- genome-analysis visualization
- frontend/backend integration

The strongest technical story is not just that the pipeline was built, but that it was iteratively corrected from a simple Lambda-first prototype into a more reliable Batch-first system with real operational visibility.
