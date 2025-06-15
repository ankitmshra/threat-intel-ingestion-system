"""MISP client for interacting with MISP instances."""

from datetime import datetime, timedelta
from typing import List, Optional

from pymisp import MISPEvent, PyMISP
from pymisp.exceptions import PyMISPError

from ..utils.exceptions import MISPConnectionError
from ..utils.logging import setup_logger

logger = setup_logger(__name__)


class MISPClient:
    """Client for interacting with MISP instances."""

    def __init__(self, url: str, api_key: str, verify_ssl: bool = True) -> None:
        """
        Initialize MISP client.

        Args:
            url: MISP instance URL
            api_key: MISP API key
            verify_ssl: Whether to verify SSL certificates
        """
        self.url = url
        self.verify_ssl = verify_ssl

        try:
            self.misp = PyMISP(url, api_key, verify_ssl)
            logger.info(f"Connected to MISP instance: {url}")
        except PyMISPError as e:
            logger.error(f"Failed to connect to MISP: {e}")
            raise MISPConnectionError(f"Cannot connect to MISP: {e}") from e

    def get_event_count(self) -> int:
        """
        Get total count of events in MISP instance.

        Returns:
            Total number of events

        Raises:
            MISPConnectionError: If count cannot be retrieved
        """
        try:
            # Use index to get event count
            result = self.misp.search_index(minimal=True)
            count = len(result)
            logger.info(f"Total events in MISP: {count}")
            return count
        except Exception as e:
            logger.error(f"Failed to get event count: {e}")
            raise MISPConnectionError(f"Cannot get event count: {e}") from e

    def get_new_events(
        self, since: Optional[datetime] = None, limit: Optional[int] = None
    ) -> List[MISPEvent]:
        """
        Fetch new events from MISP.

        Args:
            since: Fetch events created after this timestamp
            limit: Maximum number of events to fetch

        Returns:
            List of MISP events

        Raises:
            MISPConnectionError: If events cannot be fetched
        """
        try:
            # Default to last 24 hours if no timestamp provided
            if since is None:
                since = datetime.now() - timedelta(days=1)

            # Search for events
            search_params = {"timestamp": since.isoformat(), "pythonify": True}

            if limit:
                search_params["limit"] = limit

            events = self.misp.search(**search_params)

            if isinstance(events, dict) and "errors" in events:
                raise MISPConnectionError(f"MISP search error: {events['errors']}")

            logger.info(f"Fetched {len(events)} new events")
            return events
        except Exception as e:
            logger.error(f"Failed to fetch events: {e}")
            raise MISPConnectionError(f"Cannot fetch events: {e}") from e

    def get_event_by_id(self, event_id: str) -> MISPEvent:
        """
        Fetch a specific event by ID.

        Args:
            event_id: Event ID

        Returns:
            MISP event

        Raises:
            MISPConnectionError: If event cannot be fetched
        """
        try:
            event = self.misp.get_event(event_id, pythonify=True)
            logger.debug(f"Fetched event: {event_id}")
            return event
        except Exception as e:
            logger.error(f"Failed to fetch event {event_id}: {e}")
            raise MISPConnectionError(f"Cannot fetch event: {e}") from e
