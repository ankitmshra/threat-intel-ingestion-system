import os

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
