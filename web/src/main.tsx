import React, { FormEvent, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type SetupStatus = { has_admin: boolean };
type User = { id: number; username: string };
type Status = {
  mode: string;
  counts: Record<string, number>;
  total: number;
  pending_approvals: number;
  recent_records: Array<Record<string, string | number>>;
};
type Settings = {
  mode: string;
  valid_modes: string[];
  prompt: string;
  prompt_history: Array<{ id: number; content: string; active: number; created_at: string }>;
};
type Candidate = {
  provider: string;
  title: string;
  series: string;
  volume: string;
  author: string;
  publisher: string;
  cover_url: string;
  detail_url: string;
  confidence: number;
};
type Approval = {
  id: number;
  record_id: number;
  scope: string;
  collection_title: string;
  file_name: string;
  status: string;
  parsed: Record<string, string | number>;
  candidates: Candidate[];
};
type LlmRun = {
  id: number;
  record_id: number | null;
  source_name: string;
  prompt: string;
  response: string;
  parsed_json: string;
  error: string;
  elapsed_ms: number;
  created_at: string;
};

const modeLabels: Record<string, string> = {
  auto: "自动处理",
  manual_book: "每本确认",
  manual_series: "每系列确认",
  paused: "暂停",
};

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    let message = res.statusText;
    try {
      const data = await res.json();
      message = data.detail || message;
    } catch {
      // keep status text
    }
    throw new Error(message);
  }
  return res.json();
}

function App() {
  const [setupStatus, setSetupStatus] = useState<SetupStatus | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [error, setError] = useState("");

  async function refreshAuth() {
    const setup = await api<SetupStatus>("/api/setup/status");
    setSetupStatus(setup);
    if (setup.has_admin) {
      try {
        setUser(await api<User>("/api/me"));
      } catch {
        setUser(null);
      }
    }
  }

  useEffect(() => {
    refreshAuth().catch((e) => setError(e.message));
  }, []);

  if (!setupStatus) return <Shell error={error}>载入中</Shell>;
  if (!setupStatus.has_admin) {
    return <AuthForm title="创建管理员账号" endpoint="/api/setup" onDone={refreshAuth} />;
  }
  if (!user) return <AuthForm title="登录控制台" endpoint="/api/login" onDone={refreshAuth} />;

  return <Dashboard user={user} onLogout={() => setUser(null)} />;
}

function AuthForm({
  title,
  endpoint,
  onDone,
}: {
  title: string;
  endpoint: string;
  onDone: () => Promise<void>;
}) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api(endpoint, {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      await onDone();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <Shell error={error}>
      <form className="auth" onSubmit={submit}>
        <h1>{title}</h1>
        <label>
          用户名
          <input value={username} onChange={(e) => setUsername(e.target.value)} />
        </label>
        <label>
          密码
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        <button type="submit">继续</button>
      </form>
    </Shell>
  );
}

function Dashboard({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [llmRuns, setLlmRuns] = useState<LlmRun[]>([]);
  const [error, setError] = useState("");

  async function refresh() {
    const [statusData, settingsData, approvalsData, llmData] = await Promise.all([
      api<Status>("/api/status"),
      api<Settings>("/api/settings"),
      api<{ items: Approval[] }>("/api/approvals"),
      api<{ items: LlmRun[] }>("/api/llm-runs?limit=20"),
    ]);
    setStatus(statusData);
    setSettings(settingsData);
    setApprovals(approvalsData.items);
    setLlmRuns(llmData.items);
  }

  useEffect(() => {
    refresh().catch((e) => setError(e.message));
    const timer = window.setInterval(() => refresh().catch(() => undefined), 15000);
    return () => window.clearInterval(timer);
  }, []);

  async function logout() {
    await api("/api/logout", { method: "POST" });
    onLogout();
  }

  return (
    <Shell error={error}>
      <header className="topbar">
        <div>
          <h1>漫画管线控制台</h1>
          <span>当前用户：{user.username}</span>
        </div>
        <div className="actions">
          <button type="button" onClick={() => refresh().catch((e) => setError(e.message))}>
            刷新
          </button>
          <button type="button" onClick={logout}>
            退出
          </button>
        </div>
      </header>
      {status && <StatusPanel status={status} />}
      {settings && <SettingsPanel settings={settings} onSaved={refresh} />}
      <ApprovalPanel approvals={approvals} onChanged={refresh} />
      <RescrapePanel />
      <LlmPanel runs={llmRuns} />
    </Shell>
  );
}

function StatusPanel({ status }: { status: Status }) {
  const counts = Object.entries(status.counts);
  return (
    <section>
      <div className="section-head">
        <h2>状态总览</h2>
        <strong>{modeLabels[status.mode] || status.mode}</strong>
      </div>
      <div className="metric-grid">
        <div className="metric">
          <span>总任务</span>
          <strong>{status.total}</strong>
        </div>
        <div className="metric">
          <span>待确认</span>
          <strong>{status.pending_approvals}</strong>
        </div>
        {counts.map(([name, count]) => (
          <div className="metric" key={name}>
            <span>{name}</span>
            <strong>{count}</strong>
          </div>
        ))}
      </div>
      <h3>最近记录</h3>
      <DataTable
        rows={status.recent_records}
        columns={["id", "current_status", "file_name", "series", "volume", "updated_at"]}
      />
    </section>
  );
}

function SettingsPanel({ settings, onSaved }: { settings: Settings; onSaved: () => void }) {
  const [mode, setMode] = useState(settings.mode);
  const [prompt, setPrompt] = useState(settings.prompt);
  const [message, setMessage] = useState("");

  useEffect(() => {
    setMode(settings.mode);
    setPrompt(settings.prompt);
  }, [settings]);

  async function saveMode(nextMode: string) {
    setMode(nextMode);
    await api("/api/settings/mode", { method: "PUT", body: JSON.stringify({ mode: nextMode }) });
    await onSaved();
  }

  async function savePrompt() {
    await api("/api/settings/prompt", { method: "PUT", body: JSON.stringify({ content: prompt }) });
    setMessage("已保存 prompt");
    await onSaved();
  }

  return (
    <section>
      <div className="section-head">
        <h2>运行控制</h2>
        <span>{message}</span>
      </div>
      <div className="mode-row">
        {settings.valid_modes.map((item) => (
          <button
            type="button"
            className={item === mode ? "selected" : ""}
            key={item}
            onClick={() => saveMode(item)}
          >
            {modeLabels[item] || item}
          </button>
        ))}
      </div>
      <label className="prompt">
        LLM 全局 Prompt
        <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} />
      </label>
      <button type="button" onClick={savePrompt}>
        保存 Prompt
      </button>
    </section>
  );
}

function ApprovalPanel({
  approvals,
  onChanged,
}: {
  approvals: Approval[];
  onChanged: () => void;
}) {
  const [searchTitle, setSearchTitle] = useState("");
  const [provider, setProvider] = useState("bookwalker_jp");

  async function approve(id: number, index: number) {
    await api(`/api/approvals/${id}/approve`, {
      method: "POST",
      body: JSON.stringify({ candidate_index: index }),
    });
    await onChanged();
  }

  async function search(id: number) {
    if (!searchTitle.trim()) return;
    await api(`/api/approvals/${id}/search`, {
      method: "POST",
      body: JSON.stringify({ provider, title: searchTitle }),
    });
    await onChanged();
  }

  return (
    <section>
      <h2>元数据确认</h2>
      {approvals.length === 0 && <p className="empty">当前没有待确认项目。</p>}
      {approvals.map((approval) => (
        <div className="approval" key={approval.id}>
          <div className="approval-title">
            <strong>#{approval.record_id} {approval.file_name}</strong>
            <span>{approval.scope === "series" ? "每系列确认" : "每本确认"}</span>
          </div>
          <p>合集：{approval.collection_title || "独立文件"}</p>
          <p>
            解析：{approval.parsed.series || approval.parsed.title || "-"} v
            {approval.parsed.volume || "-"}
          </p>
          <div className="manual-search">
            <select value={provider} onChange={(e) => setProvider(e.target.value)}>
              <option value="bookwalker_tw">BookWalker 台湾</option>
              <option value="bookwalker_jp">BookWalker 日本</option>
              <option value="bangumi">Bangumi</option>
            </select>
            <input
              value={searchTitle}
              onChange={(e) => setSearchTitle(e.target.value)}
              placeholder="输入检索标题"
            />
            <button type="button" onClick={() => search(approval.id)}>
              重新检索
            </button>
          </div>
          <div className="candidate-grid">
            {approval.candidates.map((candidate, index) => (
              <article className="candidate" key={`${candidate.provider}-${index}`}>
                {candidate.cover_url && <img src={candidate.cover_url} alt="" />}
                <div>
                  <strong>{candidate.series || candidate.title}</strong>
                  <p>{candidate.provider} / v{candidate.volume || "-"}</p>
                  <p>{candidate.author || "-"} / {candidate.publisher || "-"}</p>
                  <p>置信度：{Number(candidate.confidence || 0).toFixed(2)}</p>
                  {candidate.detail_url && (
                    <a href={candidate.detail_url} target="_blank" rel="noreferrer">
                      来源页面
                    </a>
                  )}
                  <button type="button" onClick={() => approve(approval.id, index)}>
                    使用这个元数据
                  </button>
                </div>
              </article>
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}

function RescrapePanel() {
  const [ids, setIds] = useState("");
  const [title, setTitle] = useState("");
  const [dryRun, setDryRun] = useState(true);
  const [relocate, setRelocate] = useState(true);
  const [result, setResult] = useState<Array<Record<string, string | number>>>([]);

  async function submit() {
    const parsedIds = ids
      .split(/[,\s]+/)
      .map((item) => Number(item))
      .filter((item) => Number.isFinite(item) && item > 0);
    const data = await api<{ items: Array<Record<string, string | number>> }>("/api/rescrape", {
      method: "POST",
      body: JSON.stringify({ ids: parsedIds, title, dry_run: dryRun, relocate }),
    });
    setResult(data.items);
  }

  return (
    <section>
      <h2>手动重刮削</h2>
      <div className="form-grid">
        <label>
          记录 ID
          <input value={ids} onChange={(e) => setIds(e.target.value)} placeholder="94 95 96" />
        </label>
        <label>
          标题筛选
          <input value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label className="check">
          <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
          Dry-run
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={relocate}
            onChange={(e) => setRelocate(e.target.checked)}
          />
          同步搬移文件
        </label>
      </div>
      <button type="button" onClick={submit}>
        执行
      </button>
      {result.length > 0 && (
        <DataTable rows={result} columns={["record_id", "status", "old_series", "new_series", "old_volume", "new_volume", "confidence", "message"]} />
      )}
    </section>
  );
}

function LlmPanel({ runs }: { runs: LlmRun[] }) {
  const [openId, setOpenId] = useState<number | null>(null);
  const current = useMemo(() => runs.find((run) => run.id === openId), [runs, openId]);
  return (
    <section>
      <h2>LLM 调用记录</h2>
      <DataTable
        rows={runs}
        columns={["id", "record_id", "source_name", "elapsed_ms", "error", "created_at"]}
        onRowClick={(row) => setOpenId(Number(row.id))}
      />
      {current && (
        <div className="llm-detail">
          <h3>调用 #{current.id}</h3>
          <h4>Prompt</h4>
          <pre>{current.prompt}</pre>
          <h4>原始返回</h4>
          <pre>{current.response || current.error}</pre>
          <h4>解析 JSON</h4>
          <pre>{current.parsed_json}</pre>
        </div>
      )}
    </section>
  );
}

function DataTable({
  rows,
  columns,
  onRowClick,
}: {
  rows: Array<Record<string, string | number | null>>;
  columns: string[];
  onRowClick?: (row: Record<string, string | number | null>) => void;
}) {
  if (rows.length === 0) return <p className="empty">暂无数据。</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => (
            <tr key={idx} onClick={() => onRowClick?.(row)}>
              {columns.map((column) => (
                <td key={column}>{String(row[column] ?? "")}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Shell({ children, error }: { children: React.ReactNode; error?: string }) {
  return (
    <main>
      {error && <div className="error">{error}</div>}
      {children}
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
