import React, { useEffect, useState, useCallback } from "react";
import * as api from "../api";
import type { LlmRun, PipelineConfigResponse, Settings } from "../types";

// ─── Constants ───────────────────────────────────────────────────────────────

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

const KCC_PROFILES = [
  { value: "KoS",  label: "Kobo Sage" },
  { value: "KF",   label: "Kobo Forma" },
  { value: "KC",   label: "Kobo Clara HD" },
  { value: "KL",   label: "Kobo Libra H2O" },
  { value: "KA",   label: "Kobo Aura" },
  { value: "KPW5", label: "Kindle Paperwhite 5" },
  { value: "KV",   label: "Kindle Voyage" },
  { value: "K11",  label: "Kindle 11" },
  { value: "KO",   label: "Kindle Oasis" },
  { value: "KCC",  label: "通用（无设备优化）" },
];

const KCC_FORMATS = [
  { value: "KEPUB", label: "KEPUB (Kobo)" },
  { value: "EPUB",  label: "EPUB" },
  { value: "CBZ",   label: "CBZ" },
  { value: "MOBI",  label: "MOBI (Kindle)" },
];

// ─── Helpers ─────────────────────────────────────────────────────────────────

function ToggleSwitch({
  checked, onChange, label, description, overridden, onReset,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  description?: string;
  overridden?: boolean;
  onReset?: () => void;
}) {
  return (
    <div className="setting-row">
      <div style={{ flex: 1 }}>
        <div className="setting-label">
          {label}
          {overridden && (
            <span className="override-badge" title="已覆盖 config.yaml 默认值" onClick={onReset}>
              已覆盖 ✕
            </span>
          )}
        </div>
        {description && <div className="setting-desc">{description}</div>}
      </div>
      <label className="toggle">
        <input type="checkbox" checked={checked} onChange={e => onChange(e.target.checked)} />
        <span className="toggle-slider" />
      </label>
    </div>
  );
}

function NumberInput({
  value, onChange, label, description, min, max, step, overridden, onReset,
}: {
  value: number;
  onChange: (v: number) => void;
  label: string;
  description?: string;
  min?: number;
  max?: number;
  step?: number;
  overridden?: boolean;
  onReset?: () => void;
}) {
  return (
    <div className="setting-row">
      <div style={{ flex: 1 }}>
        <div className="setting-label">
          {label}
          {overridden && (
            <span className="override-badge" title="已覆盖 config.yaml 默认值" onClick={onReset}>
              已覆盖 ✕
            </span>
          )}
        </div>
        {description && <div className="setting-desc">{description}</div>}
      </div>
      <input
        type="number"
        value={value}
        onChange={e => onChange(Number(e.target.value))}
        min={min}
        max={max}
        step={step ?? 1}
        style={{ width: 80, textAlign: "center" }}
      />
    </div>
  );
}

function SelectInput({
  value, onChange, options, label, description, overridden, onReset,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  label: string;
  description?: string;
  overridden?: boolean;
  onReset?: () => void;
}) {
  return (
    <div className="setting-row">
      <div style={{ flex: 1 }}>
        <div className="setting-label">
          {label}
          {overridden && (
            <span className="override-badge" title="已覆盖 config.yaml 默认值" onClick={onReset}>
              已覆盖 ✕
            </span>
          )}
        </div>
        {description && <div className="setting-desc">{description}</div>}
      </div>
      <select value={value} onChange={e => onChange(e.target.value)} style={{ width: 180 }}>
        {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </div>
  );
}

function SliderInput({
  value, onChange, label, description, min, max, step, overridden, onReset,
}: {
  value: number;
  onChange: (v: number) => void;
  label: string;
  description?: string;
  min: number;
  max: number;
  step: number;
  overridden?: boolean;
  onReset?: () => void;
}) {
  return (
    <div className="setting-row">
      <div style={{ flex: 1 }}>
        <div className="setting-label">
          {label}
          {overridden && (
            <span className="override-badge" title="已覆盖 config.yaml 默认值" onClick={onReset}>
              已覆盖 ✕
            </span>
          )}
        </div>
        {description && <div className="setting-desc">{description}</div>}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 160 }}>
        <input
          type="range"
          value={value}
          onChange={e => onChange(Number(e.target.value))}
          min={min}
          max={max}
          step={step}
          style={{ flex: 1 }}
        />
        <span style={{ fontSize: 12, color: "var(--text-muted)", minWidth: 36, textAlign: "right" }}>
          {value <= 1 ? `${Math.round(value * 100)}%` : value}
        </span>
      </div>
    </div>
  );
}

// ─── Main Page ───────────────────────────────────────────────────────────────

export function SettingsPage() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [pipelineCfg, setPipelineCfg] = useState<PipelineConfigResponse | null>(null);
  const [llmRuns, setLlmRuns] = useState<LlmRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedRunId, setExpandedRunId] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState("");

  const showToast = useCallback((msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(""), 3000);
  }, []);

  async function load() {
    try {
      const [s, cfg, lr] = await Promise.all([
        api.getSettings(),
        api.getPipelineConfig(),
        api.getLlmRuns(50),
      ]);
      setSettings(s);
      setPipelineCfg(cfg);
      setLlmRuns(lr.items);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { load(); }, []);

  // ── Save a single override ──────────
  async function saveOverride(key: string, value: unknown) {
    setSaving(true);
    try {
      await api.patchPipelineConfig({ [key]: value });
      showToast(`已保存：${key}`);
      await load();
    } catch (e) {
      showToast(`保存失败：${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  }

  // ── Reset an override to config.yaml default ──────────
  async function resetOverride(key: string) {
    try {
      await api.resetPipelineConfig(key);
      showToast(`已恢复默认：${key}`);
      await load();
    } catch {
      // key might not have an override
    }
  }

  async function setMode(mode: string) {
    await api.setMode(mode);
    await load();
  }

  const isOverridden = (key: string) => pipelineCfg?._overrides?.[key] !== undefined;

  if (loading || !settings || !pipelineCfg)
    return <div className="empty-state"><div className="spinner" /></div>;

  const { kobo, processing, metadata, pdf } = pipelineCfg;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>系统设置</h1>
          <div className="subtitle">运行模式 · KCC 转换 · 管线处理 · 刮削源 · LLM 日志</div>
        </div>
      </div>

      {/* ━━━ Card 1: Mode ━━━ */}
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

      {/* ━━━ Card 2: KCC Settings ━━━ */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">📦 KCC 转换设置</span>
        </div>

        <ToggleSwitch
          checked={!kobo.skip_kcc}
          onChange={v => saveOverride("kobo.skip_kcc", !v)}
          label="启用 KCC 转换"
          description="关闭后将跳过 KCC，CBZ 归档文件直接入库 Komga（不生成 KEPUB）"
          overridden={isOverridden("kobo.skip_kcc")}
          onReset={() => resetOverride("kobo.skip_kcc")}
        />

        {!kobo.skip_kcc && (
          <>
            <SelectInput
              value={kobo.profile}
              onChange={v => saveOverride("kobo.profile", v)}
              options={KCC_PROFILES}
              label="目标设备"
              description="KCC 使用此 Profile 优化输出尺寸和分辨率"
              overridden={isOverridden("kobo.profile")}
              onReset={() => resetOverride("kobo.profile")}
            />
            <SelectInput
              value={kobo.format}
              onChange={v => saveOverride("kobo.format", v)}
              options={KCC_FORMATS}
              label="输出格式"
              description="Kobo 设备推荐 KEPUB，Kindle 设备推荐 MOBI"
              overridden={isOverridden("kobo.format")}
              onReset={() => resetOverride("kobo.format")}
            />
            <ToggleSwitch
              checked={kobo.manga_style}
              onChange={v => saveOverride("kobo.manga_style", v)}
              label="漫画模式（右 → 左翻页）"
              description="开启后使用日式漫画阅读方向"
              overridden={isOverridden("kobo.manga_style")}
              onReset={() => resetOverride("kobo.manga_style")}
            />
            <ToggleSwitch
              checked={kobo.high_quality}
              onChange={v => saveOverride("kobo.high_quality", v)}
              label="高画质模式"
              description="开启后保留更高的图片分辨率，文件体积更大"
              overridden={isOverridden("kobo.high_quality")}
              onReset={() => resetOverride("kobo.high_quality")}
            />
          </>
        )}
      </div>

      {/* ━━━ Card 3: Pipeline Settings ━━━ */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">⚙️ 管线处理设置</span>
        </div>

        <NumberInput
          value={processing.stable_check_seconds}
          onChange={v => saveOverride("processing.stable_check_seconds", v)}
          label="文件稳定等待时间（秒）"
          description="新文件出现后等待此时间确认大小不再变化"
          min={5}
          max={300}
          overridden={isOverridden("processing.stable_check_seconds")}
          onReset={() => resetOverride("processing.stable_check_seconds")}
        />
        <NumberInput
          value={processing.max_retries}
          onChange={v => saveOverride("processing.max_retries", v)}
          label="最大重试次数"
          description="每个处理步骤失败后最多重试次数"
          min={0}
          max={10}
          overridden={isOverridden("processing.max_retries")}
          onReset={() => resetOverride("processing.max_retries")}
        />
        <ToggleSwitch
          checked={processing.delete_inbox_after_archive}
          onChange={v => saveOverride("processing.delete_inbox_after_archive", v)}
          label="归档后删除 inbox 源文件"
          description="开启后 CBZ 归档完成即删除原始文件，节省空间"
          overridden={isOverridden("processing.delete_inbox_after_archive")}
          onReset={() => resetOverride("processing.delete_inbox_after_archive")}
        />
        <ToggleSwitch
          checked={processing.cleanup_after_import}
          onChange={v => saveOverride("processing.cleanup_after_import", v)}
          label="导入后清理中间文件"
          description="导入 Komga 后删除 processing/ 和 kepub_ready/ 中的临时文件"
          overridden={isOverridden("processing.cleanup_after_import")}
          onReset={() => resetOverride("processing.cleanup_after_import")}
        />
        <SliderInput
          value={metadata.confidence_auto_accept}
          onChange={v => saveOverride("metadata.confidence_auto_accept", v)}
          label="置信度自动接受阈值"
          description="刮削结果置信度高于此值时自动接受，否则进入人工审核"
          min={0}
          max={1}
          step={0.05}
          overridden={isOverridden("metadata.confidence_auto_accept")}
          onReset={() => resetOverride("metadata.confidence_auto_accept")}
        />
      </div>

      {/* ━━━ Card 4: Scrape Sources ━━━ */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">🔍 刮削源与 LLM</span>
        </div>

        <ToggleSwitch
          checked={metadata.bookwalker_tw_enabled}
          onChange={v => saveOverride("metadata.bookwalker_tw_enabled", v)}
          label="BookWalker 台湾"
          description="优先使用的繁体中文元数据源"
          overridden={isOverridden("metadata.bookwalker_tw_enabled")}
          onReset={() => resetOverride("metadata.bookwalker_tw_enabled")}
        />
        <ToggleSwitch
          checked={metadata.bookwalker_jp_enabled}
          onChange={v => saveOverride("metadata.bookwalker_jp_enabled", v)}
          label="BookWalker 日本"
          description="台湾源无结果时回退到日文原版"
          overridden={isOverridden("metadata.bookwalker_jp_enabled")}
          onReset={() => resetOverride("metadata.bookwalker_jp_enabled")}
        />
        <ToggleSwitch
          checked={metadata.bangumi_enabled}
          onChange={v => saveOverride("metadata.bangumi_enabled", v)}
          label="Bangumi"
          description="BookWalker 均无结果时的保底数据源"
          overridden={isOverridden("metadata.bangumi_enabled")}
          onReset={() => resetOverride("metadata.bangumi_enabled")}
        />

        <div style={{
          borderTop: "1px solid var(--border)",
          margin: "12px 0",
          paddingTop: 12,
        }}>
          <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 8, textTransform: "uppercase", fontWeight: 600 }}>
            LLM 增强功能
          </div>
        </div>

        <ToggleSwitch
          checked={metadata.llm_normalize_enabled}
          onChange={v => saveOverride("metadata.llm_normalize_enabled", v)}
          label="LLM 文件名归一化"
          description="使用大语言模型从文件名提取标准化书名和检索关键词"
          overridden={isOverridden("metadata.llm_normalize_enabled")}
          onReset={() => resetOverride("metadata.llm_normalize_enabled")}
        />
        <ToggleSwitch
          checked={metadata.llm_verify_scrape_enabled}
          onChange={v => saveOverride("metadata.llm_verify_scrape_enabled", v)}
          label="LLM 刮削验证"
          description="刮削后用 LLM 二次验证元数据是否与文件名匹配"
          overridden={isOverridden("metadata.llm_verify_scrape_enabled")}
          onReset={() => resetOverride("metadata.llm_verify_scrape_enabled")}
        />
      </div>

      {/* ━━━ Card 5: LLM Runs ━━━ */}
      <div className="card" style={{ padding: 0 }}>
        <div style={{ padding: "16px 16px 0" }}>
          <span className="card-title">📋 LLM 调用记录（最近50条）</span>
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

      {/* Toast */}
      {toast && <div className="toast success">{toast}</div>}
    </div>
  );
}

function fmtDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch { return iso; }
}
