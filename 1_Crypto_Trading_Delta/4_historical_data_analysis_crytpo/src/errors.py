"""Application errors."""

from __future__ import annotations


class ConfigError(Exception):
    """Invalid configuration or credentials."""


class DeltaClientError(Exception):
    """A read-only market-data request failed, or a non-GET method was refused."""
