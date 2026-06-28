import React, { useEffect, useState } from "react";
import * as api from "../api";
import type { Approval, Candidate } from "../types";
import { StatusBadge } from "../components/StatusBadge";
import { CandidateCard } from "../components/CandidateCard";

interface ApprovalsProps {
  onViewRecord?: (id: number) => void;
}

export function Approvals({ onViewRecord }: ApprovalsProps) {
  const [items, setItems] = useState<Approval[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [expandedId, setExpandedId] = useState<number | null>(null);

  // Per-approval search state
  const [searchInputs, setSearchInputs] = useState<Record<number, { provider: string; title: string }>>({});
  const [searching, setSearching] = useState<Record<number, boolean>>({});
  const [approving, setApproving] = useState<Record<number, boolean>>({});
  const [searchError, setSearchError] = useState<Record<number, string>>({});

  async function load() {
    try {
      const res = await api.getApprovals();
      setItems(res.items);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    const t = window.setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);

  function getInput(id: number, approval: Approval) {
    return searchInputs[id] ?? {
      provider: "bookwalker_tw",
      title: String(approval.parsed?.series || approval.parsed?.title || ""),
    };
  }

  async function doSearch(approval: Approval) {
    const input = getInput(approval.id, approval);
    if (!input.title.trim()) return;
    setSearching(s => ({ ...s, [approval.id]: true }));
    setSearchError(s => ({ ...s, [approval.id]: "" }));
    try {
      const res = await api.approvalSearch(approval.id, input.provider, input.title);
      await load(); // reload to get new candidates
    } catch (e) {
      setSearchError(s => ({ ...s, [approval.id]: (e as Error).message }));
    } finally {
      setSearching(s => ({ ...s, [approval.id]: false }));
    }
  }

  async function doApprove(approvalId: number, index: number) {
    setApproving(s => ({ ...s, [approvalId]: true }));
    try {
      await api.approveCandidate(approvalId, index);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setApproving(s => ({ ...s, [approvalId]: false }));
    }
  }

  if (loading) return <div className="empty-state"><div className="spinner" /></div>;

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>等待确认</h1>
          <div className="subtitle">系统无法自动确认的元数据需要人工审核</div>
        </div>
      </div>

      {error && <div className="error-msg">{error}</div>}

      {items.length === 0 ? (
        <div className="empty-state">
          <div className="icon">✅</div>
          <p>目前没有待确认项目，一切正常</p>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {items.map(approval => {
            const input = getInput(approval.id, approval);
            const isExpanded = expandedId === approval.id;
            return (
              <div key={approval.id} className="card">
                {/* Header row */}
                <div
                  style={{ display: "flex", alignItems: "flex-start", gap: 12, cursor: "pointer" }}
                  onClick={() => setExpandedId(isExpanded ? null : approval.id)}
                >
                  <div style={{ flex: 1 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                      <span style={{ fontFamily: "monospace", fontSize: 12, color: "var(--text-muted)" }}>#{approval.record_id}</span>
                      <span className="cell-title" style={{ fontSize: 14 }}>{approval.file_name}</span>
                      <span style={{ fontSize: 11, color: "var(--text-muted)", padding: "2px 8px", background: "var(--bg-elevated)", borderRadius: 999 }}>
                        {approval.scope === "series" ? "系列确认" : "单本确认"}
                      </span>
                    </div>
                    <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                      解析结果：{String(approval.parsed?.series || approval.parsed?.title || "—")}
                      {approval.parsed?.volume ? ` 卷 ${approval.parsed.volume}` : ""}
                      {approval.collection_title ? ` · ${approval.collection_title}` : ""}
                    </div>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontSize: 12, color: "var(--text-muted)" }}>{approval.candidates.length} 个候选</span>
                    <span style={{ color: "var(--text-muted)", fontSize: 18 }}>{isExpanded ? "▾" : "▸"}</span>
                  </div>
                </div>

                {isExpanded && (
                  <div style={{ marginTop: 16 }}>
                    <div className="section-divider" />

                    {/* Manual search */}
                    <div style={{ marginBottom: 16 }}>
                      <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 8 }}>手动检索</div>
                      <div className="form-row" style={{ flexWrap: "wrap" }}>
                        <select
                          value={input.provider}
                          onChange={e => setSearchInputs(s => ({ ...s, [approval.id]: { ...input, provider: e.target.value } }))}
                          style={{ maxWidth: 180 }}
                        >
                          <option value="bookwalker_tw">BookWalker 台湾</option>
                          <option value="bookwalker_jp">BookWalker 日本</option>
                          <option value="bangumi">Bangumi</option>
                        </select>
                        <input
                          value={input.title}
                          onChange={e => setSearchInputs(s => ({ ...s, [approval.id]: { ...input, title: e.target.value } }))}
                          placeholder="输入检索标题…"
                          onKeyDown={e => e.key === "Enter" && doSearch(approval)}
                        />
                        <button
                          type="button"
                          className="btn btn-primary"
                          disabled={searching[approval.id] || !input.title.trim()}
                          onClick={() => doSearch(approval)}
                          style={{ flex: "0 0 auto" }}
                        >
                          {searching[approval.id] ? <><span className="spinner" style={{ width: 14, height: 14 }} /> 搜索中</> : "搜索"}
                        </button>
                      </div>
                      {searchError[approval.id] && <div className="error-msg" style={{ marginTop: 8 }}>{searchError[approval.id]}</div>}
                    </div>

                    {/* Candidates */}
                    {approval.candidates.length === 0 ? (
                      <div className="empty-state" style={{ padding: "24px" }}>
                        <p>暂无候选，请使用上方搜索添加</p>
                      </div>
                    ) : (
                      <div className="candidate-grid">
                        {approval.candidates.map((candidate, index) => (
                          <CandidateCard
                            key={`${candidate.provider}-${index}`}
                            candidate={candidate}
                            onSelect={() => doApprove(approval.id, index)}
                            actionLabel={approving[approval.id] ? "处理中…" : "选择此元数据"}
                          />
                        ))}
                      </div>
                    )}

                    {onViewRecord && (
                      <div style={{ marginTop: 12 }}>
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          onClick={() => onViewRecord(approval.record_id)}
                        >
                          查看记录详情 →
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
