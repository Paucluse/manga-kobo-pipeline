"""Configuration management using Pydantic.

Reads from config.yaml with environment variable overrides.
All paths and settings are validated through Pydantic models.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class PathsConfig(BaseModel):
    """Data directory paths."""

    inbox: Path = Path("/data/inbox")
    processing: Path = Path("/data/processing")
    archive_cbz: Path = Path("/data/archive_cbz")
    kepub_ready: Path = Path("/data/kepub_ready")
    calibre_library: Path = Path("/data/calibre-library")
    state: Path = Path("/data/state")
    manual_review: Path = Path("/data/manual-review")
    logs: Path = Path("/data/logs")


class KoboConfig(BaseModel):
    """Kobo device and KCC conversion settings."""

    profile: str = "KoS"
    format: str = "KEPUB"
    manga_style: bool = True
    high_quality: bool = True


class MetadataConfig(BaseModel):
    """Metadata defaults for imported manga."""

    default_language: str = "zho"
    confidence_auto_accept: float = 0.4
    default_tags: list[str] = Field(
        default_factory=lambda: ["manga", "chinese-translation", "kobo-sync"]
    )


class CommandsConfig(BaseModel):
    """External command paths."""

    kcc: str = "kcc-c2e"
    calibredb: str = "calibredb"


class ProcessingConfig(BaseModel):
    """Processing behavior settings."""

    stable_check_seconds: int = 30
    stable_check_interval: int = 5
    poll_interval_seconds: int = 10
    delete_inbox_after_archive: bool = True
    cleanup_after_import: bool = True
    max_retries: int = 3


class LoggingConfig(BaseModel):
    """Logging settings."""

    level: str = "INFO"
    format: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


class PipelineConfig(BaseModel):
    """Root configuration model."""

    paths: PathsConfig = Field(default_factory=PathsConfig)
    kobo: KoboConfig = Field(default_factory=KoboConfig)
    metadata: MetadataConfig = Field(default_factory=MetadataConfig)
    commands: CommandsConfig = Field(default_factory=CommandsConfig)
    processing: ProcessingConfig = Field(default_factory=ProcessingConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


def _find_config_path() -> Path | None:
    """Find config file path from environment or default locations.

    Search order:
    1. MANGA_PIPELINE_CONFIG environment variable
    2. ./config.yaml (current directory)
    3. /app/config.yaml (Docker default)
    """
    env_path = os.environ.get("MANGA_PIPELINE_CONFIG")
    if env_path:
        p = Path(env_path)
        if p.is_file():
            return p

    local = Path("config.yaml")
    if local.is_file():
        return local

    docker_default = Path("/app/config.yaml")
    if docker_default.is_file():
        return docker_default

    return None


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load and parse a YAML file."""
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def load_config(config_path: Path | None = None) -> PipelineConfig:
    """Load pipeline configuration.

    Args:
        config_path: Explicit path to config.yaml. If None, searches default locations.

    Returns:
        Validated PipelineConfig instance with defaults for any missing values.
    """
    if config_path is None:
        config_path = _find_config_path()

    if config_path is not None and config_path.is_file():
        data = _load_yaml(config_path)
        return PipelineConfig.model_validate(data)

    # No config file found — use all defaults
    return PipelineConfig()


def get_config_path() -> Path | None:
    """Return the path to the config file that would be loaded, or None."""
    return _find_config_path()
