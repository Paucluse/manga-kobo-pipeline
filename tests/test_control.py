from __future__ import annotations

from manga_pipeline.control import (
    APPROVAL_PENDING,
    MODE_AUTO,
    MODE_MANUAL_SERIES,
    ControlStore,
    MetadataCandidate,
)
from manga_pipeline.database import Database
from manga_pipeline.models import MangaRecord, ProcessingStatus


def test_control_store_admin_session_and_defaults(tmp_path):
    store = ControlStore(tmp_path)
    try:
        assert store.get_mode() == MODE_AUTO
        assert store.get_active_prompt()
        assert not store.has_admin()

        user_id = store.create_admin("admin", "secret")
        assert store.has_admin()
        assert store.authenticate("admin", "bad") is None
        assert store.authenticate("admin", "secret") == user_id

        token = store.create_session(user_id)
        assert store.get_session_user(token) == {"id": user_id, "username": "admin"}
        store.delete_session(token)
        assert store.get_session_user(token) is None
    finally:
        store.close()


def test_control_store_mode_prompt_and_llm_log(tmp_path):
    store = ControlStore(tmp_path)
    try:
        store.set_mode(MODE_MANUAL_SERIES)
        assert store.get_mode() == MODE_MANUAL_SERIES

        prompt_id = store.set_active_prompt("只输出 JSON")
        assert prompt_id > 0
        assert store.get_active_prompt() == "只输出 JSON"

        run_id = store.log_llm_run(
            record_id=3,
            source_name="D・N・A2 01.epub",
            prompt="只输出 JSON",
            response='{"title":"D・N・A²"}',
            parsed_json={"title": "D・N・A²"},
            elapsed_ms=42,
        )
        runs = store.list_llm_runs()
        assert runs[0]["id"] == run_id
        assert runs[0]["elapsed_ms"] == 42
    finally:
        store.close()


def test_metadata_approval_and_series_policy(tmp_path):
    db = Database(tmp_path / "pipeline.db")
    store = ControlStore(tmp_path)
    try:
        record = MangaRecord(
            original_path="/data/inbox/DNA/DNA 01.epub",
            file_name="DNA 01.epub",
            file_hash="hash-dna-01",
            current_status=ProcessingStatus.WAITING_STABLE,
            collection_title="DNA",
        )
        record.id = db.insert_record(record)

        candidate = MetadataCandidate(
            provider="bookwalker_jp",
            title="D・N・A²",
            series="D・N・A²",
            volume="1",
            detail_url="https://bookwalker.jp/example",
            confidence=0.92,
        )
        approval_id = store.create_or_update_approval(
            record=record,
            scope="series",
            parsed={"title": "DNA", "volume": "1"},
            candidates=[candidate],
        )

        approvals = store.list_approvals()
        assert len(approvals) == 1
        assert approvals[0]["id"] == approval_id
        assert approvals[0]["status"] == APPROVAL_PENDING
        assert approvals[0]["candidates"][0]["provider"] == "bookwalker_jp"

        store.approve(approval_id, approvals[0]["candidates"][0])
        policy = store.get_series_policy("DNA")
        assert policy is not None
        assert policy["provider"] == "bookwalker_jp"
        assert policy["series"] == "D・N・A²"
    finally:
        store.close()
        db.close()
