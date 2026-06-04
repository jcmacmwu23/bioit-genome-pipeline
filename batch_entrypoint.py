#!/usr/bin/env python3
"""
Batch container entrypoint for genome full-analysis jobs.

This reuses the existing lambda_handler orchestration so the heavy-compute path
stores outputs in the same S3/Athena layout as the Lambda pipeline.
"""
import json
import logging
import os
import sys
from typing import Any, Dict

from lambda_handler import lambda_handler


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def load_job_payload() -> Dict[str, Any]:
    payload = os.environ.get("JOB_PAYLOAD")
    if payload:
        return json.loads(payload)

    if len(sys.argv) > 1:
        return json.loads(sys.argv[1])

    raise ValueError("Missing job payload. Provide JOB_PAYLOAD or a JSON argument.")


def main() -> int:
    try:
        event = load_job_payload()
        logger.info("Starting Batch genome analysis job for chromosome=%s mode=%s",
                    event.get("chromosome"), event.get("analysis_mode"))
        response = lambda_handler(event, None)
        status_code = int(response.get("statusCode", 500))
        body = response.get("body")
        if body:
            logger.info("Job response body: %s", body)
        if status_code >= 400:
            logger.error("Genome analysis job failed with status_code=%s", status_code)
            return 1
        logger.info("Genome analysis job completed successfully")
        return 0
    except Exception as exc:
        logger.exception("Batch entrypoint failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
