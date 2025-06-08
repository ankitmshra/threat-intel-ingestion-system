import logging
import os
from typing import Optional

from .config import MispConfig as MC
from .logging import CloudWatchFormatter


class MispThreatFeedProcessor:
    """
    Process MISP threat feeds with auto-scaling capabilities.
    """

    def __init__(self, request_id: Optional[str] = None):
        self.request_id: str = request_id or "unknown"

        # Setup Logger
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
        handler = logging.StreamHandler()
        handler.setFormatter(CloudWatchFormatter())
        self.logger.addHandler(handler)

        # Load configuration
        config: MC = MC()
        self.misp_url: str = config.misp_url
        self.misp_verify_cert: bool = config.misp_verify_cert
        self.s3_bucket: str = config.s3_bucket
        self.sqs_queue_url: str = config.sqs_queue_url
        self.last_sync_hours: str = config.last_sync_hours
        self.misp_key: str = config.misp_key
