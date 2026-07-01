// Shared TypeScript types for the manga pipeline frontend

export type SetupStatus = { has_admin: boolean };
export type User = { id: number; username: string };

export type ProcessingStatus =
  | "discovered"
  | "waiting_stable"
  | "processing"
  | "normalized"
  | "metadata_parsed"
  | "awaiting_metadata_approval"
  | "needs_review"
  | "archived"
  | "converted"
  | "importing"
  | "imported"
  | "done"
  | "failed";

export interface MangaRecord {
  id: number;
  file_name: string;
  original_path: string;
  file_hash: string;
  current_status: ProcessingStatus;
  title: string;
  author: string;
  series: string;
  volume: string;
  publisher: string;
  collection_title: string;
  summary: string;
  cover_url: string;
  source_url: string;
  isbn: string;
  page_count: string;
  confidence: number;
  archive_path: string;
  converted_path: string;
  library_book_id: string;
  error_message: string;
  retry_count: number;
  created_at: string;
  updated_at: string;
}

export interface RecordListItem {
  id: number;
  file_name: string;
  current_status: ProcessingStatus;
  title: string;
  series: string;
  volume: string;
  author: string;
  publisher: string;
  collection_title: string;
  cover_url: string;
  source_url: string;
  confidence: number;
  error_message?: string;
  created_at: string;
  updated_at: string;
}

export interface RecordListResponse {
  total: number;
  page: number;
  size: number;
  items: RecordListItem[];
}

export interface Candidate {
  provider: string;
  provider_id: string;
  title: string;
  series: string;
  volume: string;
  author: string;
  publisher: string;
  cover_url: string;
  detail_url: string;
  summary?: string;
  isbn?: string;
  publish_date?: string;
  confidence: number;
}

export interface Approval {
  id: number;
  record_id: number;
  scope: string;
  collection_title: string;
  file_name: string;
  status: string;
  parsed: Record<string, string | number>;
  candidates: Candidate[];
}

export interface StatusData {
  mode: string;
  counts: Record<string, number>;
  total: number;
  pending_approvals: number;
  recent_records: RecordListItem[];
}

export interface Settings {
  mode: string;
  valid_modes: string[];
  prompt: string;
  prompt_history: Array<{ id: number; content: string; active: number; created_at: string }>;
}

export interface LlmRun {
  id: number;
  record_id: number | null;
  source_name: string;
  prompt: string;
  response: string;
  parsed_json: string;
  error: string;
  elapsed_ms: number;
  created_at: string;
}

export interface RescrapeResult {
  record_id: number;
  file_name: string;
  status: string;
  message: string;
  old_title: string;
  new_title: string;
  old_series: string;
  new_series: string;
  old_volume: string;
  new_volume: string;
  confidence: number;
}

export interface MetadataPatch {
  title?: string;
  series?: string;
  author?: string;
  publisher?: string;
  volume?: string;
  summary?: string;
  cover_url?: string;
  isbn?: string;
}

export interface PipelineConfigResponse {
  kobo: {
    profile: string;
    format: string;
    manga_style: boolean;
    high_quality: boolean;
    skip_kcc: boolean;
  };
  processing: {
    stable_check_seconds: number;
    stable_check_interval: number;
    delete_inbox_after_archive: boolean;
    cleanup_after_import: boolean;
    max_retries: number;
  };
  metadata: {
    confidence_auto_accept: number;
    bookwalker_tw_enabled: boolean;
    bookwalker_jp_enabled: boolean;
    bangumi_enabled: boolean;
    llm_normalize_enabled: boolean;
    llm_verify_scrape_enabled: boolean;
    [key: string]: unknown;
  };
  pdf: {
    enabled: boolean;
    dpi: number;
    jpeg_quality: number;
    [key: string]: unknown;
  };
  _overrides: Record<string, string>;
  _allowed_keys: string[];
  [key: string]: unknown;
}
