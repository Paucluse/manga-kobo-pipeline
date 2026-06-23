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
    komga_library: Path = Path("/data/komga-library")
    state: Path = Path("/data/state")
    manual_review: Path = Path("/data/manual-review")
    logs: Path = Path("/data/logs")


class KoboConfig(BaseModel):
    """Kobo device and KCC conversion settings."""

    profile: str = "KoS"
    format: str = "KEPUB"
    manga_style: bool = True
    high_quality: bool = True


class PdfConfig(BaseModel):
    """PDF extraction/rasterization settings."""

    enabled: bool = True
    strategy: str = "extract_first"
    render_fallback: bool = False
    preserve_original: bool = True
    dpi: int = 180
    image_format: str = "jpg"
    jpeg_quality: int = 92


class MetadataConfig(BaseModel):
    """Metadata defaults for imported manga."""

    default_language: str = "zho"
    confidence_auto_accept: float = 0.4
    default_tags: list[str] = Field(
        default_factory=lambda: ["manga", "chinese-translation", "kobo-sync"]
    )
    bookwalker_tw_enabled: bool = True
    bookwalker_tw_min_confidence: float = 0.65
    bookwalker_tw_max_candidates: int = 8
    bookwalker_jp_enabled: bool = True
    bookwalker_jp_min_confidence: float = 0.65
    bookwalker_jp_max_candidates: int = 8
    bangumi_enabled: bool = True
    bangumi_min_confidence: float = 0.65
    bangumi_max_candidates: int = 30
    download_bookwalker_covers: bool = True
    llm_normalize_enabled: bool = False
    llm_verify_scrape_enabled: bool = False  # extra LLM call to validate scrape results
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = ""
    llm_api_key_file: Path | None = None
    llm_api_key_env: str = "OPENAI_API_KEY"
    llm_timeout_seconds: int = 30


class CommandsConfig(BaseModel):
    """External command paths."""

    kcc: str = "kcc-c2e"
    pdfimages: str = "pdfimages"
    pdftoppm: str = "pdftoppm"


class KomgaServerConfig(BaseModel):
    """Komga server connection settings."""

    base_uri: str = "http://komga:25600"
    user: str = "admin@manga.local"
    password: str = "changeme"
    library_id: str = ""  # Will auto-detect if empty


class ProcessingConfig(BaseModel):
    """Processing behavior settings."""

    stable_check_seconds: int = 30
    stable_check_interval: int = 5
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
    pdf: PdfConfig = Field(default_factory=PdfConfig)
    metadata: MetadataConfig = Field(default_factory=MetadataConfig)
    commands: CommandsConfig = Field(default_factory=CommandsConfig)
    komga: KomgaServerConfig = Field(default_factory=KomgaServerConfig)
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
