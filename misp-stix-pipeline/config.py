"""Configuration management using AWS Parameter Store and Secrets Manager."""

import json
from functools import lru_cache
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError

from .utils.exceptions import ConfigurationError
from .utils.logging import setup_logger

logger = setup_logger(__name__)


class ConfigManager:
    """
    Singleton configuration manager that fetches configuration from AWS services.

    Uses AWS Parameter Store for application configuration and
    AWS Secrets Manager for sensitive data like API keys.
    """

    _instance: Optional["ConfigManager"] = None

    def __new__(cls) -> "ConfigManager":
        """Implement singleton pattern."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """Initialize AWS clients if not already initialized."""
        if not hasattr(self, "_initialized"):
            self.ssm_client = boto3.client("ssm")
            self.secrets_client = boto3.client("secretsmanager")
            self._config_cache: Dict[str, Any] = {}
            self._initialized = True
            logger.info("ConfigManager initialized")

    @lru_cache(maxsize=128)
    def get_parameter(self, parameter_name: str, decrypt: bool = False) -> str:
        """
        Fetch parameter from AWS Parameter Store.

        Args:
            parameter_name: Name of the parameter
            decrypt: Whether to decrypt SecureString parameters

        Returns:
            Parameter value

        Raises:
            ConfigurationError: If parameter cannot be fetched
        """
        try:
            response = self.ssm_client.get_parameter(Name=parameter_name, WithDecryption=decrypt)
            value = response["Parameter"]["Value"]
            logger.debug(f"Retrieved parameter: {parameter_name}")
            return value
        except ClientError as e:
            logger.error(f"Failed to fetch parameter {parameter_name}: {e}")
            raise ConfigurationError(f"Cannot fetch parameter: {parameter_name}") from e

    @lru_cache(maxsize=32)
    def get_secret(self, secret_name: str) -> Dict[str, Any]:
        """
        Fetch secret from AWS Secrets Manager.

        Args:
            secret_name: Name of the secret

        Returns:
            Secret value (parsed as JSON if possible)

        Raises:
            ConfigurationError: If secret cannot be fetched
        """
        try:
            response = self.secrets_client.get_secret_value(SecretId=secret_name)
            secret_string = response["SecretString"]

            # Try to parse as JSON
            try:
                secret_value = json.loads(secret_string)
            except json.JSONDecodeError:
                secret_value = {"value": secret_string}

            logger.debug(f"Retrieved secret: {secret_name}")
            return secret_value
        except ClientError as e:
            logger.error(f"Failed to fetch secret {secret_name}: {e}")
            raise ConfigurationError(f"Cannot fetch secret: {secret_name}") from e

    def get_config(self) -> Dict[str, Any]:
        """
        Get all configuration values.

        Returns:
            Dictionary containing all configuration
        """
        if not self._config_cache:
            self._load_config()
        return self._config_cache.copy()

    def _load_config(self) -> None:
        """Load all configuration from AWS services."""
        try:
            # Load from Parameter Store
            self._config_cache["sqs_queue_url"] = self.get_parameter("/misp-pipeline/sqs-queue-url")
            self._config_cache["dlq_url"] = self.get_parameter("/misp-pipeline/dlq-url")
            self._config_cache["s3_bucket"] = self.get_parameter("/misp-pipeline/s3-bucket")
            self._config_cache["s3_prefix"] = self.get_parameter("/misp-pipeline/s3-prefix")
            self._config_cache["misp_url"] = self.get_parameter("/misp-pipeline/misp-url")
            self._config_cache["batch_size"] = int(
                self.get_parameter("/misp-pipeline/batch-size", "100")
            )

            # Load from Secrets Manager
            misp_secret = self.get_secret("misp-pipeline/api-key")
            self._config_cache["misp_api_key"] = misp_secret.get(
                "api_key", misp_secret.get("value")
            )

            logger.info("Configuration loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load configuration: {e}")
            raise ConfigurationError("Cannot load configuration") from e
