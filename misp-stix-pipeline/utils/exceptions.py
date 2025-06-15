"""Custom exceptions for the MISP-STIX pipeline."""


class MISPPipelineError(Exception):
    """Base exception for all pipeline errors."""

    pass


class ConfigurationError(MISPPipelineError):
    """Raised when configuration is invalid or missing."""

    pass


class MISPConnectionError(MISPPipelineError):
    """Raised when MISP connection fails."""

    pass


class STIXConversionError(MISPPipelineError):
    """Raised when STIX conversion fails."""

    pass


class AWSServiceError(MISPPipelineError):
    """Raised when AWS service operations fail."""

    pass
