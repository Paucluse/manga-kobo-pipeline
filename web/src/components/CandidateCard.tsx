import React from "react";
import type { Candidate } from "../types";

interface CandidateCardProps {
  candidate: Candidate;
  selected?: boolean;
  onSelect: () => void;
  actionLabel?: string;
}

export function CandidateCard({ candidate, selected, onSelect, actionLabel = "使用此元数据" }: CandidateCardProps) {
  const conf = Math.min(1, Math.max(0, candidate.confidence));
  return (
    <article
      className={`candidate-card${selected ? " selected" : ""}`}
      onClick={onSelect}
    >
      {candidate.cover_url ? (
        <img className="candidate-cover" src={candidate.cover_url} alt="" loading="lazy" />
      ) : (
        <div className="candidate-cover" style={{ display: "flex", alignItems: "center", justifyContent: "center", fontSize: 40, color: "var(--text-muted)" }}>
          📖
        </div>
      )}
      <div className="candidate-body">
        <div className="candidate-title">{candidate.series || candidate.title || "—"}</div>
        {candidate.volume && (
          <div className="candidate-meta">卷 {candidate.volume}</div>
        )}
        <div className="candidate-meta">{candidate.author || "—"}</div>
        <div className="candidate-meta" style={{ color: "var(--accent-light)", fontSize: 11 }}>
          {providerLabel(candidate.provider)}
        </div>
        {candidate.publisher && (
          <div className="candidate-meta">{candidate.publisher}</div>
        )}
        <div className="candidate-confidence">
          <div className="confidence-bar">
            <div className="confidence-fill" style={{ width: `${conf * 100}%` }} />
          </div>
          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{(conf * 100).toFixed(0)}%</span>
        </div>
      </div>
      <div className="candidate-actions">
        <div style={{ display: "flex", gap: 6 }}>
          {candidate.detail_url && (
            <a
              href={candidate.detail_url}
              target="_blank"
              rel="noreferrer"
              onClick={e => e.stopPropagation()}
              className="btn btn-secondary btn-sm"
            >
              来源↗
            </a>
          )}
          <button
            type="button"
            className="btn btn-primary btn-sm"
            style={{ flex: 1 }}
            onClick={e => { e.stopPropagation(); onSelect(); }}
          >
            {actionLabel}
          </button>
        </div>
      </div>
    </article>
  );
}

function providerLabel(provider: string): string {
  const map: Record<string, string> = {
    bookwalker_tw: "BookWalker 台湾",
    bookwalker_jp: "BookWalker 日本",
    bangumi: "Bangumi",
  };
  return map[provider] ?? provider;
}
