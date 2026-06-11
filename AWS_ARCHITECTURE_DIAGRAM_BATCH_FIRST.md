# AWS Architecture Diagram: Batch-First Runtime

This architecture view reflects the current deployed BioIT genome pipeline in AWS account `443568785165` in `us-east-1`.

It captures the current batch-first processing path, dashboard/API layer, and operational status tracking used by the live system.

## Service Map

```mermaid
flowchart LR
    subgraph Sources["Genome Sources"]
        NCBI["NCBI Entrez"]
        URLS["Direct URL / alternate source"]
    end

    subgraph Entry["User Entry Points"]
        CLI["CLI / Python client"]
        DASH["CloudFront dashboard"]
    end

    subgraph API["API and Submission Layer"]
        APIGW["Amazon API Gateway"]
        WEB["Web API Lambda"]
        SQS["Amazon SQS job queue"]
        OPS["DynamoDB operations store"]
    end

    subgraph Compute["Processing Layer"]
        INGEST["Sequence ingestion Lambda"]
        BATCH["AWS Batch on Fargate\nPython orchestration + C++ parser"]
        LOGS["CloudWatch Logs"]
    end

    subgraph Storage["Data Lake and Artifacts"]
        TEMP["S3 temp bucket"]
        OUT["S3 output bucket"]
        ATHRES["S3 Athena results bucket"]
    end

    subgraph Catalog["Catalog and Query"]
        GLUE["AWS Glue catalog / crawler"]
        ATHENA["Amazon Athena"]
    end

    subgraph UX["Live Experience"]
        CF["CloudFront static app"]
        DASHAPI["Dashboard status + chromosome views"]
    end

    CLI --> APIGW
    DASH --> CF
    CF --> APIGW
    APIGW --> WEB
    WEB --> SQS
    WEB --> OPS
    WEB --> ATHENA

    SQS --> INGEST
    INGEST -->|"download chromosome FASTA"| NCBI
    INGEST -->|"optional source"| URLS
    INGEST --> TEMP
    INGEST -->|"sequence parquet"| OUT
    INGEST --> OPS
    INGEST --> LOGS

    WEB -->|"submit full analysis"| BATCH
    BATCH -->|"read landed sequence / write outputs"| OUT
    BATCH -->|"temporary working files"| TEMP
    BATCH --> OPS
    BATCH --> LOGS

    OUT --> GLUE
    GLUE --> ATHENA
    ATHENA --> ATHRES
    ATHENA --> WEB
    OPS --> WEB
    WEB --> DASHAPI
    DASHAPI --> CF

    classDef source fill:#E5E7EB,stroke:#6B7280,color:#111827
    classDef entry fill:#FDE68A,stroke:#B45309,color:#111827
    classDef api fill:#FCA5A5,stroke:#B91C1C,color:#111827
    classDef compute fill:#A7F3D0,stroke:#047857,color:#111827
    classDef storage fill:#93C5FD,stroke:#1D4ED8,color:#111827
    classDef catalog fill:#DDD6FE,stroke:#6D28D9,color:#111827
    classDef ux fill:#FBCFE8,stroke:#BE185D,color:#111827

    class NCBI,URLS source
    class CLI,DASH entry
    class APIGW,WEB,SQS,OPS api
    class INGEST,BATCH,LOGS compute
    class TEMP,OUT,ATHRES storage
    class GLUE,ATHENA catalog
    class CF,DASHAPI ux
```

## Batch-First Runtime

1. A user submits a chromosome request from the dashboard or client.
2. The web API records operational state in DynamoDB and routes the request into the pipeline.
3. Sequence ingestion Lambda downloads and lands the chromosome sequence parquet in S3.
4. Full analysis is submitted to AWS Batch on Fargate.
5. The Batch container runs the C++ parser and Python analysis flow, then writes:
   - `genome_data/`
   - `pattern_data/`
   - `region_data/`
6. Glue/Athena expose those datasets for query.
7. The dashboard API reads both Athena and DynamoDB so the UI can show:
   - chromosome readiness
   - analysis progress
   - summary card values
   - lens and atlas visualizations

## Operational State

DynamoDB is used as the operational store for near-real-time status rather than as the analytical system of record.

Typical state captured there includes:

- job submission metadata
- latest phase (`queued`, `running`, `syncing`, `complete`, `failed`)
- Batch job identifiers
- timestamps and elapsed time
- progress hints shown in the dashboard
- failure messages for troubleshooting

## Data Products

The pipeline currently exposes these main analytical datasets:

- `genome_sequences`
- `sequence_patterns`
- `sequence_regions`
- `gene_annotations` when annotation data is loaded

Partition layout is Hive-style:

```text
source=<source>/species=<species>/chr=<chromosome>/year=<YYYY>/month=<MM>/
```

## Why This Diagram Exists

This version is aligned with the current deployed system, where:

- full analysis is Batch-first
- Lambda is mainly used for ingestion and lightweight API/orchestration work
- DynamoDB stores operational progress
- Athena remains the analytical query layer
- CloudFront and the dashboard API provide the live visualization surface
