// Unified API client for all backend calls

import type {
  Approval,
  Candidate,
  LlmRun,
  MangaRecord,
  MetadataPatch,
  PipelineConfigResponse,
  RecordListResponse,
  RescrapeResult,
  Settings,
  SetupStatus,
  StatusData,
  User,
} from "./types";

const BASE = "";

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(BASE + path, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  if (!res.ok) {
    let message = res.statusText;
    try {
      const data = await res.json();
      message = data.detail || message;
    } catch {
      // keep statusText
    }
    throw new Error(message);
  }
  return res.json() as Promise<T>;
}

// Auth
export const getSetupStatus = () => request<SetupStatus>("/api/setup/status");
export const setup = (username: string, password: string) =>
  request<{ ok: boolean }>("/api/setup", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
export const login = (username: string, password: string) =>
  request<{ ok: boolean }>("/api/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
export const logout = () => request<{ ok: boolean }>("/api/logout", { method: "POST" });
export const getMe = () => request<User>("/api/me");

// Dashboard
export const getStatus = () => request<StatusData>("/api/status");

// Settings
export const getSettings = () => request<Settings>("/api/settings");
export const setMode = (mode: string) =>
  request<{ mode: string }>("/api/settings/mode", {
    method: "PUT",
    body: JSON.stringify({ mode }),
  });

// Records
export const getRecords = (params: {
  status_filter?: string;
  search?: string;
  page?: number;
  size?: number;
}) => {
  const q = new URLSearchParams();
  if (params.status_filter) q.set("status_filter", params.status_filter);
  if (params.search) q.set("search", params.search);
  if (params.page) q.set("page", String(params.page));
  if (params.size) q.set("size", String(params.size));
  return request<RecordListResponse>(`/api/records?${q}`);
};
export const getRecord = (id: number) => request<MangaRecord>(`/api/records/${id}`);
export const patchMetadata = (id: number, patch: MetadataPatch) =>
  request<MangaRecord>(`/api/records/${id}/metadata`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
export const rescrapeRecord = (
  id: number,
  opts: { provider?: string; title?: string; volume?: string; author?: string; dry_run?: boolean; relocate?: boolean; force?: boolean }
) =>
  request<{ result: RescrapeResult }>(`/api/records/${id}/rescrape`, {
    method: "POST",
    body: JSON.stringify({
      provider: opts.provider ?? "bookwalker_tw",
      title: opts.title ?? "",
      volume: opts.volume ?? "",
      author: opts.author ?? "",
      dry_run: opts.dry_run ?? false,
      relocate: opts.relocate ?? true,
      force: opts.force ?? true,
    }),
  });
export const reimportRecord = (id: number) =>
  request<{ ok: boolean }>(`/api/records/${id}/reimport`, { method: "POST" });
export const resetRecord = (id: number) =>
  request<{ ok: boolean }>(`/api/records/${id}/reset`, { method: "POST" });

// Search (not tied to a record)
export const searchProvider = (provider: string, title: string, volume = "", author = "") =>
  request<{ candidate: Candidate | null }>("/api/search", {
    method: "POST",
    body: JSON.stringify({ provider, title, volume, author }),
  });

// Approvals
export const getApprovals = () => request<{ items: Approval[] }>("/api/approvals");
export const getApproval = (id: number) => request<Approval>(`/api/approvals/${id}`);
export const approvalSearch = (id: number, provider: string, title: string) =>
  request<{ candidate: Candidate | null }>(`/api/approvals/${id}/search`, {
    method: "POST",
    body: JSON.stringify({ provider, title }),
  });
export const approveCandidate = (id: number, candidate_index: number) =>
  request<{ ok: boolean }>(`/api/approvals/${id}/approve`, {
    method: "POST",
    body: JSON.stringify({ candidate_index }),
  });

// LLM Runs
export const getLlmRuns = (limit = 50) =>
  request<{ items: LlmRun[] }>(`/api/llm-runs?limit=${limit}`);

// Batch operations
export const batchReset = (ids: number[]) =>
  request<{ results: Array<{ id: number; ok: boolean; detail?: string; status?: string }> }>(
    "/api/records/batch-reset",
    { method: "POST", body: JSON.stringify({ ids }) }
  );

export const batchForceRescrape = (opts: {
  ids: number[];
  provider: string;
  title?: string;
  volume?: string;
  author?: string;
  relocate?: boolean;
}) =>
  request<{ results: RescrapeResult[]; any_changed: boolean }>(
    "/api/records/batch-force-rescrape",
    {
      method: "POST",
      body: JSON.stringify({
        ids: opts.ids,
        provider: opts.provider,
        title: opts.title ?? "",
        volume: opts.volume ?? "",
        author: opts.author ?? "",
        relocate: opts.relocate ?? true,
      }),
    }
  );

// Pipeline configuration
export const getPipelineConfig = () =>
  request<PipelineConfigResponse>("/api/pipeline-config");

export const patchPipelineConfig = (overrides: Record<string, unknown>) =>
  request<{ applied: Record<string, string>; rejected: string[] }>("/api/pipeline-config", {
    method: "PATCH",
    body: JSON.stringify({ overrides }),
  });

export const resetPipelineConfig = (key: string) =>
  request<{ ok: boolean; key: string }>(`/api/pipeline-config/${key}`, {
    method: "DELETE",
  });
