import os
from typing import Dict, Union

import boto3
from mypy_boto3_secretsmanager import SecretsManagerClient
from mypy_boto3_ssm import SSMClient


class parameterStoreManager:
    """Manages SSM Parameter Store operations with caching"""

    def __init__(self):
        self._cache: dict[str, str] = {}
        self.parameter_prefix: str = os.getenv("PARAMETER_PREFIX", "/misp-threat-feeds")

    def get_parameter(self, parameter_name: str, decrypt: bool = False) -> str:
        """get parameter from parameter store with caching"""
        cache_key = f"{parameter_name}_{decrypt}"

        if cache_key in self._cache:
            return self._cache[cache_key]

        ssm_client: SSMClient = boto3.client("ssm")  # type: ignore[misc]
        if not ssm_client:
            raise RuntimeError(
                "Failed to create SSM client. Ensure AWS credentials are configured."
            )

        try:
            response = ssm_client.get_parameter(
                Name=f"{self.parameter_prefix}/{parameter_name}", WithDecryption=decrypt
            )
            parameter_value: str = response["Parameter"].get("Value", "")
            if not parameter_value:
                raise ValueError(f"Parameter {parameter_name} has no value")
            self._cache[cache_key] = parameter_value
            return parameter_value

        except ssm_client.exceptions.ParameterNotFound:
            raise ValueError(f"Parameter '{parameter_name}' not found in Parameter Store.")
        except Exception as e:
            raise RuntimeError(f"Failed to retrieve parameter '{parameter_name}': {str(e)}") from e


class secretsManager:
    """Manages Secrets Manager operations with caching"""

    def __init__(self):
        self._cache: dict[str, str] = {}

    def get_secret(self, secret_name: str) -> str:
        """Get secret from Secrets Manager with caching"""
        if secret_name in self._cache:
            return self._cache[secret_name]

        secrets_client: SecretsManagerClient = boto3.client("secretsmanager")  # type: ignore[misc]
        if not secrets_client:
            raise RuntimeError(
                "Failed to create Secrets Manager client. Ensure AWS credentials are configured."
            )

        try:
            response = secrets_client.get_secret_value(SecretId=secret_name)
            secret_value = response["SecretString"]
            self._cache[secret_name] = secret_value
            return secret_value

        except secrets_client.exceptions.ResourceNotFoundException:
            raise ValueError(f"Secret '{secret_name}' not found in Secrets Manager.")
        except Exception as e:
            raise RuntimeError(f"Failed to retrieve secret '{secret_name}': {str(e)}") from e


class MispConfig:
    """Configuration manager for MISP threat feeds"""

    def __init__(self):
        self.parameter_store: parameterStoreManager = parameterStoreManager()
        self.secrets_manager: secretsManager = secretsManager()

        # Cache attributes for properties
        self._misp_url: str | None = None
        self._misp_verify_cert: bool | None = None
        self._s3_bucket: str | None = None
        self._sqs_queue_url: str | None = None
        self._last_sync_hours: str | None = None
        self._misp_key: str | None = None

    @property
    def misp_url(self) -> str:
        """MISP server URL"""
        if self._misp_url is None:
            self._misp_url = self.parameter_store.get_parameter("misp-url")
        return self._misp_url

    @property
    def misp_verify_cert(self) -> bool:
        """Whether to verify MISP SSL certificate"""
        if self._misp_verify_cert is None:
            self._misp_verify_cert = (
                self.parameter_store.get_parameter("misp-verify-cert").lower() == "true"
            )
        return self._misp_verify_cert

    @property
    def s3_bucket(self) -> str:
        """S3 bucket name for storing threat feeds"""
        if self._s3_bucket is None:
            self._s3_bucket = self.parameter_store.get_parameter("s3-bucket-name")
        return self._s3_bucket

    @property
    def sqs_queue_url(self) -> str:
        """SQS queue URL for processing messages"""
        if self._sqs_queue_url is None:
            self._sqs_queue_url = self.parameter_store.get_parameter("sqs-queue-url")
        return self._sqs_queue_url

    @property
    def last_sync_hours(self) -> str:
        """Hours since last synchronization"""
        if self._last_sync_hours is None:
            self._last_sync_hours = self.parameter_store.get_parameter("last-sync-hours")
        return self._last_sync_hours

    @property
    def misp_key(self) -> str:
        """MISP API key from Secrets Manager"""
        if self._misp_key is None:
            self._misp_key = self.secrets_manager.get_secret("misp-api-key")
        return self._misp_key

    def load_config(self) -> Dict[str, Union[str, bool]]:
        """Load all configuration parameters as a dictionary"""
        return {
            "misp_url": self.misp_url,
            "misp_verify_cert": self.misp_verify_cert,
            "s3_bucket": self.s3_bucket,
            "sqs_queue_url": self.sqs_queue_url,
            "last_sync_hours": self.last_sync_hours,
            "misp_key": self.misp_key,
        }

    def clear_cache(self) -> None:
        """Clear all cached property values to force re-fetching"""
        self._misp_url = None
        self._misp_verify_cert = None
        self._s3_bucket = None
        self._sqs_queue_url = None
        self._last_sync_hours = None
        self._misp_key = None

    def get_parameter(self, parameter_name: str, decrypt: bool = False) -> str:
        """Get a specific parameter from Parameter Store"""
        return self.parameter_store.get_parameter(parameter_name, decrypt)

    def get_secret(self, secret_name: str) -> str:
        """Get a specific secret from Secrets Manager"""
        return self.secrets_manager.get_secret(secret_name)
