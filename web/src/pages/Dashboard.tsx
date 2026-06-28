import React, { useEffect, useState } from "react";
import * as api from "../api";
import type { StatusData } from "../types";
import { StatusBadge } from "../components/StatusBadge";

const modeIcons: Record<string, string> = {
  auto: "🤖",
  manual_book: "📖",
  manual_series: "📚",
  paused: "⏸️",
};

const modeLabels: Record<string, string> = {
  auto: "自动处理",
  manual_book: "每本确认",
  manual_series: "每系列确认",
  paused: "暂停",
};

interface DashboardProps {
  onNavigateToRecord?: (id: number) => void;
  onNavigateToApprovals?: () => void;
}

export function Dashboard({ onNavigateToRecord, onNavigateToApprovals }: DashboardProps) {
  const [data, setData] = useState<StatusData | null>(null);
  const [error, setError] = useState("");

  async function load() {
    try {
      const status = await api.getStatus();
      setData(status);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  useEffect(() => {
    load();
    const t = window.setInterval(load, 20000);
    return () => clearInterval(t);
  }, []);

  if (!data) return <div className="empty-state"><div className="spinner" /></div>;

  const statusOrder = ["failed", "needs_review", "awaiting_metadata_approval", "processing", "importing", "imported", "done", "discovered", "waiting_stable", "normalized", "metadata_parsed", "archived", "converted"];
  const sortedCounts = Object.entries(data.counts).sort(
    ([a], [b]) => statusOrder.indexOf(a) - statusOrder.indexOf(b)
  );

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>概览</h1>
          <div className="subtitle">实时管线状态</div>
        </div>
        <button type="button" className="btn btn-secondary btn-sm" onClick={load}>⟳ 刷新</button>
      </div>

      {error && <div className="error-msg">{error}</div>}

      {/* Mode indicator */}
      <div className="card" style={{ marginBottom: 0 }}>
        <div className="card-header">
          <span className="card-title">运行模式</span>
          <span style={{ fontSize: 22 }}>{modeIcons[data.mode] ?? "❓"}</span>
        </div>
        <div style={{ fontSize: 20, fontWeight: 700, color: "var(--accent-light)" }}>
          {modeLabels[data.mode] ?? data.mode}
        </div>
        {data.pending_approvals > 0 && (
          <div
            style={{ marginTop: 12, cursor: "pointer", color: "var(--status-review)", fontSize: 13 }}
            onClick={onNavigateToApprovals}
          >
            ⚠️ 有 {data.pending_approvals} 条记录等待人工确认 →
          </div>
        )}
      </div>

      {/* Status counts */}
      <div>
        <h2 style={{ fontSize: 14, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 12, textTransform: "uppercase", letterSpacing: "0.06em" }}>状态分布</h2>
        <div className="metric-grid">
          <div className="metric-card" style={{ gridColumn: "1 / -1", background: "var(--accent-dim)", borderColor: "var(--accent)" }}>
            <div className="metric-label">总任务数</div>
            <div className="metric-value">{data.total}</div>
          </div>
          {sortedCounts.map(([st, count]) => (
            <div key={st} className="metric-card">
              <div className="metric-label">{st}</div>
              <div className="metric-value" style={{ fontSize: 22 }}>{count}</div>
              <StatusBadge status={st} />
            </div>
          ))}
        </div>
      </div>

      {/* Recent records */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">最近活动</span>
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>最近 20 条</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>文件名</th>
                <th>系列</th>
                <th>卷号</th>
                <th>状态</th>
                <th>更新时间</th>
              </tr>
            </thead>
            <tbody>
              {data.recent_records.map(r => (
                <tr
                  key={r.id}
                  className="clickable"
                  onClick={() => onNavigateToRecord?.(r.id)}
                >
                  <td style={{ color: "var(--text-muted)", fontFamily: "monospace" }}>#{r.id}</td>
                  <td className="cell-title" style={{ maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.file_name}</td>
                  <td>{r.series || r.title || "—"}</td>
                  <td>{r.volume || "—"}</td>
                  <td><StatusBadge status={r.current_status} /></td>
                  <td className="cell-muted">{formatDate(r.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}
