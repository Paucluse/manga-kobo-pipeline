import React, { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../api";
import type { RecordListItem, RescrapeResult } from "../types";
import { StatusBadge } from "../components/StatusBadge";

const ALL_STATUSES = [
  "", "failed", "needs_review", "awaiting_metadata_approval",
  "processing", "done", "discovered", "waiting_stable", "normalized",
  "metadata_parsed", "archived", "converted", "importing", "imported",
];
const STATUS_LABELS: Record<string, string> = {
  "": "全部",
  failed: "失败",
  needs_review: "需审核",
  awaiting_metadata_approval: "待确认",
  processing: "处理中",
  done: "完成",
  discovered: "已发现",
  waiting_stable: "等待稳定",
  normalized: "已规范化",
  metadata_parsed: "元数据已解析",
  archived: "已归档",
  converted: "已转换",
  importing: "导入中",
  imported: "已导入",
};

const PROVIDERS = [
  { value: "bookwalker_tw", label: "BookWalker 台湾" },
  { value: "bookwalker_jp", label: "BookWalker 日本" },
  { value: "bangumi",       label: "Bangumi" },
];

interface RecordsProps {
  onViewDetail: (id: number) => void;
  initialStatus?: string;
}

// ─── Batch Rescrape Modal ────────────────────────────────────────────────────
interface BatchRescrapeModalProps {
  ids: number[];
  onClose: () => void;
  onDone: (results: RescrapeResult[]) => void;
}

function BatchRescrapeModal({ ids, onClose, onDone }: BatchRescrapeModalProps) {
  const [provider, setProvider]   = useState("bookwalker_tw");
  const [title, setTitle]         = useState("");
  const [volume, setVolume]       = useState("");
  const [author, setAuthor]       = useState("");
  const [relocate, setRelocate]   = useState(true);
  const [running, setRunning]     = useState(false);
  const [error, setError]         = useState("");
  const [results, setResults]     = useState<RescrapeResult[] | null>(null);

  async function run() {
    setRunning(true);
    setError("");
    try {
      const res = await api.batchForceRescrape({ ids, provider, title, volume, author, relocate });
      setResults(res.results);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  }

  const updated  = results?.filter(r => r.status === "updated").length ?? 0;
  const failed   = results?.filter(r => r.status !== "updated").length ?? 0;

  return (
    <div style={{
      position: "fixed", inset: 0, zIndex: 1000,
      background: "rgba(0,0,0,0.6)", backdropFilter: "blur(4px)",
      display: "flex", alignItems: "center", justifyContent: "center",
    }}>
      <div className="card" style={{ width: 600, maxWidth: "95vw", maxHeight: "88vh", overflow: "auto", gap: 0 }}>
        {/* Header */}
        <div className="card-header" style={{ marginBottom: 16 }}>
          <span className="card-title">批量重刮削 — 已选 {ids.length} 条记录</span>
          {!results && (
            <button type="button" className="btn-icon" onClick={onClose}>✕</button>
          )}
        </div>

        {!results ? (
          <>
            <div className="form-grid" style={{ marginBottom: 14 }}>
              <label>
                刮削数据源
                <select value={provider} onChange={e => setProvider(e.target.value)}>
                  {PROVIDERS.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
                </select>
              </label>
              <label>
                统一检索标题
                <input
                  value={title}
                  onChange={e => setTitle(e.target.value)}
                  placeholder="留空则各用本身系列名"
                />
              </label>
              <label>
                统一卷号
                <input value={volume} onChange={e => setVolume(e.target.value)} placeholder="留空则各用原卷号" />
              </label>
              <label>
                作者（可选）
                <input value={author} onChange={e => setAuthor(e.target.value)} placeholder="留空则各用原作者" />
              </label>
            </div>

            <label style={{ flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 14, fontSize: 13, fontWeight: 400, color: "var(--text-secondary)" }}>
              <input type="checkbox" checked={relocate} onChange={e => setRelocate(e.target.checked)} style={{ width: 16, height: 16 }} />
              刮削后同步移动文件到正确目录（relocate）
            </label>

            <div style={{
              padding: "10px 14px",
              background: "rgba(124,106,247,0.08)",
              border: "1px solid var(--accent-dim)",
              borderRadius: "var(--r-sm)",
              fontSize: 12,
              color: "var(--text-muted)",
              marginBottom: 16,
              lineHeight: 1.6,
            }}>
              ⚡ 此操作绕过 LLM 归一化和置信度门槛，直接使用指定数据源刮削。
              建议开启"同步移动文件"以确保文件路径与元数据匹配。
            </div>

            {error && <div className="error-msg" style={{ marginBottom: 12 }}>{error}</div>}

            <div style={{ display: "flex", gap: 10 }}>
              <button type="button" className="btn btn-primary" disabled={running} onClick={run} style={{ flex: 1, justifyContent: "center" }}>
                {running ? <><span className="spinner" style={{ width: 14, height: 14 }} /> 刮削中，请稍候…</> : `开始批量刮削 (${ids.length} 条)`}
              </button>
              <button type="button" className="btn btn-secondary" disabled={running} onClick={onClose}>取消</button>
            </div>
          </>
        ) : (
          /* Results */
          <>
            <div style={{ display: "flex", gap: 12, marginBottom: 16 }}>
              <div className="metric-card" style={{ flex: 1 }}>
                <div className="metric-label">成功</div>
                <div className="metric-value" style={{ fontSize: 28, color: "var(--status-done)" }}>{updated}</div>
              </div>
              <div className="metric-card" style={{ flex: 1 }}>
                <div className="metric-label">未命中/失败</div>
                <div className="metric-value" style={{ fontSize: 28, color: failed > 0 ? "var(--status-failed)" : "var(--text-muted)" }}>{failed}</div>
              </div>
            </div>

            <div className="table-wrap" style={{ marginBottom: 16 }}>
              <table>
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>结果</th>
                    <th>原系列 → 新系列</th>
                    <th>卷号</th>
                    <th>置信度</th>
                    <th>说明</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map(r => (
                    <tr key={r.record_id}>
                      <td style={{ fontFamily: "monospace" }}>#{r.record_id}</td>
                      <td>
                        <span style={{ color: r.status === "updated" ? "var(--status-done)" : "var(--status-failed)", fontWeight: 600, fontSize: 11 }}>
                          {r.status === "updated" ? "✓ 成功" : r.status}
                        </span>
                      </td>
                      <td style={{ fontSize: 12 }}>
                        {r.old_series && r.new_series && r.old_series !== r.new_series ? (
                          <span>
                            <span style={{ color: "var(--text-muted)" }}>{r.old_series}</span>
                            {" → "}
                            <span style={{ color: "var(--status-done)" }}>{r.new_series}</span>
                          </span>
                        ) : (
                          <span style={{ color: "var(--text-muted)" }}>{r.new_series || r.old_series || "—"}</span>
                        )}
                      </td>
                      <td style={{ color: "var(--text-muted)", fontSize: 12 }}>
                        {r.old_volume !== r.new_volume && r.new_volume
                          ? <>{r.old_volume} → <span style={{ color: "var(--status-done)" }}>{r.new_volume}</span></>
                          : r.new_volume || r.old_volume || "—"
                        }
                      </td>
                      <td style={{ fontSize: 12 }}>{r.confidence > 0 ? `${(r.confidence * 100).toFixed(0)}%` : "—"}</td>
                      <td style={{ color: "var(--text-muted)", fontSize: 11, maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.message || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <button type="button" className="btn btn-primary" style={{ justifyContent: "center" }} onClick={() => onDone(results)}>
              完成并刷新记录
            </button>
          </>
        )}
      </div>
    </div>
  );
}

// ─── Main Records Page ───────────────────────────────────────────────────────
export function Records({ onViewDetail, initialStatus = "" }: RecordsProps) {
  const [items, setItems]             = useState<RecordListItem[]>([]);
  const [total, setTotal]             = useState(0);
  const [page, setPage]               = useState(1);
  const [search, setSearch]           = useState("");
  const [statusFilter, setStatusFilter] = useState(initialStatus);
  const [loading, setLoading]         = useState(false);
  const [error, setError]             = useState("");

  // Selection state
  const [selected, setSelected]       = useState<Set<number>>(new Set());
  const [showBatchModal, setShowBatchModal] = useState(false);
  const [batchResetRunning, setBatchResetRunning] = useState(false);
  const [toastMsg, setToastMsg]       = useState("");

  const PAGE_SIZE = 50;

  const load = useCallback(async (pg = page, sf = statusFilter, sq = search) => {
    setLoading(true);
    setError("");
    try {
      const res = await api.getRecords({ page: pg, size: PAGE_SIZE, status_filter: sf, search: sq });
      setItems(res.items);
      setTotal(res.total);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [page, statusFilter, search]);

  useEffect(() => { load(1, statusFilter, search); setSelected(new Set()); }, [statusFilter]);

  useEffect(() => {
    const t = setTimeout(() => { setPage(1); load(1, statusFilter, search); setSelected(new Set()); }, 350);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => { load(page, statusFilter, search); }, [page]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  function showToast(msg: string) {
    setToastMsg(msg);
    setTimeout(() => setToastMsg(""), 3000);
  }

  // ── Selection helpers ────
  const allPageIds   = items.map(r => r.id);
  const allSelected  = allPageIds.length > 0 && allPageIds.every(id => selected.has(id));
  const someSelected = allPageIds.some(id => selected.has(id));

  function toggleRow(id: number) {
    setSelected(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  function toggleAll() {
    if (allSelected) {
      setSelected(prev => {
        const next = new Set(prev);
        allPageIds.forEach(id => next.delete(id));
        return next;
      });
    } else {
      setSelected(prev => new Set([...prev, ...allPageIds]));
    }
  }

  function clearSelection() { setSelected(new Set()); }

  // ── Batch reset ──────────
  async function doBatchReset() {
    const ids = [...selected];
    if (!ids.length) return;
    if (!window.confirm(`确定将选中的 ${ids.length} 条记录重置为初始状态？管线将从头重新处理。`)) return;
    setBatchResetRunning(true);
    try {
      const res = await api.batchReset(ids);
      const ok = res.results.filter(r => r.ok).length;
      showToast(`已重置 ${ok}/${ids.length} 条记录`);
      clearSelection();
      await load(page, statusFilter, search);
    } catch (e) {
      showToast(`重置失败：${(e as Error).message}`);
    } finally {
      setBatchResetRunning(false);
    }
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>所有记录</h1>
          <div className="subtitle">共 {total} 条</div>
        </div>
      </div>

      {error && <div className="error-msg">{error}</div>}

      {/* Search + filter */}
      <div className="card" style={{ marginBottom: 0 }}>
        <div className="search-bar" style={{ marginBottom: 12 }}>
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="🔍 搜索文件名、系列名、作者…"
          />
          {search && (
            <button type="button" className="btn btn-secondary btn-sm" onClick={() => setSearch("")}>✕</button>
          )}
        </div>
        <div className="chip-row">
          {ALL_STATUSES.map(s => (
            <button
              key={s}
              type="button"
              className={`chip${statusFilter === s ? " active" : ""}`}
              onClick={() => { setPage(1); setStatusFilter(s); }}
            >
              {STATUS_LABELS[s]}
            </button>
          ))}
        </div>
      </div>

      {/* Batch action bar */}
      {selected.size > 0 && (
        <div style={{
          position: "sticky", top: 0, zIndex: 50,
          background: "var(--bg-elevated)",
          border: "1px solid var(--accent)",
          borderRadius: "var(--r-md)",
          padding: "10px 16px",
          display: "flex", alignItems: "center", gap: 12,
          boxShadow: "var(--shadow-glow)",
          animation: "slide-in 0.2s ease",
        }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: "var(--accent-light)", flex: 1 }}>
            已选 {selected.size} 条
          </span>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={() => setShowBatchModal(true)}
          >
            🔍 批量重刮削
          </button>
          <button
            type="button"
            className="btn btn-danger btn-sm"
            disabled={batchResetRunning}
            onClick={doBatchReset}
          >
            {batchResetRunning
              ? <><span className="spinner" style={{ width: 12, height: 12 }} /> 处理中</>
              : "🔄 批量清除管线"}
          </button>
          <button type="button" className="btn btn-secondary btn-sm" onClick={clearSelection}>
            取消选择
          </button>
        </div>
      )}

      {/* Table */}
      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        {loading ? (
          <div className="empty-state"><div className="spinner" /></div>
        ) : items.length === 0 ? (
          <div className="empty-state">
            <div className="icon">📭</div>
            <p>没有符合条件的记录</p>
          </div>
        ) : (
          <div className="table-wrap" style={{ border: "none", borderRadius: 0 }}>
            <table>
              <thead>
                <tr>
                  <th style={{ width: 36, paddingRight: 0 }}>
                    <input
                      type="checkbox"
                      style={{ width: 15, height: 15, cursor: "pointer" }}
                      checked={allSelected}
                      ref={el => { if (el) el.indeterminate = someSelected && !allSelected; }}
                      onChange={toggleAll}
                    />
                  </th>
                  <th>ID</th>
                  <th>封面</th>
                  <th>文件名</th>
                  <th>系列</th>
                  <th>卷号</th>
                  <th>作者</th>
                  <th>置信度</th>
                  <th>状态</th>
                  <th>更新</th>
                </tr>
              </thead>
              <tbody>
                {items.map(r => {
                  const isSelected = selected.has(r.id);
                  return (
                    <tr
                      key={r.id}
                      style={{ background: isSelected ? "var(--accent-dim)" : undefined }}
                    >
                      <td
                        style={{ paddingRight: 0, cursor: "pointer" }}
                        onClick={e => { e.stopPropagation(); toggleRow(r.id); }}
                      >
                        <input
                          type="checkbox"
                          style={{ width: 15, height: 15, cursor: "pointer" }}
                          checked={isSelected}
                          onChange={() => toggleRow(r.id)}
                          onClick={e => e.stopPropagation()}
                        />
                      </td>
                      <td
                        style={{ color: "var(--text-muted)", fontFamily: "monospace", fontSize: 12, cursor: "pointer" }}
                        onClick={() => onViewDetail(r.id)}
                      >
                        #{r.id}
                      </td>
                      <td className="cell-cover" style={{ cursor: "pointer" }} onClick={() => onViewDetail(r.id)}>
                        {r.cover_url ? (
                          <img src={r.cover_url} alt="" loading="lazy" />
                        ) : (
                          <div style={{ width: 36, height: 50, background: "var(--bg-elevated)", borderRadius: 4, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 18 }}>📖</div>
                        )}
                      </td>
                      <td style={{ maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", cursor: "pointer" }} onClick={() => onViewDetail(r.id)}>
                        <span className="cell-title">{r.file_name}</span>
                        {r.collection_title && <div className="cell-muted">{r.collection_title}</div>}
                      </td>
                      <td style={{ maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", cursor: "pointer" }} onClick={() => onViewDetail(r.id)}>
                        {r.series || r.title || "—"}
                      </td>
                      <td style={{ cursor: "pointer" }} onClick={() => onViewDetail(r.id)}>{r.volume || "—"}</td>
                      <td className="cell-muted" style={{ cursor: "pointer" }} onClick={() => onViewDetail(r.id)}>{r.author || "—"}</td>
                      <td style={{ cursor: "pointer" }} onClick={() => onViewDetail(r.id)}>
                        {r.confidence > 0 && <ConfBar value={r.confidence} />}
                      </td>
                      <td style={{ cursor: "pointer" }} onClick={() => onViewDetail(r.id)}>
                        <StatusBadge status={r.current_status} />
                      </td>
                      <td className="cell-muted" style={{ whiteSpace: "nowrap", cursor: "pointer" }} onClick={() => onViewDetail(r.id)}>
                        {fmtDate(r.updated_at)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="pagination">
          <button type="button" className="btn btn-secondary btn-sm" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>← 上一页</button>
          <span>{page} / {totalPages}</span>
          <button type="button" className="btn btn-secondary btn-sm" disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}>下一页 →</button>
        </div>
      )}

      {/* Batch rescrape modal */}
      {showBatchModal && (
        <BatchRescrapeModal
          ids={[...selected]}
          onClose={() => setShowBatchModal(false)}
          onDone={async () => {
            setShowBatchModal(false);
            clearSelection();
            await load(page, statusFilter, search);
          }}
        />
      )}

      {/* Toast */}
      {toastMsg && <div className="toast success">{toastMsg}</div>}
    </div>
  );
}

function ConfBar({ value }: { value: number }) {
  const pct = Math.min(100, Math.round(value * 100));
  const color = pct >= 70 ? "var(--status-done)" : pct >= 40 ? "var(--status-review)" : "var(--status-failed)";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <div style={{ width: 40, height: 4, background: "var(--border)", borderRadius: 2, overflow: "hidden" }}>
        <div style={{ width: `${pct}%`, height: "100%", background: color, borderRadius: 2 }} />
      </div>
      <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{pct}%</span>
    </div>
  );
}

function fmtDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch { return iso; }
}
