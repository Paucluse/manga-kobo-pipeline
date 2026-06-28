"""Control-plane storage for the web console.

The processing pipeline keeps running without this module being actively used.
When the API/frontend writes settings here, the pipeline reads them at stage
boundaries to decide whether to keep running automatically or pause for review.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from manga_pipeline.llm_metadata import SYSTEM_PROMPT
from manga_pipeline.models import MangaRecord

LEGACY_DEFAULT_LLM_PROMPT = (
    "你是漫画电子书文件名标准化器。只输出 JSON, 不要解释。"
    "你的任务是从可能不准确的文件名推断正式书名和检索关键词。"
    "优先给出台版正式书名; 如台版可能不存在, 同时给出日版正式书名。"
    "重点给出可用于 BookWalker 台湾、BookWalker 日本、Bangumi 检索的别名。"
    "不要编造出版社; 不确定则留空。"
)
DEFAULT_LLM_PROMPT = SYSTEM_PROMPT

MODE_AUTO = "auto"
MODE_MANUAL_BOOK = "manual_book"
MODE_MANUAL_SERIES = "manual_series"
MODE_PAUSED = "paused"
VALID_MODES = {MODE_AUTO, MODE_MANUAL_BOOK, MODE_MANUAL_SERIES, MODE_PAUSED}

APPROVAL_PENDING = "pending"
APPROVAL_APPROVED = "approved"
APPROVAL_REJECTED = "rejected"


@dataclass
class MetadataCandidate:
    """Provider metadata candidate shown in the review UI."""

    provider: str
    title: str = ""
    series: str = ""
    volume: str = ""
    author: str = ""
    publisher: str = ""
    summary: str = ""
    cover_url: str = ""
    detail_url: str = ""
    isbn: str = ""
    page_count: str = ""
    confidence: float = 0.0
    provider_id: str = ""


class ControlStore:
    """SQLite-backed control-plane store."""

    def __init__(self, state_dir: Path) -> None:
        state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = state_dir / "pipeline.db"
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def close(self) -> None:
        self.conn.close()

    def _create_tables(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS app_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS web_sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES app_users(id)
            );

            CREATE TABLE IF NOT EXISTS pipeline_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS llm_prompts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS llm_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                record_id INTEGER,
                source_name TEXT NOT NULL DEFAULT '',
                prompt TEXT NOT NULL DEFAULT '',
                response TEXT NOT NULL DEFAULT '',
                parsed_json TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                elapsed_ms INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS metadata_approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                record_id INTEGER NOT NULL,
                scope TEXT NOT NULL DEFAULT 'book',
                collection_title TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                parsed_json TEXT NOT NULL DEFAULT '{}',
                candidates_json TEXT NOT NULL DEFAULT '[]',
                selected_candidate_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(record_id, status)
            );

            CREATE TABLE IF NOT EXISTS series_policies (
                collection_title TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                series TEXT NOT NULL,
                candidate_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        active_prompt = self.get_active_prompt()
        if active_prompt in {"", LEGACY_DEFAULT_LLM_PROMPT}:
            self.set_active_prompt(DEFAULT_LLM_PROMPT)
        if not self.get_setting("control_mode"):
            self.set_mode(MODE_AUTO)
        self.conn.commit()

    def has_admin(self) -> bool:
        row = self.conn.execute("SELECT 1 FROM app_users LIMIT 1").fetchone()
        return row is not None

    def create_admin(self, username: str, password: str) -> int:
        if self.has_admin():
            raise ValueError("管理员账号已存在")
        username = username.strip()
        if not username or not password:
            raise ValueError("用户名和密码不能为空")
        salt = secrets.token_hex(16)
        password_hash = _hash_password(password, salt)
        cursor = self.conn.execute(
            """
            INSERT INTO app_users (username, password_hash, salt, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (username, password_hash, salt, _now()),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def authenticate(self, username: str, password: str) -> int | None:
        row = self.conn.execute(
            "SELECT id, password_hash, salt FROM app_users WHERE username = ?",
            (username.strip(),),
        ).fetchone()
        if not row:
            return None
        actual = _hash_password(password, row["salt"])
        if not hmac.compare_digest(actual, row["password_hash"]):
            return None
        return int(row["id"])

    def create_session(self, user_id: int, ttl_hours: int = 168) -> str:
        token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        expires_at = now + timedelta(hours=ttl_hours)
        self.conn.execute(
            """
            INSERT INTO web_sessions (token, user_id, expires_at, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (token, user_id, expires_at.isoformat(), now.isoformat()),
        )
        self.conn.commit()
        return token

    def get_session_user(self, token: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT u.id, u.username, s.expires_at
            FROM web_sessions s
            JOIN app_users u ON u.id = s.user_id
            WHERE s.token = ?
            """,
            (token,),
        ).fetchone()
        if not row:
            return None
        if datetime.fromisoformat(row["expires_at"]) < datetime.now(UTC):
            self.delete_session(token)
            return None
        return {"id": row["id"], "username": row["username"]}

    def delete_session(self, token: str) -> None:
        self.conn.execute("DELETE FROM web_sessions WHERE token = ?", (token,))
        self.conn.commit()

    def get_setting(self, key: str) -> str:
        row = self.conn.execute(
            "SELECT value FROM pipeline_settings WHERE key = ?", (key,)
        ).fetchone()
        return str(row["value"]) if row else ""

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute(
            """
            INSERT INTO pipeline_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, value, _now()),
        )
        self.conn.commit()

    def get_mode(self) -> str:
        mode = self.get_setting("control_mode") or MODE_AUTO
        return mode if mode in VALID_MODES else MODE_AUTO

    def set_mode(self, mode: str) -> None:
        if mode not in VALID_MODES:
            raise ValueError(f"Unsupported mode: {mode}")
        self.set_setting("control_mode", mode)

    def get_active_prompt(self) -> str:
        row = self.conn.execute(
            "SELECT content FROM llm_prompts WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return str(row["content"]) if row else ""

    def set_active_prompt(self, content: str) -> int:
        content = content.strip()
        if not content:
            raise ValueError("Prompt 不能为空")
        self.conn.execute("UPDATE llm_prompts SET active = 0")
        cursor = self.conn.execute(
            "INSERT INTO llm_prompts (content, active, created_at) VALUES (?, 1, ?)",
            (content, _now()),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def list_prompts(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT id, content, active, created_at
            FROM llm_prompts
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def log_llm_run(
        self,
        *,
        record_id: int | None,
        source_name: str,
        prompt: str,
        response: str = "",
        parsed_json: dict[str, Any] | None = None,
        error: str = "",
        elapsed_ms: int = 0,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO llm_runs (
                record_id, source_name, prompt, response, parsed_json,
                error, elapsed_ms, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                source_name,
                prompt,
                response,
                json.dumps(parsed_json or {}, ensure_ascii=False),
                error,
                elapsed_ms,
                _now(),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def list_llm_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM llm_runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def create_or_update_approval(
        self,
        *,
        record: MangaRecord,
        scope: str,
        parsed: dict[str, Any],
        candidates: list[MetadataCandidate],
    ) -> int:
        now = _now()
        existing = self.conn.execute(
            """
            SELECT id FROM metadata_approvals
            WHERE record_id = ? AND status = ?
            """,
            (record.id, APPROVAL_PENDING),
        ).fetchone()
        payload = (
            scope,
            record.collection_title,
            json.dumps(parsed, ensure_ascii=False),
            json.dumps([asdict(candidate) for candidate in candidates], ensure_ascii=False),
            now,
        )
        if existing:
            self.conn.execute(
                """
                UPDATE metadata_approvals
                SET scope = ?, collection_title = ?, parsed_json = ?,
                    candidates_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (*payload, existing["id"]),
            )
            self.conn.commit()
            return int(existing["id"])
        cursor = self.conn.execute(
            """
            INSERT INTO metadata_approvals (
                record_id, scope, collection_title, parsed_json,
                candidates_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (record.id, *payload[:-1], now, now),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def list_approvals(self, status: str = APPROVAL_PENDING) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT a.*, r.file_name, r.title, r.series, r.volume, r.current_status
            FROM metadata_approvals a
            JOIN manga_records r ON r.id = a.record_id
            WHERE a.status = ?
            ORDER BY a.created_at
            """,
            (status,),
        ).fetchall()
        return [_decode_approval(dict(row)) for row in rows]

    def get_approval(self, approval_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            SELECT a.*, r.file_name, r.title, r.series, r.volume, r.current_status
            FROM metadata_approvals a
            JOIN manga_records r ON r.id = a.record_id
            WHERE a.id = ?
            """,
            (approval_id,),
        ).fetchone()
        return _decode_approval(dict(row)) if row else None

    def approve(self, approval_id: int, candidate: dict[str, Any]) -> None:
        approval = self.get_approval(approval_id)
        if not approval:
            raise ValueError("确认项不存在")
        self.conn.execute(
            """
            UPDATE metadata_approvals
            SET status = ?, selected_candidate_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                APPROVAL_APPROVED,
                json.dumps(candidate, ensure_ascii=False),
                _now(),
                approval_id,
            ),
        )
        if approval["scope"] == "series" and approval["collection_title"]:
            self.save_series_policy(approval["collection_title"], candidate)
        self.conn.commit()

    def save_series_policy(self, collection_title: str, candidate: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO series_policies (
                collection_title, provider, series, candidate_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(collection_title) DO UPDATE SET
                provider = excluded.provider,
                series = excluded.series,
                candidate_json = excluded.candidate_json,
                updated_at = excluded.updated_at
            """,
            (
                collection_title,
                str(candidate.get("provider") or ""),
                str(candidate.get("series") or candidate.get("title") or ""),
                json.dumps(candidate, ensure_ascii=False),
                _now(),
                _now(),
            ),
        )

    def get_series_policy(self, collection_title: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM series_policies WHERE collection_title = ?",
            (collection_title,),
        ).fetchone()
        if not row:
            return None
        value = dict(row)
        value["candidate"] = _json_loads(value.pop("candidate_json"), {})
        return value


def candidate_from_metadata(provider: str, metadata: Any) -> MetadataCandidate:
    provider_id = (
        getattr(metadata, "product_id", "")
        or getattr(metadata, "subject_id", "")
        or getattr(metadata, "detail_url", "")
    )
    return MetadataCandidate(
        provider=provider,
        title=getattr(metadata, "title", "") or "",
        series=getattr(metadata, "series", "") or "",
        volume=getattr(metadata, "volume", "") or "",
        author=getattr(metadata, "author_text", "") or "",
        publisher=getattr(metadata, "publisher", "") or "",
        summary=getattr(metadata, "summary", "") or "",
        cover_url=getattr(metadata, "cover_url", "") or "",
        detail_url=getattr(metadata, "detail_url", "") or "",
        isbn=getattr(metadata, "isbn", "") or "",
        page_count=getattr(metadata, "page_count", "") or "",
        confidence=float(getattr(metadata, "confidence", 0.0) or 0.0),
        provider_id=str(provider_id),
    )


def _decode_approval(value: dict[str, Any]) -> dict[str, Any]:
    value["parsed"] = _json_loads(value.pop("parsed_json"), {})
    value["candidates"] = _json_loads(value.pop("candidates_json"), [])
    value["selected_candidate"] = _json_loads(value.pop("selected_candidate_json"), {})
    return value


def _json_loads(value: str, default: Any) -> Any:
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def _hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        120_000,
    ).hex()


def _now() -> str:
    return datetime.now(UTC).isoformat()
