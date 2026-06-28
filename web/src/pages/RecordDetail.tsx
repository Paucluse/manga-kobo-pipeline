import React, { useEffect, useState } from "react";
import * as api from "../api";
import type { Candidate, MangaRecord, MetadataPatch, RescrapeResult } from "../types";
import { StatusBadge } from "../components/StatusBadge";
import { CandidateCard } from "../components/CandidateCard";

interface RecordDetailProps {
  recordId: number;
  onBack: () => void;
}

type Toast = { message: string; type: "success" | "error" };

export function RecordDetail({ recordId, onBack }: RecordDetailProps) {
  const [record, setRecord] = useState<MangaRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [toast, setToast] = useState<Toast | null>(null);

  // Edit form
  const [editing, setEditing] = useState(false);
  const [patch, setPatch] = useState<MetadataPatch>({});

  // Rescrape
  const [searchTitle, setSearchTitle] = useState("");
  const [searchVolume, setSearchVolume] = useState("");
  const [searchAuthor, setSearchAuthor] = useState("");
  const [provider, setProvider] = useState("bookwalker_tw");
  const [searching, setSearching] = useState(false);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [searchError, setSearchError] = useState("");

  // Last rescrape result
  const [rescrapeResult, setRescrapeResult] = useState<RescrapeResult | null>(null);
  const [rescraping, setRescraping] = useState(false);

  // Reimport
  const [reimporting, setReimporting] = useState(false);

  async function load() {
    try {
      const rec = await api.getRecord(recordId);
      setRecord(rec);
      setSearchTitle(rec.series || rec.title || "");
      setSearchVolume(rec.volume || "");
      setSearchAuthor(rec.author || "");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, [recordId]);

  function showToast(message: string, type: "success" | "error") {
    setToast({ message, type });
    setTimeout(() => setToast(null), 3500);
  }

  // ── Edit metadata ─────────────────────────────────────────────
  function startEdit() {
    if (!record) return;
    setPatch({
      title: record.title,
      series: record.series,
      author: record.author,
      publisher: record.publisher,
      volume: record.volume,
      summary: record.summary,
      isbn: record.isbn,
    });
    setEditing(true);
  }

  async function saveEdit() {
    if (!record) return;
    try {
      await api.patchMetadata(record.id, patch);
      setEditing(false);
      showToast("元数据已保存", "success");
      await load();
    } catch (e) {
      showToast((e as Error).message, "error");
    }
  }

  // ── Search provider ───────────────────────────────────────────
  async function doSearch() {
    if (!searchTitle.trim()) return;
    setSearching(true);
    setSearchError("");
    setCandidates([]);
    try {
      const res = await api.searchProvider(provider, searchTitle, searchVolume, searchAuthor);
      if (res.candidate) {
        setCandidates([res.candidate]);
      } else {
        setSearchError("未找到结果");
      }
    } catch (e) {
      setSearchError((e as Error).message);
    } finally {
      setSearching(false);
    }
  }

  // ── Use candidate (apply metadata + trigger rescrape) ─────────
  async function applyCandidate(candidate: Candidate) {
    if (!record) return;
    setRescraping(true);
    setRescrapeResult(null);
    try {
      const res = await api.rescrapeRecord(record.id, {
        provider,
        title: candidate.series || candidate.title || searchTitle,
        dry_run: false,
        relocate: true,
      });
      setRescrapeResult(res.result);
      showToast(`重刮削完成：${res.result.new_series || res.result.new_title || "已更新"}`, "success");
      await load();
    } catch (e) {
      showToast((e as Error).message, "error");
    } finally {
      setRescraping(false);
    }
  }

  // ── Reimport ──────────────────────────────────────────────────
  async function doReimport() {
    if (!record) return;
    setReimporting(true);
    try {
      await api.reimportRecord(record.id);
      showToast("已重置为待导入状态，管线将重新处理", "success");
      await load();
    } catch (e) {
      showToast((e as Error).message, "error");
    } finally {
      setReimporting(false);
    }
  }

  // ── Reset ─────────────────────────────────────────────────────
  async function doReset() {
    if (!record) return;
    if (!window.confirm("确定将此记录重置为初始状态？")) return;
    try {
      await api.resetRecord(record.id);
      showToast("已重置，管线将从头重新处理", "success");
      await load();
    } catch (e) {
      showToast((e as Error).message, "error");
    }
  }

  if (loading) return <div className="empty-state"><div className="spinner" /></div>;
  if (!record) return <div className="error-msg">{error || "记录不存在"}</div>;

  return (
    <div>
      {/* Header */}
      <div className="page-header">
        <button type="button" className="btn btn-secondary btn-sm" onClick={onBack}>← 返回</button>
        <div style={{ flex: 1 }}>
          <h1 style={{ fontSize: 18 }}>#{record.id} {record.series || record.title || record.file_name}</h1>
          <div className="subtitle">{record.file_name}</div>
        </div>
        <StatusBadge status={record.current_status} />
      </div>

      {error && <div className="error-msg">{error}</div>}

      {/* Current metadata */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">当前元数据</span>
          <div style={{ display: "flex", gap: 8 }}>
            {!editing && (
              <button type="button" className="btn btn-secondary btn-sm" onClick={startEdit}>✏️ 编辑</button>
            )}
          </div>
        </div>

        {editing ? (
          /* Edit form */
          <div>
            <div className="form-grid" style={{ marginBottom: 16 }}>
              <label>
                系列名
                <input value={patch.series ?? ""} onChange={e => setPatch(p => ({ ...p, series: e.target.value }))} />
              </label>
              <label>
                书名
                <input value={patch.title ?? ""} onChange={e => setPatch(p => ({ ...p, title: e.target.value }))} />
              </label>
              <label>
                作者
                <input value={patch.author ?? ""} onChange={e => setPatch(p => ({ ...p, author: e.target.value }))} />
              </label>
              <label>
                出版社
                <input value={patch.publisher ?? ""} onChange={e => setPatch(p => ({ ...p, publisher: e.target.value }))} />
              </label>
              <label>
                卷号
                <input value={patch.volume ?? ""} onChange={e => setPatch(p => ({ ...p, volume: e.target.value }))} />
              </label>
              <label>
                ISBN
                <input value={patch.isbn ?? ""} onChange={e => setPatch(p => ({ ...p, isbn: e.target.value }))} />
              </label>
              <label style={{ gridColumn: "1 / -1" }}>
                简介
                <textarea
                  value={patch.summary ?? ""}
                  onChange={e => setPatch(p => ({ ...p, summary: e.target.value }))}
                  style={{ minHeight: 80 }}
                />
              </label>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button type="button" className="btn btn-primary" onClick={saveEdit}>保存</button>
              <button type="button" className="btn btn-secondary" onClick={() => setEditing(false)}>取消</button>
            </div>
          </div>
        ) : (
          /* View mode */
          <div className="detail-grid">
            <div className="detail-cover">
              {record.cover_url ? (
                <img src={record.cover_url} alt="" />
              ) : (
                <div className="detail-cover-placeholder">📖</div>
              )}
              {record.source_url && (
                <a href={record.source_url} target="_blank" rel="noreferrer" style={{ display: "block", textAlign: "center", marginTop: 8, fontSize: 12 }}>
                  查看来源 ↗
                </a>
              )}
            </div>
            <div>
              <div className="detail-fields">
                <MetaField label="系列名" value={record.series} />
                <MetaField label="书名" value={record.title} />
                <MetaField label="作者" value={record.author} />
                <MetaField label="出版社" value={record.publisher} />
                <MetaField label="卷号" value={record.volume} />
                <MetaField label="ISBN" value={record.isbn} />
                <MetaField label="页数" value={record.page_count} />
                <MetaField label="置信度" value={record.confidence > 0 ? `${(record.confidence * 100).toFixed(1)}%` : ""} />
                <MetaField label="合集" value={record.collection_title} />
              </div>
              {record.summary && (
                <div style={{ marginTop: 16 }}>
                  <div style={{ fontSize: 11, textTransform: "uppercase", color: "var(--text-muted)", letterSpacing: "0.05em", marginBottom: 4 }}>简介</div>
                  <div style={{ fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.6 }}>{record.summary}</div>
                </div>
              )}
              {record.error_message && (
                <div className="error-msg" style={{ marginTop: 12 }}>
                  ❌ {record.error_message}
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Quick actions */}
      <div className="card">
        <div className="card-title" style={{ marginBottom: 12 }}>快捷操作</div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <button
            type="button"
            className="btn btn-primary"
            disabled={reimporting}
            onClick={doReimport}
          >
            {reimporting ? <span className="spinner" style={{ width: 14, height: 14 }} /> : "📥"} 重新入库
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={doReset}
          >
            🔄 重置管线
          </button>
          {record.library_book_id && (
            <span style={{ fontSize: 12, color: "var(--text-muted)", padding: "8px 0" }}>
              Komga ID: {record.library_book_id}
            </span>
          )}
        </div>
        {rescrapeResult && (
          <div style={{ marginTop: 12, padding: 12, background: "var(--bg-elevated)", borderRadius: "var(--r-sm)", fontSize: 13 }}>
            <div style={{ color: rescrapeResult.status === "updated" ? "var(--status-done)" : "var(--status-review)", fontWeight: 600, marginBottom: 4 }}>
              {rescrapeResult.status === "updated" ? "✓ 更新成功" : rescrapeResult.status}
            </div>
            {rescrapeResult.old_series !== rescrapeResult.new_series && (
              <div style={{ color: "var(--text-secondary)" }}>
                系列：{rescrapeResult.old_series || "—"} → <strong>{rescrapeResult.new_series}</strong>
              </div>
            )}
            {rescrapeResult.message && (
              <div style={{ color: "var(--text-muted)", marginTop: 4 }}>{rescrapeResult.message}</div>
            )}
          </div>
        )}
      </div>

      {/* Manual rescrape */}
      <div className="rescrape-panel">
        <h3>🔍 手动重新刮削</h3>
        <div className="form-grid" style={{ marginBottom: 12 }}>
          <label>
            数据源
            <select value={provider} onChange={e => setProvider(e.target.value)}>
              <option value="bookwalker_tw">BookWalker 台湾</option>
              <option value="bookwalker_jp">BookWalker 日本</option>
              <option value="bangumi">Bangumi</option>
            </select>
          </label>
          <label>
            检索标题
            <input
              value={searchTitle}
              onChange={e => setSearchTitle(e.target.value)}
              placeholder="例：D・N・A2"
              onKeyDown={e => e.key === "Enter" && doSearch()}
            />
          </label>
          <label>
            卷号（可选）
            <input value={searchVolume} onChange={e => setSearchVolume(e.target.value)} placeholder="例：1" />
          </label>
          <label>
            作者（可选）
            <input value={searchAuthor} onChange={e => setSearchAuthor(e.target.value)} placeholder="例：桂正和" />
          </label>
        </div>
        <button
          type="button"
          className="btn btn-primary"
          disabled={searching || !searchTitle.trim()}
          onClick={doSearch}
        >
          {searching ? <><span className="spinner" style={{ width: 14, height: 14 }} /> 搜索中…</> : "搜索"}
        </button>

        {searchError && <div className="error-msg" style={{ marginTop: 12 }}>{searchError}</div>}

        {candidates.length > 0 && (
          <div style={{ marginTop: 16 }}>
            <div style={{ fontSize: 12, color: "var(--text-muted)", marginBottom: 8 }}>
              找到 {candidates.length} 个候选 — 点击候选卡片以更新此记录的元数据
            </div>
            <div className="candidate-grid">
              {candidates.map((c, i) => (
                <CandidateCard
                  key={i}
                  candidate={c}
                  onSelect={() => applyCandidate(c)}
                  actionLabel={rescraping ? "处理中…" : "使用此元数据并重刮削"}
                />
              ))}
            </div>
          </div>
        )}
      </div>

      {/* File paths (collapsible) */}
      <details className="card" style={{ cursor: "default" }}>
        <summary style={{ cursor: "pointer", fontSize: 13, fontWeight: 600, color: "var(--text-secondary)", userSelect: "none" }}>
          文件路径 &amp; 内部信息
        </summary>
        <div style={{ marginTop: 12 }}>
          <div className="detail-fields">
            <MetaField label="原始路径" value={record.original_path} mono />
            <MetaField label="归档路径" value={record.archive_path} mono />
            <MetaField label="转换路径" value={record.converted_path} mono />
            <MetaField label="文件哈希" value={record.file_hash} mono />
            <MetaField label="重试次数" value={String(record.retry_count)} />
            <MetaField label="创建时间" value={record.created_at} />
          </div>
        </div>
      </details>

      {/* Toast */}
      {toast && (
        <div className={`toast ${toast.type}`}>{toast.message}</div>
      )}
    </div>
  );
}

function MetaField({ label, value, mono }: { label: string; value: string | undefined; mono?: boolean }) {
  if (!value) return null;
  return (
    <div className="detail-field">
      <label>{label}</label>
      <div
        className="value"
        style={{
          fontFamily: mono ? "monospace" : undefined,
          fontSize: mono ? 11 : undefined,
          wordBreak: "break-all",
          color: mono ? "var(--text-muted)" : undefined,
        }}
      >
        {value}
      </div>
    </div>
  );
}
