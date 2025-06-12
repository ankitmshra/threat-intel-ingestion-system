import logging
import os
from typing import Any, Optional

from pymisp import PyMISP

from .config import MispConfig as MC
from .logging import CloudWatchFormatter


class MispThreatFeedProcessor:
    """
    Process MISP threat feeds with auto-scaling capabilities.
    """

    def __init__(self, request_id: Optional[str] = None):
        self.request_id: str = request_id or "unknown"

        # Setup Logger FIRST
        self.logger = logging.getLogger(__name__)
        if not self.logger.handlers:
            self.logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
            handler = logging.StreamHandler()
            handler.setFormatter(CloudWatchFormatter())
            self.logger.addHandler(handler)

        # Log initialization start
        self.logger.info(
            "Initializing MISP Threat Feed Processor",
            extra={"extra_fields": {"request_id": self.request_id}},
        )

        # Load configuration
        try:
            config: MC = MC()
            self.misp_url: str = config.misp_url
            self.misp_verify_cert: bool = config.misp_verify_cert
            self.s3_bucket: str = config.s3_bucket
            self.sqs_queue_url: str = config.sqs_queue_url
            self.last_sync_hours: str = config.last_sync_hours
            self.misp_key: str = config.misp_key

            self.logger.info(
                "MISP Threat Feed Processor initialized successfully",
                extra={
                    "extra_fields": {
                        "request_id": self.request_id,
                        "misp_url": self.misp_url,
                        "s3_bucket": self.s3_bucket,
                        "last_sync_hours": self.last_sync_hours,
                    }
                },
            )

        except Exception as e:
            # Config classes will have already logged the specific errors
            # This is our final log before the Lambda crashes
            self.logger.critical(
                f"Failed to initialize MISP Threat Feed Processor: {str(e)}",
                extra={"extra_fields": {"request_id": self.request_id}},
                exc_info=True,
            )
            # Re-raise to crash the Lambda immediately
            raise

        # initialize MISP client
        self.misp: PyMISP = self._init_misp_client()
        self.logger.info(
            "MISP client initialized",
            extra={"extra_fields": {"request_id": self.request_id, "misp_url": self.misp_url}},
        )

    def _init_misp_client(self) -> PyMISP:
        """Initialize and test MISP client connection"""
        try:
            misp = PyMISP(
                url=self.misp_url,
                key=self.misp_key,
                ssl=self.misp_verify_cert,
                debug=False,
                proxies=None,
                cert=None,
                auth=None,
                tool=f"AWS-Lambda-MISP-Processor-{self.request_id}",
            )

            # Test connection with recommended PyMISP version check
            version_check: Any = misp.recommended_pymisp_version
            if "errors" in version_check:
                raise Exception(f"MISP connection test failed: {version_check['errors']}")

            self.logger.info(
                "Connected to MISP instance",
                extra={
                    "extra_fields": {
                        "recommended_pymisp_version": version_check.get("version", "unknown"),
                        "request_id": self.request_id,
                    }
                },
            )
            return misp

        except Exception as e:
            self.logger.error(
                f"Failed to initialize MISP client: {str(e)}",
                extra={"extra_fields": {"request_id": self.request_id}},
            )
            raise

    def get_events_count_since_last_sync(self) -> int:
        """Get count of events modified since last sync (based on last_sync_hours config)"""
        try:
            sync_hours = self.last_sync_hours

            events_response = self.misp.search_index(
                timestamp=f"{sync_hours}h",
                pythonify=False,
            )

            # Type guard for error response
            if isinstance(events_response, dict) and "errors" in events_response:
                self.logger.error(
                    f"Error getting events count: {events_response['errors']}",
                    extra={
                        "extra_fields": {
                            "last_sync_hours": sync_hours,
                            "request_id": self.request_id,
                        }
                    },
                )
                return 0

            # Type guard for successful list response
            if isinstance(events_response, list):
                events_count = len(events_response)

                self.logger.info(
                    f"Found {events_count} events modified in last {sync_hours} hours",
                    extra={
                        "extra_fields": {
                            "events_count": events_count,
                            "last_sync_hours": sync_hours,
                            "request_id": self.request_id,
                        }
                    },
                )
                return events_count

            # Fallback for unexpected response types
            self.logger.warning(
                f"Unexpected response type from search_index: {type(events_response)}",
                extra={
                    "extra_fields": {"last_sync_hours": sync_hours, "request_id": self.request_id}
                },
            )
            return 0

        except Exception as e:
            self.logger.error(
                f"Failed to get events count for last {self.last_sync_hours} hours: {str(e)}",
                extra={
                    "extra_fields": {
                        "last_sync_hours": self.last_sync_hours,
                        "request_id": self.request_id,
                    }
                },
            )
            return 0
