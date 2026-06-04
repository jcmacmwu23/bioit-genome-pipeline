# Schema as Code — Data Lake Governance Guide

**Last Updated:** 2026-02-10

---

## The Problem This Solves

Without schema-as-code, data lake schema is **inferred at runtime** by the Glue Crawler. This means:
- No git history of schema changes
- Silent breaking changes (renamed columns, dropped fields)
- No way to know what a pipeline outputs without querying Athena
- Multiple pipelines become impossible to track

With schema-as-code, **every pipeline's output is documented and versioned in the repo**.

---

## Repo Structure

```
bioproject files/
├── schemas/                          ← all pipeline schemas live here
│   ├── genome_sequences.json         ← pipeline 1 (current)
│   ├── variants.json                 ← pipeline 2 (future example)
│   └── annotations.json             ← pipeline 3 (future example)
├── main.tf                           ← deploys schemas as Glue tables
└── ...
```

---

## How It Works

### Two layers working together:

```
schemas/<pipeline>.json      ←  human-readable schema definition (source of truth)
        ↓  mirrors
main.tf (aws_glue_catalog_table)  ←  deploys schema to AWS Glue catalog
        ↓  enforces
Athena queries               ←  schema validated at query time
        ↓  supplements
Glue Crawler                 ←  keeps partition metadata up to date only
```

The **Glue Crawler no longer owns schema** — it only discovers new S3 partitions. Schema is exclusively defined in Terraform, sourced from the JSON schema files.

---

## Current Schema: genome_sequences

**File:** [schemas/genome_sequences.json](schemas/genome_sequences.json)
**Glue Table:** `genome_sequences` in `genome_pipeline_db`
**S3 Prefix:** `genome_data/`

### Columns

| Column | Type | Description |
|---|---|---|
| `id` | string | Sequence identifier from FASTA/FASTQ header |
| `description` | string | Full header description text |
| `sequence` | string | DNA/RNA sequence string |
| `length` | bigint | Sequence length in base pairs |
| `gc_content` | double | GC percentage (0.0 – 100.0) |
| `base_composition` | struct<A,T,G,C,N:int> | Per-base nucleotide counts |
| `quality` | string | Phred quality scores (FASTQ only, null for FASTA) |

### Partition Keys

| Key | Type | Example Values |
|---|---|---|
| `source` | string | `ncbi`, `ensembl`, `url` |
| `species` | string | `homo_sapiens`, `mus_musculus` |
| `chr` | string | `NC_000022`, `22` |
| `year` | string | `2026` |
| `month` | string | `01` – `12` |

---

## Adding a New Pipeline — Step by Step

### Step 1 — Create the schema file

Create `schemas/<pipeline_name>.json`:

```json
{
  "pipeline": "variants",
  "description": "VCF variant calls from genome sequences",
  "s3_prefix": "variant_data/",
  "partition_keys": ["source", "species", "chr", "year", "month"],
  "format": "parquet",
  "compression": "snappy",
  "columns": [
    {"name": "variant_id",   "type": "string",  "description": "Unique variant identifier"},
    {"name": "chromosome",   "type": "string",  "description": "Chromosome name"},
    {"name": "position",     "type": "int64",   "description": "Genomic position (1-based)"},
    {"name": "ref_allele",   "type": "string",  "description": "Reference allele"},
    {"name": "alt_allele",   "type": "string",  "description": "Alternate allele"},
    {"name": "quality_score","type": "double",  "description": "Variant call quality score"},
    {"name": "filter_status","type": "string",  "description": "PASS or filter reason"}
  ],
  "owner": "variant-pipeline",
  "version": "1.0"
}
```

### Step 2 — Add Glue table to main.tf

```hcl
resource "aws_glue_catalog_table" "variants" {
  name          = "variants"
  database_name = aws_glue_catalog_database.genome_db.name
  description   = "VCF variant calls from genome sequences"
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    "classification"   = "parquet"
    "compressionType"  = "snappy"
    "EXTERNAL"         = "TRUE"
    "parquet.compress" = "SNAPPY"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.genome_output.id}/variant_data/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
      parameters = { "serialization.format" = "1" }
    }

    columns { name = "variant_id";    type = "string" }
    columns { name = "chromosome";    type = "string" }
    columns { name = "position";      type = "bigint" }
    columns { name = "ref_allele";    type = "string" }
    columns { name = "alt_allele";    type = "string" }
    columns { name = "quality_score"; type = "double" }
    columns { name = "filter_status"; type = "string" }
  }

  partition_keys { name = "source";  type = "string" }
  partition_keys { name = "species"; type = "string" }
  partition_keys { name = "chr";     type = "string" }
  partition_keys { name = "year";    type = "string" }
  partition_keys { name = "month";   type = "string" }
}
```

### Step 3 — Add crawler S3 target for the new prefix

```hcl
# In the existing aws_glue_crawler resource, add:
s3_target {
  path = "s3://${aws_s3_bucket.genome_output.id}/variant_data/"
}
```

### Step 4 — Deploy

```bash
terraform plan    # review what will change
terraform apply   # creates the new Glue table
```

---

## Schema Versioning Rules

When you need to change an existing schema, follow these rules to avoid breaking Athena queries:

| Change Type | Safe? | How to handle |
|---|---|---|
| Add a new column | Yes | Add to JSON + Terraform, old data returns `null` for new col |
| Rename a column | **No** | Create new column, deprecate old one — never rename in place |
| Change column type | **No** | New column with new name + type, keep old column |
| Remove a column | **No** | Mark as `deprecated` in JSON, keep in Terraform until all consumers updated |
| Add a partition key | **No** | Requires table recreation — discuss before doing |
| Change S3 prefix | **No** | New table resource + migrate data |

### Marking a column as deprecated in the schema JSON

```json
{
  "name": "old_column_name",
  "type": "string",
  "description": "DEPRECATED — use new_column_name instead. Will be removed in v2.0",
  "deprecated": true,
  "deprecated_since": "2026-03-01",
  "replaced_by": "new_column_name"
}
```

---

## Detecting Breaking Changes

Since schema is in Terraform, breaking changes are visible before deployment:

```bash
terraform plan
```

Example output when a column type changes:
```
~ resource "aws_glue_catalog_table" "genome_sequences" {
    ~ storage_descriptor {
        ~ columns {
            ~ type = "string" -> "bigint"   # ← breaking change caught before deploy
          }
      }
  }
```

This means **no silent schema changes ever reach production**.

---

## Multi-Pipeline Data Lake Layout

As pipelines grow, the S3 and Glue structure scales cleanly:

```
S3 output bucket/
├── genome_data/          → Glue table: genome_sequences
│   └── source=ncbi/species=homo_sapiens/chr=NC_000022/year=2026/month=02/
├── variant_data/         → Glue table: variants (future)
│   └── source=gatk/species=homo_sapiens/chr=NC_000022/year=2026/month=02/
└── annotation_data/      → Glue table: annotations (future)
    └── source=ensembl/assembly=GRCh38/year=2026/month=02/
```

**One Glue database** (`genome_pipeline_db`) holds all tables.
**One Athena workgroup** (`genome-pipeline-workgroup`) queries all tables.
**One crawler** can be extended with additional `s3_target` blocks.
**Each pipeline** owns one JSON schema file and one Terraform table block.

---

## Cross-Pipeline Athena Query Example

Once variants pipeline is added, you can join across pipelines:

```sql
SELECT
  s.id,
  s.chr,
  s.gc_content,
  v.position,
  v.ref_allele,
  v.alt_allele,
  v.quality_score
FROM genome_sequences s
JOIN variants v
  ON s.chr = v.chr
  AND s.source = v.source
  AND s.year = v.year
WHERE v.filter_status = 'PASS'
  AND s.gc_content > 50.0;
```

---

## Files Reference

| File | Purpose |
|---|---|
| [schemas/genome_sequences.json](schemas/genome_sequences.json) | Schema definition for pipeline 1 |
| [main.tf](main.tf) — `aws_glue_catalog_table.genome_sequences` | Deploys schema to AWS Glue |
| [main.tf](main.tf) — `aws_glue_crawler` | Keeps S3 partition metadata current |
| [main.tf](main.tf) — `aws_athena_workgroup` | Query engine configuration |
