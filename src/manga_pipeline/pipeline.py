"""Pipeline orchestrator.

Coordinates the full manga processing pipeline:
discovered -> stable -> parse -> normalize -> archive -> convert -> import -> done
"""

from __future__ import annotations

import contextlib
import fcntl
import re
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path

from manga_pipeline.bangumi import search_bangumi
from manga_pipeline.bookwalker_jp import search_bookwalker_jp
from manga_pipeline.bookwalker_tw import (
    BookwalkerMetadata,
    download_cover,
    search_bookwalker_tw,
    to_bookwalker_traditional,
)
from manga_pipeline.comicinfo import write_comicinfo_to_cbz
from manga_pipeline.config import PipelineConfig
from manga_pipeline.control import (
    MODE_MANUAL_BOOK,
    MODE_MANUAL_SERIES,
    MODE_PAUSED,
    ControlStore,
    MetadataCandidate,
    candidate_from_metadata,
)
from manga_pipeline.database import Database
from manga_pipeline.epub_metadata import write_epub_metadata
from manga_pipeline.filename_parser import ParseResult, parse_filename
from manga_pipeline.kcc import run_kcc
from manga_pipeline.komga import get_library_id, trigger_library_scan
from manga_pipeline.llm_metadata import (
    LlmMetadata,
    ScrapeVerification,
    normalize_collection_with_llm,
    normalize_with_llm,
    verify_scrape_with_llm,
)
from manga_pipeline.logging_config import get_logger
from manga_pipeline.models import (
    SUPPORTED_EXTENSIONS,
    MangaRecord,
    ProcessingStatus,
    SeriesAnchor,
)
from manga_pipeline.normalizer import normalize_to_cbz

logger = get_logger(__name__)


def process_all_pending(cfg: PipelineConfig, db: Database) -> int:
    """Process all pending manga records through the pipeline.

    Iterates through records in order of their pipeline stage,
    advancing each one step at a time.

    Args:
        cfg: Pipeline configuration.
        db: Database instance.

    Returns:
        Number of records successfully processed to completion.
    """
    lock_path = cfg.paths.state / "process.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.info("Another manga-pipeline process is already running; skipping.")
            return 0

        return _process_all_pending_locked(cfg, db)


def _process_all_pending_locked(cfg: PipelineConfig, db: Database) -> int:
    completed = 0
    control = ControlStore(db.db_path.parent)
    try:
        if control.get_mode() == MODE_PAUSED:
            logger.info("Pipeline control mode is paused; skipping processing cycle.")
            return 0
    finally:
        control.close()

    _recover_already_imported_failed_records(cfg, db)
    _repair_done_collection_title_records(cfg, db)
    # Recover zombie tasks (stuck in intermediate states due to crash/restart)
    for status, fallback in [
        (ProcessingStatus.PROCESSING, ProcessingStatus.WAITING_STABLE),
        (ProcessingStatus.IMPORTING, ProcessingStatus.CONVERTED),
    ]:
        zombies = db.get_records_by_status(status)
        for z in zombies:
            logger.info(
                "[ID:%s] Recovering zombie %s task to %s",
                z.id,
                status.value,
                fallback.value,
            )
            db.update_status(z.id, fallback)  # type: ignore

    # Process each stage in order
    for status_to_process in [
        ProcessingStatus.DISCOVERED,
        ProcessingStatus.WAITING_STABLE,
        ProcessingStatus.NORMALIZED,
        ProcessingStatus.METADATA_PARSED,
        ProcessingStatus.ARCHIVED,
        ProcessingStatus.CONVERTED,
    ]:
        records = db.get_records_by_status(status_to_process)
        for record in records:
            try:
                result = _advance_record(record, cfg, db)
                if result and record.current_status == ProcessingStatus.DONE:
                    completed += 1
            except Exception:
                logger.exception(
                    "[ID:%s] Unexpected error processing %s",
                    record.id,
                    record.file_name,
                )
                db.update_status(
                    record.id,  # type: ignore[arg-type]
                    ProcessingStatus.FAILED,
                    error_message="Unexpected error during processing",
                )

    return completed


def _recover_already_imported_failed_records(
    cfg: PipelineConfig,
    db: Database,
) -> None:
    """Mark failed records as done when the KEPUB is already in Komga."""
    for record in db.get_records_by_status(ProcessingStatus.FAILED):
        if not record.converted_path:
            continue
        converted_path = Path(record.converted_path)
        series_name, _series_dir, dest_path = _expected_import_destination(
            record,
            cfg,
            converted_path,
        )
        if not dest_path.is_file():
            continue
        record_id = record.id
        assert record_id is not None
        logger.info(
            "[ID:%s] Recovering failed record already imported at %s",
            record_id,
            dest_path,
        )
        _mark_import_done(record_id, record, db, cfg, series_name, dest_path)


def _repair_done_collection_title_records(
    cfg: PipelineConfig,
    db: Database,
) -> None:
    """Normalize already imported collection records after title rules change."""
    for record in db.get_records_by_status(ProcessingStatus.DONE):
        if not record.collection_title:
            continue

        title = to_bookwalker_traditional(record.title or record.series)
        series = to_bookwalker_traditional(record.series or record.title)
        if title == record.title and series == record.series:
            continue

        record_id = record.id
        assert record_id is not None
        logger.info(
            "[ID:%s] Repairing collection title: %s -> %s",
            record_id,
            record.series or record.title,
            series or title,
        )

        record.title = title or record.title
        record.series = series or record.series or record.title

        archive_path = _repair_archive_path(record, cfg)
        imported_path = _repair_imported_epub_path(record, cfg)
        _rewrite_imported_metadata(record, cfg, archive_path, imported_path)

        db.update_status(
            record_id,
            ProcessingStatus.DONE,
            title=record.title,
            series=record.series,
            archive_path=str(archive_path) if archive_path else record.archive_path,
            converted_path=str(imported_path) if imported_path else record.converted_path,
            library_book_id=_build_series_name(record),
        )


def _repair_archive_path(record: MangaRecord, cfg: PipelineConfig) -> Path | None:
    archive_path = Path(record.archive_path)
    if not archive_path.is_file():
        return None

    target = cfg.paths.archive_cbz / f"{_build_clean_name(record)}.cbz"
    if archive_path != target and not target.exists():
        archive_path.rename(target)
        archive_path = target
    return archive_path


def _repair_imported_epub_path(record: MangaRecord, cfg: PipelineConfig) -> Path | None:
    current = _find_imported_epub(record, cfg)
    if current is None:
        return None

    target_dir = cfg.paths.komga_library / _build_series_name(record)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{_build_clean_name(record)}.kepub.epub"
    if current == target:
        return current
    if target.exists():
        return target

    old_cover = current.with_suffix(".jpg")
    new_cover = target.with_suffix(".jpg")
    old_parent = current.parent
    shutil.move(str(current), str(target))
    if old_cover.is_file() and not new_cover.exists():
        shutil.move(str(old_cover), str(new_cover))
    with contextlib.suppress(OSError):
        old_parent.rmdir()
    return target


def _find_imported_epub(record: MangaRecord, cfg: PipelineConfig) -> Path | None:
    candidates = []
    if record.converted_path:
        candidates.append(Path(record.converted_path))
    if record.archive_path:
        candidates.append(
            cfg.paths.komga_library
            / _sanitize_dirname(record.series or record.title)
            / Path(record.archive_path).with_suffix(".kepub.epub").name
        )

    names = [candidate.name for candidate in candidates if candidate.name]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    for name in dict.fromkeys(names):
        matches = list(cfg.paths.komga_library.rglob(name))
        if matches:
            return matches[0]
    return None


def _rewrite_imported_metadata(
    record: MangaRecord,
    cfg: PipelineConfig,
    archive_path: Path | None,
    imported_path: Path | None,
) -> None:
    if archive_path and archive_path.is_file():
        write_comicinfo_to_cbz(
            archive_path,
            title=_build_book_title(record),
            series=_build_series_name(record),
            number=record.volume,
            writer=record.author,
            publisher=record.publisher,
            summary=record.summary,
            web=record.source_url,
            language_iso=cfg.metadata.default_language,
            manga=cfg.kobo.manga_style,
            tags=cfg.metadata.default_tags,
        )

    if imported_path and imported_path.is_file():
        write_epub_metadata(
            imported_path,
            title=_build_book_title(record),
            series=_build_series_name(record),
            number=record.volume,
            writer=record.author,
            language_iso=cfg.metadata.default_language,
            summary=record.summary,
            manga=cfg.kobo.manga_style,
        )


def _advance_record(
    record: MangaRecord,
    cfg: PipelineConfig,
    db: Database,
) -> bool:
    """Advance a single record to its next pipeline stage.

    Returns True if the record was advanced, False if it couldn't be.
    """
    record_id = record.id
    assert record_id is not None

    status = record.current_status

    if status == ProcessingStatus.DISCOVERED:
        source_path = Path(record.original_path)
        if not record.original_path or not source_path.exists():
            db.update_status(
                record_id,
                ProcessingStatus.FAILED,
                error_message="Source file missing after pipeline reset",
            )
            return False
        db.update_status(record_id, ProcessingStatus.WAITING_STABLE)
        record.current_status = ProcessingStatus.WAITING_STABLE
        return _step_parse_metadata(record_id, record, cfg, db)

    if status == ProcessingStatus.WAITING_STABLE:
        return _step_parse_metadata(record_id, record, cfg, db)

    if status == ProcessingStatus.METADATA_PARSED:
        return _step_normalize_and_archive(record_id, record, cfg, db)

    if status == ProcessingStatus.ARCHIVED:
        return _step_convert_kcc(record_id, record, cfg, db)

    if status == ProcessingStatus.CONVERTED:
        return _step_import_komga(record_id, record, cfg, db)

    return False


def _search_bookwalker_tw_metadata(
    record_id: int,
    parsed: ParseResult,
    record: MangaRecord,
    llm_metadata: LlmMetadata | None,
    cfg: PipelineConfig,
) -> BookwalkerMetadata | None:
    if not cfg.metadata.bookwalker_tw_enabled:
        return None
    return _search_best_bookwalker_metadata(
        record_id=record_id,
        provider_name="BookWalker TW",
        search_func=search_bookwalker_tw,
        titles=_metadata_search_titles(parsed, record, llm_metadata, "tw"),
        parsed=parsed,
        record=record,
        volume=parsed.volume,
        author=parsed.author,
        min_confidence=cfg.metadata.bookwalker_tw_min_confidence,
        max_candidates=cfg.metadata.bookwalker_tw_max_candidates,
        filename=record.file_name,
        llm_metadata=llm_metadata,
        cfg=cfg,
    )


def _search_bookwalker_jp_metadata(
    record_id: int,
    parsed: ParseResult,
    record: MangaRecord,
    llm_metadata: LlmMetadata | None,
    cfg: PipelineConfig,
) -> BookwalkerMetadata | None:
    if not cfg.metadata.bookwalker_jp_enabled:
        return None
    return _search_best_bookwalker_metadata(
        record_id=record_id,
        provider_name="BookWalker JP",
        search_func=search_bookwalker_jp,
        titles=_metadata_search_titles(parsed, record, llm_metadata, "jp"),
        parsed=parsed,
        record=record,
        volume=parsed.volume,
        author=parsed.author,
        min_confidence=cfg.metadata.bookwalker_jp_min_confidence,
        max_candidates=cfg.metadata.bookwalker_jp_max_candidates,
        filename=record.file_name,
        llm_metadata=llm_metadata,
        cfg=cfg,
    )


def _search_best_bookwalker_metadata(
    record_id: int,
    provider_name: str,
    search_func: object,
    titles: list[str],
    parsed: ParseResult,
    record: MangaRecord,
    volume: str,
    author: str,
    min_confidence: float,
    max_candidates: int,
    filename: str = "",
    llm_metadata: LlmMetadata | None = None,
    cfg: PipelineConfig | None = None,
) -> BookwalkerMetadata | None:
    best: BookwalkerMetadata | None = None
    for title in titles:
        try:
            metadata = search_func(
                title,
                volume=volume,
                author=author,
                max_candidates=max_candidates,
            )
        except Exception as e:
            logger.warning(
                "[ID:%s] %s lookup failed for %s: %s",
                record_id,
                provider_name,
                title,
                e,
            )
            continue
        if metadata is None:
            continue
        # --- LLM verification ---
        if cfg is not None and filename:
            verification = _verify_candidate(
                record_id, provider_name, filename, llm_metadata, metadata, cfg
            )
            if verification is None and cfg.metadata.llm_verify_scrape_enabled:
                logger.info(
                    "[ID:%s] %s rejected unverified candidate '%s'",
                    record_id,
                    provider_name,
                    metadata.series or metadata.title,
                )
                continue
            if verification is not None and not verification.match:
                logger.info(
                    "[ID:%s] %s LLM rejected candidate '%s': %s",
                    record_id,
                    provider_name,
                    metadata.series or metadata.title,
                    verification.reason,
                )
                continue
            if verification is not None and verification.match:
                metadata.confidence = _verified_candidate_confidence(
                    metadata.confidence,
                    verification,
                    cfg,
                )
                logger.info(
                    "[ID:%s] %s LLM confirmed '%s' (confidence=%.2f): %s",
                    record_id,
                    provider_name,
                    metadata.series or metadata.title,
                    metadata.confidence,
                    verification.reason,
                )
        if best is None or metadata.confidence > best.confidence:
            best = metadata
        if metadata.confidence >= min_confidence:
            return metadata

    if best is not None:
        if best.confidence >= min_confidence:
            return best
        logger.info(
            "[ID:%s] %s match below threshold: %.2f < %.2f",
            record_id,
            provider_name,
            best.confidence,
            min_confidence,
        )
    return None


def _search_bangumi_metadata(
    record_id: int,
    parsed: ParseResult,
    record: MangaRecord,
    llm_metadata: LlmMetadata | None,
    cfg: PipelineConfig,
):
    if not cfg.metadata.bangumi_enabled:
        return None

    best = None
    for title in _metadata_search_titles(parsed, record, llm_metadata, "bangumi"):
        try:
            metadata = search_bangumi(
                title,
                volume=parsed.volume,
                author=parsed.author,
                max_candidates=cfg.metadata.bangumi_max_candidates,
            )
        except Exception as e:
            logger.warning("[ID:%s] Bangumi lookup failed for %s: %s", record_id, title, e)
            continue
        if metadata is None:
            continue
        # --- LLM verification ---
        verification = _verify_candidate(
            record_id, "Bangumi", record.file_name, llm_metadata, metadata, cfg
        )
        if verification is None and cfg.metadata.llm_verify_scrape_enabled:
            logger.info(
                "[ID:%s] Bangumi rejected unverified candidate '%s'",
                record_id,
                metadata.series or metadata.title,
            )
            continue
        if verification is not None and not verification.match:
            logger.info(
                "[ID:%s] Bangumi LLM rejected candidate '%s': %s",
                record_id,
                metadata.series or metadata.title,
                verification.reason,
            )
            continue
        if verification is not None and verification.match:
            metadata.confidence = _verified_candidate_confidence(
                metadata.confidence,
                verification,
                cfg,
            )
            logger.info(
                "[ID:%s] Bangumi LLM confirmed '%s' (confidence=%.2f): %s",
                record_id,
                metadata.series or metadata.title,
                metadata.confidence,
                verification.reason,
            )
        if best is None or metadata.confidence > best.confidence:
            best = metadata
        if metadata.confidence >= cfg.metadata.bangumi_min_confidence:
            return metadata

    if best is not None:
        if best.confidence >= cfg.metadata.bangumi_min_confidence:
            return best
        logger.info(
            "[ID:%s] Bangumi match below threshold: %.2f < %.2f",
            record_id,
            best.confidence,
            cfg.metadata.bangumi_min_confidence,
        )
    return None


def _verify_candidate(
    record_id: int,
    provider_name: str,
    filename: str,
    llm_metadata: LlmMetadata | None,
    metadata: object,
    cfg: PipelineConfig,
) -> ScrapeVerification | None:
    """Call LLM to verify a scraped metadata candidate matches the source file.

    Uses duck-typing on metadata so it works with both BookwalkerMetadata
    and BangumiMetadata without an explicit isinstance check.
    """
    return verify_scrape_with_llm(
        filename=filename,
        llm_parse=llm_metadata,
        provider=provider_name,
        scraped_title=str(getattr(metadata, "title", "") or ""),
        scraped_series=str(getattr(metadata, "series", "") or ""),
        scraped_volume=str(getattr(metadata, "volume", "") or ""),
        scraped_author=str(getattr(metadata, "author_text", "") or ""),
        cfg=cfg.metadata,
    )


def _verified_candidate_confidence(
    provider_confidence: float,
    verification: ScrapeVerification,
    cfg: PipelineConfig,
) -> float:
    """Let high-confidence LLM verification override provider scorer limits."""
    confidence = max(provider_confidence, verification.confidence)
    if verification.confidence >= cfg.metadata.llm_verify_accept_confidence:
        provider_threshold = max(
            cfg.metadata.bookwalker_tw_min_confidence,
            cfg.metadata.bookwalker_jp_min_confidence,
            cfg.metadata.bangumi_min_confidence,
        )
        confidence = max(confidence, provider_threshold)
    return min(1.0, confidence)


def _get_or_create_series_anchor(
    record_id: int,
    record: MangaRecord,
    cfg: PipelineConfig,
    db: Database,
    control: ControlStore,
) -> tuple[SeriesAnchor | None, LlmMetadata | None]:
    """Return the collection-level series anchor, creating it from the folder once."""
    if not _has_real_collection_path(record):
        return None, None

    existing = db.get_series_anchor(record.collection_title)
    if existing is not None:
        return existing, _anchor_to_llm_metadata(existing)

    filenames = _collection_filenames(record)
    try:
        metadata = normalize_collection_with_llm(
            record.collection_title,
            filenames,
            cfg.metadata,
        )
    except Exception as e:
        logger.warning("[ID:%s] LLM series anchor normalization failed: %s", record_id, e)
        with contextlib.suppress(Exception):
            control.log_llm_run(
                record_id=record_id,
                source_name=record.collection_title,
                prompt=control.get_active_prompt(),
                error=str(e),
            )
        return None, None

    if metadata is None:
        return None, None

    control.log_llm_run(
        record_id=record_id,
        source_name=record.collection_title,
        prompt=metadata.prompt,
        response=metadata.raw_content,
        parsed_json={
            "title": metadata.title,
            "title_tw": metadata.title_tw,
            "title_jp": metadata.title_jp,
            "author": metadata.author,
            "publisher": metadata.publisher,
            "search_titles": metadata.search_titles or [],
            "confidence": metadata.confidence,
            "scope": "series_anchor",
        },
        elapsed_ms=metadata.elapsed_ms,
    )

    canonical_series = _anchor_canonical_series(metadata, record.collection_title, cfg)
    if not canonical_series:
        return None, metadata

    anchor = SeriesAnchor(
        collection_title=record.collection_title,
        canonical_series=canonical_series,
        title_tw=metadata.title_tw,
        title_jp=metadata.title_jp,
        author=metadata.author,
        publisher=metadata.publisher,
        queries_tw=metadata.queries_tw,
        queries_jp=metadata.queries_jp,
        queries_bangumi=metadata.queries_bangumi,
        aliases=metadata.search_titles or [],
    )
    db.upsert_series_anchor(anchor)
    logger.info(
        "[ID:%s] Created series anchor for '%s': %s",
        record_id,
        record.collection_title,
        anchor.canonical_series,
    )
    return anchor, _anchor_to_llm_metadata(anchor)


def _has_real_collection_path(record: MangaRecord) -> bool:
    if not record.collection_title or not record.original_path:
        return False
    return Path(record.original_path).parent.name == record.collection_title


def _collection_filenames(record: MangaRecord) -> list[str]:
    source_path = Path(record.original_path)
    parent = source_path.parent
    if not record.collection_title or not parent.is_dir():
        return [record.file_name]

    names: list[str] = []
    for child in sorted(parent.iterdir()):
        if child.is_file() and child.suffix.lower() in SUPPORTED_EXTENSIONS:
            names.append(child.name)
        elif child.is_dir():
            names.append(child.name)
    return names[:80] or [record.file_name]


def _anchor_canonical_series(
    metadata: LlmMetadata,
    collection_title: str,
    cfg: PipelineConfig,
) -> str:
    candidates = _dedupe_titles(
        [
            metadata.title_tw,
            metadata.title,
            metadata.title_jp,
            collection_title,
            *(metadata.search_titles or []),
        ]
    )
    existing = _find_existing_komga_series_name(cfg, candidates)
    if existing:
        return _canonical_series_name(existing)
    return _canonical_series_name(metadata.title_tw or metadata.title or metadata.title_jp or collection_title)


def _find_existing_komga_series_name(
    cfg: PipelineConfig,
    names: list[str],
) -> str:
    if not cfg.paths.komga_library.is_dir():
        return ""

    expected_keys = {
        _title_key(name)
        for name in names + [to_bookwalker_traditional(name) for name in names]
        if name
    }
    expected_keys.discard("")
    if not expected_keys:
        return ""

    for child in sorted(cfg.paths.komga_library.iterdir()):
        if child.is_dir() and _title_key(child.name) in expected_keys:
            return child.name
    return ""


def _anchor_to_llm_metadata(anchor: SeriesAnchor) -> LlmMetadata:
    return LlmMetadata(
        title=anchor.canonical_series,
        title_tw=anchor.title_tw or anchor.canonical_series,
        title_jp=anchor.title_jp,
        author=anchor.author,
        publisher=anchor.publisher,
        queries_tw=anchor.queries_tw,
        queries_jp=anchor.queries_jp,
        queries_bangumi=anchor.queries_bangumi,
        search_titles=_dedupe_titles(
            [
                anchor.canonical_series,
                anchor.title_tw,
                anchor.title_jp,
                *anchor.aliases,
                *anchor.queries_tw,
                *anchor.queries_jp,
                *anchor.queries_bangumi,
            ]
        ),
        parse_status="ok",
        verified=True,
        verification_level="series_anchor",
        confidence=0.85,
    )


def _merge_anchor_metadata(
    llm_metadata: LlmMetadata | None,
    anchor: SeriesAnchor | None,
) -> LlmMetadata | None:
    if anchor is None:
        return llm_metadata

    anchor_metadata = _anchor_to_llm_metadata(anchor)
    if llm_metadata is None:
        return anchor_metadata

    llm_metadata.title = anchor.canonical_series
    llm_metadata.title_tw = anchor.title_tw or anchor.canonical_series
    llm_metadata.title_jp = anchor.title_jp or llm_metadata.title_jp
    llm_metadata.author = llm_metadata.author or anchor.author
    llm_metadata.publisher = llm_metadata.publisher or anchor.publisher
    llm_metadata.queries_tw = _dedupe_titles(anchor_metadata.queries_tw + llm_metadata.queries_tw)
    llm_metadata.queries_jp = _dedupe_titles(anchor_metadata.queries_jp + llm_metadata.queries_jp)
    llm_metadata.queries_bangumi = _dedupe_titles(
        anchor_metadata.queries_bangumi + llm_metadata.queries_bangumi
    )
    llm_metadata.search_titles = _dedupe_titles(
        (anchor_metadata.search_titles or []) + (llm_metadata.search_titles or [])
    )
    llm_metadata.confidence = max(llm_metadata.confidence, anchor_metadata.confidence)
    return llm_metadata


def _apply_series_anchor(
    parsed: ParseResult,
    anchor: SeriesAnchor | None,
) -> None:
    if anchor is None:
        return
    parsed.title = anchor.canonical_series
    parsed.series = anchor.canonical_series
    parsed.author = parsed.author or anchor.author
    parsed.publisher = parsed.publisher or anchor.publisher
    parsed.confidence = max(parsed.confidence, 0.85)


def _dedupe_titles(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        title = value.strip()
        if title and title not in result:
            result.append(title)
    return result


def _collect_metadata_candidates(
    record_id: int,
    parsed: ParseResult,
    record: MangaRecord,
    llm_metadata: LlmMetadata | None,
    cfg: PipelineConfig,
) -> list[MetadataCandidate]:
    """Collect best provider candidates for human review."""
    candidates: list[MetadataCandidate] = []
    providers = [
        ("bookwalker_tw", _search_bookwalker_tw_metadata),
        ("bookwalker_jp", _search_bookwalker_jp_metadata),
        ("bangumi", _search_bangumi_metadata),
    ]
    seen: set[tuple[str, str]] = set()
    for provider, search_func in providers:
        metadata = search_func(record_id, parsed, record, llm_metadata, cfg)
        if metadata is None:
            continue
        candidate = candidate_from_metadata(provider, metadata)
        key = (candidate.provider, candidate.detail_url or candidate.provider_id)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    return candidates


def _metadata_search_titles(
    parsed: ParseResult,
    record: MangaRecord,
    llm_metadata: LlmMetadata | None,
    provider: str,
) -> list[str]:
    """Build ordered search title list for a specific provider.

    LLM per-provider queries come first (most relevant), followed by
    the LLM-derived title fields, then the regex-parsed titles, and
    finally any collection-directory hints.
    """
    titles: list[str] = []
    if llm_metadata:
        if provider == "tw":
            # Use LLM's curated TW queries first, then TW title, then general title
            titles.extend(llm_metadata.queries_tw)
            titles.extend([llm_metadata.title_tw, llm_metadata.title])
        elif provider == "jp":
            # Use LLM's curated JP queries first, then JP title, then general title
            titles.extend(llm_metadata.queries_jp)
            titles.extend([llm_metadata.title_jp, llm_metadata.title])
        else:  # bangumi
            titles.extend(llm_metadata.queries_bangumi)
            titles.extend([llm_metadata.title_jp, llm_metadata.title_tw, llm_metadata.title])
        titles.extend([parsed.title, parsed.series])
    else:
        titles.extend([parsed.title, parsed.series])
    if llm_metadata is None and record.collection_title:
        collection = _parse_collection_title(record.collection_title)
        titles.extend([collection.title, record.collection_title])

    result: list[str] = []
    for title in titles:
        title = title.strip()
        if title and title not in result:
            result.append(title)
        for alias in _metadata_title_aliases(title, provider):
            if alias not in result:
                result.append(alias)
    return result[:10]


def _metadata_title_aliases(title: str, provider: str) -> list[str]:
    key = _title_key(title)
    aliases: list[str] = []
    if provider != "tw" and key == "dna2":
        aliases.append(
            "D・N・A2 "
            "\N{FULLWIDTH TILDE}"
            "何処かで失くしたあいつのアイツ"
            "\N{FULLWIDTH TILDE}"
        )
    if provider != "tw" and key == "銃夢":
        aliases.append(
            "銃夢"
            "\N{FULLWIDTH LEFT PARENTHESIS}"
            "講談社"
            "\N{FULLWIDTH RIGHT PARENTHESIS}"
        )
    return aliases


def _metadata_matches_record_context(
    metadata: object,
    parsed: ParseResult,
    record: MangaRecord,
) -> bool:
    """Reject provider false positives that conflict with a collection folder."""
    if not record.collection_title:
        return True

    collection = _parse_collection_title(record.collection_title)
    expected_title = parsed.series or parsed.title or collection.title
    if not expected_title:
        return True

    metadata_title = " ".join(
        [
            str(getattr(metadata, "series", "") or ""),
            str(getattr(metadata, "title", "") or ""),
        ]
    )
    if not metadata_title.strip():
        return True

    expected_markers = _edition_markers(expected_title)
    actual_markers = _edition_markers(metadata_title)
    if expected_markers and not expected_markers.issubset(actual_markers):
        return False
    if actual_markers - expected_markers:
        return False

    expected_keys = _title_match_keys(expected_title)
    actual_keys = _metadata_primary_title_keys(metadata)
    if not expected_keys or not actual_keys:
        return True

    return _title_keys_match(expected_keys, actual_keys)


def _title_candidate_matches_record_context(
    title: str,
    parsed: ParseResult,
    record: MangaRecord,
) -> bool:
    """Return whether a search title is worth querying for this record."""
    if not record.collection_title:
        return True

    collection = _parse_collection_title(record.collection_title)
    expected_title = parsed.series or parsed.title or collection.title
    if not expected_title or not title:
        return True

    expected_markers = _edition_markers(expected_title)
    actual_markers = _edition_markers(title)
    if expected_markers and not expected_markers.issubset(actual_markers):
        return False
    if actual_markers - expected_markers:
        return False

    expected_keys = _title_match_keys(expected_title)
    actual_keys = [_title_key(title)]
    if not expected_keys or not actual_keys:
        return True
    return _title_keys_match(expected_keys, actual_keys)


def _title_match_keys(title: str) -> list[str]:
    title = _strip_edition_markers(title)
    parts = [title]
    parts.extend(re.split(r"[\s_\-./・:\uFF1A~\u301C]+", title))
    keys: list[str] = []
    for part in parts:
        key = _title_key(part)
        if len(key) >= 2 and key not in keys:
            keys.append(key)
        for alias in _title_aliases(key):
            if alias not in keys:
                keys.append(alias)
    return keys


def _title_keys_match(expected_keys: list[str], actual_keys: list[str]) -> bool:
    """Match title keys while avoiding short-title substring false positives."""
    for expected in expected_keys:
        for actual in actual_keys:
            if expected == actual:
                return True
            if len(expected) >= 4 and (actual.startswith(expected) or expected.startswith(actual)):
                return True
    return False


def _metadata_primary_title_keys(metadata: object) -> list[str]:
    keys: list[str] = []
    for field in ("series", "title"):
        key = _title_key(str(getattr(metadata, field, "") or ""))
        if key and key not in keys:
            keys.append(key)
    return keys


def _title_key(value: str) -> str:
    value = to_bookwalker_traditional(_strip_edition_markers(value))
    value = unicodedata.normalize("NFKC", value).replace("²", "2")
    return re.sub(
        r"[\s_\-./・()\uFF08\uFF09【】\[\]:\uFF1A~\u301C]+",
        "",
        value.casefold(),
    )


def _title_aliases(key: str) -> list[str]:
    aliases: dict[str, tuple[str, ...]] = {
        "slamdunk": ("灌籃高手",),
        "灌籃高手": ("slamdunk",),
        "灌篮高手": ("slamdunk",),
    }
    result = list(aliases.get(key, ()))
    for noise in ("evangelion", "eva"):
        if noise in key:
            alias = key.replace(noise, "")
            if alias and alias not in result:
                result.append(alias)
    return result


def _edition_markers(value: str) -> set[str]:
    value = to_bookwalker_traditional(value)
    markers: set[str] = set()
    marker_groups = {
        "complete": ("完全版",),
        "collector": ("典藏版", "典藏"),
        "new": ("新裝再編版", "新裝", "再編"),
        "deluxe": ("豪華版", "豪華"),
        "bunkoban": ("文庫版", "文庫"),
        "aizoban": ("愛藏版", "愛藏"),
    }
    for key, aliases in marker_groups.items():
        if any(alias in value for alias in aliases):
            markers.add(key)
    return markers


def _strip_edition_markers(value: str) -> str:
    value = to_bookwalker_traditional(value)
    for marker in (
        "完全版",
        "典藏版",
        "新裝再編版",
        "豪華版",
        "文庫版",
        "愛藏版",
        "典藏",
        "新裝",
        "再編",
        "豪華",
        "文庫",
        "愛藏",
    ):
        value = value.replace(marker, " ")
    return value


def _step_parse_metadata(
    record_id: int,
    record: MangaRecord,
    cfg: PipelineConfig,
    db: Database,
) -> bool:
    """Parse filename metadata."""
    parsed = ParseResult()
    source_volume = _explicit_collection_child_volume(record)
    control = ControlStore(db.db_path.parent)
    llm_run_id: int | None = None
    series_anchor, _anchor_metadata = _get_or_create_series_anchor(
        record_id,
        record,
        cfg,
        db,
        control,
    )
    if (
        _has_real_collection_path(record)
        and cfg.metadata.llm_normalize_enabled
        and series_anchor is None
    ):
        logger.warning(
            "[ID:%s] LLM series anchor unavailable, sending to review: %s",
            record_id,
            record.collection_title,
        )
        db.update_status(
            record_id,
            ProcessingStatus.NEEDS_REVIEW,
            error_message="LLM series anchor unavailable",
        )
        control.close()
        return False

    try:
        source_name = record.file_name if series_anchor else (
            f"{record.collection_title} {record.file_name}"
            if record.collection_title
            else record.file_name
        )
        prompt = control.get_active_prompt()
        file_llm_metadata = normalize_with_llm(source_name, parsed, cfg.metadata, prompt)
        llm_metadata = (
            _merge_anchor_metadata(file_llm_metadata, series_anchor)
            if file_llm_metadata is not None
            else None
        )
        if llm_metadata is not None:
            llm_run_id = control.log_llm_run(
                record_id=record_id,
                source_name=source_name,
                prompt=llm_metadata.prompt,
                response=llm_metadata.raw_content,
                parsed_json={
                    "title": llm_metadata.title,
                    "title_tw": llm_metadata.title_tw,
                    "title_jp": llm_metadata.title_jp,
                    "author": llm_metadata.author,
                    "publisher": llm_metadata.publisher,
                    "volume": llm_metadata.volume,
                    "search_titles": llm_metadata.search_titles or [],
                    "confidence": llm_metadata.confidence,
                },
                elapsed_ms=llm_metadata.elapsed_ms,
            )
    except Exception as e:
        logger.warning("[ID:%s] LLM filename normalization failed: %s", record_id, e)
        with contextlib.suppress(Exception):
            control.log_llm_run(
                record_id=record_id,
                source_name=record.file_name,
                prompt=control.get_active_prompt(),
                error=str(e),
            )
        llm_metadata = None

    if llm_metadata is not None:
        status = llm_metadata.parse_status
        logger.info(
            "[ID:%s] LLM parse_status=%s confidence=%.2f verified=%s",
            record_id,
            status,
            llm_metadata.confidence,
            llm_metadata.verified,
        )
        if llm_metadata.warnings:
            logger.warning(
                "[ID:%s] LLM warnings: %s",
                record_id,
                "; ".join(llm_metadata.warnings),
            )
        logger.info(
            "[ID:%s] LLM applied: title=%s, author=%s, vol=%s",
            record_id,
            llm_metadata.title,
            llm_metadata.author,
            llm_metadata.volume,
        )
        parsed.title = llm_metadata.title
        parsed.series = llm_metadata.title
        parsed.author = llm_metadata.author
        parsed.publisher = llm_metadata.publisher
        parsed.volume = source_volume or llm_metadata.volume
        parsed.confidence = llm_metadata.confidence
        _apply_series_anchor(parsed, series_anchor)
    elif cfg.metadata.llm_normalize_enabled:
        logger.warning(
            "[ID:%s] LLM filename normalization unavailable, sending to review: %s",
            record_id,
            record.file_name,
        )
        db.update_status(
            record_id,
            ProcessingStatus.NEEDS_REVIEW,
            error_message="LLM filename normalization unavailable",
        )
        return False
    else:
        parsed = parse_filename(record.file_name)
        if record.collection_title:
            _apply_collection_title(parsed, record.collection_title)
        if source_volume:
            parsed.volume = source_volume
        _apply_series_anchor(parsed, series_anchor)

    mode = control.get_mode()
    if mode in {MODE_MANUAL_BOOK, MODE_MANUAL_SERIES}:
        candidates = _collect_metadata_candidates(record_id, parsed, record, llm_metadata, cfg)
        policy = (
            control.get_series_policy(record.collection_title)
            if mode == MODE_MANUAL_SERIES and record.collection_title
            else None
        )
        if policy:
            selected = _select_policy_candidate(candidates, policy)
            if selected is not None:
                _apply_review_candidate(parsed, selected)
                if source_volume:
                    parsed.volume = source_volume
                _apply_series_anchor(parsed, series_anchor)
                db.update_status(
                    record_id,
                    ProcessingStatus.METADATA_PARSED,
                    **_candidate_record_fields(parsed, selected),
                )
                control.close()
                return True

        scope = "series" if mode == MODE_MANUAL_SERIES and record.collection_title else "book"
        control.create_or_update_approval(
            record=record,
            scope=scope,
            parsed={
                "title": parsed.title,
                "series": parsed.series,
                "author": parsed.author,
                "publisher": parsed.publisher,
                "volume": source_volume or parsed.volume,
                "confidence": parsed.confidence,
                "llm_run_id": llm_run_id,
            },
            candidates=candidates,
        )
        db.update_status(
            record_id,
            ProcessingStatus.AWAITING_METADATA_APPROVAL,
            error_message="等待前端确认元数据",
        )
        control.close()
        return False
    control.close()

    accepted_metadata = None
    bookwalker = _search_bookwalker_tw_metadata(
        record_id,
        parsed,
        record,
        llm_metadata,
        cfg,
    )
    if bookwalker is not None:
        accepted_metadata = bookwalker
        logger.info(
            "[ID:%s] BookWalker TW matched %s (confidence=%.2f, product=%s)",
            record_id,
            bookwalker.title,
            bookwalker.confidence,
            bookwalker.product_id,
        )
        parsed.title = bookwalker.series or parsed.title
        parsed.series = bookwalker.series or parsed.series or parsed.title
        parsed.volume = _metadata_volume(parsed.volume, bookwalker.volume)
        parsed.author = bookwalker.author_text or parsed.author
        parsed.publisher = bookwalker.publisher or parsed.publisher
        parsed.confidence = max(parsed.confidence, bookwalker.confidence)
        _apply_series_anchor(parsed, series_anchor)
    else:
        bookwalker_jp = _search_bookwalker_jp_metadata(
            record_id,
            parsed,
            record,
            llm_metadata,
            cfg,
        )
        if bookwalker_jp is not None:
            accepted_metadata = bookwalker_jp
            logger.info(
                "[ID:%s] BookWalker JP matched %s (confidence=%.2f, product=%s)",
                record_id,
                bookwalker_jp.title,
                bookwalker_jp.confidence,
                bookwalker_jp.product_id,
            )
            parsed.title = bookwalker_jp.series or parsed.title
            parsed.series = bookwalker_jp.series or parsed.series or parsed.title
            parsed.volume = _metadata_volume(parsed.volume, bookwalker_jp.volume)
            parsed.author = bookwalker_jp.author_text or parsed.author
            parsed.publisher = bookwalker_jp.publisher or parsed.publisher
            parsed.confidence = max(parsed.confidence, bookwalker_jp.confidence)
            _apply_series_anchor(parsed, series_anchor)

    if accepted_metadata is None:
        bangumi = _search_bangumi_metadata(
            record_id,
            parsed,
            record,
            llm_metadata,
            cfg,
        )
        if bangumi is not None:
            accepted_metadata = bangumi
            logger.info(
                "[ID:%s] Bangumi matched %s (confidence=%.2f, subject=%s)",
                record_id,
                bangumi.title,
                bangumi.confidence,
                bangumi.subject_id,
            )
            parsed.title = bangumi.series or parsed.title
            parsed.series = bangumi.series or parsed.series or parsed.title
            parsed.volume = _metadata_volume(parsed.volume, bangumi.volume)
            parsed.author = bangumi.author_text or parsed.author
            parsed.publisher = bangumi.publisher or parsed.publisher
            parsed.confidence = max(parsed.confidence, bangumi.confidence)
            _apply_series_anchor(parsed, series_anchor)

    logger.info(
        "[ID:%s] Parsed %s: title=%s, author=%s, vol=%s (confidence=%.2f)",
        record_id,
        record.file_name,
        parsed.title,
        parsed.author,
        parsed.volume,
        parsed.confidence,
    )

    if cfg.metadata.llm_verify_scrape_enabled and accepted_metadata is None:
        logger.warning(
            "[ID:%s] No LLM-verified metadata candidate, sending to review: %s",
            record_id,
            record.file_name,
        )
        db.update_status(
            record_id,
            ProcessingStatus.NEEDS_REVIEW,
            error_message="No LLM-verified metadata candidate",
        )
        return False

    # Check confidence threshold
    threshold = cfg.metadata.confidence_auto_accept
    if parsed.confidence < threshold:
        logger.warning(
            "[ID:%s] Low confidence (%.2f < %.2f), sending to review: %s",
            record_id,
            parsed.confidence,
            threshold,
            record.file_name,
        )
        db.update_status(
            record_id,
            ProcessingStatus.NEEDS_REVIEW,
            error_message=f"Low confidence: {parsed.confidence:.2f}",
        )
        return False

    # Update record with parsed metadata
    parsed.series = _canonical_series_name(parsed.series or parsed.title)
    if source_volume:
        parsed.volume = source_volume
    _apply_series_anchor(parsed, series_anchor)
    db.update_status(
        record_id,
        ProcessingStatus.METADATA_PARSED,
        title=parsed.title,
        author=parsed.author,
        series=parsed.series,
        volume=parsed.volume,
        publisher=parsed.publisher,
        summary=accepted_metadata.summary if accepted_metadata else "",
        cover_url=accepted_metadata.cover_url if accepted_metadata else "",
        source_url=accepted_metadata.detail_url if accepted_metadata else "",
        isbn=accepted_metadata.isbn if accepted_metadata else "",
        page_count=accepted_metadata.page_count if accepted_metadata else "",
        confidence=str(parsed.confidence),
    )
    return True


def _explicit_collection_child_volume(record: MangaRecord) -> str:
    """Return a structural volume from a direct collection child name.

    For inbox layouts like ``Series/1/`` or ``Series/Vol_02.zip``, the child
    item name is a stronger signal than LLM or provider metadata. Keep this
    deliberately narrow so title digits such as ``DNA2`` are not treated as
    volume numbers.
    """
    if not record.collection_title or not record.file_name:
        return ""

    name = _strip_known_book_suffix(record.file_name).strip()
    patterns = (
        r"^0*(\d{1,3})$",
        r"^(?:v|vol)[._\-\s]*0*(\d{1,3})$",
        r"^第0*(\d{1,3})[卷巻]$",
    )
    for pattern in patterns:
        match = re.match(pattern, name, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def _strip_known_book_suffix(value: str) -> str:
    lower = value.lower()
    for suffix in sorted(SUPPORTED_EXTENSIONS, key=len, reverse=True):
        if lower.endswith(suffix):
            return value[: -len(suffix)]
    return value


def _select_policy_candidate(
    candidates: list[MetadataCandidate],
    policy: dict[str, object],
) -> MetadataCandidate | None:
    provider = str(policy.get("provider") or "")
    for candidate in candidates:
        if candidate.provider == provider:
            return candidate
    return None


def _apply_review_candidate(parsed: ParseResult, candidate: MetadataCandidate) -> None:
    parsed.title = candidate.series or candidate.title or parsed.title
    parsed.series = candidate.series or parsed.series or parsed.title
    parsed.volume = _metadata_volume(parsed.volume, candidate.volume)
    parsed.author = candidate.author or parsed.author
    parsed.publisher = candidate.publisher or parsed.publisher
    parsed.confidence = max(parsed.confidence, candidate.confidence)


def _metadata_volume(parsed_volume: str, metadata_volume: str) -> str:
    """Prefer the source filename volume over provider volume metadata.

    Provider records often describe only one purchasable volume or subject entry.
    For files inside a complete-series folder (for example BTX01.zip ... BTX16.zip),
    the filename is the source of truth for the individual book number.
    """
    return parsed_volume or metadata_volume


def _candidate_record_fields(
    parsed: ParseResult,
    candidate: MetadataCandidate,
) -> dict[str, str]:
    return {
        "title": parsed.title,
        "author": parsed.author,
        "series": parsed.series,
        "volume": parsed.volume,
        "publisher": parsed.publisher,
        "summary": candidate.summary,
        "cover_url": candidate.cover_url,
        "source_url": candidate.detail_url,
        "isbn": candidate.isbn,
        "page_count": candidate.page_count,
        "confidence": str(parsed.confidence),
    }


def _step_normalize_and_archive(
    record_id: int,
    record: MangaRecord,
    cfg: PipelineConfig,
    db: Database,
) -> bool:
    """Normalize archive format and archive the CBZ."""
    file_path = Path(record.original_path)
    if not file_path.exists():
        db.update_status(
            record_id,
            ProcessingStatus.FAILED,
            error_message=f"Source file missing: {file_path}",
        )
        return False

    try:
        # Build a clean filename
        clean_name = _build_clean_name(record)
        target_archive = cfg.paths.archive_cbz / f"{clean_name}.cbz"
        if target_archive.exists():
            _prepare_existing_volume_replacement(
                record_id=record_id,
                record=record,
                cfg=cfg,
                db=db,
                source_path=file_path,
                clean_name=clean_name,
                target_archive=target_archive,
            )

        # Normalize to CBZ in archive directory
        archive_path = normalize_to_cbz(
            file_path,
            cfg.paths.archive_cbz,
            clean_name,
            pdf_config=cfg.pdf,
            pdfimages_cmd=cfg.commands.pdfimages,
            pdftoppm_cmd=cfg.commands.pdftoppm,
        )
        write_comicinfo_to_cbz(
            archive_path,
            title=_build_book_title(record),
            series=_build_series_name(record),
            number=record.volume,
            writer=record.author,
            publisher=record.publisher,
            summary=record.summary,
            web=record.source_url,
            language_iso=cfg.metadata.default_language,
            manga=cfg.kobo.manga_style,
            tags=cfg.metadata.default_tags,
        )

        db.update_status(
            record_id,
            ProcessingStatus.ARCHIVED,
            archive_path=str(archive_path),
        )
        logger.info("[ID:%s] Archived: %s -> %s", record_id, file_path.name, archive_path.name)

        return True

    except (ValueError, OSError, ImportError) as e:
        logger.error(
            "[ID:%s] Normalization failed for %s: %s", record_id, record.file_name, e
        )
        db.update_status(
            record_id,
            ProcessingStatus.FAILED,
            error_message=f"Normalization failed: {e}",
        )
        return False


def _prepare_existing_volume_replacement(
    record_id: int,
    record: MangaRecord,
    cfg: PipelineConfig,
    db: Database,
    source_path: Path,
    clean_name: str,
    target_archive: Path,
) -> None:
    """Move generated files aside when a larger source replaces an old volume."""
    source_size = _source_payload_size(source_path)
    archive_size = target_archive.stat().st_size
    imported_owner = _find_imported_owner_for_archive(db, cfg, target_archive)
    if imported_owner and source_size <= archive_size:
        raise FileExistsError(
            f"Archive target already exists and source is not larger: {target_archive}"
        )

    backup_dir = _replacement_backup_dir(cfg, record_id, clean_name)
    moved: list[Path] = []
    for artifact in _replacement_artifact_candidates(record, cfg, clean_name):
        if artifact.is_file():
            moved.append(_move_replacement_artifact(artifact, backup_dir, cfg))

    logger.info(
        "[ID:%s] Preparing %s for %s "
        "(source=%d bytes, existing_archive=%d bytes, moved=%d, backup=%s)",
        record_id,
        "larger replacement" if imported_owner else "stale archive cleanup",
        clean_name,
        source_size,
        archive_size,
        len(moved),
        backup_dir,
    )


def _source_payload_size(source_path: Path) -> int:
    if source_path.is_dir():
        return sum(path.stat().st_size for path in source_path.rglob("*") if path.is_file())
    return source_path.stat().st_size


def _find_imported_owner_for_archive(
    db: Database,
    cfg: PipelineConfig,
    target_archive: Path,
) -> MangaRecord | None:
    for existing in db.get_all_records():
        if existing.current_status != ProcessingStatus.DONE:
            continue
        if existing.archive_path and Path(existing.archive_path) == target_archive:
            return existing
        if not existing.converted_path:
            continue
        converted_path = Path(existing.converted_path)
        series_name, _series_dir, dest_path = _expected_import_destination(
            existing,
            cfg,
            converted_path,
        )
        if dest_path.is_file() and target_archive.name == f"{_build_clean_name(existing)}.cbz":
            logger.debug(
                "Found imported owner for %s via Komga destination %s (series=%s)",
                target_archive,
                dest_path,
                series_name,
            )
            return existing
    return None


def _replacement_backup_dir(
    cfg: PipelineConfig,
    record_id: int,
    clean_name: str,
) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = (
        cfg.paths.processing
        / "replacement-backups"
        / f"{timestamp}-id{record_id}-{_sanitize_filename(clean_name)}"
    )
    backup_dir.mkdir(parents=True, exist_ok=False)
    return backup_dir


def _replacement_artifact_candidates(
    record: MangaRecord,
    cfg: PipelineConfig,
    clean_name: str,
) -> list[Path]:
    series_dir = cfg.paths.komga_library / _sanitize_dirname(
        record.series or record.title or "Unknown"
    )
    archive_path = cfg.paths.archive_cbz / f"{clean_name}.cbz"
    ready_path = cfg.paths.kepub_ready / f"{clean_name}.kepub.epub"
    imported_path = series_dir / f"{clean_name}.kepub.epub"

    candidates = [
        archive_path,
        ready_path,
        ready_path.with_suffix(".jpg"),
        imported_path,
        imported_path.with_suffix(".jpg"),
    ]
    if record.archive_path:
        candidates.append(Path(record.archive_path))
    if record.converted_path:
        converted_path = Path(record.converted_path)
        candidates.extend([converted_path, converted_path.with_suffix(".jpg")])

    return list(dict.fromkeys(candidates))


def _move_replacement_artifact(
    artifact: Path,
    backup_dir: Path,
    cfg: PipelineConfig,
) -> Path:
    try:
        relative = artifact.relative_to(cfg.paths.archive_cbz)
        destination = backup_dir / "archive_cbz" / relative
    except ValueError:
        try:
            relative = artifact.relative_to(cfg.paths.kepub_ready)
            destination = backup_dir / "kepub_ready" / relative
        except ValueError:
            try:
                relative = artifact.relative_to(cfg.paths.komga_library)
                destination = backup_dir / "komga-library" / relative
            except ValueError:
                destination = backup_dir / "other" / artifact.name

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(artifact), str(destination))
    return destination


def _step_convert_kcc(
    record_id: int,
    record: MangaRecord,
    cfg: PipelineConfig,
    db: Database,
) -> bool:
    """Convert archived CBZ to KEPUB via KCC."""
    archive_path = Path(record.archive_path)
    if not archive_path.is_file():
        db.update_status(
            record_id,
            ProcessingStatus.FAILED,
            error_message=f"Archive missing: {archive_path}",
        )
        return False

    cfg.paths.kepub_ready.mkdir(parents=True, exist_ok=True)

    result = run_kcc(
        input_path=archive_path,
        output_dir=cfg.paths.kepub_ready,
        kcc_cmd=cfg.commands.kcc,
        kobo_config=cfg.kobo,
    )

    if result.success and result.output_path:
        converted_path = Path(result.output_path)
        try:
            write_epub_metadata(
                converted_path,
                title=_build_book_title(record),
                series=_build_series_name(record),
                number=record.volume,
                writer=record.author,
                language_iso=cfg.metadata.default_language,
                summary=record.summary,
                manga=cfg.kobo.manga_style,
            )
        except (KeyError, ValueError, OSError) as e:
            logger.warning("[ID:%s] Could not update EPUB metadata: %s", record_id, e)

        db.update_status(
            record_id,
            ProcessingStatus.CONVERTED,
            converted_path=result.output_path,
        )
        logger.info(
            "[ID:%s] Converted: %s -> %s",
            record_id,
            archive_path.name,
            result.output_path,
        )
        return True
    else:
        # Check retry
        if record.retry_count < cfg.processing.max_retries:
            db.increment_retry(record_id)
            logger.warning(
                "[ID:%s] KCC failed for %s (retry %d/%d): %s",
                record_id,
                record.file_name,
                record.retry_count + 1,
                cfg.processing.max_retries,
                result.stderr[:200],
            )
            return False
        else:
            db.update_status(
                record_id,
                ProcessingStatus.FAILED,
                error_message=f"KCC failed after {cfg.processing.max_retries} retries",
            )
            return False


def _step_import_komga(
    record_id: int,
    record: MangaRecord,
    cfg: PipelineConfig,
    db: Database,
) -> bool:
    """Import converted file into Komga library by moving it to the library directory."""
    converted_path = Path(record.converted_path)
    series_name, series_dir, dest_path = _expected_import_destination(
        record,
        cfg,
        converted_path,
    )
    series_dir.mkdir(parents=True, exist_ok=True)

    if not converted_path.is_file():
        if dest_path.is_file():
            logger.info(
                "[ID:%s] Converted file already imported: %s",
                record_id,
                dest_path,
            )
            _mark_import_done(record_id, record, db, cfg, series_name, dest_path)
            return True
        db.update_status(
            record_id,
            ProcessingStatus.FAILED,
            error_message=f"Converted file missing: {converted_path}",
        )
        return False

    db.update_status(record_id, ProcessingStatus.IMPORTING)

    try:
        # Move (or copy + delete) the converted file into the Komga library
        shutil.move(str(converted_path), str(dest_path))
        _download_metadata_artwork(record, dest_path, series_dir, cfg)
        logger.info(
            "[ID:%s] Moved to Komga library: %s -> %s",
            record_id,
            converted_path.name,
            dest_path,
        )
    except OSError as e:
        if dest_path.is_file():
            logger.info(
                "[ID:%s] Import destination already exists after move failure: %s",
                record_id,
                dest_path,
            )
            _mark_import_done(record_id, record, db, cfg, series_name, dest_path)
            return True
        logger.error("[ID:%s] Failed to move file to Komga library: %s", record_id, e)
        if record.retry_count < cfg.processing.max_retries:
            db.increment_retry(record_id)
            db.update_status(record_id, ProcessingStatus.CONVERTED)
            return False
        else:
            db.update_status(
                record_id,
                ProcessingStatus.FAILED,
                error_message=f"Failed to move to Komga library: {e}",
            )
            return False

    _mark_import_done(record_id, record, db, cfg, series_name, dest_path)
    return True


def _expected_import_destination(
    record: MangaRecord,
    cfg: PipelineConfig,
    converted_path: Path,
) -> tuple[str, Path, Path]:
    series_name = _sanitize_dirname(record.series or record.title or "Unknown")
    series_dir = cfg.paths.komga_library / series_name
    filename = converted_path.name
    if not filename and record.archive_path:
        filename = Path(record.archive_path).with_suffix(".kepub.epub").name
    return series_name, series_dir, series_dir / filename


def _mark_import_done(
    record_id: int,
    record: MangaRecord,
    db: Database,
    cfg: PipelineConfig,
    series_name: str,
    dest_path: Path,
) -> None:
    """Mark a record as imported and perform scan/cleanup side effects."""
    library_id = cfg.komga.library_id
    if not library_id:
        library_id = get_library_id(
            cfg.komga.base_uri, cfg.komga.user, cfg.komga.password
        ) or ""

    if library_id:
        scan_result = trigger_library_scan(
            base_uri=cfg.komga.base_uri,
            library_id=library_id,
            user=cfg.komga.user,
            password=cfg.komga.password,
        )
        if not scan_result.success:
            logger.warning(
                "[ID:%s] Komga scan trigger failed (non-fatal): %s",
                record_id,
                scan_result.error,
            )
    else:
        logger.warning("[ID:%s] No Komga library ID found, skipping scan trigger.", record_id)

    # Mark as done
    db.update_status(
        record_id,
        ProcessingStatus.DONE,
        converted_path=str(dest_path),
        library_book_id=series_name,
    )
    logger.info(
        "[ID:%s] Successfully imported to Komga: %s (series=%s)",
        record_id,
        record.file_name,
        series_name,
    )

    _cleanup_inbox_original(record_id, record, cfg)


def _cleanup_inbox_original(
    record_id: int,
    record: MangaRecord,
    cfg: PipelineConfig,
) -> None:
    if cfg.processing.delete_inbox_after_archive:
        inbox_path = Path(record.original_path)
        try:
            if inbox_path.is_file():
                inbox_path.unlink()
                logger.info("[ID:%s] Deleted inbox original: %s", record_id, inbox_path.name)
                _remove_empty_inbox_parent(inbox_path, cfg)
            elif inbox_path.is_dir():
                shutil.rmtree(inbox_path)
                logger.info(
                    "[ID:%s] Deleted inbox source directory: %s",
                    record_id,
                    inbox_path.name,
                )
                _remove_empty_inbox_parent(inbox_path, cfg)
        except OSError as e:
            logger.warning(
                "[ID:%s] Could not delete inbox file %s: %s", record_id, inbox_path.name, e
            )


def _apply_collection_title(parsed: ParseResult, collection_title: str) -> None:
    """Apply an inbox collection directory name as the primary series title."""
    collection = _parse_collection_title(collection_title)
    title = to_bookwalker_traditional(collection.title or collection_title.strip())
    if not title:
        return
    parsed.title = title
    parsed.series = title
    if collection.author:
        parsed.author = collection.author
    if collection.publisher:
        parsed.publisher = collection.publisher
    parsed.confidence = max(parsed.confidence, 0.6)


def _parse_collection_title(collection_title: str) -> ParseResult:
    """Parse a top-level inbox collection directory name.

    Collection folders are user-facing series folders, so their first title-like
    component is more trustworthy than generic release-group bracket heuristics.
    """
    value = collection_title.strip()

    loose = None
    if not value.startswith("["):
        loose = re.match(
            r"^(?P<title>.+?)\s*\[(?P<author>[^\]]+)\]\[(?P<publisher>[^\]]+)\]",
            value,
        )
    if loose:
        title = loose.group("title").strip(" []")
        if title:
            return ParseResult(
                title=title,
                series=title,
                author=loose.group("author").strip(),
                publisher=loose.group("publisher").strip(),
                confidence=0.8,
            )

    bracketed = re.findall(r"\[([^\]]+)\]", value)
    if value.startswith("[") and len(bracketed) >= 3:
        title_index = _collection_title_bracket_index(bracketed)
        title = bracketed[title_index].strip()
        if title_index > 0:
            author = bracketed[0].strip()
            publisher = (
                bracketed[title_index + 1].strip()
                if title_index + 1 < len(bracketed)
                else ""
            )
        else:
            author = (
                bracketed[title_index + 1].strip()
                if title_index + 1 < len(bracketed)
                else ""
            )
            publisher = (
                bracketed[title_index + 2].strip()
                if title_index + 2 < len(bracketed)
                else ""
            )
        return ParseResult(
            title=title,
            series=title,
            author=author,
            publisher=publisher,
            confidence=0.8,
        )

    return parse_filename(collection_title)


def _collection_title_bracket_index(bracketed: list[str]) -> int:
    first = bracketed[0].strip()
    if len(bracketed) >= 2 and _looks_like_author_group(first):
        return 1
    return 0


def _looks_like_author_group(value: str) -> bool:
    """Return whether a bracket looks like author/circle credits, not a title."""
    value = value.strip()
    if "_" in value:
        return True
    if re.search(r"[×・·．/／&＆]", value) and re.search(
        r"[\u3040-\u30ff\u3400-\u9fffA-Za-z]", value
    ):
        return True
    return False


def _remove_empty_inbox_parent(path: Path, cfg: PipelineConfig) -> None:
    parent = path.parent
    if parent == cfg.paths.inbox:
        return
    try:
        parent.relative_to(cfg.paths.inbox)
    except ValueError:
        return
    with contextlib.suppress(OSError):
        parent.rmdir()


def _sanitize_dirname(name: str) -> str:
    """Sanitize a string for use as a directory name."""
    # Remove characters that are problematic in file paths
    forbidden = '<>:"/\\|?*'
    for ch in forbidden:
        name = name.replace(ch, "")
    # Collapse whitespace
    name = " ".join(name.split())
    return name.strip() or "Unknown"


def _download_metadata_artwork(
    record: MangaRecord,
    dest_path: Path,
    series_dir: Path,
    cfg: PipelineConfig,
) -> None:
    """Download metadata provider cover art as Komga local artwork sidecars."""
    if not cfg.metadata.download_bookwalker_covers or not record.cover_url:
        return

    book_cover_path = dest_path.with_suffix(".jpg")
    if download_cover(record.cover_url, book_cover_path):
        logger.info("Downloaded book cover: %s", book_cover_path.name)

    series_cover_path = series_dir / "cover.jpg"
    should_update_series_cover = (
        not series_cover_path.exists() or _is_first_volume(record.volume)
    )
    if should_update_series_cover and download_cover(record.cover_url, series_cover_path):
        logger.info("Downloaded series cover: %s", series_cover_path.name)


def _is_first_volume(volume: str) -> bool:
    """Return whether a volume token represents the first book in a series."""
    normalized = volume.strip().lower().lstrip("v")
    return normalized in {"1", "01", "001"}


def _build_clean_name(record: MangaRecord) -> str:
    """Build a Komga-friendly filename from parsed metadata.

    Komga groups books by parent directory, while book names come from the
    filename. Keep the file stem predictable for metadata matchers and sorting;
    richer display metadata is written into ComicInfo/EPUB metadata.
    """
    series = _build_series_name(record)
    if series:
        return _sanitize_filename(f"{series} {_format_volume_token(record.volume)}").strip()

    return _sanitize_filename(record.file_name.rsplit(".", 1)[0])


def _build_series_name(record: MangaRecord) -> str:
    """Return the canonical series name used for Komga folders and metadata."""
    return _sanitize_dirname(_canonical_series_name(record.series or record.title or ""))


def _build_book_title(record: MangaRecord) -> str:
    """Return a human-readable book title for Komga metadata."""
    title = record.title or record.series or record.file_name.rsplit(".", 1)[0]
    if record.volume:
        return f"{title} 卷{record.volume}"
    return title


def _canonical_series_name(value: str) -> str:
    """Normalize provider-specific label suffixes out of Komga series names."""
    value = value.strip()
    if not value:
        return ""
    label_suffixes = (
        "ビッグコミックス",
        "ジャンプコミックス",
        "ジャンプコミックスDIGITAL",
        "少年サンデーコミックス",
        "少年マガジンコミックス",
        "モーニングコミックス",
        "ヤングジャンプコミックス",
    )
    pattern = "|".join(re.escape(label) for label in label_suffixes)
    value = re.sub(rf"\s*(?:\(({pattern})\)|\uFF08({pattern})\uFF09)\s*$", "", value)
    return value.strip()


def _format_volume_token(volume: str) -> str:
    """Return a scraper-friendly sortable volume token."""
    if not volume:
        return ""
    if volume.isdigit():
        return f"v{int(volume):03d}"
    return f"v{volume}"


def _sanitize_filename(name: str) -> str:
    """Sanitize a string for use as a file stem."""
    name = _sanitize_dirname(name)
    return " ".join(name.split())
