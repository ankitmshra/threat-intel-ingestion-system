"""AWS service clients for S3 and SQS operations."""

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

from ..utils.exceptions import AWSServiceError
from ..utils.logging import log_metric, setup_logger
from .interfaces import DataStore, MessageQueue

logger = setup_logger(__name__)


class S3Manager(DataStore):
    """Manager for S3 operations."""

    def __init__(self, bucket_name: str, prefix: str = "") -> None:
        """
        Initialize S3 manager.

        Args:
            bucket_name: S3 bucket name
            prefix: Optional prefix for S3 keys
        """
        self.bucket_name = bucket_name
        self.prefix = prefix.rstrip("/") + "/" if prefix else ""
        self.s3_client = boto3.client("s3")
        logger.info(f"Initialized S3Manager for bucket: {bucket_name}")

    def save(self, data: bytes, key: str) -> str:
        """
        Save data to S3.

        Args:
            data: Data to save
            key: S3 key

        Returns:
            S3 URL of saved object

        Raises:
            AWSServiceError: If save fails
        """
        try:
            full_key = f"{self.prefix}{key}"
            start_time = datetime.utcnow()

            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=full_key,
                Body=data,
                ContentType="application/json",
                Metadata={
                    "uploaded_at": datetime.utcnow().isoformat(),
                    "pipeline_version": "1.0.0",
                },
            )

            upload_duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
            s3_url = f"s3://{self.bucket_name}/{full_key}"

            # Log successful upload
            logger.info(
                "S3 upload successful",
                extra={
                    "s3_bucket": self.bucket_name,
                    "s3_key": full_key,
                    "object_size_bytes": len(data),
                    "upload_duration_ms": upload_duration_ms,
                    "operation": "s3_put_object",
                },
            )

            # Log metrics
            log_metric(logger, "s3_uploads", 1, "Count", {"Bucket": self.bucket_name})
            log_metric(logger, "s3_upload_size", len(data), "Bytes")
            log_metric(logger, "s3_upload_duration", upload_duration_ms, "Milliseconds")

            return s3_url

        except ClientError as e:
            error_code = e.response["Error"]["Code"]

            logger.error(
                "S3 upload failed",
                extra={
                    "s3_bucket": self.bucket_name,
                    "s3_key": full_key,
                    "error_code": error_code,
                    "error_message": str(e),
                    "operation": "s3_put_object",
                },
                exc_info=True,
            )

            # Log error metric
            log_metric(
                logger,
                "s3_upload_errors",
                1,
                "Count",
                {"ErrorCode": error_code, "Bucket": self.bucket_name},
            )

            raise AWSServiceError(f"Cannot save to S3: {e}") from e

    def retrieve(self, key: str) -> bytes:
        """
        Retrieve data from S3.

        Args:
            key: S3 key

        Returns:
            Retrieved data

        Raises:
            AWSServiceError: If retrieval fails
        """
        try:
            full_key = f"{self.prefix}{key}"

            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=full_key)

            data = response["Body"].read()
            logger.debug(f"Retrieved object from S3: {full_key}")
            return data
        except ClientError as e:
            logger.error(f"Failed to retrieve from S3: {e}")
            raise AWSServiceError(f"Cannot retrieve from S3: {e}") from e

    def save_stix_bundle(self, stix_data: Dict[str, Any]) -> str:
        """
        Save STIX bundle to S3.

        Args:
            stix_data: STIX data containing bundle and metadata

        Returns:
            S3 URL of saved bundle
        """
        # Generate key based on event UUID and timestamp
        timestamp = datetime.utcnow().strftime("%Y/%m/%d/%H")
        event_uuid = stix_data.get("event_uuid", str(uuid.uuid4()))
        key = f"stix-bundles/{timestamp}/{event_uuid}.json"

        # Serialize STIX bundle
        data = json.dumps(stix_data, indent=2).encode("utf-8")

        return self.save(data, key)


class SQSManager(MessageQueue):
    """Manager for SQS operations."""

    def __init__(self, queue_url: str, dlq_url: Optional[str] = None) -> None:
        """
        Initialize SQS manager.

        Args:
            queue_url: Main SQS queue URL
            dlq_url: Dead letter queue URL
        """
        self.queue_url = queue_url
        self.dlq_url = dlq_url
        self.sqs_client = boto3.client("sqs")
        logger.info(f"Initialized SQSManager for queue: {queue_url}")

    def send_message(self, message: Dict[str, Any]) -> str:
        """
        Send message to SQS queue.

        Args:
            message: Message to send

        Returns:
            Message ID

        Raises:
            AWSServiceError: If send fails
        """
        try:
            start_time = datetime.utcnow()

            response = self.sqs_client.send_message(
                QueueUrl=self.queue_url,
                MessageBody=json.dumps(message),
                MessageAttributes={
                    "timestamp": {
                        "StringValue": datetime.utcnow().isoformat(),
                        "DataType": "String",
                    },
                    "pipeline_version": {"StringValue": "1.0.0", "DataType": "String"},
                },
            )

            send_duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
            message_id = response["MessageId"]

            # Log successful send
            logger.info(
                "SQS message sent",
                extra={
                    "message_id": message_id,
                    "queue_url": self.queue_url,
                    "message_size_bytes": len(json.dumps(message)),
                    "send_duration_ms": send_duration_ms,
                    "operation": "sqs_send_message",
                },
            )

            # Log metrics
            log_metric(logger, "sqs_messages_sent", 1, "Count")
            log_metric(logger, "sqs_send_duration", send_duration_ms, "Milliseconds")

            return message_id

        except ClientError as e:
            error_code = e.response["Error"]["Code"]

            logger.error(
                "SQS send failed",
                extra={
                    "queue_url": self.queue_url,
                    "error_code": error_code,
                    "error_message": str(e),
                    "operation": "sqs_send_message",
                },
                exc_info=True,
            )

            # Log error metric
            log_metric(logger, "sqs_send_errors", 1, "Count", {"ErrorCode": error_code})

            raise AWSServiceError(f"Cannot send to SQS: {e}") from e

    def receive_messages(self, max_messages: int = 1) -> List[Dict[str, Any]]:
        """
        Receive messages from SQS queue.

        Args:
            max_messages: Maximum messages to receive

        Returns:
            List of messages
        """
        try:
            response = self.sqs_client.receive_message(
                QueueUrl=self.queue_url,
                MaxNumberOfMessages=max_messages,
                MessageAttributeNames=["All"],
            )

            messages = []
            for msg in response.get("Messages", []):
                messages.append(
                    {
                        "id": msg["MessageId"],
                        "receipt_handle": msg["ReceiptHandle"],
                        "body": json.loads(msg["Body"]),
                        "attributes": msg.get("MessageAttributes", {}),
                    }
                )

            logger.debug(f"Received {len(messages)} messages from SQS")
            return messages
        except ClientError as e:
            logger.error(f"Failed to receive from SQS: {e}")
            raise AWSServiceError(f"Cannot receive from SQS: {e}") from e

    def send_to_dlq(self, message: Dict[str, Any], error: str) -> str:
        """
        Send failed message to dead letter queue.

        Args:
            message: Original message
            error: Error description

        Returns:
            DLQ message ID
        """
        if not self.dlq_url:
            logger.warning("No DLQ configured, message will be lost")
            return ""

        try:
            dlq_message = {
                "original_message": message,
                "error": error,
                "failed_at": datetime.utcnow().isoformat(),
            }

            response = self.sqs_client.send_message(
                QueueUrl=self.dlq_url, MessageBody=json.dumps(dlq_message)
            )

            message_id = response["MessageId"]
            logger.info(f"Sent message to DLQ: {message_id}")
            return message_id
        except ClientError as e:
            logger.error(f"Failed to send to DLQ: {e}")
            raise AWSServiceError(f"Cannot send to DLQ: {e}") from e
