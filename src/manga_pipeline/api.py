"""FastAPI control API for the manga pipeline web console."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from pathlib import Path
from typing import Any

from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from manga_pipeline.bangumi import search_bangumi
from manga_pipeline.bookwalker_jp import search_bookwalker_jp
from manga_pipeline.bookwalker_tw import search_bookwalker_tw
from manga_pipeline.config import PipelineConfig, load_config
from manga_pipeline.control import (
    VALID_MODES,
    ControlStore,
    MetadataCandidate,
    candidate_from_metadata,
)
from manga_pipeline.database import Database
from manga_pipeline.filename_parser import ParseResult
from manga_pipeline.models import ProcessingStatus
from manga_pipeline.pipeline import _apply_review_candidate, _candidate_record_fields
from manga_pipeline.rescrape import force_rescrape_record, rescrape_records, select_records

SESSION_COOKIE = "pipeline_session"

app = FastAPI(title="Manga Pipeline Control API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class SetupRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class ModeRequest(BaseModel):
    mode: str


class PromptRequest(BaseModel):
    content: str


class ApproveRequest(BaseModel):
    candidate_index: int = Field(ge=0)


class CandidateSearchRequest(BaseModel):
    provider: str
    title: str


class RescrapeRequest(BaseModel):
    ids: list[int] = Field(default_factory=list)
    title: str = ""
    all_records: bool = False
    dry_run: bool = True
    relocate: bool = False
    include_unfinished: bool = False


class MetadataPatchRequest(BaseModel):
    """Fields that can be overridden manually."""
    title: str | None = None
    series: str | None = None
    author: str | None = None
    publisher: str | None = None
    volume: str | None = None
    summary: str | None = None
    cover_url: str | None = None
    isbn: str | None = None


class SingleRescrapeRequest(BaseModel):
    """Trigger a rescrape for a single record with an explicit search term."""
    provider: str = "bookwalker_tw"
    title: str = ""
    volume: str = ""
    author: str = ""
    dry_run: bool = False
    relocate: bool = True
    force: bool = False
    """When True, bypass LLM normalization, confidence thresholds, and context
    matching filters. The user's explicit provider+title selection is final."""


class SearchRequest(BaseModel):
    """Freeform provider search not tied to a record."""
    provider: str
    title: str
    volume: str = ""
    author: str = ""


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------

def get_cfg() -> PipelineConfig:
    return load_config()


def get_store(cfg: PipelineConfig = Depends(get_cfg)) -> Generator[ControlStore]:
    store = ControlStore(cfg.paths.state)
    try:
        yield store
    finally:
        store.close()


def require_user(
    pipeline_session: str | None = Cookie(default=None),
    store: ControlStore = Depends(get_store),
) -> dict[str, Any]:
    if not store.has_admin():
        raise HTTPException(status_code=428, detail="需要创建管理员账号")
    if not pipeline_session:
        raise HTTPException(status_code=401, detail="未登录")
    user = store.get_session_user(pipeline_session)
    if not user:
        raise HTTPException(status_code=401, detail="登录已过期")
    return user


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

@app.get("/api/setup/status")
def setup_status(store: ControlStore = Depends(get_store)) -> dict[str, Any]:
    return {"has_admin": store.has_admin()}


@app.post("/api/setup")
def setup(
    request: SetupRequest,
    response: Response,
    store: ControlStore = Depends(get_store),
) -> dict[str, Any]:
    try:
        user_id = store.create_admin(request.username, request.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    token = store.create_session(user_id)
    _set_session_cookie(response, token)
    return {"ok": True, "username": request.username}


@app.post("/api/login")
def login(
    request: LoginRequest,
    response: Response,
    store: ControlStore = Depends(get_store),
) -> dict[str, Any]:
    user_id = store.authenticate(request.username, request.password)
    if user_id is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = store.create_session(user_id)
    _set_session_cookie(response, token)
    return {"ok": True}


@app.post("/api/logout")
def logout(
    response: Response,
    pipeline_session: str | None = Cookie(default=None),
    store: ControlStore = Depends(get_store),
) -> dict[str, Any]:
    if pipeline_session:
        store.delete_session(pipeline_session)
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/me")
def me(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    return user


# ---------------------------------------------------------------------------
# Dashboard / status
# ---------------------------------------------------------------------------

@app.get("/api/status")
def status(
    _user: dict[str, Any] = Depends(require_user),
    cfg: PipelineConfig = Depends(get_cfg),
    store: ControlStore = Depends(get_store),
) -> dict[str, Any]:
    db = Database(cfg.paths.state / "pipeline.db")
    try:
        counts = db.get_status_counts()
        recent = _query_records(db.db_path, limit=20)
        approvals = store.list_approvals()
        return {
            "mode": store.get_mode(),
            "counts": counts,
            "total": sum(counts.values()),
            "pending_approvals": len(approvals),
            "recent_records": recent,
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.get("/api/settings")
def get_settings(
    _user: dict[str, Any] = Depends(require_user),
    store: ControlStore = Depends(get_store),
) -> dict[str, Any]:
    return {
        "mode": store.get_mode(),
        "valid_modes": sorted(VALID_MODES),
        "prompt": store.get_active_prompt(),
        "prompt_history": store.list_prompts(),
    }


@app.put("/api/settings/mode")
def set_mode(
    request: ModeRequest,
    _user: dict[str, Any] = Depends(require_user),
    store: ControlStore = Depends(get_store),
) -> dict[str, Any]:
    try:
        store.set_mode(request.mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"mode": store.get_mode()}


@app.put("/api/settings/prompt")
def set_prompt(
    request: PromptRequest,
    _user: dict[str, Any] = Depends(require_user),
    store: ControlStore = Depends(get_store),
) -> dict[str, Any]:
    try:
        prompt_id = store.set_active_prompt(request.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"id": prompt_id, "prompt": store.get_active_prompt()}


# ---------------------------------------------------------------------------
# Records — list & detail
# ---------------------------------------------------------------------------

@app.get("/api/records")
def list_records(
    status_filter: str = "",
    search: str = "",
    page: int = 1,
    size: int = 50,
    _user: dict[str, Any] = Depends(require_user),
    cfg: PipelineConfig = Depends(get_cfg),
) -> dict[str, Any]:
    """Paginated, filterable record listing."""
    db_path = cfg.paths.state / "pipeline.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conditions: list[str] = []
        params: list[Any] = []
        if status_filter:
            conditions.append("current_status = ?")
            params.append(status_filter)
        if search:
            like = f"%{search}%"
            conditions.append(
                "(file_name LIKE ? OR series LIKE ? OR title LIKE ? OR collection_title LIKE ?)"
            )
            params.extend([like, like, like, like])

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        total = conn.execute(
            f"SELECT COUNT(*) FROM manga_records {where}", params
        ).fetchone()[0]

        offset = (page - 1) * size
        rows = conn.execute(
            f"""
            SELECT id, file_name, current_status, title, series, volume, author,
                   publisher, collection_title, cover_url, source_url, confidence,
                   error_message, created_at, updated_at
            FROM manga_records {where}
            ORDER BY updated_at DESC
            LIMIT ? OFFSET ?
            """,
            params + [size, offset],
        ).fetchall()
        return {
            "total": total,
            "page": page,
            "size": size,
            "items": [dict(row) for row in rows],
        }
    finally:
        conn.close()


@app.get("/api/records/{record_id}")
def get_record(
    record_id: int,
    _user: dict[str, Any] = Depends(require_user),
    cfg: PipelineConfig = Depends(get_cfg),
) -> dict[str, Any]:
    """Fetch a single record with all fields."""
    db = Database(cfg.paths.state / "pipeline.db")
    try:
        record = db.get_record_by_id(record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        return record.__dict__
    finally:
        db.close()


@app.patch("/api/records/{record_id}/metadata")
def patch_metadata(
    record_id: int,
    request: MetadataPatchRequest,
    _user: dict[str, Any] = Depends(require_user),
    cfg: PipelineConfig = Depends(get_cfg),
) -> dict[str, Any]:
    """Manually override metadata fields for a record."""
    from datetime import datetime

    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="没有提供任何字段")

    db_path = cfg.paths.state / "pipeline.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT id FROM manga_records WHERE id = ?", (record_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        set_clauses = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [datetime.now().isoformat(), record_id]
        conn.execute(
            f"UPDATE manga_records SET {set_clauses}, updated_at = ? WHERE id = ?",
            values,
        )
        conn.commit()
        row = conn.execute("SELECT * FROM manga_records WHERE id = ?", (record_id,)).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()



@app.post("/api/records/{record_id}/rescrape")
def single_rescrape(
    record_id: int,
    request: SingleRescrapeRequest,
    _user: dict[str, Any] = Depends(require_user),
    cfg: PipelineConfig = Depends(get_cfg),
) -> dict[str, Any]:
    """Re-scrape a single record with an optional custom search title.

    When ``force=True`` the request bypasses all automatic filters (LLM
    normalization, confidence thresholds, context-matching). The user's
    explicit provider + title choice is treated as authoritative.
    """
    db = Database(cfg.paths.state / "pipeline.db")
    try:
        record = db.get_record_by_id(record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="记录不存在")

        if request.force:
            # ── Manual override path: no LLM, no threshold, no context check ──
            result = force_rescrape_record(
                record,
                cfg,
                db,
                provider=request.provider,
                search_title=request.title or record.series or record.title or record.file_name,
                volume=request.volume,
                author=request.author,
                dry_run=request.dry_run,
                relocate=request.relocate,
            )
            return {"result": result.__dict__}

        # ── Automatic path: LLM normalization + confidence gates apply ──
        if request.title:
            from dataclasses import replace as _replace
            record = _replace(record, series=request.title, title=request.title)
        results = rescrape_records(
            [record],
            cfg,
            db,
            dry_run=request.dry_run,
            relocate=request.relocate,
            trigger_scan=not request.dry_run,
        )
        return {"result": results[0].__dict__ if results else {}}
    finally:
        db.close()


@app.post("/api/records/{record_id}/reimport")
def reimport_record(
    record_id: int,
    _user: dict[str, Any] = Depends(require_user),
    cfg: PipelineConfig = Depends(get_cfg),
) -> dict[str, Any]:
    """Reset record to METADATA_PARSED so the pipeline re-runs convert+import steps."""
    db = Database(cfg.paths.state / "pipeline.db")
    try:
        record = db.get_record_by_id(record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        # Only reset if the record is in a terminal or failed state
        allowed_statuses = {
            ProcessingStatus.DONE,
            ProcessingStatus.FAILED,
            ProcessingStatus.NEEDS_REVIEW,
            ProcessingStatus.METADATA_PARSED,
            ProcessingStatus.IMPORTED,
        }
        if record.current_status not in allowed_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"记录当前状态 {record.current_status} 不支持重新入库",
            )
        db.update_status(record_id, ProcessingStatus.METADATA_PARSED, error_message="")
        return {"ok": True, "status": ProcessingStatus.METADATA_PARSED}
    finally:
        db.close()


@app.post("/api/records/{record_id}/reset")
def reset_record(
    record_id: int,
    _user: dict[str, Any] = Depends(require_user),
    cfg: PipelineConfig = Depends(get_cfg),
) -> dict[str, Any]:
    """Reset a failed/stuck record back to DISCOVERED so the pipeline retries from scratch."""
    db = Database(cfg.paths.state / "pipeline.db")
    try:
        record = db.get_record_by_id(record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        db.update_status(record_id, ProcessingStatus.DISCOVERED, error_message="")
        return {"ok": True, "status": ProcessingStatus.DISCOVERED}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Provider search (standalone, not tied to a record)
# ---------------------------------------------------------------------------

@app.post("/api/search")
def search_provider(
    request: SearchRequest,
    _user: dict[str, Any] = Depends(require_user),
    cfg: PipelineConfig = Depends(get_cfg),
) -> dict[str, Any]:
    """Search a metadata provider with arbitrary terms and return candidates."""
    metadata = _search_provider(
        request.provider,
        request.title,
        request.volume,
        request.author,
        cfg,
    )
    if metadata is None:
        return {"candidate": None}
    candidate = candidate_from_metadata(request.provider, metadata)
    return {"candidate": candidate.__dict__}


# ---------------------------------------------------------------------------
# LLM runs
# ---------------------------------------------------------------------------

@app.get("/api/llm-runs")
def llm_runs(
    limit: int = 50,
    _user: dict[str, Any] = Depends(require_user),
    store: ControlStore = Depends(get_store),
) -> dict[str, Any]:
    return {"items": store.list_llm_runs(limit=limit)}


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------

@app.get("/api/approvals")
def approvals(
    _user: dict[str, Any] = Depends(require_user),
    store: ControlStore = Depends(get_store),
) -> dict[str, Any]:
    return {"items": store.list_approvals()}


@app.get("/api/approvals/{approval_id}")
def approval_detail(
    approval_id: int,
    _user: dict[str, Any] = Depends(require_user),
    store: ControlStore = Depends(get_store),
) -> dict[str, Any]:
    approval = store.get_approval(approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="确认项不存在")
    return approval


@app.post("/api/approvals/{approval_id}/search")
def approval_search(
    approval_id: int,
    request: CandidateSearchRequest,
    _user: dict[str, Any] = Depends(require_user),
    cfg: PipelineConfig = Depends(get_cfg),
    store: ControlStore = Depends(get_store),
) -> dict[str, Any]:
    approval = store.get_approval(approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="确认项不存在")
    db = Database(cfg.paths.state / "pipeline.db")
    try:
        record = db.get_record_by_id(approval["record_id"])
        if record is None:
            raise HTTPException(status_code=404, detail="记录不存在")
    finally:
        db.close()
    metadata = _search_provider(
        request.provider,
        request.title,
        record.volume,
        record.author,
        cfg,
    )
    if metadata is None:
        return {"candidate": None}
    candidate = candidate_from_metadata(request.provider, metadata)
    candidates = [
        MetadataCandidate(**item)
        for item in approval.get("candidates", [])
        if isinstance(item, dict)
    ]
    candidates.append(candidate)
    parsed = approval.get("parsed", {})
    store.create_or_update_approval(
        record=record,
        scope=approval["scope"],
        parsed=parsed,
        candidates=candidates,
    )
    return {"candidate": candidate}


@app.post("/api/approvals/{approval_id}/approve")
def approve(
    approval_id: int,
    request: ApproveRequest,
    _user: dict[str, Any] = Depends(require_user),
    cfg: PipelineConfig = Depends(get_cfg),
    store: ControlStore = Depends(get_store),
) -> dict[str, Any]:
    approval = store.get_approval(approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail="确认项不存在")
    candidates = approval.get("candidates", [])
    if request.candidate_index >= len(candidates):
        raise HTTPException(status_code=400, detail="候选索引无效")
    candidate_data = candidates[request.candidate_index]
    candidate = MetadataCandidate(**candidate_data)
    parsed_data = approval.get("parsed", {})
    parsed = ParseResult(
        title=str(parsed_data.get("title") or ""),
        series=str(parsed_data.get("series") or ""),
        author=str(parsed_data.get("author") or ""),
        publisher=str(parsed_data.get("publisher") or ""),
        volume=str(parsed_data.get("volume") or ""),
        confidence=float(parsed_data.get("confidence") or 0.0),
    )
    _apply_review_candidate(parsed, candidate)
    db = Database(cfg.paths.state / "pipeline.db")
    try:
        db.update_status(
            approval["record_id"],
            ProcessingStatus.METADATA_PARSED,
            **_candidate_record_fields(parsed, candidate),
        )
    finally:
        db.close()
    store.approve(approval_id, candidate_data)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Batch rescrape (existing)
# ---------------------------------------------------------------------------

@app.post("/api/rescrape")
def rescrape(
    request: RescrapeRequest,
    _user: dict[str, Any] = Depends(require_user),
    cfg: PipelineConfig = Depends(get_cfg),
) -> dict[str, Any]:
    db = Database(cfg.paths.state / "pipeline.db")
    try:
        records = select_records(
            db,
            ids=request.ids,
            title=request.title,
            all_records=request.all_records,
            done_only=not request.include_unfinished,
        )
        results = rescrape_records(
            records,
            cfg,
            db,
            dry_run=request.dry_run,
            relocate=request.relocate,
            trigger_scan=not request.dry_run,
        )
        return {"items": [result.__dict__ for result in results]}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _search_provider(
    provider: str,
    title: str,
    volume: str,
    author: str,
    cfg: PipelineConfig,
) -> Any:
    if provider == "bookwalker_tw":
        return search_bookwalker_tw(
            title,
            volume=volume,
            author=author,
            max_candidates=cfg.metadata.bookwalker_tw_max_candidates,
        )
    if provider == "bookwalker_jp":
        return search_bookwalker_jp(
            title,
            volume=volume,
            author=author,
            max_candidates=cfg.metadata.bookwalker_jp_max_candidates,
        )
    if provider == "bangumi":
        return search_bangumi(
            title,
            volume=volume,
            author=author,
            max_candidates=cfg.metadata.bangumi_max_candidates,
        )
    raise HTTPException(status_code=400, detail="未知 provider")


def _query_records(db_path: Path, limit: int) -> list[dict[str, Any]]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, file_name, current_status, title, series, volume, author,
                   collection_title, cover_url, source_url, confidence, updated_at
            FROM manga_records
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
    )
