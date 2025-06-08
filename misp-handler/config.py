import logging
import os
from typing import Dict, Optional, Union

import boto3
from mypy_boto3_secretsmanager import SecretsManagerClient
from mypy_boto3_ssm import SSMClient

from .logging import CloudWatchFormatter


def _setup_logger(name: str) -> logging.Logger:
    """Setup logger with CloudWatch formatter"""
    logger = logging.getLogger(name)
    if not logger.handlers:  # Avoid duplicate handlers
        logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
        handler = logging.StreamHandler()
        handler.setFormatter(CloudWatchFormatter())
        logger.addHandler(handler)
    return logger


class parameterStoreManager:
    """Manages SSM Parameter Store operations with caching"""

    def __init__(self):
        self._cache: Dict[str, str] = {}
        self.parameter_prefix: str = os.getenv("PARAMETER_PREFIX", "/misp-threat-feeds")
        self.logger = _setup_logger(f"{__name__}.{self.__class__.__name__}")

    def clear_cache(self) -> None:
        """Clear the parameter cache"""
        self.logger.debug("Clearing parameter store cache")
        self._cache.clear()

    def get_parameter(self, parameter_name: str, decrypt: bool = False) -> str:
        """get parameter from parameter store with caching"""
        cache_key = f"{parameter_name}_{decrypt}"

        if cache_key in self._cache:
            self.logger.debug(f"Retrieved parameter '{parameter_name}' from cache")
            return self._cache[cache_key]

        self.logger.info(f"Retrieving parameter '{parameter_name}' from SSM Parameter Store")

        ssm_client: SSMClient = boto3.client("ssm")  # type: ignore[misc]
        if not ssm_client:
            error_msg = "Failed to create SSM client. Ensure AWS credentials are configured."
            self.logger.error(error_msg)
            raise RuntimeError(error_msg)

        try:
            response = ssm_client.get_parameter(
                Name=f"{self.parameter_prefix}/{parameter_name}", WithDecryption=decrypt
            )
            parameter_value: str = response["Parameter"].get("Value", "")
            if not parameter_value:
                error_msg = f"Parameter {parameter_name} has no value"
                self.logger.error(error_msg)
                raise ValueError(error_msg)

            self._cache[cache_key] = parameter_value
            self.logger.info(f"Successfully retrieved parameter '{parameter_name}'")
            return parameter_value

        except ssm_client.exceptions.ParameterNotFound:
            error_msg = f"Parameter '{parameter_name}' not found in Parameter Store."
            self.logger.error(error_msg)
            raise ValueError(error_msg)
        except Exception as e:
            error_msg = f"Failed to retrieve parameter '{parameter_name}': {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e


class secretsManager:
    """Manages Secrets Manager operations with caching"""

    def __init__(self):
        self._cache: Dict[str, str] = {}
        self.logger = _setup_logger(f"{__name__}.{self.__class__.__name__}")

    def clear_cache(self) -> None:
        """Clear the secrets cache"""
        self.logger.debug("Clearing secrets manager cache")
        self._cache.clear()

    def get_secret(self, secret_name: str) -> str:
        """Get secret from Secrets Manager with caching"""
        if secret_name in self._cache:
            self.logger.debug(f"Retrieved secret '{secret_name}' from cache")
            return self._cache[secret_name]

        self.logger.info(f"Retrieving secret '{secret_name}' from Secrets Manager")

        secrets_client: SecretsManagerClient = boto3.client("secretsmanager")  # type: ignore[misc]
        if not secrets_client:
            error_msg = (
                "Failed to create Secrets Manager client. Ensure AWS credentials are configured."
            )
            self.logger.error(error_msg)
            raise RuntimeError(error_msg)

        try:
            response = secrets_client.get_secret_value(SecretId=secret_name)
            secret_value = response["SecretString"]
            self._cache[secret_name] = secret_value
            self.logger.info(f"Successfully retrieved secret '{secret_name}'")
            return secret_value

        except secrets_client.exceptions.ResourceNotFoundException:
            error_msg = f"Secret '{secret_name}' not found in Secrets Manager."
            self.logger.error(error_msg)
            raise ValueError(error_msg)
        except Exception as e:
            error_msg = f"Failed to retrieve secret '{secret_name}': {str(e)}"
            self.logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg) from e


class MispConfig:
    """Configuration manager for MISP threat feeds"""

    def __init__(self):
        self.logger = _setup_logger(f"{__name__}.{self.__class__.__name__}")
        self.logger.info("Initializing MISP configuration")

        try:
            self.parameter_store: parameterStoreManager = parameterStoreManager()
            self.secrets_manager: secretsManager = secretsManager()
        except Exception as e:
            self.logger.error(f"Failed to initialize config managers: {str(e)}", exc_info=True)
            raise

        # Cache attributes for properties
        self._misp_url: Optional[str] = None
        self._misp_verify_cert: Optional[bool] = None
        self._s3_bucket: Optional[str] = None
        self._sqs_queue_url: Optional[str] = None
        self._last_sync_hours: Optional[str] = None
        self._misp_key: Optional[str] = None

    @property
    def misp_url(self) -> str:
        """MISP server URL"""
        if self._misp_url is None:
            try:
                self._misp_url = self.parameter_store.get_parameter("misp-url")
            except Exception as e:
                self.logger.error(f"Failed to load MISP URL configuration: {str(e)}")
                raise
        return self._misp_url

    @property
    def misp_verify_cert(self) -> bool:
        """Whether to verify MISP SSL certificate"""
        if self._misp_verify_cert is None:
            try:
                cert_value = self.parameter_store.get_parameter("misp-verify-cert").lower()
                self._misp_verify_cert = cert_value == "true"
                self.logger.info(f"MISP certificate verification set to: {self._misp_verify_cert}")
            except Exception as e:
                self.logger.error(f"Failed to load MISP certificate verification setting: {str(e)}")
                raise
        return self._misp_verify_cert

    @property
    def s3_bucket(self) -> str:
        """S3 bucket name for storing threat feeds"""
        if self._s3_bucket is None:
            try:
                self._s3_bucket = self.parameter_store.get_parameter("s3-bucket-name")
            except Exception as e:
                self.logger.error(f"Failed to load S3 bucket configuration: {str(e)}")
                raise
        return self._s3_bucket

    @property
    def sqs_queue_url(self) -> str:
        """SQS queue URL for processing messages"""
        if self._sqs_queue_url is None:
            try:
                self._sqs_queue_url = self.parameter_store.get_parameter("sqs-queue-url")
            except Exception as e:
                self.logger.error(f"Failed to load SQS queue URL configuration: {str(e)}")
                raise
        return self._sqs_queue_url

    @property
    def last_sync_hours(self) -> str:
        """Hours since last synchronization"""
        if self._last_sync_hours is None:
            try:
                self._last_sync_hours = self.parameter_store.get_parameter("last-sync-hours")
            except Exception as e:
                self.logger.error(f"Failed to load last sync hours configuration: {str(e)}")
                raise
        return self._last_sync_hours

    @property
    def misp_key(self) -> str:
        """MISP API key from Secrets Manager"""
        if self._misp_key is None:
            try:
                self._misp_key = self.secrets_manager.get_secret("misp-api-key")
            except Exception as e:
                self.logger.error(f"Failed to load MISP API key from Secrets Manager: {str(e)}")
                raise
        return self._misp_key

    def load_config(self) -> Dict[str, Union[str, bool]]:
        """Load all configuration parameters as a dictionary"""
        self.logger.info("Loading all configuration parameters")
        try:
            config_dict: Dict[str, Union[str, bool]] = {
                "misp_url": self.misp_url,
                "misp_verify_cert": self.misp_verify_cert,
                "s3_bucket": self.s3_bucket,
                "sqs_queue_url": self.sqs_queue_url,
                "last_sync_hours": self.last_sync_hours,
                "misp_key": self.misp_key,
            }
            self.logger.info("Successfully loaded all configuration parameters")
            return config_dict
        except Exception as e:
            self.logger.error(f"Failed to load complete configuration: {str(e)}")
            raise

    def clear_cache(self) -> None:
        """Clear all cached property values to force re-fetching"""
        self.logger.info("Clearing configuration cache")
        self._misp_url = None
        self._misp_verify_cert = None
        self._s3_bucket = None
        self._sqs_queue_url = None
        self._last_sync_hours = None
        self._misp_key = None

        # Clear the underlying manager caches using their public methods
        self.parameter_store.clear_cache()
        self.secrets_manager.clear_cache()
        self.logger.info("Configuration cache cleared successfully")

    def get_parameter(self, parameter_name: str, decrypt: bool = False) -> str:
        """Get a specific parameter from Parameter Store"""
        return self.parameter_store.get_parameter(parameter_name, decrypt)

    def get_secret(self, secret_name: str) -> str:
        """Get a specific secret from Secrets Manager"""
        return self.secrets_manager.get_secret(secret_name)
