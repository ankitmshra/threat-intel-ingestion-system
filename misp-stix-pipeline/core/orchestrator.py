"""Main orchestrator for the MISP-STIX pipeline."""

import os
from datetime import datetime
from typing import Any, Dict, Optional

from ..utils.exceptions import MISPPipelineError
from ..utils.logging import log_metric, log_performance, setup_logger
from .aws_clients import S3Manager, SQSManager
from .misp_client import MISPClient
from .stix_converter import STIXConverter

# Use Lambda-optimized config if in Lambda environment
if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
    from ..lambda_config import LambdaConfigManager as ConfigManager
else:
    from ..config import ConfigManager

logger = setup_logger(__name__)


class PipelineOrchestrator:
    """
    Main orchestrator that coordinates the entire pipeline flow.

    This class implements the Strategy pattern for different processing modes
    and uses dependency injection for its components.
    """

    def __init__(self) -> None:
        """Initialize the orchestrator with all required components."""
        # Get configuration
        self.config = ConfigManager()
        config_data = self.config.get_config()

        # Initialize components
        self.misp_client = MISPClient(
            url=config_data["misp_url"], api_key=config_data["misp_api_key"]
        )

        self.stix_converter = STIXConverter(stix_version="2.1")

        self.s3_manager = S3Manager(
            bucket_name=config_data["s3_bucket"], prefix=config_data["s3_prefix"]
        )

        self.sqs_manager = SQSManager(
            queue_url=config_data["sqs_queue_url"], dlq_url=config_data["dlq_url"]
        )

        self.batch_size = config_data.get("batch_size", 100)

        logger.info("Pipeline orchestrator initialized")

    @log_performance
    def run(self, since: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Run the complete pipeline.

        Args:
            since: Process events created after this timestamp

        Returns:
            Summary of pipeline execution
        """
        start_time = datetime.utcnow()
        summary = {
            "start_time": start_time.isoformat(),
            "total_events": 0,
            "processed_events": 0,
            "failed_events": 0,
            "messages_sent": 0,
            "errors": [],
        }

        try:
            # Step 1: Get event count
            event_count = self.misp_client.get_event_count()
            summary["total_events"] = event_count

            logger.info(
                "MISP event count retrieved",
                extra={"total_events": event_count, "operation": "get_event_count"},
            )

            # Log metric for monitoring
            log_metric(logger, "misp_total_events", event_count, "Count")

            # Step 2: Fetch new events
            events = self.misp_client.get_new_events(since=since, limit=self.batch_size)

            logger.info(
                "New events fetched",
                extra={
                    "new_events_count": len(events),
                    "since": since.isoformat() if since else "none",
                    "batch_size": self.batch_size,
                    "operation": "fetch_events",
                },
            )

            # Log fetch metric
            log_metric(logger, "events_fetched", len(events), "Count")

            # Step 3: Process events
            for event in events:
                try:
                    event_start = datetime.utcnow()
                    self._process_single_event(event)
                    event_duration_ms = (datetime.utcnow() - event_start).total_seconds() * 1000

                    summary["processed_events"] += 1
                    summary["messages_sent"] += 1

                    # Log successful processing
                    logger.info(
                        "Event processed successfully",
                        extra={
                            "event_uuid": event.uuid,
                            "event_id": event.id,
                            "processing_time_ms": event_duration_ms,
                            "operation": "process_event",
                            "status": "success",
                        },
                    )

                    # Log processing time metric
                    log_metric(
                        logger,
                        "event_processing_time",
                        event_duration_ms,
                        "Milliseconds",
                        {"Status": "Success"},
                    )

                except Exception as e:
                    summary["failed_events"] += 1
                    error_info = {
                        "event_uuid": event.uuid,
                        "error": str(e),
                        "error_type": type(e).__name__,
                    }
                    summary["errors"].append(error_info)

                    logger.error(
                        "Event processing failed",
                        extra={
                            "event_uuid": event.uuid,
                            "event_id": event.id,
                            "error_type": type(e).__name__,
                            "error_message": str(e),
                            "operation": "process_event",
                            "status": "failed",
                        },
                        exc_info=True,
                    )

                    # Log error metric
                    log_metric(
                        logger,
                        "event_processing_errors",
                        1,
                        "Count",
                        {"ErrorType": type(e).__name__},
                    )

                    # Send to DLQ
                    try:
                        self.sqs_manager.send_to_dlq({"event_uuid": event.uuid}, str(e))

                        logger.info(
                            "Failed event sent to DLQ",
                            extra={"event_uuid": event.uuid, "operation": "send_to_dlq"},
                        )

                    except Exception as dlq_error:
                        logger.error(
                            "Failed to send to DLQ",
                            extra={
                                "event_uuid": event.uuid,
                                "dlq_error": str(dlq_error),
                                "operation": "send_to_dlq",
                            },
                            exc_info=True,
                        )

                        log_metric(logger, "dlq_send_errors", 1, "Count")

            # Calculate execution time
            end_time = datetime.utcnow()
            summary["end_time"] = end_time.isoformat()
            summary["duration_seconds"] = (end_time - start_time).total_seconds()

            # Log completion
            logger.info(
                "Pipeline execution completed",
                extra={"summary": summary, "operation": "pipeline_complete"},
            )

            # Log summary metrics
            log_metric(
                logger,
                "pipeline_success_rate",
                (summary["processed_events"] / len(events) * 100) if events else 100,
                "Percent",
            )

            return summary

        except Exception as e:
            logger.error(
                "Pipeline execution failed",
                extra={
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "operation": "pipeline_error",
                },
                exc_info=True,
            )

            summary["pipeline_error"] = str(e)
            log_metric(logger, "pipeline_failures", 1, "Count", {"ErrorType": type(e).__name__})

            raise MISPPipelineError(f"Pipeline execution failed: {e}") from e

    def _process_single_event(self, event) -> None:
        """
        Process a single MISP event through the pipeline.

        Args:
            event: MISP event to process
        """
        # Convert to STIX
        stix_data = self.stix_converter.process(event)

        # Save to S3
        s3_url = self.s3_manager.save_stix_bundle(stix_data)

        # Prepare message for SQS
        message = {
            "event_id": stix_data["event_id"],
            "event_uuid": stix_data["event_uuid"],
            "s3_url": s3_url,
            "converted_at": stix_data["converted_at"],
            "pipeline_version": "1.0.0",
        }

        # Send to SQS
        self.sqs_manager.send_message(message)

        logger.info(f"Successfully processed event {event.uuid}")
