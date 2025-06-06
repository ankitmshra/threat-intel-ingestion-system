import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional


class CloudWatchFormatter(logging.Formatter):
    """Custom formatter for CloudWatch structured logging"""

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record into a structured JSON format for CloudWatch"""
        timestamp: str = datetime.now(timezone.utc).isoformat()
        log_entry: Dict[str, Any] = {
            "timestamp": timestamp + "Z",
            "message": record.getMessage(),
            "logger": record.name,
            "function_name": os.getenv("AWS_LAMBDA_FUNCTION_NAME", "unknown"),
            "request_id": getattr(record, "request_id", "unknown"),
        }

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Add extra fields if present
        extra_fields: Optional[Dict[str, Any]] = getattr(record, "extra_fields", None)
        if extra_fields is not None:
            log_entry.update(extra_fields)

        return json.dumps(log_entry)


class CloudWatchLogger:

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
        handler = logging.StreamHandler()
        handler.setFormatter(CloudWatchFormatter())
        self.logger.addHandler(handler)
