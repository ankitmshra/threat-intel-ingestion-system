"""Abstract base classes and interfaces for the pipeline components."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List


class EventProcessor(ABC):
    """Abstract base class for event processors."""

    @abstractmethod
    def process(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a single event.

        Args:
            event: Event data to process

        Returns:
            Processed event data
        """
        pass


class DataStore(ABC):
    """Abstract base class for data storage."""

    @abstractmethod
    def save(self, data: bytes, key: str) -> str:
        """
        Save data to storage.

        Args:
            data: Data to save
            key: Storage key/identifier

        Returns:
            Location/URL of saved data
        """
        pass

    @abstractmethod
    def retrieve(self, key: str) -> bytes:
        """
        Retrieve data from storage.

        Args:
            key: Storage key/identifier

        Returns:
            Retrieved data
        """
        pass


class MessageQueue(ABC):
    """Abstract base class for message queue operations."""

    @abstractmethod
    def send_message(self, message: Dict[str, Any]) -> str:
        """
        Send message to queue.

        Args:
            message: Message to send

        Returns:
            Message ID
        """
        pass

    @abstractmethod
    def receive_messages(self, max_messages: int = 1) -> List[Dict[str, Any]]:
        """
        Receive messages from queue.

        Args:
            max_messages: Maximum messages to receive

        Returns:
            List of messages
        """
        pass
