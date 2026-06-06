"""
Lightweight API layer for the BioIT dashboard.

This handler is intended for API Gateway / Lambda and sits in front of the
existing genome processing pipeline. It exposes dashboard-friendly endpoints
for status, chromosome availability, and safe queue submission.
"""
import json
import logging
import os
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import unquote

import boto3
from operations_store import (
    build_processing_status,
    get_current_status,
    record_submission,
)


logger = logging.getLogger()
logger.setLevel(logging.INFO)


s3_client = boto3.client("s3")
sqs_client = boto3.client("sqs")
logs_client = boto3.client("logs")
sts_client = boto3.client("sts")
athena_client = boto3.client("athena")
batch_client = boto3.client("batch")


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
BATCH_ACTIVE_STATUSES = ("SUBMITTED", "PENDING", "RUNNABLE", "STARTING", "RUNNING")
BATCH_RECENT_STATUSES = BATCH_ACTIVE_STATUSES + ("SUCCEEDED", "FAILED")


def json_response(status_code: int, body: Dict[str, Any]) -> Dict[str, Any]:
    def _json_default(value: Any) -> Any:
        if isinstance(value, Decimal):
            if value % 1 == 0:
                return int(value)
            return float(value)
        raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")

    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(body, default=_json_default),
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
            "reason": "Sequence length is not available yet, so full-analysis routing cannot be confirmed.",
            "max_bases": FULL_ANALYSIS_MAX_BASES,
            "backend": "none",
        }

    if batch_full_analysis_enabled():
        return {
            "eligible": True,
            "status": "batch_required",
            "reason": (
                f"Chromosome is {length_value:,} bp. Full chromosome analysis runs on "
                "AWS Batch on Fargate for predictable memory and runtime."
            ),
            "max_bases": FULL_ANALYSIS_MAX_BASES,
            "backend": "batch",
        }

    return {
        "eligible": False,
        "status": "batch_unavailable",
        "reason": (
            "Full chromosome analysis is configured to run on AWS Batch, but the Batch "
            "job queue or job definition is not available right now."
        ),
        "max_bases": FULL_ANALYSIS_MAX_BASES,
        "backend": "none",
    }


def batch_job_expected_minutes(chromosome: str) -> Optional[int]:
    chromosome = safe_chromosome(chromosome)
    length_value = HUMAN_CHROMOSOME_LENGTHS.get(chromosome)
    if not length_value:
        return None
    return max(6, round(length_value / 4000000))


def batch_job_progress(status: str, elapsed_minutes: int, expected_minutes: Optional[int]) -> int:
    status = (status or "").upper()
    if status == "SUCCEEDED":
        return 100
    if status == "FAILED":
        return 0
    if status in {"SUBMITTED", "PENDING"}:
        return 5
    if status == "RUNNABLE":
        return 12
    if status == "STARTING":
        return 22
    if status == "RUNNING":
        if expected_minutes and expected_minutes > 0:
            return min(96, max(28, round((elapsed_minutes / expected_minutes) * 100)))
        return 40
    return 0


def normalize_batch_status(job: Dict[str, Any], chromosome: str) -> Dict[str, Any]:
    created_at_ms = int(job.get("createdAt") or 0)
    started_at_ms = int(job.get("startedAt") or 0)
    stopped_at_ms = int(job.get("stoppedAt") or 0)
    started_reference_ms = started_at_ms or created_at_ms
    finished_reference_ms = stopped_at_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
    elapsed_minutes = 0
    if started_reference_ms:
        elapsed_minutes = max(0, round((finished_reference_ms - started_reference_ms) / 60000))
    expected_minutes = batch_job_expected_minutes(chromosome)
    status = str(job.get("status") or "UNKNOWN").upper()

    return {
        "job_id": job.get("jobId"),
        "job_name": job.get("jobName"),
        "status": status,
        "status_reason": job.get("statusReason"),
        "created_at": format_timestamp(
            datetime.fromtimestamp(created_at_ms / 1000, tz=timezone.utc) if created_at_ms else None
        ),
        "started_at": format_timestamp(
            datetime.fromtimestamp(started_at_ms / 1000, tz=timezone.utc) if started_at_ms else None
        ),
        "stopped_at": format_timestamp(
            datetime.fromtimestamp(stopped_at_ms / 1000, tz=timezone.utc) if stopped_at_ms else None
        ),
        "elapsed_minutes": elapsed_minutes,
        "expected_minutes": expected_minutes,
        "progress_pct": batch_job_progress(status, elapsed_minutes, expected_minutes),
    }


def latest_batch_job_for_chromosome(chromosome: str) -> Optional[Dict[str, Any]]:
    chromosome = safe_chromosome(chromosome)
    job_queue = resolve_batch_job_queue()
    if not job_queue:
        return None

    matching_jobs: List[Dict[str, Any]] = []
    needle = f"chr{chromosome.lower()}-"

    for status in BATCH_RECENT_STATUSES:
        try:
            response = batch_client.list_jobs(
                jobQueue=job_queue,
                jobStatus=status,
                maxResults=100,
            )
        except Exception as exc:
            logger.warning("Unable to list AWS Batch jobs for %s: %s", chromosome, exc)
            return None

        for job in response.get("jobSummaryList", []):
            job_name = str(job.get("jobName") or "")
            if needle in job_name.lower():
                matching_jobs.append(job)

    if not matching_jobs:
        return None

    latest = max(matching_jobs, key=lambda item: int(item.get("createdAt") or 0))
    return normalize_batch_status(latest, chromosome)


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


def get_chromosome_pattern_summary(chromosome: str) -> Dict[str, Optional[str]]:
    chromosome = safe_chromosome(chromosome)
    query = f"""
    SELECT
      CAST(COUNT(*) AS bigint) AS pattern_hit_count
    FROM sequence_patterns
    WHERE source = 'ncbi'
      AND species = 'homo_sapiens'
      AND chr = '{chromosome}'
    """
    rows = run_athena_query(query)
    return rows[0] if rows else {}


def get_chromosome_region_rows(chromosome: str, limit: int = 12) -> List[Dict[str, Optional[str]]]:
    chromosome = safe_chromosome(chromosome)
    safe_limit = max(1, min(int(limit), 10000))
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
    LIMIT {safe_limit}
    """
    return run_athena_query(query)


def get_chromosome_region_summary(chromosome: str) -> Dict[str, Optional[str]]:
    chromosome = safe_chromosome(chromosome)
    query = f"""
    SELECT
      CAST(COALESCE(SUM(orf_count), 0) AS bigint) AS orf_count
    FROM sequence_regions
    WHERE source = 'ncbi'
      AND species = 'homo_sapiens'
      AND chr = '{chromosome}'
    """
    rows = run_athena_query(query)
    return rows[0] if rows else {}


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
    metrics_by_chromosome = get_all_chromosome_metrics()

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
    pattern_summary = get_chromosome_pattern_summary(chromosome)
    region_summary = get_chromosome_region_summary(chromosome)
    top_pattern = pattern_rows[0] if pattern_rows else None
    support = summarize_full_analysis_support(
        sequence_ready=item["sequence_ready"],
        patterns_ready=item["patterns_ready"],
        regions_ready=item["regions_ready"],
        sequence_length=sequence_length,
    )
    batch_status = latest_batch_job_for_chromosome(chromosome)
    current_status = get_current_status(chromosome)
    processing_status = build_processing_status(current_status, fallback_chromosome=chromosome)

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
        "batch_status": batch_status,
        "processing_status": processing_status,
        "pattern_hit_count": str(int(pattern_summary.get("pattern_hit_count") or "0")),
        "top_pattern": top_pattern,
        "orf_count": str(int(region_summary.get("orf_count") or "0")),
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
    record_submission(
        normalized,
        backend="lambda",
        message_id=response.get("MessageId"),
    )

    return {
        "message_id": response.get("MessageId"),
        "job": normalized,
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

    response = batch_client.submit_job(
        jobName=job_name,
        jobQueue=job_queue,
        jobDefinition=job_definition,
        containerOverrides={
            "environment": [
                {"name": "JOB_PAYLOAD", "value": json.dumps(normalized)},
            ]
        },
    )
    record_submission(
        normalized,
        backend="batch",
        batch_job_id=response.get("jobId"),
        batch_job_name=response.get("jobName"),
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
        return json_response(200, build_overview())

    if method == "GET" and path.endswith("/api/chromosomes"):
        return json_response(200, {"items": build_chromosome_inventory()})

    if method == "GET" and "/api/chromosomes/" in path and path.endswith("/summary"):
        chromosome = path.rstrip("/").split("/")[-2]
        return json_response(200, build_chromosome_summary(chromosome))

    if method == "GET" and "/api/chromosomes/" in path and path.endswith("/patterns"):
        chromosome = path.rstrip("/").split("/")[-2]
        return json_response(
            200,
            {
                "chromosome": safe_chromosome(chromosome),
                "items": get_chromosome_pattern_rows(chromosome),
            },
        )

    if method == "GET" and "/api/chromosomes/" in path and path.endswith("/regions"):
        chromosome = path.rstrip("/").split("/")[-2]
        query = event.get("queryStringParameters") or {}
        limit = query.get("limit")
        return json_response(
            200,
            {
                "chromosome": safe_chromosome(chromosome),
                "items": get_chromosome_region_rows(
                    chromosome,
                    limit=int(limit) if limit not in (None, "") else 12,
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
        return json_response(
            200,
            latest_batch_job_for_chromosome(chromosome) or {},
        )

    if method == "GET" and "/api/chromosomes/" in path and path.endswith("/operations"):
        chromosome = path.rstrip("/").split("/")[-2]
        current_status = get_current_status(chromosome)
        return json_response(
            200,
            {
                "chromosome": safe_chromosome(chromosome),
                "item": current_status,
                "processing_status": build_processing_status(current_status, fallback_chromosome=chromosome),
            },
        )

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
    try:
        return route(event)
    except ValueError as exc:
        return json_response(400, {"error": "bad_request", "message": str(exc)})
    except KeyError as exc:
        return json_response(404, {"error": "not_found", "message": str(exc)})
    except Exception as exc:
        logger.exception("Unhandled API error")
        return json_response(500, {"error": "internal_error", "message": str(exc)})
