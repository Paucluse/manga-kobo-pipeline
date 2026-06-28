import React, { useEffect, useState } from "react";
import * as api from "../api";
import type { LlmRun, RescrapeResult, Settings } from "../types";

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
  const [prompt, setPrompt] = useState("");
  const [promptSaved, setPromptSaved] = useState(false);
  const [expandedRunId, setExpandedRunId] = useState<number | null>(null);

  // Batch rescrape
  const [batchIds, setBatchIds] = useState("");
  const [batchTitle, setBatchTitle] = useState("");
  const [batchDryRun, setBatchDryRun] = useState(false);
  const [batchRelocate, setBatchRelocate] = useState(true);
  const [batchIncludeUnfinished, setBatchIncludeUnfinished] = useState(false);
  const [batchAllRecords, setBatchAllRecords] = useState(false);
  const [batchResults, setBatchResults] = useState<RescrapeResult[]>([]);
  const [batchRunning, setBatchRunning] = useState(false);
  const [batchError, setBatchError] = useState("");

  async function load() {
    try {
      const [s, lr] = await Promise.all([api.getSettings(), api.getLlmRuns(50)]);
      setSettings(s);
      setPrompt(s.prompt || "");
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

  async function savePrompt() {
    await api.setPrompt(prompt);
    setPromptSaved(true);
    setTimeout(() => setPromptSaved(false), 2000);
    await load();
  }

  async function runBatch() {
    setBatchRunning(true);
    setBatchError("");
    setBatchResults([]);
    const ids = batchIds
      .split(/[,\s]+/)
      .map(Number)
      .filter(n => Number.isFinite(n) && n > 0);
    try {
      const res = await api.batchRescrape({
        ids: ids.length > 0 ? ids : undefined,
        title: batchTitle || undefined,
        dry_run: batchDryRun,
        relocate: batchRelocate,
        all_records: batchAllRecords,
        include_unfinished: batchIncludeUnfinished,
      });
      setBatchResults(res.items);
    } catch (e) {
      setBatchError((e as Error).message);
    } finally {
      setBatchRunning(false);
    }
  }

  if (loading || !settings) return <div className="empty-state"><div className="spinner" /></div>;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>运行控制</h1>
          <div className="subtitle">管线模式、LLM 设置、批量操作</div>
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

      {/* Batch rescrape */}
      <div className="card">
        <div className="card-title" style={{ marginBottom: 12 }}>批量重刮削</div>
        <div className="form-grid" style={{ marginBottom: 12 }}>
          <label>
            记录 ID（逗号/空格分隔）
            <input value={batchIds} onChange={e => setBatchIds(e.target.value)} placeholder="1 2 3 或留空" />
          </label>
          <label>
            标题筛选
            <input value={batchTitle} onChange={e => setBatchTitle(e.target.value)} placeholder="按标题模糊筛选" />
          </label>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 12, marginBottom: 14 }}>
          <CheckOption
            checked={batchDryRun}
            onChange={setBatchDryRun}
            label="Dry-run（仅预览，不写入）"
          />
          <CheckOption
            checked={batchRelocate}
            onChange={setBatchRelocate}
            label="同步搬移文件"
          />
          <CheckOption
            checked={batchAllRecords}
            onChange={setBatchAllRecords}
            label="处理所有记录"
          />
          <CheckOption
            checked={batchIncludeUnfinished}
            onChange={setBatchIncludeUnfinished}
            label="包含未完成记录"
          />
        </div>
        <button type="button" className="btn btn-primary" disabled={batchRunning} onClick={runBatch}>
          {batchRunning ? <><span className="spinner" style={{ width: 14, height: 14 }} /> 执行中…</> : "执行批量重刮削"}
        </button>
        {batchError && <div className="error-msg" style={{ marginTop: 12 }}>{batchError}</div>}
        {batchResults.length > 0 && (
          <div className="table-wrap" style={{ marginTop: 12 }}>
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>文件名</th>
                  <th>结果</th>
                  <th>旧系列</th>
                  <th>新系列</th>
                  <th>旧卷</th>
                  <th>新卷</th>
                  <th>置信度</th>
                  <th>说明</th>
                </tr>
              </thead>
              <tbody>
                {batchResults.map(r => (
                  <tr key={r.record_id}>
                    <td style={{ fontFamily: "monospace" }}>#{r.record_id}</td>
                    <td style={{ maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.file_name}</td>
                    <td>
                      <span style={{
                        color: r.status === "updated" ? "var(--status-done)"
                          : r.status === "no_match" ? "var(--status-failed)"
                          : "var(--status-review)"
                      }}>
                        {r.status}
                      </span>
                    </td>
                    <td style={{ color: "var(--text-muted)" }}>{r.old_series}</td>
                    <td style={{ color: "var(--status-done)" }}>{r.new_series}</td>
                    <td style={{ color: "var(--text-muted)" }}>{r.old_volume}</td>
                    <td style={{ color: "var(--status-done)" }}>{r.new_volume}</td>
                    <td>{r.confidence > 0 ? `${(r.confidence * 100).toFixed(0)}%` : "—"}</td>
                    <td style={{ color: "var(--text-muted)", fontSize: 11 }}>{r.message}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* LLM Prompt */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">LLM 全局 Prompt</span>
          {promptSaved && <span style={{ color: "var(--status-done)", fontSize: 13 }}>✓ 已保存</span>}
        </div>
        <label>
          <textarea
            value={prompt}
            onChange={e => setPrompt(e.target.value)}
            style={{ minHeight: 140, fontFamily: "monospace", fontSize: 12 }}
          />
        </label>
        <button type="button" className="btn btn-primary" style={{ marginTop: 8 }} onClick={savePrompt}>
          保存 Prompt
        </button>
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

function CheckOption({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label style={{ flexDirection: "row", alignItems: "center", gap: 6, cursor: "pointer", fontSize: 13, fontWeight: 400, color: "var(--text-secondary)" }}>
      <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} style={{ width: 16, height: 16 }} />
      {label}
    </label>
  );
}

function fmtDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch { return iso; }
}
