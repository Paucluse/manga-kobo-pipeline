import React from "react";
import type { ProcessingStatus } from "../types";

const labels: Record<string, string> = {
  discovered: "已发现",
  waiting_stable: "等待稳定",
  processing: "处理中",
  normalized: "已规范化",
  metadata_parsed: "元数据已解析",
  awaiting_metadata_approval: "等待确认",
  needs_review: "需人工审核",
  archived: "已归档",
  converted: "已转换",
  importing: "导入中",
  imported: "已导入",
  done: "完成",
  failed: "失败",
};

export function StatusBadge({ status }: { status: ProcessingStatus | string }) {
  const label = labels[status] ?? status;
  const cls = `status-badge status-${status}`;
  return <span className={cls}>{label}</span>;
}
