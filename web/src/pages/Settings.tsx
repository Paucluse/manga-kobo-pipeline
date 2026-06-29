import React, { useEffect, useState } from "react";
import * as api from "../api";
import type { LlmRun, Settings } from "../types";

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

const modeDescriptions: Record<string, string> = {
  auto: "所有文件自动处理，无需人工干预",
  manual_book: "每本书导入前需要人工确认元数据",
  manual_series: "同一系列只需第一次确认",
  paused: "暂停所有自动处理",
};

export function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [llmRuns, setLlmRuns] = useState<LlmRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedRunId, setExpandedRunId] = useState<number | null>(null);

  async function load() {
    try {
      const [s, lr] = await Promise.all([api.getSettings(), api.getLlmRuns(50)]);
      setSettings(s);
      setLlmRuns(lr.items);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  async function setMode(mode: string) {
    await api.setMode(mode);
    await load();
  }

  if (loading || !settings) return <div className="empty-state"><div className="spinner" /></div>;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>运行控制</h1>
          <div className="subtitle">管线运行模式与 LLM 调用日志</div>
        </div>
      </div>

      {/* Mode selector */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">运行模式</span>
          <span style={{ fontSize: 22 }}>{modeIcons[settings.mode]}</span>
        </div>
        <div className="mode-grid">
          {settings.valid_modes.map(m => (
            <button
              key={m}
              type="button"
              className={`mode-btn${settings.mode === m ? " active" : ""}`}
              onClick={() => setMode(m)}
            >
              <span className="mode-icon">{modeIcons[m] ?? "❓"}</span>
              {modeLabels[m] ?? m}
              <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4, whiteSpace: "normal" }}>
                {modeDescriptions[m] ?? ""}
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* LLM Runs */}
      <div className="card" style={{ padding: 0 }}>
        <div style={{ padding: "16px 16px 0" }}>
          <span className="card-title">LLM 调用记录（最近50条）</span>
        </div>
        <div className="table-wrap" style={{ border: "none", borderRadius: 0 }}>
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>记录</th>
                <th>类型</th>
                <th>耗时</th>
                <th>错误</th>
                <th>时间</th>
              </tr>
            </thead>
            <tbody>
              {llmRuns.map(run => (
                <React.Fragment key={run.id}>
                  <tr
                    className="clickable"
                    onClick={() => setExpandedRunId(expandedRunId === run.id ? null : run.id)}
                  >
                    <td style={{ fontFamily: "monospace" }}>{run.id}</td>
                    <td>{run.record_id != null ? `#${run.record_id}` : "—"}</td>
                    <td><span style={{ fontSize: 11, color: "var(--accent-light)" }}>{run.source_name}</span></td>
                    <td style={{ color: "var(--text-muted)" }}>{run.elapsed_ms ? `${run.elapsed_ms}ms` : "—"}</td>
                    <td style={{ color: run.error ? "var(--status-failed)" : "var(--text-muted)", maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {run.error || "—"}
                    </td>
                    <td style={{ color: "var(--text-muted)", whiteSpace: "nowrap" }}>{fmtDate(run.created_at)}</td>
                  </tr>
                  {expandedRunId === run.id && (
                    <tr>
                      <td colSpan={6} style={{ background: "var(--bg-surface)", padding: 16 }}>
                        {run.parsed_json && (
                          <>
                            <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-muted)", marginBottom: 4, textTransform: "uppercase" }}>解析 JSON</div>
                            <pre style={{ fontSize: 11, lineHeight: 1.5, color: "var(--text-secondary)", overflow: "auto", maxHeight: 200, margin: 0 }}>{run.parsed_json}</pre>
                          </>
                        )}
                        {run.error && (
                          <>
                            <div style={{ fontSize: 11, fontWeight: 600, color: "var(--status-failed)", marginTop: 8, marginBottom: 4 }}>错误</div>
                            <pre style={{ fontSize: 11, color: "var(--status-failed)", margin: 0 }}>{run.error}</pre>
                          </>
                        )}
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))}
              {llmRuns.length === 0 && (
                <tr>
                  <td colSpan={6} style={{ textAlign: "center", color: "var(--text-muted)", padding: 24 }}>暂无 LLM 调用记录</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function fmtDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch { return iso; }
}
