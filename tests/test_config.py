"""Tests for config module."""

from pathlib import Path

import yaml

from manga_pipeline.config import PipelineConfig, load_config


class TestPipelineConfigDefaults:
    """Test default configuration values."""

    def test_default_config_has_all_sections(self) -> None:
        """Default config should have all sections populated."""
        cfg = PipelineConfig()
        assert cfg.paths is not None
        assert cfg.kobo is not None
        assert cfg.metadata is not None
        assert cfg.commands is not None
        assert cfg.processing is not None
        assert cfg.logging is not None

    def test_default_paths(self) -> None:
        """Default paths should point to /data/ subdirectories."""
        cfg = PipelineConfig()
        assert cfg.paths.inbox == Path("/data/inbox")
        assert cfg.paths.processing == Path("/data/processing")
        assert cfg.paths.calibre_library == Path("/data/calibre-library")

    def test_default_kobo_profile(self) -> None:
        """Default Kobo profile should be KoS (Kobo Sage)."""
        cfg = PipelineConfig()
        assert cfg.kobo.profile == "KoS"
        assert cfg.kobo.manga_style is True
        assert cfg.kobo.high_quality is True
        assert cfg.kobo.format == "KEPUB"

    def test_default_metadata(self) -> None:
        """Default metadata should have Chinese language and manga tags."""
        cfg = PipelineConfig()
        assert cfg.metadata.default_language == "zho"
        assert cfg.metadata.confidence_auto_accept == 0.85
        assert "manga" in cfg.metadata.default_tags
        assert "kobo-sync" in cfg.metadata.default_tags

    def test_default_commands(self) -> None:
        """Default commands should use bare names (found via PATH)."""
        cfg = PipelineConfig()
        assert cfg.commands.kcc == "kcc-c2e"
        assert cfg.commands.calibredb == "calibredb"

    def test_default_processing(self) -> None:
        """Default processing should have safe defaults."""
        cfg = PipelineConfig()
        assert cfg.processing.delete_inbox_after_archive is True
        assert cfg.processing.cleanup_after_import is True
        assert cfg.processing.max_retries == 3
        assert cfg.processing.stable_check_seconds == 30


class TestLoadConfigFromYaml:
    """Test loading config from YAML files."""

    def test_load_from_yaml_file(self, tmp_path: Path) -> None:
        """Should load and merge values from a YAML file."""
        config_data = {
            "paths": {
                "inbox": "/custom/inbox",
                "processing": "/custom/processing",
            },
            "kobo": {
                "profile": "KoA",
            },
            "metadata": {
                "default_language": "eng",
            },
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(config_data), encoding="utf-8")

        cfg = load_config(config_file)

        # Overridden values
        assert cfg.paths.inbox == Path("/custom/inbox")
        assert cfg.paths.processing == Path("/custom/processing")
        assert cfg.kobo.profile == "KoA"
        assert cfg.metadata.default_language == "eng"

        # Default values preserved for non-overridden fields
        assert cfg.paths.archive_cbz == Path("/data/archive_cbz")
        assert cfg.kobo.manga_style is True
        assert cfg.commands.kcc == "kcc-c2e"

    def test_load_empty_yaml(self, tmp_path: Path) -> None:
        """Empty YAML file should produce all defaults."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("", encoding="utf-8")

        cfg = load_config(config_file)
        assert cfg.paths.inbox == Path("/data/inbox")
        assert cfg.kobo.profile == "KoS"

    def test_load_partial_yaml(self, tmp_path: Path) -> None:
        """Partial YAML should only override specified values."""
        config_data = {
            "processing": {
                "max_retries": 5,
                "delete_inbox_after_archive": False,
            }
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(config_data), encoding="utf-8")

        cfg = load_config(config_file)
        assert cfg.processing.max_retries == 5
        assert cfg.processing.delete_inbox_after_archive is False
        # Other sections untouched
        assert cfg.paths.inbox == Path("/data/inbox")

    def test_load_nonexistent_file_returns_defaults(self) -> None:
        """Non-existent config file should return defaults."""
        cfg = load_config(Path("/nonexistent/config.yaml"))
        assert cfg.paths.inbox == Path("/data/inbox")

    def test_load_with_none_returns_defaults(self) -> None:
        """load_config(None) should return defaults when no config found."""
        # This test works because we don't have config.yaml in CWD during tests
        cfg = load_config(None)
        assert isinstance(cfg, PipelineConfig)

    def test_load_full_example_config(self, tmp_path: Path) -> None:
        """Full config with all sections should load correctly."""
        config_data = {
            "paths": {
                "inbox": "/srv/ebooks/inbox",
                "processing": "/srv/ebooks/processing",
                "archive_cbz": "/srv/ebooks/archive_cbz",
                "kepub_ready": "/srv/ebooks/kepub_ready",
                "calibre_library": "/srv/ebooks/calibre-library",
                "state": "/srv/ebooks/state",
                "manual_review": "/srv/ebooks/manual-review",
                "logs": "/srv/ebooks/logs",
            },
            "kobo": {
                "profile": "KoS",
                "format": "EPUB",
                "manga_style": True,
                "high_quality": True,
            },
            "metadata": {
                "default_language": "zho",
                "confidence_auto_accept": 0.9,
                "default_tags": ["manga", "chinese-translation"],
            },
            "commands": {
                "kcc": "/usr/local/bin/kcc-c2e",
                "calibredb": "/usr/bin/calibredb",
            },
            "processing": {
                "stable_check_seconds": 60,
                "stable_check_interval": 10,
                "delete_inbox_after_archive": False,
                "cleanup_after_import": False,
                "max_retries": 5,
            },
            "logging": {
                "level": "DEBUG",
                "format": "%(message)s",
            },
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(config_data), encoding="utf-8")

        cfg = load_config(config_file)
        assert cfg.paths.inbox == Path("/srv/ebooks/inbox")
        assert cfg.metadata.confidence_auto_accept == 0.9
        assert cfg.commands.kcc == "/usr/local/bin/kcc-c2e"
        assert cfg.processing.stable_check_seconds == 60
        assert cfg.logging.level == "DEBUG"
        assert len(cfg.metadata.default_tags) == 2


class TestLoadConfigEnvironment:
    """Test config path resolution from environment."""

    def test_env_var_config_path(self, tmp_path: Path, monkeypatch: object) -> None:
        """MANGA_PIPELINE_CONFIG env var should override default search."""
        config_data = {"kobo": {"profile": "KoF"}}
        config_file = tmp_path / "custom_config.yaml"
        config_file.write_text(yaml.dump(config_data), encoding="utf-8")

        import os

        env = {**os.environ, "MANGA_PIPELINE_CONFIG": str(config_file)}
        monkeypatch.setattr(os, "environ", env)  # type: ignore[attr-defined]

        cfg = load_config()
        assert cfg.kobo.profile == "KoF"
