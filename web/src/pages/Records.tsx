import React, { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../api";
import type { RecordListItem } from "../types";
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

interface RecordsProps {
  onViewDetail: (id: number) => void;
  initialStatus?: string;
}

export function Records({ onViewDetail, initialStatus = "" }: RecordsProps) {
  const [items, setItems] = useState<RecordListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState(initialStatus);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
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

  useEffect(() => { load(1, statusFilter, search); }, [statusFilter]);

  // Debounce search
  useEffect(() => {
    const t = setTimeout(() => { setPage(1); load(1, statusFilter, search); }, 350);
    return () => clearTimeout(t);
  }, [search]);

  useEffect(() => { load(page, statusFilter, search); }, [page]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

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
            ref={inputRef}
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
                {items.map(r => (
                  <tr
                    key={r.id}
                    className="clickable"
                    onClick={() => onViewDetail(r.id)}
                  >
                    <td style={{ color: "var(--text-muted)", fontFamily: "monospace", fontSize: 12 }}>#{r.id}</td>
                    <td className="cell-cover">
                      {r.cover_url ? (
                        <img src={r.cover_url} alt="" loading="lazy" />
                      ) : (
                        <div style={{ width: 36, height: 50, background: "var(--bg-elevated)", borderRadius: 4, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 18 }}>📖</div>
                      )}
                    </td>
                    <td style={{ maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      <span className="cell-title">{r.file_name}</span>
                      {r.collection_title && <div className="cell-muted">{r.collection_title}</div>}
                    </td>
                    <td style={{ maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.series || r.title || "—"}</td>
                    <td>{r.volume || "—"}</td>
                    <td className="cell-muted">{r.author || "—"}</td>
                    <td>
                      {r.confidence > 0 && (
                        <ConfBar value={r.confidence} />
                      )}
                    </td>
                    <td><StatusBadge status={r.current_status} /></td>
                    <td className="cell-muted" style={{ whiteSpace: "nowrap" }}>{fmtDate(r.updated_at)}</td>
                  </tr>
                ))}
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
