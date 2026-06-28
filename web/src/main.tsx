import React, { FormEvent, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./global.css";
import * as api from "./api";
import type { User } from "./types";
import { Sidebar } from "./components/Sidebar";
import { Dashboard } from "./pages/Dashboard";
import { Records } from "./pages/Records";
import { RecordDetail } from "./pages/RecordDetail";
import { Approvals } from "./pages/Approvals";
import { SettingsPage } from "./pages/Settings";

type Page =
  | { id: "dashboard" }
  | { id: "records"; status?: string }
  | { id: "record-detail"; recordId: number; prevPage: Page }
  | { id: "approvals" }
  | { id: "settings" };

function App() {
  const [hasAdmin, setHasAdmin] = useState<boolean | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [initError, setInitError] = useState("");

  async function init() {
    try {
      const setup = await api.getSetupStatus();
      setHasAdmin(setup.has_admin);
      if (setup.has_admin) {
        try {
          const me = await api.getMe();
          setUser(me);
        } catch {
          setUser(null);
        }
      }
    } catch (e) {
      setInitError((e as Error).message);
    }
  }

  useEffect(() => { init(); }, []);

  if (hasAdmin === null) {
    return (
      <div className="auth-wrap">
        <div className="auth-card" style={{ textAlign: "center" }}>
          <div className="spinner" style={{ margin: "0 auto" }} />
          <p style={{ marginTop: 16, color: "var(--text-muted)" }}>正在连接…</p>
          {initError && <p style={{ color: "var(--status-failed)", marginTop: 8 }}>{initError}</p>}
        </div>
      </div>
    );
  }

  if (!hasAdmin) {
    return <AuthPage mode="setup" onDone={init} />;
  }

  if (!user) {
    return <AuthPage mode="login" onDone={init} />;
  }

  return <MainApp user={user} onLogout={() => { setUser(null); }} />;
}

function AuthPage({ mode, onDone }: { mode: "setup" | "login"; onDone: () => void }) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      if (mode === "setup") {
        await api.setup(username, password);
      } else {
        await api.login(username, password);
      }
      await onDone();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-wrap">
      <form className="auth-card" onSubmit={submit}>
        <h1>{mode === "setup" ? "初始化控制台" : "登录"}</h1>
        <p>{mode === "setup" ? "创建管理员账号以开始使用" : "漫画管线控制台"}</p>

        <label>
          用户名
          <input
            value={username}
            onChange={e => setUsername(e.target.value)}
            autoFocus={mode === "login"}
          />
        </label>
        <label>
          密码
          <input
            type="password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            autoFocus={mode === "setup"}
          />
        </label>

        {error && (
          <div style={{ color: "var(--status-failed)", fontSize: 13, marginTop: 4 }}>{error}</div>
        )}

        <button type="submit" className="btn btn-primary" disabled={loading}>
          {loading ? <><span className="spinner" style={{ width: 14, height: 14 }} /> 处理中</> : mode === "setup" ? "创建账号" : "登录"}
        </button>
      </form>
    </div>
  );
}

function MainApp({ user, onLogout }: { user: User; onLogout: () => void }) {
  const [page, setPage] = useState<Page>({ id: "dashboard" });
  const [pendingApprovals, setPendingApprovals] = useState(0);

  // Periodically refresh approval badge count
  useEffect(() => {
    async function refreshCount() {
      try {
        const res = await api.getApprovals();
        setPendingApprovals(res.items.length);
      } catch {
        // ignore
      }
    }
    refreshCount();
    const t = window.setInterval(refreshCount, 20000);
    return () => clearInterval(t);
  }, []);

  function navigate(pageId: string) {
    if (pageId === "dashboard") setPage({ id: "dashboard" });
    else if (pageId === "records") setPage({ id: "records" });
    else if (pageId === "approvals") setPage({ id: "approvals" });
    else if (pageId === "settings") setPage({ id: "settings" });
  }

  function viewRecord(id: number) {
    setPage({ id: "record-detail", recordId: id, prevPage: page });
  }

  async function handleLogout() {
    try { await api.logout(); } catch { /* ignore */ }
    onLogout();
  }

  const currentPageId = page.id === "record-detail" ? (page.prevPage?.id ?? "records") : page.id;

  function renderContent() {
    switch (page.id) {
      case "dashboard":
        return (
          <Dashboard
            onNavigateToRecord={viewRecord}
            onNavigateToApprovals={() => setPage({ id: "approvals" })}
          />
        );
      case "records":
        return (
          <Records
            onViewDetail={viewRecord}
            initialStatus={page.status}
          />
        );
      case "record-detail":
        return (
          <RecordDetail
            recordId={page.recordId}
            onBack={() => setPage(page.prevPage)}
          />
        );
      case "approvals":
        return (
          <Approvals
            onViewRecord={viewRecord}
          />
        );
      case "settings":
        return <SettingsPage />;
    }
  }

  return (
    <div className="app-shell">
      <Sidebar
        user={user}
        currentPage={currentPageId}
        onNavigate={navigate}
        onLogout={handleLogout}
        pendingApprovals={pendingApprovals}
      />
      <main className="main-content">
        {renderContent()}
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
