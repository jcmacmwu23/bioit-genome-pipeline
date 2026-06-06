import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import boto3


logger = logging.getLogger(__name__)


dynamodb = boto3.resource("dynamodb")

OPERATIONS_TABLE = os.environ.get("OPERATIONS_TABLE")
OPERATIONS_TTL_DAYS = int(os.environ.get("OPERATIONS_TTL_DAYS", "30"))

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


def operations_enabled() -> bool:
    return bool(OPERATIONS_TABLE)


def _table():
    if not OPERATIONS_TABLE:
        raise RuntimeError("OPERATIONS_TABLE is not configured")
    return dynamodb.Table(OPERATIONS_TABLE)


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def ttl_epoch(days: int = OPERATIONS_TTL_DAYS) -> int:
    return int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp())


def normalize_chromosome(value: Any) -> str:
    chromosome = str(value or "unknown")
    if chromosome.lower() in {"x", "y"}:
        return chromosome.upper()
    return chromosome


def chromosome_length(chromosome: Any) -> Optional[int]:
    return HUMAN_CHROMOSOME_LENGTHS.get(normalize_chromosome(chromosome))


def estimated_duration_minutes(chromosome: Any, backend: str, analysis_mode: str) -> Optional[int]:
    if str(analysis_mode or "full").lower() != "full":
        return 2

    length_value = chromosome_length(chromosome)
    if not length_value:
        return None

    backend_name = str(backend or "lambda").lower()
    if backend_name == "batch":
        return max(6, round(length_value / 4_000_000))
    return max(4, round(length_value / 8_500_000))


def status_pk(chromosome: Any) -> str:
    return f"CHR#{normalize_chromosome(chromosome)}"


def status_sk(job_type: str, analysis_mode: str) -> str:
    return f"STATUS#{str(job_type or 'sequence_analysis').lower()}#{str(analysis_mode or 'full').lower()}"


def event_sk(job_type: str, analysis_mode: str, event_type: str, timestamp: Optional[str] = None) -> str:
    return (
        f"EVENT#{str(job_type or 'sequence_analysis').lower()}#"
        f"{str(analysis_mode or 'full').lower()}#"
        f"{timestamp or iso_now()}#"
        f"{str(event_type or 'unknown').lower()}"
    )


def _base_status_item(
    payload: Dict[str, Any],
    backend: str,
    status: str,
    detail: Optional[str] = None,
) -> Dict[str, Any]:
    chromosome = normalize_chromosome(payload.get("chromosome"))
    job_type = str(payload.get("job_type", "sequence_analysis")).lower()
    analysis_mode = str(payload.get("analysis_mode", "full")).lower()
    submitted_at = payload.get("submitted_at") or iso_now()
    return {
        "pk": status_pk(chromosome),
        "sk": status_sk(job_type, analysis_mode),
        "item_type": "current_status",
        "chromosome": chromosome,
        "job_type": job_type,
        "analysis_mode": analysis_mode,
        "source": payload.get("source"),
        "species": payload.get("species", "homo_sapiens"),
        "accession_id": payload.get("accession_id"),
        "output_prefix": payload.get("output_prefix"),
        "backend": backend,
        "status": status,
        "detail": detail,
        "submitted_at": submitted_at,
        "updated_at": iso_now(),
        "estimated_duration_minutes": estimated_duration_minutes(chromosome, backend, analysis_mode),
        "expires_at": ttl_epoch(),
    }


def _merge_current_status(payload: Dict[str, Any], backend: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    if not operations_enabled():
        return {}

    chromosome = normalize_chromosome(payload.get("chromosome"))
    job_type = str(payload.get("job_type", "sequence_analysis")).lower()
    analysis_mode = str(payload.get("analysis_mode", "full")).lower()
    key = {"pk": status_pk(chromosome), "sk": status_sk(job_type, analysis_mode)}

    current = _table().get_item(Key=key).get("Item", {})
    merged = {
        **_base_status_item(payload, backend, fields.get("status") or current.get("status") or "submitted"),
        **current,
        **fields,
        **key,
        "chromosome": chromosome,
        "job_type": job_type,
        "analysis_mode": analysis_mode,
        "backend": backend,
        "updated_at": iso_now(),
        "expires_at": ttl_epoch(),
    }
    _table().put_item(Item=merged)
    return merged


def _put_event(payload: Dict[str, Any], event_type: str, backend: str, fields: Dict[str, Any]) -> None:
    if not operations_enabled():
        return

    chromosome = normalize_chromosome(payload.get("chromosome"))
    job_type = str(payload.get("job_type", "sequence_analysis")).lower()
    analysis_mode = str(payload.get("analysis_mode", "full")).lower()
    timestamp = iso_now()
    item = {
        "pk": status_pk(chromosome),
        "sk": event_sk(job_type, analysis_mode, event_type, timestamp=timestamp),
        "item_type": "event",
        "chromosome": chromosome,
        "job_type": job_type,
        "analysis_mode": analysis_mode,
        "backend": backend,
        "event_type": event_type,
        "event_at": timestamp,
        "expires_at": ttl_epoch(),
    }
    item.update({key: value for key, value in fields.items() if value is not None})
    _table().put_item(Item=item)


def record_submission(
    payload: Dict[str, Any],
    backend: str,
    message_id: Optional[str] = None,
    batch_job_id: Optional[str] = None,
    batch_job_name: Optional[str] = None,
) -> Dict[str, Any]:
    detail = "Queued for processing" if backend == "lambda" else "Submitted to AWS Batch"
    fields = {
        "status": "submitted",
        "detail": detail,
        "submitted_at": payload.get("submitted_at") or iso_now(),
        "message_id": message_id,
        "batch_job_id": batch_job_id,
        "batch_job_name": batch_job_name,
        "sequence_ready": False,
        "patterns_ready": False,
        "regions_ready": False,
        "annotations_ready": False,
        "failure_reason": None,
        "finished_at": None,
        "started_at": None,
        "attempt_count": 0,
        "error_type": None,
    }
    item = _merge_current_status(payload, backend, fields)
    _put_event(payload, "submitted", backend, fields)
    return item


def record_start(
    payload: Dict[str, Any],
    backend: str,
    message_id: Optional[str] = None,
    batch_job_id: Optional[str] = None,
    batch_job_name: Optional[str] = None,
) -> Dict[str, Any]:
    current = _merge_current_status(
        payload,
        backend,
        {
            "status": "running",
            "detail": "Genome analysis is running",
            "message_id": message_id,
            "batch_job_id": batch_job_id,
            "batch_job_name": batch_job_name,
        },
    )
    if not current.get("started_at"):
        current["started_at"] = iso_now()
    current["attempt_count"] = int(current.get("attempt_count") or 0) + 1
    current["updated_at"] = iso_now()
    current["expires_at"] = ttl_epoch()
    _table().put_item(Item=current)
    _put_event(
        payload,
        "started",
        backend,
        {
            "message_id": message_id,
            "batch_job_id": batch_job_id,
            "batch_job_name": batch_job_name,
            "attempt_count": current["attempt_count"],
        },
    )
    return current


def record_success(
    payload: Dict[str, Any],
    backend: str,
    outputs: Optional[Dict[str, str]] = None,
    extra_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    outputs = outputs or {}
    dataset_flags = {
        "sequence_ready": bool(outputs.get("sequences")),
        "patterns_ready": bool(outputs.get("patterns")),
        "regions_ready": bool(outputs.get("regions")),
        "annotations_ready": bool(outputs.get("annotations")),
        "latest_raw_output": outputs.get("raw"),
        "latest_sequence_output": outputs.get("sequences"),
        "latest_pattern_output": outputs.get("patterns"),
        "latest_region_output": outputs.get("regions"),
        "latest_annotation_output": outputs.get("annotations"),
    }
    fields = {
        "status": "succeeded",
        "detail": "Pipeline completed successfully",
        "finished_at": iso_now(),
        "failure_reason": None,
        **dataset_flags,
        **(extra_fields or {}),
    }
    item = _merge_current_status(payload, backend, fields)
    _put_event(payload, "succeeded", backend, fields)
    return item


def record_failure(
    payload: Dict[str, Any],
    backend: str,
    failure_reason: str,
    error_type: Optional[str] = None,
    extra_fields: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    fields = {
        "status": "failed",
        "detail": failure_reason,
        "failure_reason": failure_reason,
        "error_type": error_type,
        "finished_at": iso_now(),
        **(extra_fields or {}),
    }
    item = _merge_current_status(payload, backend, fields)
    _put_event(payload, "failed", backend, fields)
    return item


def get_current_status(
    chromosome: Any,
    job_type: str = "sequence_analysis",
    analysis_mode: str = "full",
) -> Optional[Dict[str, Any]]:
    if not operations_enabled():
        return None

    item = _table().get_item(
        Key={
            "pk": status_pk(chromosome),
            "sk": status_sk(job_type, analysis_mode),
        }
    ).get("Item")
    if not item:
        return None
    return item


def build_processing_status(
    item: Optional[Dict[str, Any]],
    fallback_chromosome: Any = None,
) -> Optional[Dict[str, Any]]:
    if not item:
        return None

    status = str(item.get("status") or "").lower()
    backend = str(item.get("backend") or "lambda").lower()
    submitted_at = parse_iso_timestamp(item.get("submitted_at"))
    started_at = parse_iso_timestamp(item.get("started_at")) or submitted_at
    finished_at = parse_iso_timestamp(item.get("finished_at"))
    now = datetime.now(timezone.utc)
    elapsed_seconds = 0
    if started_at:
        end_time = finished_at or now
        elapsed_seconds = max(0, int((end_time - started_at).total_seconds()))
    expected_minutes = item.get("estimated_duration_minutes")
    try:
        expected_minutes = int(expected_minutes) if expected_minutes is not None else None
    except (TypeError, ValueError):
        expected_minutes = None
    progress_pct: Optional[int] = None
    if status == "submitted":
        progress_pct = 2
    elif status == "running":
        if expected_minutes and expected_minutes > 0:
            progress_pct = min(96, max(8, round((elapsed_seconds / 60) / expected_minutes * 100)))
        else:
            progress_pct = 30
    elif status == "succeeded":
        progress_pct = 100
    elif status == "failed":
        progress_pct = 0

    chromosome = item.get("chromosome") or normalize_chromosome(fallback_chromosome)
    return {
        "backend": backend,
        "status": status,
        "detail": item.get("detail"),
        "failure_reason": item.get("failure_reason"),
        "error_type": item.get("error_type"),
        "submitted_at": item.get("submitted_at"),
        "started_at": item.get("started_at"),
        "finished_at": item.get("finished_at"),
        "updated_at": item.get("updated_at"),
        "attempt_count": int(item.get("attempt_count") or 0),
        "progress_pct": progress_pct,
        "elapsed_minutes": round(elapsed_seconds / 60) if elapsed_seconds else 0,
        "expected_minutes": expected_minutes,
        "chromosome": chromosome,
        "message_id": item.get("message_id"),
        "batch_job_id": item.get("batch_job_id"),
        "batch_job_name": item.get("batch_job_name"),
        "latest_sequence_output": item.get("latest_sequence_output"),
        "latest_pattern_output": item.get("latest_pattern_output"),
        "latest_region_output": item.get("latest_region_output"),
        "latest_annotation_output": item.get("latest_annotation_output"),
    }
