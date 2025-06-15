# misp_stix_pipeline/tests/test_core.py
"""Unit tests for the MISP-STIX pipeline core components."""

import unittest
from unittest.mock import MagicMock, Mock, patch

from misp_stix_pipeline.config import ConfigManager
from misp_stix_pipeline.core.aws_clients import S3Manager, SQSManager
from misp_stix_pipeline.core.misp_client import MISPClient
from misp_stix_pipeline.core.orchestrator import PipelineOrchestrator
from misp_stix_pipeline.core.stix_converter import STIXConverter
from misp_stix_pipeline.utils.exceptions import (
    AWSServiceError,
    ConfigurationError,
    MISPConnectionError,
    STIXConversionError,
)


class TestConfigManager(unittest.TestCase):
    """Test cases for ConfigManager."""

    def setUp(self):
        """Reset singleton instance before each test."""
        ConfigManager._instance = None

    @patch("boto3.client")
    def test_singleton_pattern(self, mock_boto_client):
        """Test that ConfigManager follows singleton pattern."""
        config1 = ConfigManager()
        config2 = ConfigManager()
        self.assertIs(config1, config2)

    @patch("boto3.client")
    def test_get_parameter(self, mock_boto_client):
        """Test parameter retrieval from Parameter Store."""
        # Mock SSM client
        mock_ssm = MagicMock()
        mock_boto_client.return_value = mock_ssm

        mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "test-value"}}

        config = ConfigManager()
        value = config.get_parameter("test-param")

        self.assertEqual(value, "test-value")
        mock_ssm.get_parameter.assert_called_once_with(Name="test-param", WithDecryption=False)

    @patch("boto3.client")
    def test_get_secret(self, mock_boto_client):
        """Test secret retrieval from Secrets Manager."""
        # Mock secrets client
        mock_secrets = MagicMock()
        mock_boto_client.return_value = mock_secrets

        mock_secrets.get_secret_value.return_value = {"SecretString": '{"api_key": "secret-key"}'}

        config = ConfigManager()
        secret = config.get_secret("test-secret")

        self.assertEqual(secret, {"api_key": "secret-key"})
        mock_secrets.get_secret_value.assert_called_once_with(SecretId="test-secret")


class TestMISPClient(unittest.TestCase):
    """Test cases for MISPClient."""

    @patch("misp_stix_pipeline.core.misp_client.PyMISP")
    def test_initialization(self, mock_pymisp):
        """Test MISP client initialization."""
        client = MISPClient("https://misp.test", "api-key")
        mock_pymisp.assert_called_once_with("https://misp.test", "api-key", True)

    @patch("misp_stix_pipeline.core.misp_client.PyMISP")
    def test_get_event_count(self, mock_pymisp):
        """Test getting event count."""
        mock_misp_instance = MagicMock()
        mock_pymisp.return_value = mock_misp_instance

        # Mock search_index response
        mock_misp_instance.search_index.return_value = [{"id": "1"}, {"id": "2"}, {"id": "3"}]

        client = MISPClient("https://misp.test", "api-key")
        count = client.get_event_count()

        self.assertEqual(count, 3)
        mock_misp_instance.search_index.assert_called_once_with(minimal=True)

    @patch("misp_stix_pipeline.core.misp_client.PyMISP")
    def test_get_new_events(self, mock_pymisp):
        """Test fetching new events."""
        mock_misp_instance = MagicMock()
        mock_pymisp.return_value = mock_misp_instance

        # Mock search response
        mock_events = [Mock(uuid="event-1"), Mock(uuid="event-2")]
        mock_misp_instance.search.return_value = mock_events

        client = MISPClient("https://misp.test", "api-key")
        events = client.get_new_events(limit=2)

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].uuid, "event-1")


class TestSTIXConverter(unittest.TestCase):
    """Test cases for STIXConverter."""

    def test_initialization(self):
        """Test STIX converter initialization."""
        converter = STIXConverter(stix_version="2.1")
        self.assertEqual(converter.stix_version, "2.1")

        # Test invalid version
        with self.assertRaises(ValueError):
            STIXConverter(stix_version="1.0")

    @patch("misp_stix_pipeline.core.stix_converter.MISPtoSTIX21Parser")
    def test_convert_to_stix(self, mock_parser_class):
        """Test MISP to STIX conversion."""
        # Mock parser
        mock_parser = MagicMock()
        mock_parser_class.return_value = mock_parser

        # Mock bundle
        mock_bundle = MagicMock()
        mock_bundle.serialize.return_value = '{"type": "bundle"}'
        mock_parser.bundle = mock_bundle

        # Mock event
        mock_event = Mock(uuid="test-uuid", id="123")

        converter = STIXConverter()
        result = converter.convert_to_stix(mock_event)

        self.assertEqual(result, {"type": "bundle"})
        mock_parser.parse_misp_event.assert_called_once_with(mock_event)


class TestS3Manager(unittest.TestCase):
    """Test cases for S3Manager."""

    @patch("boto3.client")
    def test_save(self, mock_boto_client):
        """Test saving data to S3."""
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        manager = S3Manager("test-bucket", "prefix")
        data = b"test data"
        url = manager.save(data, "test-key.json")

        self.assertEqual(url, "s3://test-bucket/prefix/test-key.json")
        mock_s3.put_object.assert_called_once()

    @patch("boto3.client")
    def test_retrieve(self, mock_boto_client):
        """Test retrieving data from S3."""
        mock_s3 = MagicMock()
        mock_boto_client.return_value = mock_s3

        # Mock response
        mock_body = MagicMock()
        mock_body.read.return_value = b"test data"
        mock_s3.get_object.return_value = {"Body": mock_body}

        manager = S3Manager("test-bucket", "prefix")
        data = manager.retrieve("test-key.json")

        self.assertEqual(data, b"test data")


class TestSQSManager(unittest.TestCase):
    """Test cases for SQSManager."""

    @patch("boto3.client")
    def test_send_message(self, mock_boto_client):
        """Test sending message to SQS."""
        mock_sqs = MagicMock()
        mock_boto_client.return_value = mock_sqs

        mock_sqs.send_message.return_value = {"MessageId": "msg-123"}

        manager = SQSManager("https://sqs.test/queue")
        message = {"test": "data"}
        msg_id = manager.send_message(message)

        self.assertEqual(msg_id, "msg-123")
        mock_sqs.send_message.assert_called_once()

    @patch("boto3.client")
    def test_send_to_dlq(self, mock_boto_client):
        """Test sending message to DLQ."""
        mock_sqs = MagicMock()
        mock_boto_client.return_value = mock_sqs

        mock_sqs.send_message.return_value = {"MessageId": "dlq-123"}

        manager = SQSManager("https://sqs.test/queue", "https://sqs.test/dlq")

        original_msg = {"test": "data"}
        msg_id = manager.send_to_dlq(original_msg, "Test error")

        self.assertEqual(msg_id, "dlq-123")


class TestPipelineOrchestrator(unittest.TestCase):
    """Test cases for PipelineOrchestrator."""

    @patch("misp_stix_pipeline.core.orchestrator.ConfigManager")
    @patch("misp_stix_pipeline.core.orchestrator.MISPClient")
    @patch("misp_stix_pipeline.core.orchestrator.STIXConverter")
    @patch("misp_stix_pipeline.core.orchestrator.S3Manager")
    @patch("misp_stix_pipeline.core.orchestrator.SQSManager")
    def test_initialization(self, mock_sqs, mock_s3, mock_stix, mock_misp, mock_config):
        """Test orchestrator initialization."""
        # Mock config
        mock_config_instance = MagicMock()
        mock_config.return_value = mock_config_instance
        mock_config_instance.get_config.return_value = {
            "misp_url": "https://misp.test",
            "misp_api_key": "test-key",
            "s3_bucket": "test-bucket",
            "s3_prefix": "prefix",
            "sqs_queue_url": "https://sqs.test/queue",
            "dlq_url": "https://sqs.test/dlq",
            "batch_size": 100,
        }

        orchestrator = PipelineOrchestrator()

        # Verify components were initialized
        mock_misp.assert_called_once()
        mock_stix.assert_called_once()
        mock_s3.assert_called_once()
        mock_sqs.assert_called_once()

    @patch("misp_stix_pipeline.core.orchestrator.ConfigManager")
    @patch("misp_stix_pipeline.core.orchestrator.MISPClient")
    @patch("misp_stix_pipeline.core.orchestrator.STIXConverter")
    @patch("misp_stix_pipeline.core.orchestrator.S3Manager")
    @patch("misp_stix_pipeline.core.orchestrator.SQSManager")
    def test_run_pipeline(self, mock_sqs, mock_s3, mock_stix, mock_misp, mock_config):
        """Test running the complete pipeline."""
        # Setup mocks
        mock_config_instance = MagicMock()
        mock_config.return_value = mock_config_instance
        mock_config_instance.get_config.return_value = {
            "misp_url": "https://misp.test",
            "misp_api_key": "test-key",
            "s3_bucket": "test-bucket",
            "s3_prefix": "prefix",
            "sqs_queue_url": "https://sqs.test/queue",
            "dlq_url": "https://sqs.test/dlq",
            "batch_size": 100,
        }

        # Mock MISP client
        mock_misp_instance = MagicMock()
        mock_misp.return_value = mock_misp_instance
        mock_misp_instance.get_event_count.return_value = 10

        # Mock events
        mock_event = Mock(uuid="test-uuid", id="123")
        mock_misp_instance.get_new_events.return_value = [mock_event]

        # Mock STIX converter
        mock_stix_instance = MagicMock()
        mock_stix.return_value = mock_stix_instance
        mock_stix_instance.process.return_value = {
            "event_id": "123",
            "event_uuid": "test-uuid",
            "stix_bundle": {"type": "bundle"},
            "converted_at": "2024-01-01T00:00:00",
        }

        # Mock S3 manager
        mock_s3_instance = MagicMock()
        mock_s3.return_value = mock_s3_instance
        mock_s3_instance.save_stix_bundle.return_value = "s3://bucket/key"

        # Mock SQS manager
        mock_sqs_instance = MagicMock()
        mock_sqs.return_value = mock_sqs_instance

        orchestrator = PipelineOrchestrator()
        summary = orchestrator.run()

        # Verify results
        self.assertEqual(summary["total_events"], 10)
        self.assertEqual(summary["processed_events"], 1)
        self.assertEqual(summary["failed_events"], 0)
        self.assertEqual(summary["messages_sent"], 1)

        # Verify method calls
        mock_misp_instance.get_event_count.assert_called_once()
        mock_misp_instance.get_new_events.assert_called_once()
        mock_stix_instance.process.assert_called_once()
        mock_s3_instance.save_stix_bundle.assert_called_once()
        mock_sqs_instance.send_message.assert_called_once()


if __name__ == "__main__":
    unittest.main()

# Additional test file for integration tests
"""
# misp_stix_pipeline/tests/test_integration.py
'''Integration tests for the MISP-STIX pipeline.'''

import unittest
import json
from unittest.mock import patch, MagicMock
from datetime import datetime

from misp_stix_pipeline.core.orchestrator import PipelineOrchestrator


class TestPipelineIntegration(unittest.TestCase):
    '''Integration tests for complete pipeline flow.'''
    
    @patch('boto3.client')
    @patch('misp_stix_pipeline.core.misp_client.PyMISP')
    def test_end_to_end_flow(self, mock_pymisp, mock_boto):
        '''Test complete end-to-end pipeline flow.'''
        # This would be a more comprehensive integration test
        # that tests the full flow with minimal mocking
        pass
    
    def test_error_handling_flow(self):
        '''Test pipeline error handling and DLQ flow.'''
        # Test various error scenarios and verify DLQ handling
        pass


if __name__ == '__main__':
    unittest.main()
"""
