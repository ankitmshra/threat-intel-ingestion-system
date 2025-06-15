"""STIX converter for transforming MISP events to STIX format."""

import json
from datetime import datetime
from typing import Any, Dict, List

from misp_stix import MISPtoSTIX20Parser, MISPtoSTIX21Parser
from pymisp import MISPEvent

from ..utils.exceptions import STIXConversionError
from ..utils.logging import setup_logger
from .interfaces import EventProcessor

logger = setup_logger(__name__)


class STIXConverter(EventProcessor):
    """Converter for transforming MISP events to STIX format."""

    def __init__(self, stix_version: str = "2.1") -> None:
        """
        Initialize STIX converter.

        Args:
            stix_version: STIX version to use ("2.0" or "2.1")
        """
        self.stix_version = stix_version

        if stix_version == "2.1":
            self.parser_class = MISPtoSTIX21Parser
        elif stix_version == "2.0":
            self.parser_class = MISPtoSTIX20Parser
        else:
            raise ValueError(f"Unsupported STIX version: {stix_version}")

        logger.info(f"Initialized STIX converter for version {stix_version}")

    def process(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a MISP event and convert to STIX.

        Args:
            event: MISP event data

        Returns:
            Processed event with STIX data
        """
        try:
            # Convert dict to MISPEvent if needed
            if isinstance(event, dict):
                misp_event = MISPEvent()
                misp_event.from_dict(**event)
            else:
                misp_event = event

            # Convert to STIX
            stix_bundle = self.convert_to_stix(misp_event)

            return {
                "event_id": misp_event.id,
                "event_uuid": misp_event.uuid,
                "stix_bundle": stix_bundle,
                "converted_at": datetime.utcnow().isoformat(),
            }
        except Exception as e:
            logger.error(f"Failed to process event: {e}")
            raise STIXConversionError(f"Cannot process event: {e}") from e

    def convert_to_stix(self, misp_event: MISPEvent) -> Dict[str, Any]:
        """
        Convert MISP event to STIX bundle.

        Args:
            misp_event: MISP event object

        Returns:
            STIX bundle as dictionary

        Raises:
            STIXConversionError: If conversion fails
        """
        try:
            # Initialize parser
            parser = self.parser_class()

            # Parse MISP event
            parser.parse_misp_event(misp_event)

            # Get STIX bundle
            stix_bundle = parser.bundle

            logger.debug(f"Converted event {misp_event.uuid} to STIX")
            return json.loads(stix_bundle.serialize())
        except Exception as e:
            logger.error(f"Failed to convert to STIX: {e}")
            raise STIXConversionError(f"Cannot convert to STIX: {e}") from e

    def batch_convert(self, events: List[MISPEvent]) -> List[Dict[str, Any]]:
        """
        Convert multiple MISP events to STIX format.

        Args:
            events: List of MISP events

        Returns:
            List of STIX bundles
        """
        results = []
        errors = []

        for event in events:
            try:
                stix_data = self.process(event)
                results.append(stix_data)
            except STIXConversionError as e:
                logger.warning(f"Failed to convert event {event.uuid}: {e}")
                errors.append({"event_uuid": event.uuid, "error": str(e)})

        if errors:
            logger.warning(f"Failed to convert {len(errors)} events")

        return results
