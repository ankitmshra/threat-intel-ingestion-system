"""CloudWatch-optimized logging configuration for Lambda."""

import json
import logging
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import os

# Lambda context for correlation
_lambda_context = None


class CloudWatchJSONFormatter(logging.Formatter):
    """JSON formatter for CloudWatch Logs with Lambda context."""

    def format(self, record: logging.LogRecord) -> str:
        """
        Format log record as JSON for CloudWatch.

        Args:
            record: Log record to format

        Returns:
            JSON-formatted log string
        """
        # Base log structure
        log_data: Dict[str, str] = {
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": str(record.lineno),
        }

        # Add Lambda context if available
        if _lambda_context:
            log_data["aws_request_id"] = getattr(_lambda_context, "aws_request_id", "")
            log_data["function_name"] = getattr(_lambda_context, "function_name", "")
            log_data["function_version"] = getattr(_lambda_context, "function_version", "")
            log_data["memory_limit_mb"] = getattr(_lambda_context, "memory_limit_in_mb", "")
            log_data["remaining_time_ms"] = getattr(
                _lambda_context, "get_remaining_time_in_millis", lambda: "0"
            )()

        # Add environment info
        log_data["environment"] = os.environ.get("ENVIRONMENT", "dev")
        log_data["service"] = "misp-stix-pipeline"

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": traceback.format_exception(*record.exc_info),
            }

        # Add extra fields from record
        for key, value in record.__dict__.items():
            if key not in [
                "name",
                "msg",
                "args",
                "created",
                "filename",
                "funcName",
                "levelname",
                "levelno",
                "lineno",
                "module",
                "msecs",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "thread",
                "threadName",
                "exc_info",
                "exc_text",
                "stack_info",
            ]:
                log_data[key] = value

        return json.dumps(log_data, default=str)


def setup_logger(
    name: str, level: int = logging.INFO, correlation_id: Optional[str] = None
) -> logging.LoggerAdapter[Any]:
    """
    Set up a CloudWatch-optimized logger for Lambda.

    Args:
        name: Logger name
        level: Logging level
        correlation_id: Optional correlation ID for tracing

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)

    # Only configure if not already configured
    if not logger.handlers:
        logger.setLevel(level)

        # Console handler with JSON formatter
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(CloudWatchJSONFormatter())
        logger.addHandler(console_handler)

        # Prevent propagation to avoid duplicate logs
        logger.propagate = False

    # Always return a LoggerAdapter for consistent typing
    if correlation_id:
        adapter = logging.LoggerAdapter(logger, {"correlation_id": correlation_id})
    else:
        adapter = logging.LoggerAdapter(logger, {})

    return adapter


def set_lambda_context(context: Any) -> None:
    """
    Set Lambda context for logging.

    Args:
        context: Lambda context object
    """
    global _lambda_context
    _lambda_context = context


def log_metric(
    logger: logging.Logger,
    metric_name: str,
    value: float,
    unit: str = "Count",
    dimensions: Optional[Dict[str, str]] = None,
) -> None:
    """
    Log a metric in CloudWatch EMF format.

    Args:
        logger: Logger instance
        metric_name: Name of the metric
        value: Metric value
        unit: Metric unit (Count, Milliseconds, Bytes, etc.)
        dimensions: Optional dimensions for the metric
    """
    metric_data = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": "MISP-STIX-Pipeline",
                    "Dimensions": [list(dimensions.keys())] if dimensions else [],
                    "Metrics": [{"Name": metric_name, "Unit": unit}],
                }
            ],
        },
        metric_name: value,
    }

    if dimensions:
        metric_data.update(dimensions)

    # Log as info with metric flag
    logger.info("CloudWatch Metric", extra={"metric": metric_data, "is_metric": True})


def log_performance(func):
    """
    Decorator to log function performance metrics.

    Args:
        func: Function to wrap
    """

    def wrapper(*args, **kwargs):
        logger = setup_logger(func.__module__)
        start_time = time.time()

        try:
            result = func(*args, **kwargs)
            duration_ms = (time.time() - start_time) * 1000

            logger.info(
                f"Function executed successfully",
                extra={"function": func.__name__, "duration_ms": duration_ms, "status": "success"},
            )

            # Log performance metric
            log_metric(
                logger,
                f"{func.__name__}_duration",
                duration_ms,
                "Milliseconds",
                {"Function": func.__name__},
            )

            return result

        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000

            logger.error(
                "Function failed",
                extra={
                    "function": func.__name__,
                    "duration_ms": duration_ms,
                    "status": "error",
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )

            # Log error metric
            log_metric(
                logger,
                f"{func.__name__}_errors",
                1,
                "Count",
                {"Function": func.__name__, "ErrorType": type(e).__name__},
            )

            raise

    return wrapper
