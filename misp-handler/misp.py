import logging
import os
from typing import Optional

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
