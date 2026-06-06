# Detailed Project Work Summary

This document is the detailed record of the work completed on the BioIT Genome Pipeline project.

## 1. Original Project Direction

The project started as a genome-processing pipeline built around:

- AWS Lambda
- Terraform-managed infrastructure
- a C++ FASTA parser
- S3 output storage

The initial concept was valid, but the implementation needed substantial correction and extension before it behaved reliably for full chromosome analysis and dashboard-driven review.

## 2. Early Review and Structural Fixes

The first pass uncovered several important problems in the production path:

- Lambda did not correctly handle SQS-triggered event shape
- Terraform named Athena queries referenced a table name that did not exist
- build packaging assumed a missing `terraform/` directory
- some tests were out of sync with the real code path
- `output_prefix` behavior did not match the project’s real partitioned-write model

These issues were corrected so the project could move from a prototype toward a deployable system.

## 3. Local Build and Toolchain Work

Local build work included:

- getting the C++ parser building cleanly on macOS
- making build steps more tolerant of local tool availability
- fixing packaging assumptions in `Makefile` and `build.sh`
- aligning Python dependency installation with the actual runtime target
- getting local build artifacts generated end-to-end

This also included:

- handling pyarrow compatibility constraints
- ensuring Python and pip usage were consistent
- installing Terraform locally
- validating the generated Terraform bundle

## 4. AWS Deployment Readiness and First Deploy

Before full deployment, the project went through:

- Terraform `init`
- Terraform `validate`
- warning cleanup in `main.tf`
- AWS CLI installation
- AWS credential setup
- initial IAM permission troubleshooting

Key deployment blockers that were resolved:

- missing infra permissions on the deployment identity
- Lambda layer upload size limits
- packaging strategy changes to move artifacts through S3-backed deployment

## 5. Live AWS Infrastructure Work

The deployed stack eventually included:

- Lambda processor
- Lambda layer
- SQS queue and DLQ
- S3 output buckets
- Athena workgroup
- Glue database and tables
- dashboard API
- CloudFront-hosted static frontend
- AWS Batch on Fargate
- DynamoDB operations table

The system was validated incrementally rather than as one blind full deploy.

## 6. Sequence and Analysis Pipeline Evolution

The project’s execution model changed significantly over time.

### Early state

- Lambda handled the main analysis path
- this worked better for smaller jobs
- large or memory-heavy chromosome analysis was unstable

### Final state

- Lambda retained lighter ingestion/orchestration responsibilities
- full-analysis requests were routed to AWS Batch on Fargate
- dashboard and API logic were updated to reflect Batch-first execution

This was one of the most important architecture improvements in the project.

## 7. Dashboard and Web API Buildout

The dashboard grew from a simple static site into a live operational and analytical interface.

Work completed here included:

- dashboard API Lambda packaging
- live chromosome summary routes
- live pattern and region routes
- inventory and completion tracking
- CloudFront deployment and cache invalidation workflows
- status-aware UI behavior based on live backend state

The dashboard was iterated heavily based on real runtime behavior.

## 8. Full Human Chromosome Processing

The project progressed from validating individual chromosomes to supporting full human chromosome coverage.

Important milestones included:

- validating chromosome 22 end to end
- downloading and landing all human chromosome sequences
- incrementally running full analysis across chromosomes
- stabilizing dashboard behavior for chromosomes with large analysis outputs

The work also included repeated live troubleshooting for chromosomes whose dashboard behavior exposed gaps in status handling, summary aggregation, or frontend rendering.

## 9. Athena, Glue, and Data-Lake Query Work

The project was extended beyond raw output landing into actual queryable analytics.

That included:

- validating `genome_pipeline_db`
- checking saved Athena queries
- handling partition refresh behavior
- verifying tables after `MSCK REPAIR TABLE`
- aligning dashboard summaries with Athena-backed truth

This was especially important because many user-visible “bugs” were actually mismatches between:

- landed S3 data
- Athena metadata visibility
- API summary logic
- dashboard rendering state

## 10. Operations Tracking with DynamoDB

The original system did not expose enough operational truth for reliable dashboard status tracking.

To solve that, DynamoDB-backed operational tracking was added.

Stored/used fields included:

- backend
- status
- detail / failure reason
- submitted, started, finished timestamps
- attempt count
- progress percentage
- batch job metadata
- latest output paths

This let the dashboard show much more useful status such as:

- job submitted
- Batch starting
- Batch running
- Athena syncing
- completed
- failed

## 11. Major Runtime Failures Investigated and Fixed

The project encountered several real runtime issues:

### Lambda SQS parsing failure

- fixed by properly unwrapping queue events

### Lambda memory/runtime failures

- especially visible on full-chromosome analysis
- led to the Batch-first transition

### Stale status after retries or re-submission

- fixed by resetting operational fields on re-submit

### Athena summary mismatch

- fixed by using whole-chromosome aggregate queries instead of preview rows

### Frontend bundle drift

- fixed by explicitly syncing `webapp/` into `dist/terraform/webapp/`
- followed by targeted S3 object apply and CloudFront invalidation

## 12. Lens and Atlas Visualization Work

The selected chromosome lens and atlas took multiple rounds of improvement.

Work included:

- ideogram-style atlas layout
- atlas selection highlighting
- making the selected chromosome more visually prominent
- adding a dual-view lens model
  - broad scroll view
  - zoomed local detail view
- candidate ORF track improvements
- CpG density chart improvements
- zoom / reset interactions
- tooltip and hover behavior corrections
- more informative labels and badges
- spacing and readability cleanup

Additional visualization improvements included:

- ORF GC-color legend
- consistent genomic x-axis labels
- y-axis labeling for CpG density
- badge placement cleanup
- removal of duplicate table scrollbar treatment

## 13. Summary Card and Detail Table Work

The dashboard summary area was also improved through many iterations:

- status cards became progress-aware
- sequence / pattern / region / full-analysis path each got more useful messaging
- Athena sync messages became explicit
- example summary card became tied to live chromosome detail
- candidate coding region table became scrollable and more readable
- region chart preview was reworked to better communicate GC and ORF behavior

## 14. Documentation Work Completed

Documentation grew alongside the code and deployment work.

Notable documentation tasks included:

- deployment notes
- queue submission guidance
- Batch-on-Fargate process notes
- dashboard build steps
- architecture summary
- project structure notes
- troubleshooting guides
- summary writeups for project progress

The final docs strategy was then simplified so the core repository reading path stayed short and clear.

## 15. Final Documentation Strategy

Two layers of documentation now exist:

### Core project docs

- `README.md`
- `TROUBLESHOOTING.md`
- `PROJECT_WORK_SUMMARY.md`

### Extended reference docs

- `TROUBLESHOOTING_DETAILED.md`
- `PROJECT_WORK_SUMMARY_DETAILED.md`

This keeps the repo readable while preserving the deeper operational record for future maintenance.

Additional cleanup decisions:

- repo markdown should use relative links where possible
- docs should avoid machine-specific folder paths
- the short-form docs should explain the project clearly without requiring the full troubleshooting history

## 16. Practical Engineering Themes in This Project

This project ended up demonstrating a lot more than just “build a parser and upload files.”

It involved:

- cloud architecture correction
- deployment debugging
- packaging and artifact strategy
- serverless versus container execution tradeoffs
- data lake design
- queryability and metadata synchronization
- observability and operations-state tracking
- frontend visualization design and refinement
- repeated live-system debugging under real workloads

## 17. End State

By the end of this work, the project had:

- live AWS deployment
- Batch-first full-chromosome processing
- dashboard-based status tracking
- DynamoDB-backed operations visibility
- queryable Athena datasets
- full chromosome coverage
- significantly improved visual presentation
- a cleaner set of docs for both reviewers and maintainers

## 18. Most Important Lesson

The biggest improvement was not a single code fix. It was the shift from a nominally working Lambda-centric prototype to a more honest and robust system:

- Batch for full analysis
- DynamoDB for operational truth
- Athena for analytics truth
- dashboard API for live presentation

That separation made the system easier to reason about, easier to debug, and much better suited to real chromosome-scale workloads.
