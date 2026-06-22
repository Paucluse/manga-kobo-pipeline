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
from manga_pipeline.rescrape import rescrape_records, select_records

SESSION_COOKIE = "pipeline_session"

app = FastAPI(title="Manga Pipeline Control API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.get("/api/llm-runs")
def llm_runs(
    limit: int = 50,
    _user: dict[str, Any] = Depends(require_user),
    store: ControlStore = Depends(get_store),
) -> dict[str, Any]:
    return {"items": store.list_llm_runs(limit=limit)}


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
            SELECT id, file_name, current_status, title, series, volume,
                   collection_title, source_url, updated_at
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
