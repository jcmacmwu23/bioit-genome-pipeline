"""
Lightweight API layer for the BioIT dashboard.

This handler is intended for API Gateway / Lambda and sits in front of the
existing genome processing pipeline. It exposes dashboard-friendly endpoints
for status, chromosome availability, and safe queue submission.
"""
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import unquote

import boto3


logger = logging.getLogger()
logger.setLevel(logging.INFO)


import time as _time

s3_client = boto3.client("s3")
sqs_client = boto3.client("sqs")
logs_client = boto3.client("logs")
sts_client = boto3.client("sts")
athena_client = boto3.client("athena")
batch_client = boto3.client("batch")
ddb_client = boto3.client("dynamodb")

CACHE_TABLE = os.environ.get("CACHE_TABLE", "genome-pipeline-cache")


def cache_get(key: str) -> Optional[Dict]:
    """Read a cached API response from DynamoDB. Returns parsed dict or None."""
    try:
        resp = ddb_client.get_item(TableName=CACHE_TABLE, Key={"pk": {"S": key}})
        item = resp.get("Item")
        if item and "data" in item:
            return json.loads(item["data"]["S"])
    except Exception as exc:
        logger.debug("DynamoDB cache miss or error for %s: %s", key, exc)
    return None


def cache_put(key: str, data: Dict, ttl_seconds: int = 3600) -> None:
    """Write an API response to DynamoDB with a TTL. Fire-and-forget."""
    try:
        ddb_client.put_item(
            TableName=CACHE_TABLE,
            Item={
                "pk": {"S": key},
                "data": {"S": json.dumps(data)},
                "ttl": {"N": str(int(_time.time()) + ttl_seconds)},
            },
        )
    except Exception as exc:
        logger.warning("DynamoDB cache write failed for %s: %s", key, exc)


def cache_delete(keys: List[str]) -> None:
    """Delete one or more cache entries (e.g. after new analysis data lands)."""
    for key in keys:
        try:
            ddb_client.delete_item(TableName=CACHE_TABLE, Key={"pk": {"S": key}})
        except Exception as exc:
            logger.warning("DynamoDB cache delete failed for %s: %s", key, exc)


DATASET_PREFIXES = {
    "sequences": "genome_data",
    "patterns": "pattern_data",
    "regions": "region_data",
    "annotations": "gene_annotation_data",
}

HUMAN_CHROMOSOMES = [str(i) for i in range(1, 23)] + ["X", "Y"]

HUMAN_CHROMOSOME_ACCESSIONS = {
    "1": "NC_000001.11",
    "2": "NC_000002.12",
    "3": "NC_000003.12",
    "4": "NC_000004.12",
    "5": "NC_000005.10",
    "6": "NC_000006.12",
    "7": "NC_000007.14",
    "8": "NC_000008.11",
    "9": "NC_000009.12",
    "10": "NC_000010.11",
    "11": "NC_000011.10",
    "12": "NC_000012.12",
    "13": "NC_000013.11",
    "14": "NC_000014.9",
    "15": "NC_000015.10",
    "16": "NC_000016.10",
    "17": "NC_000017.11",
    "18": "NC_000018.10",
    "19": "NC_000019.10",
    "20": "NC_000020.11",
    "21": "NC_000021.9",
    "22": "NC_000022.11",
    "X": "NC_000023.11",
    "Y": "NC_000024.10",
}
HUMAN_CHROMOSOME_LENGTHS = {
    "1": 248956422,
    "2": 242193529,
    "3": 198295559,
    "4": 190214555,
    "5": 181538259,
    "6": 170805979,
    "7": 159345973,
    "8": 145138636,
    "9": 138394717,
    "10": 133797422,
    "11": 135086622,
    "12": 133275309,
    "13": 114364328,
    "14": 107043718,
    "15": 101991189,
    "16": 90338345,
    "17": 83257441,
    "18": 80373285,
    "19": 58617616,
    "20": 64444167,
    "21": 46709983,
    "22": 50818468,
    "X": 156040895,
    "Y": 57227415,
}
ALLOWED_ANALYSIS_MODES = {"full", "sequence_only"}
ALLOWED_JOB_TYPES = {"sequence_analysis", "gene_annotations"}
FULL_ANALYSIS_MAX_BASES = int(os.environ.get("FULL_ANALYSIS_MAX_BASES", "60000000"))


def json_response(status_code: int, body: Dict[str, Any], cache_seconds: int = 0) -> Dict[str, Any]:
    cache_control = (
        f"public, max-age={cache_seconds}, stale-while-revalidate=60"
        if cache_seconds > 0
        else "no-cache, no-store, must-revalidate"
    )
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Cache-Control": cache_control,
        },
        "body": json.dumps(body),
    }


def parse_body(event: Dict[str, Any]) -> Dict[str, Any]:
    body = event.get("body")
    if not body:
        return {}
    if event.get("isBase64Encoded"):
        import base64

        body = base64.b64decode(body).decode("utf-8")
    if isinstance(body, str):
        return json.loads(body)
    return body


def resolve_account_id() -> str:
    return os.environ.get("AWS_ACCOUNT_ID") or sts_client.get_caller_identity()["Account"]


def resolve_region() -> str:
    return os.environ.get("AWS_REGION", "us-east-1")


def resolve_project_name() -> str:
    return os.environ.get("PROJECT_NAME", "genome-pipeline")


def resolve_output_bucket() -> str:
    bucket = os.environ.get("OUTPUT_BUCKET")
    if bucket:
        return bucket
    return f"{resolve_project_name()}-output-{resolve_account_id()}"


def resolve_queue_url() -> str:
    queue_url = os.environ.get("QUEUE_URL")
    if queue_url:
        return queue_url
    return (
        f"https://sqs.{resolve_region()}.amazonaws.com/"
        f"{resolve_account_id()}/{resolve_project_name()}-queue"
    )


def resolve_dlq_url() -> str:
    queue_url = os.environ.get("DLQ_URL")
    if queue_url:
        return queue_url
    return (
        f"https://sqs.{resolve_region()}.amazonaws.com/"
        f"{resolve_account_id()}/{resolve_project_name()}-dlq"
    )


def resolve_log_group() -> str:
    return os.environ.get(
        "PIPELINE_LOG_GROUP",
        f"/aws/lambda/{resolve_project_name()}-processor",
    )


def resolve_athena_database() -> str:
    return os.environ.get("ATHENA_DATABASE", "genome_pipeline_db")


def resolve_athena_workgroup() -> str:
    return os.environ.get("ATHENA_WORKGROUP", "genome-pipeline-workgroup")


def resolve_athena_results_bucket() -> str:
    bucket = os.environ.get("ATHENA_RESULTS_BUCKET")
    if bucket:
        return bucket
    return f"{resolve_project_name()}-athena-results-{resolve_account_id()}"


def resolve_batch_job_queue() -> Optional[str]:
    return os.environ.get("BATCH_JOB_QUEUE")


def resolve_batch_job_definition() -> Optional[str]:
    return os.environ.get("BATCH_JOB_DEFINITION")


def batch_full_analysis_enabled() -> bool:
    return bool(resolve_batch_job_queue() and resolve_batch_job_definition())


def sqs_depth(queue_url: str) -> int:
    response = sqs_client.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=["ApproximateNumberOfMessages"],
    )
    attributes = response.get("Attributes", {})
    return int(attributes.get("ApproximateNumberOfMessages", "0"))


def dataset_prefix(dataset_key: str, source: str = "ncbi", species: str = "homo_sapiens") -> str:
    return f"{DATASET_PREFIXES[dataset_key]}/source={source}/species={species}/"


def list_common_prefixes(bucket: str, prefix: str) -> List[str]:
    paginator = s3_client.get_paginator("list_objects_v2")
    results: List[str] = []

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter="/"):
        for item in page.get("CommonPrefixes", []):
            candidate = item.get("Prefix")
            if candidate:
                results.append(candidate)

    return results


def chromosome_set_for_dataset(bucket: str, dataset_key: str) -> Set[str]:
    prefixes = list_common_prefixes(bucket, dataset_prefix(dataset_key))
    chromosomes: Set[str] = set()
    for item in prefixes:
        fragment = item.rstrip("/").split("/")[-1]
        if fragment.startswith("chr="):
            chromosomes.add(fragment.split("=", 1)[1])
    return chromosomes


def latest_object_for_dataset(
    bucket: str,
    dataset_key: str,
    chromosome: Optional[str] = None,
    source: str = "ncbi",
    species: str = "homo_sapiens",
) -> Optional[Dict[str, Any]]:
    prefix = dataset_prefix(dataset_key, source=source, species=species)
    if chromosome:
        prefix = f"{prefix}chr={chromosome}/"

    paginator = s3_client.get_paginator("list_objects_v2")
    latest: Optional[Dict[str, Any]] = None

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if latest is None or obj["LastModified"] > latest["LastModified"]:
                latest = obj

    return latest


def format_timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def recent_failures(limit: int = 5) -> List[Dict[str, Any]]:
    log_group = resolve_log_group()
    start_time = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp() * 1000)

    try:
        response = logs_client.filter_log_events(
            logGroupName=log_group,
            startTime=start_time,
            limit=50,
        )
    except Exception as exc:
        logger.warning("Unable to read recent failures from CloudWatch: %s", exc)
        return []

    matches: List[Dict[str, Any]] = []
    for event in response.get("events", []):
        message = event.get("message", "")
        if not any(term in message for term in ("Runtime.OutOfMemory", "ERROR", "Task timed out")):
            continue
        matches.append(
            {
                "timestamp": format_timestamp(
                    datetime.fromtimestamp(event["timestamp"] / 1000, tz=timezone.utc)
                ),
                "message": message.strip(),
            }
        )
        if len(matches) >= limit:
            break

    return matches


def athena_value(cell: Dict[str, Any]) -> Optional[str]:
    return cell.get("VarCharValue")


def run_athena_query(query: str, timeout_seconds: int = 20) -> List[Dict[str, Optional[str]]]:
    response = athena_client.start_query_execution(
        QueryString=query,
        QueryExecutionContext={"Database": resolve_athena_database()},
        WorkGroup=resolve_athena_workgroup(),
        ResultConfiguration={
            "OutputLocation": f"s3://{resolve_athena_results_bucket()}/query-results/"
        },
    )
    execution_id = response["QueryExecutionId"]
    deadline = datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)

    while datetime.now(timezone.utc) < deadline:
        execution = athena_client.get_query_execution(QueryExecutionId=execution_id)
        state = execution["QueryExecution"]["Status"]["State"]
        if state == "SUCCEEDED":
            break
        if state in {"FAILED", "CANCELLED"}:
            reason = execution["QueryExecution"]["Status"].get("StateChangeReason", state)
            raise RuntimeError(f"Athena query failed: {reason}")
    else:
        raise TimeoutError("Athena query timed out")

    paginator = athena_client.get_paginator("get_query_results")
    rows: List[Dict[str, Optional[str]]] = []
    headers: List[str] = []

    for page in paginator.paginate(QueryExecutionId=execution_id):
        for row in page["ResultSet"]["Rows"]:
            values = [athena_value(cell) for cell in row.get("Data", [])]
            if not headers:
                headers = [value or f"col_{index}" for index, value in enumerate(values)]
                continue
            rows.append({headers[index]: values[index] if index < len(values) else None for index in range(len(headers))})

    return rows


def safe_chromosome(chromosome: str) -> str:
    normalized = chromosome.upper() if chromosome.lower() in {"x", "y"} else chromosome
    if normalized not in HUMAN_CHROMOSOMES:
        raise ValueError("invalid chromosome")
    return normalized


def metric_int(value: Optional[str]) -> Optional[int]:
    if value in (None, ""):
        return None
    return int(value)


def resolve_sequence_length(chromosome: str, sequence_length: Optional[str]) -> Optional[str]:
    direct_value = metric_int(sequence_length)
    if direct_value is not None:
        return str(direct_value)

    fallback = HUMAN_CHROMOSOME_LENGTHS.get(safe_chromosome(chromosome))
    return str(fallback) if fallback is not None else None


def summarize_full_analysis_support(
    sequence_ready: bool,
    patterns_ready: bool,
    regions_ready: bool,
    sequence_length: Optional[str],
) -> Dict[str, Any]:
    length_value = metric_int(sequence_length)

    if patterns_ready and regions_ready:
        return {
            "eligible": False,
            "status": "complete",
            "reason": "Full analysis outputs already exist for this chromosome.",
            "max_bases": FULL_ANALYSIS_MAX_BASES,
            "backend": "complete",
        }

    if not sequence_ready:
        return {
            "eligible": False,
            "status": "sequence_pending",
            "reason": "Sequence data must land before full analysis can run.",
            "max_bases": FULL_ANALYSIS_MAX_BASES,
            "backend": "none",
        }

    if length_value is None:
        return {
            "eligible": False,
            "status": "size_unknown",
            "reason": "Sequence length is not available yet, so Lambda eligibility cannot be confirmed.",
            "max_bases": FULL_ANALYSIS_MAX_BASES,
            "backend": "none",
        }

    if length_value > FULL_ANALYSIS_MAX_BASES:
        if batch_full_analysis_enabled():
            return {
                "eligible": True,
                "status": "batch_required",
                "reason": (
                    f"Chromosome is {length_value:,} bp, above the Lambda full-analysis "
                    f"limit of {FULL_ANALYSIS_MAX_BASES:,} bp. This job will run on AWS Batch on Fargate."
                ),
                "max_bases": FULL_ANALYSIS_MAX_BASES,
                "backend": "batch",
            }
        return {
            "eligible": False,
            "status": "too_large",
            "reason": (
                f"Chromosome is {length_value:,} bp, above the current Lambda full-analysis "
                f"limit of {FULL_ANALYSIS_MAX_BASES:,} bp."
            ),
            "max_bases": FULL_ANALYSIS_MAX_BASES,
            "backend": "none",
        }

    return {
        "eligible": True,
        "status": "eligible",
        "reason": (
            f"Chromosome is within the current Lambda full-analysis limit "
            f"({FULL_ANALYSIS_MAX_BASES:,} bp)."
        ),
        "max_bases": FULL_ANALYSIS_MAX_BASES,
        "backend": "lambda",
    }


def get_chromosome_metrics(chromosome: str) -> Dict[str, Optional[str]]:
    chromosome = safe_chromosome(chromosome)
    query = f"""
    SELECT
      CAST(MAX(length) AS bigint) AS sequence_length,
      CAST(ROUND(AVG(gc_content), 2) AS double) AS avg_gc_content
    FROM genome_sequences
    WHERE source = 'ncbi'
      AND species = 'homo_sapiens'
      AND chr = '{chromosome}'
    """
    rows = run_athena_query(query)
    return rows[0] if rows else {}


def get_all_chromosome_metrics() -> Dict[str, Dict[str, Optional[str]]]:
    query = """
    SELECT
      chr,
      CAST(MAX(length) AS bigint) AS sequence_length,
      CAST(ROUND(AVG(gc_content), 2) AS double) AS avg_gc_content
    FROM genome_sequences
    WHERE source = 'ncbi'
      AND species = 'homo_sapiens'
    GROUP BY chr
    """
    rows = run_athena_query(query)
    return {
        safe_chromosome(str(row["chr"])): row
        for row in rows
        if row.get("chr")
    }


def get_chromosome_pattern_rows(chromosome: str, limit: int = 10) -> List[Dict[str, Optional[str]]]:
    chromosome = safe_chromosome(chromosome)
    query = f"""
    SELECT
      pattern_name,
      pattern_type,
      CAST(COUNT(*) AS bigint) AS hit_count
    FROM sequence_patterns
    WHERE source = 'ncbi'
      AND species = 'homo_sapiens'
      AND chr = '{chromosome}'
    GROUP BY pattern_name, pattern_type
    ORDER BY hit_count DESC, pattern_name ASC
    LIMIT {int(limit)}
    """
    return run_athena_query(query)


def get_chromosome_region_rows(chromosome: str, limit: int = 2000) -> List[Dict[str, Optional[str]]]:
    chromosome = safe_chromosome(chromosome)
    query = f"""
    SELECT DISTINCT
      CAST(window_start AS bigint) AS window_start,
      CAST(window_end AS bigint) AS window_end,
      CAST(ROUND(gc_content, 2) AS double) AS gc_content,
      CAST(orf_count AS bigint) AS orf_count,
      CAST(motif_hits AS bigint) AS motif_hits,
      CAST(repeat_bases AS bigint) AS repeat_bases
    FROM sequence_regions
    WHERE source = 'ncbi'
      AND species = 'homo_sapiens'
      AND chr = '{chromosome}'
    ORDER BY window_start ASC
    LIMIT {int(limit)}
    """
    return run_athena_query(query)


def get_chromosome_orf_positions(
    chromosome: str,
    region_start: Optional[int] = None,
    region_end: Optional[int] = None,
    limit: int = 2000,
) -> List[Dict[str, Optional[str]]]:
    chromosome = safe_chromosome(chromosome)
    filters = [
        "source = 'ncbi'",
        "species = 'homo_sapiens'",
        f"chr = '{chromosome}'",
        "pattern_type = 'orf'",
    ]
    if region_start is not None:
        filters.append(f'"end" >= {int(region_start)}')
    if region_end is not None:
        filters.append(f'"start" <= {int(region_end)}')

    query = f"""
    SELECT
      "start"  AS pos_start,
      "end"    AS pos_end,
      length   AS hit_length,
      strand,
      score
    FROM sequence_patterns
    WHERE {' AND '.join(filters)}
    LIMIT {int(limit)}
    """
    return run_athena_query(query, timeout_seconds=55)


def get_chromosome_cpg_positions(
    chromosome: str,
    region_start: Optional[int] = None,
    region_end: Optional[int] = None,
    limit: int = 5000,
) -> List[Dict[str, Optional[str]]]:
    chromosome = safe_chromosome(chromosome)
    filters = [
        "source = 'ncbi'",
        "species = 'homo_sapiens'",
        f"chr = '{chromosome}'",
        "pattern_type = 'motif'",
        "LOWER(pattern_name) LIKE '%cpg%'",
    ]
    if region_start is not None:
        filters.append(f'"end" >= {int(region_start)}')
    if region_end is not None:
        filters.append(f'"start" <= {int(region_end)}')

    query = f"""
    SELECT
      "start" AS pos_start,
      "end"   AS pos_end,
      length  AS hit_length
    FROM sequence_patterns
    WHERE {' AND '.join(filters)}
    LIMIT {int(limit)}
    """
    return run_athena_query(query, timeout_seconds=55)


def get_chromosome_annotation_rows(
    chromosome: str,
    start: Optional[int] = None,
    end: Optional[int] = None,
    limit: int = 25,
) -> List[Dict[str, Optional[str]]]:
    chromosome = safe_chromosome(chromosome)
    filters = [
        "source = 'ensembl'",
        "species = 'homo_sapiens'",
        f"chr = '{chromosome}'",
    ]
    if start is not None:
        filters.append(f'"end" >= {int(start)}')
    if end is not None:
        filters.append(f'"start" <= {int(end)}')

    query = f"""
    SELECT
      gene_id,
      gene_symbol,
      gene_name,
      feature_type,
      biotype,
      CAST("start" AS bigint) AS start,
      CAST("end" AS bigint) AS end,
      CAST(length AS bigint) AS length,
      strand,
      assembly_name,
      source_name
    FROM gene_annotations
    WHERE {' AND '.join(filters)}
    ORDER BY "start" ASC, gene_symbol ASC
    LIMIT {int(limit)}
    """
    return run_athena_query(query)


def build_overview() -> Dict[str, Any]:
    bucket = resolve_output_bucket()
    sequence_chromosomes = chromosome_set_for_dataset(bucket, "sequences")
    pattern_chromosomes = chromosome_set_for_dataset(bucket, "patterns")
    region_chromosomes = chromosome_set_for_dataset(bucket, "regions")
    annotation_chromosomes = chromosome_set_for_dataset(bucket, "annotations")
    latest_sequence = latest_object_for_dataset(bucket, "sequences")

    return {
        "generated_at": format_timestamp(datetime.now(timezone.utc)),
        "pipeline": {
            "project_name": resolve_project_name(),
            "region": resolve_region(),
            "output_bucket": bucket,
        },
        "queue": {
            "depth": sqs_depth(resolve_queue_url()),
            "dlq_depth": sqs_depth(resolve_dlq_url()),
        },
        "datasets": {
            "sequence_ready_count": len(sequence_chromosomes),
            "pattern_ready_count": len(pattern_chromosomes),
            "region_ready_count": len(region_chromosomes),
            "annotation_ready_count": len(annotation_chromosomes),
            "ready_union_count": len(
                sequence_chromosomes | pattern_chromosomes | region_chromosomes | annotation_chromosomes
            ),
            "latest_sequence_output": {
                "key": latest_sequence["Key"] if latest_sequence else None,
                "last_modified": format_timestamp(
                    latest_sequence["LastModified"] if latest_sequence else None
                ),
            },
        },
        "recent_failures": recent_failures(),
    }


def build_chromosome_inventory() -> List[Dict[str, Any]]:
    bucket = resolve_output_bucket()
    sequence_ready = chromosome_set_for_dataset(bucket, "sequences")
    pattern_ready = chromosome_set_for_dataset(bucket, "patterns")
    region_ready = chromosome_set_for_dataset(bucket, "regions")
    annotation_ready = chromosome_set_for_dataset(bucket, "annotations")
    try:
        metrics_by_chromosome = get_all_chromosome_metrics()
    except Exception as exc:
        logger.warning("Athena metrics unavailable, building inventory from S3 only: %s", exc)
        metrics_by_chromosome = {}

    items = []
    for chromosome in HUMAN_CHROMOSOMES:
        latest = latest_object_for_dataset(bucket, "sequences", chromosome=chromosome)
        metrics = metrics_by_chromosome.get(chromosome, {})
        sequence_length = resolve_sequence_length(chromosome, metrics.get("sequence_length"))
        support = summarize_full_analysis_support(
            sequence_ready=chromosome in sequence_ready,
            patterns_ready=chromosome in pattern_ready,
            regions_ready=chromosome in region_ready,
            sequence_length=sequence_length,
        )
        items.append(
            {
                "chromosome": chromosome,
                "sequence_ready": chromosome in sequence_ready,
                "patterns_ready": chromosome in pattern_ready,
                "regions_ready": chromosome in region_ready,
                "annotations_ready": chromosome in annotation_ready,
                "latest_output_at": format_timestamp(
                    latest["LastModified"] if latest else None
                ),
                "latest_key": latest["Key"] if latest else None,
                "sequence_length": sequence_length,
                "avg_gc_content": metrics.get("avg_gc_content"),
                "full_analysis_eligible": support["eligible"],
                "full_analysis_status": support["status"],
                "full_analysis_reason": support["reason"],
                "full_analysis_max_bases": support["max_bases"],
                "full_analysis_backend": support["backend"],
            }
        )
    return items


def build_chromosome_summary(chromosome: str) -> Dict[str, Any]:
    chromosome = safe_chromosome(chromosome)
    inventory = {item["chromosome"]: item for item in build_chromosome_inventory()}
    item = inventory.get(chromosome)
    if not item:
        raise KeyError(chromosome)

    bucket = resolve_output_bucket()
    patterns = latest_object_for_dataset(bucket, "patterns", chromosome=chromosome)
    regions = latest_object_for_dataset(bucket, "regions", chromosome=chromosome)
    annotations = latest_object_for_dataset(bucket, "annotations", chromosome=chromosome)
    metrics = get_chromosome_metrics(chromosome)
    sequence_length = resolve_sequence_length(chromosome, metrics.get("sequence_length"))
    pattern_rows = get_chromosome_pattern_rows(chromosome, limit=5)
    region_rows = get_chromosome_region_rows(chromosome, limit=5)
    top_pattern = pattern_rows[0] if pattern_rows else None
    support = summarize_full_analysis_support(
        sequence_ready=item["sequence_ready"],
        patterns_ready=item["patterns_ready"],
        regions_ready=item["regions_ready"],
        sequence_length=sequence_length,
    )

    return {
        "chromosome": chromosome,
        "sequence_ready": item["sequence_ready"],
        "patterns_ready": item["patterns_ready"],
        "regions_ready": item["regions_ready"],
        "latest_sequence_output": item["latest_key"],
        "latest_pattern_output": patterns["Key"] if patterns else None,
        "latest_region_output": regions["Key"] if regions else None,
        "latest_annotation_output": annotations["Key"] if annotations else None,
        "latest_output_at": item["latest_output_at"],
        "annotations_ready": item["annotations_ready"],
        "sequence_length": sequence_length,
        "avg_gc_content": metrics.get("avg_gc_content"),
        "full_analysis_eligible": support["eligible"],
        "full_analysis_status": support["status"],
        "full_analysis_reason": support["reason"],
        "full_analysis_max_bases": support["max_bases"],
        "full_analysis_backend": support["backend"],
        "pattern_hit_count": str(sum(int(row.get("hit_count") or "0") for row in pattern_rows)),
        "top_pattern": top_pattern,
        "orf_count": str(sum(int(row.get("orf_count") or "0") for row in region_rows)),
    }


def validate_job_payload(payload: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    job_type = str(payload.get("job_type", "sequence_analysis")).strip().lower()
    if job_type not in ALLOWED_JOB_TYPES:
        return False, "job_type must be one of: sequence_analysis, gene_annotations"

    source = payload.get("source")
    if source not in {"ncbi", "ensembl", "url"}:
        return False, "source must be one of: ncbi, ensembl, url"

    if job_type == "gene_annotations":
        if source != "ensembl":
            return False, "gene_annotations jobs currently require source=ensembl"
        if not payload.get("chromosome"):
            return False, "gene_annotations jobs require chromosome"
        return True, None

    analysis_mode = str(payload.get("analysis_mode", "full")).strip().lower()
    if analysis_mode not in ALLOWED_ANALYSIS_MODES:
        return False, "analysis_mode must be one of: full, sequence_only"

    if source == "ncbi" and not payload.get("accession_id"):
        return False, "ncbi jobs require accession_id"
    if source == "ensembl" and not payload.get("chromosome"):
        return False, "ensembl jobs require chromosome"
    if source == "url" and not payload.get("url"):
        return False, "url jobs require url"
    return True, None


def enqueue_job(payload: Dict[str, Any]) -> Dict[str, Any]:
    ok, error = validate_job_payload(payload)
    if not ok:
        raise ValueError(error)

    normalized = dict(payload)
    normalized.setdefault("species", "homo_sapiens")
    normalized["job_type"] = str(normalized.get("job_type", "sequence_analysis")).strip().lower()
    if normalized["job_type"] == "sequence_analysis":
        normalized["analysis_mode"] = str(normalized.get("analysis_mode", "full")).strip().lower()
    if normalized.get("source") == "ncbi" and normalized.get("chromosome") and normalized["job_type"] == "sequence_analysis":
        normalized.setdefault("output_prefix", f"human_genome/chr{normalized['chromosome']}")
    normalized["submitted_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    response = sqs_client.send_message(
        QueueUrl=resolve_queue_url(),
        MessageBody=json.dumps(normalized),
    )

    return {
        "message_id": response.get("MessageId"),
        "job": normalized,
    }


CHROMOSOME_EXPECTED_MINUTES: Dict[str, int] = {
    "1": 55, "2": 53, "3": 44, "4": 42, "5": 40, "6": 38,
    "7": 35, "8": 32, "9": 30, "10": 29, "11": 30, "12": 29,
    "13": 25, "14": 23, "15": 22, "16": 20, "17": 18, "18": 18,
    "19": 13, "20": 14, "21": 10, "22": 11, "X": 34, "Y": 13,
}


def get_chromosome_batch_status(chromosome: str) -> Dict[str, Any]:
    chromosome = safe_chromosome(chromosome)
    job_queue = resolve_batch_job_queue()
    if not job_queue:
        return {"status": "not_configured"}

    prefix = f"{resolve_project_name()}-chr{chromosome.lower()}-"
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    # Search active states first, then recently finished
    search_order = ["RUNNING", "STARTING", "RUNNABLE", "PENDING", "SUBMITTED", "SUCCEEDED", "FAILED"]
    latest: Optional[Dict] = None

    for state in search_order:
        try:
            resp = batch_client.list_jobs(jobQueue=job_queue, jobStatus=state)
        except Exception:
            continue
        for job in resp.get("jobSummaryList", []):
            if not job.get("jobName", "").startswith(prefix):
                continue
            if latest is None or job.get("createdAt", 0) > latest.get("createdAt", 0):
                latest = job
        if latest and latest.get("status") in ("RUNNING", "STARTING", "RUNNABLE", "PENDING", "SUBMITTED"):
            break

    if not latest:
        return {"status": "no_job"}

    status = latest.get("status", "UNKNOWN")
    started_at = latest.get("startedAt")
    elapsed_minutes: Optional[float] = None
    if started_at:
        elapsed_minutes = round((now_ms - started_at) / 60000, 1)

    expected = CHROMOSOME_EXPECTED_MINUTES.get(chromosome, 35)
    progress_pct: Optional[int] = None
    if status == "SUCCEEDED":
        progress_pct = 100
    elif status in ("SUBMITTED", "PENDING", "RUNNABLE"):
        progress_pct = 0
    elif status == "STARTING":
        progress_pct = 2
    elif status == "RUNNING" and elapsed_minutes is not None:
        progress_pct = min(95, round((elapsed_minutes / expected) * 100))
    elif status == "FAILED":
        progress_pct = None

    return {
        "status": status,
        "job_id": latest.get("jobId"),
        "job_name": latest.get("jobName"),
        "elapsed_minutes": elapsed_minutes,
        "progress_pct": progress_pct,
        "expected_minutes": expected,
    }


def submit_batch_job(payload: Dict[str, Any]) -> Dict[str, Any]:
    ok, error = validate_job_payload(payload)
    if not ok:
        raise ValueError(error)

    job_queue = resolve_batch_job_queue()
    job_definition = resolve_batch_job_definition()
    if not job_queue or not job_definition:
        raise ValueError("AWS Batch is not configured for full analysis jobs")

    normalized = dict(payload)
    normalized.setdefault("species", "homo_sapiens")
    normalized["job_type"] = str(normalized.get("job_type", "sequence_analysis")).strip().lower()
    normalized["analysis_mode"] = str(normalized.get("analysis_mode", "full")).strip().lower()
    normalized["submitted_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    chromosome = normalized.get("chromosome", "unknown")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    job_name = f"{resolve_project_name()}-chr{chromosome.lower()}-{timestamp}"

    patch_bucket = resolve_output_bucket()
    patch_key = "batch-patches/lambda_handler.py"
    patch_cmd = (
        f"python -c \""
        f"import boto3; boto3.client('s3').download_file('{patch_bucket}','{patch_key}','/app/lambda_handler.py'); "
        f"print('patch applied')"
        f"\" && python /app/batch_entrypoint.py"
    )

    response = batch_client.submit_job(
        jobName=job_name,
        jobQueue=job_queue,
        jobDefinition=job_definition,
        containerOverrides={
            "command": ["bash", "-c", patch_cmd],
            "environment": [
                {"name": "JOB_PAYLOAD", "value": json.dumps(normalized)},
            ],
        },
    )

    return {
        "job_id": response.get("jobId"),
        "job_name": response.get("jobName"),
        "job_queue": job_queue,
        "job_definition": job_definition,
        "job": normalized,
    }


def submit_human_reference_batch(
    species: str = "homo_sapiens",
    analysis_mode: str = "sequence_only"
) -> Dict[str, Any]:
    submissions = []
    for chromosome in HUMAN_CHROMOSOMES:
        payload = {
            "source": "ncbi",
            "accession_id": HUMAN_CHROMOSOME_ACCESSIONS[chromosome],
            "chromosome": chromosome,
            "species": species,
            "output_prefix": f"human_genome/chr{chromosome}",
            "analysis_mode": analysis_mode,
        }
        submissions.append(enqueue_job(payload))

    return {
        "submitted_count": len(submissions),
        "items": submissions,
    }


def submit_single_chromosome_analysis(chromosome: str, species: str = "homo_sapiens") -> Dict[str, Any]:
    chromosome = safe_chromosome(chromosome)
    inventory = {item["chromosome"]: item for item in build_chromosome_inventory()}
    item = inventory.get(chromosome)
    if not item or not item["sequence_ready"]:
        raise ValueError(f"chromosome {chromosome} must have sequence data before full analysis")
    if item["patterns_ready"] and item["regions_ready"]:
        raise ValueError(f"chromosome {chromosome} already has full analysis outputs")
    if not item.get("full_analysis_eligible"):
        raise ValueError(item.get("full_analysis_reason") or f"chromosome {chromosome} is not eligible for Lambda full analysis")

    payload = {
        "source": "ncbi",
        "accession_id": HUMAN_CHROMOSOME_ACCESSIONS[chromosome],
        "chromosome": chromosome,
        "species": species,
        "output_prefix": f"human_genome/chr{chromosome}",
        "analysis_mode": "full",
    }

    # Reuse existing raw FASTA from S3 if available — avoids re-downloading from NCBI
    bucket = resolve_output_bucket()
    raw_prefix = f"raw_data/source=ncbi/species={species}/chr={chromosome}/"
    try:
        paginator = s3_client.get_paginator("list_objects_v2")
        latest_raw = None
        for page in paginator.paginate(Bucket=bucket, Prefix=raw_prefix):
            for obj in page.get("Contents", []):
                if obj["Key"].endswith(".fasta") or obj["Key"].endswith(".fa"):
                    if latest_raw is None or obj["LastModified"] > latest_raw["LastModified"]:
                        latest_raw = obj
        if latest_raw:
            payload["s3_raw_key"] = latest_raw["Key"]
            logger.info(f"Reusing raw file for chr{chromosome}: {latest_raw['Key']}")
    except Exception as exc:
        logger.warning("Could not look up existing raw file: %s", exc)

    backend = item.get("full_analysis_backend") or "lambda"
    if backend == "batch":
        submission = submit_batch_job(payload)
    else:
        submission = enqueue_job(payload)
    return {
        "submitted": True,
        "chromosome": chromosome,
        "analysis_mode": "full",
        "analysis_backend": backend,
        "item": submission,
    }


def submit_gene_annotation_sync(chromosome: str, species: str = "homo_sapiens") -> Dict[str, Any]:
    chromosome = safe_chromosome(chromosome)
    payload = {
        "job_type": "gene_annotations",
        "source": "ensembl",
        "chromosome": chromosome,
        "species": species,
        "output_prefix": f"annotations/chr{chromosome}",
    }
    submission = enqueue_job(payload)
    return {
        "submitted": True,
        "chromosome": chromosome,
        "job_type": "gene_annotations",
        "item": submission,
    }


def route(event: Dict[str, Any]) -> Dict[str, Any]:
    method = event.get("requestContext", {}).get("http", {}).get("method") or event.get(
        "httpMethod", "GET"
    )
    path = event.get("rawPath") or event.get("path") or "/"
    path = unquote(path)

    if method == "OPTIONS":
        return json_response(200, {"ok": True})

    if method == "GET" and path.endswith("/api/status/overview"):
        cached = cache_get("OVERVIEW")
        if cached:
            return json_response(200, cached, cache_seconds=30)
        data = build_overview()
        cache_put("OVERVIEW", data, ttl_seconds=30)
        return json_response(200, data, cache_seconds=30)

    if method == "GET" and path.endswith("/api/chromosomes"):
        cached = cache_get("CHROMOSOMES")
        if cached:
            return json_response(200, cached, cache_seconds=120)
        data = {"items": build_chromosome_inventory()}
        cache_put("CHROMOSOMES", data, ttl_seconds=120)
        return json_response(200, data, cache_seconds=120)

    if method == "GET" and "/api/chromosomes/" in path and path.endswith("/summary"):
        chromosome = path.rstrip("/").split("/")[-2]
        ck = f"CHR#{safe_chromosome(chromosome)}#SUMMARY"
        cached = cache_get(ck)
        if cached:
            return json_response(200, cached, cache_seconds=300)
        data = build_chromosome_summary(chromosome)
        cache_put(ck, data, ttl_seconds=300)
        return json_response(200, data, cache_seconds=300)

    if method == "GET" and "/api/chromosomes/" in path and path.endswith("/patterns"):
        chromosome = path.rstrip("/").split("/")[-2]
        ck = f"CHR#{safe_chromosome(chromosome)}#PATTERNS"
        cached = cache_get(ck)
        if cached:
            return json_response(200, cached, cache_seconds=3600)
        data = {
            "chromosome": safe_chromosome(chromosome),
            "items": get_chromosome_pattern_rows(chromosome),
        }
        cache_put(ck, data, ttl_seconds=3600)
        return json_response(200, data, cache_seconds=3600)

    if method == "GET" and "/api/chromosomes/" in path and path.endswith("/regions"):
        chromosome = path.rstrip("/").split("/")[-2]
        ck = f"CHR#{safe_chromosome(chromosome)}#REGIONS"
        cached = cache_get(ck)
        if cached:
            return json_response(200, cached, cache_seconds=3600)
        data = {
            "chromosome": safe_chromosome(chromosome),
            "items": get_chromosome_region_rows(chromosome),
        }
        cache_put(ck, data, ttl_seconds=3600)
        return json_response(200, data, cache_seconds=3600)

    if method == "GET" and "/api/chromosomes/" in path and path.endswith("/orfs"):
        chromosome = path.rstrip("/").split("/")[-2]
        query = event.get("queryStringParameters") or {}
        region_start = query.get("start")
        region_end = query.get("end")
        return json_response(
            200,
            {
                "chromosome": safe_chromosome(chromosome),
                "items": get_chromosome_orf_positions(
                    chromosome,
                    region_start=int(region_start) if region_start not in (None, "") else None,
                    region_end=int(region_end) if region_end not in (None, "") else None,
                ),
            },
        )

    if method == "GET" and "/api/chromosomes/" in path and path.endswith("/cpg"):
        chromosome = path.rstrip("/").split("/")[-2]
        query = event.get("queryStringParameters") or {}
        region_start = query.get("start")
        region_end = query.get("end")
        return json_response(
            200,
            {
                "chromosome": safe_chromosome(chromosome),
                "items": get_chromosome_cpg_positions(
                    chromosome,
                    region_start=int(region_start) if region_start not in (None, "") else None,
                    region_end=int(region_end) if region_end not in (None, "") else None,
                ),
            },
        )

    if method == "GET" and "/api/chromosomes/" in path and path.endswith("/annotations"):
        chromosome = path.rstrip("/").split("/")[-2]
        query = event.get("queryStringParameters") or {}
        start = query.get("start")
        end = query.get("end")
        return json_response(
            200,
            {
                "chromosome": safe_chromosome(chromosome),
                "items": get_chromosome_annotation_rows(
                    chromosome,
                    start=int(start) if start not in (None, "") else None,
                    end=int(end) if end not in (None, "") else None,
                ),
            },
        )

    if method == "GET" and "/api/chromosomes/" in path and path.endswith("/batch-status"):
        chromosome = path.rstrip("/").split("/")[-2]
        return json_response(200, get_chromosome_batch_status(chromosome))

    if method == "POST" and "/api/chromosomes/" in path and path.endswith("/analyze"):
        chromosome = path.rstrip("/").split("/")[-2]
        payload = parse_body(event)
        species = payload.get("species", "homo_sapiens")
        return json_response(202, submit_single_chromosome_analysis(chromosome, species=species))

    if method == "POST" and "/api/chromosomes/" in path and path.endswith("/annotations/sync"):
        chromosome = path.rstrip("/").split("/")[-3]
        payload = parse_body(event)
        species = payload.get("species", "homo_sapiens")
        return json_response(202, submit_gene_annotation_sync(chromosome, species=species))

    if method == "POST" and path.endswith("/api/jobs"):
        payload = parse_body(event)
        return json_response(202, enqueue_job(payload))

    if method == "POST" and path.endswith("/api/jobs/human-reference"):
        payload = parse_body(event)
        species = payload.get("species", "homo_sapiens")
        analysis_mode = payload.get("analysis_mode", "sequence_only")
        return json_response(
            202,
            submit_human_reference_batch(species=species, analysis_mode=analysis_mode)
        )

    return json_response(
        404,
        {
            "error": "route_not_found",
            "path": path,
            "method": method,
        },
    )


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    # EventBridge keep-warm ping — return immediately without touching Athena
    if event.get("source") == "warmup":
        logger.info("Keep-warm ping received")
        return {"statusCode": 200, "body": "warm"}
    try:
        return route(event)
    except ValueError as exc:
        return json_response(400, {"error": "bad_request", "message": str(exc)})
    except KeyError as exc:
        return json_response(404, {"error": "not_found", "message": str(exc)})
    except Exception as exc:
        logger.exception("Unhandled API error")
        return json_response(500, {"error": "internal_error", "message": str(exc)})
