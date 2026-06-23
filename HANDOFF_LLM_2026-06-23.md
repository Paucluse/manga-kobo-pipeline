# Agent Handoff — LLM 集成重构（2026-06-22 / 2026-06-23）

本文件由原始开发 agent 生成，供另一台主机上的 agent 进行验证。

---

## 改动概述

本次共两批改动，均已合并到 `main` 分支并推送 GitHub。

### 批次一：LLM 文件名归一化重构（commit `6d233a8`）

**问题**：
1. 代码内置的 4 行默认 prompt 与用户设想的详细 prompt 不一致。
2. User message 里同时发了 `output_schema`，和 system prompt 的 schema 冲突。
3. `LlmMetadata` 没有 per-provider 检索词字段；所有平台用同一个 `search_titles` 列表。
4. `_metadata_search_titles` 对 BW TW、BW JP、Bangumi 用同一套词，没有区分。
5. LLM 结果应用门槛固定 0.65，导致有用结果被丢弃。

**改动文件**：
- `src/manga_pipeline/llm_metadata.py`：完整重写
  - 新增 `SYSTEM_PROMPT` 常量（固定 prompt，不再依赖 config 或 Web UI）
  - `LlmMetadata` 新增字段：`queries_tw`, `queries_jp`, `queries_bangumi`, `parse_status`, `verified`, `verification_level`, `warnings`, `noise_removed`
  - User message 只发 filename + regex_parse_hint，不再发 output_schema
  - `_parse_llm_json` 拦截 `"null"/"none"` 字符串；按 parse_status 自动压缩置信度
- `src/manga_pipeline/pipeline.py`：
  - `_metadata_search_titles` 按 provider 路由（tw/jp/bangumi 各自用 LLM 对应列表）
  - LLM 应用门槛：`ok` 时 0.5，`ambiguous` 时 0.4
  - 记录 `parse_status` 和 `warnings` 到日志
- `src/manga_pipeline/rescrape.py`：同步以上逻辑

---

### 批次二：刮削后 LLM 验证（本次提交）

**设计**：
每个刮削平台（BookWalker TW / JP / Bangumi）返回候选后，可选调用 LLM 做一次二次判断：
「刮削结果描述的是否与原始文件同一部作品？」
- 如果 LLM 说 `match=false` → 该候选被丢弃，继续试下一个平台
- 如果 LLM 说 `match=true` → 置信度 +最多 0.15，继续正常流程
- LLM 无响应/报错 → 默认 `match=true`（fail-open，不阻断流程）

**改动文件**：
- `src/manga_pipeline/config.py`：
  - 新增 `llm_verify_scrape_enabled: bool = False`（默认关闭）
- `src/manga_pipeline/llm_metadata.py`：
  - 新增 `VERIFY_PROMPT` 常量
  - 新增 `ScrapeVerification` dataclass（`match`, `confidence`, `reason`, `elapsed_ms`）
  - 新增 `verify_scrape_with_llm()` 函数
- `src/manga_pipeline/pipeline.py`：
  - `_search_best_bookwalker_metadata` 新增 `filename`, `llm_metadata`, `cfg` 参数
  - `_search_bangumi_metadata` 同样加入验证逻辑
  - 新增 `_verify_candidate()` 辅助函数（duck-typing 兼容两种 metadata 对象）
- `README.md`：新增 LLM 功能说明章节

---

## 关键配置参数

```yaml
# config.yaml 中 metadata 节新增的字段：
metadata:
  llm_normalize_enabled: true          # 批次一：文件名归一化（原有，行为已改变）
  llm_verify_scrape_enabled: false     # 批次二：刮削后验证（新增，默认关闭）
  llm_base_url: https://generativelanguage.googleapis.com/v1beta/openai
  llm_model: gemini-3.1-flash-lite
  llm_api_key_file: /run/secrets/gemini_api_key
```

---

## 验证任务清单

### 环境准备

```bash
cd /home/kuraki/kobo/manga-kobo-pipeline
git pull origin main
docker compose up -d --build manga-pipeline
```

### 验证一：SYSTEM_PROMPT 是否生效

```bash
# 在容器里看 llm_metadata.py 的 SYSTEM_PROMPT 常量是否存在
docker compose exec manga-pipeline python3 -c "
from manga_pipeline.llm_metadata import SYSTEM_PROMPT, VERIFY_PROMPT
print('SYSTEM_PROMPT length:', len(SYSTEM_PROMPT))
print('VERIFY_PROMPT length:', len(VERIFY_PROMPT))
print('OK')
"
```

**预期**：两个 prompt 都有内容，不报错。

### 验证二：LlmMetadata 新字段

```bash
docker compose exec manga-pipeline python3 -c "
from manga_pipeline.llm_metadata import LlmMetadata, ScrapeVerification
m = LlmMetadata()
print('queries_tw:', m.queries_tw)
print('queries_jp:', m.queries_jp)
print('queries_bangumi:', m.queries_bangumi)
print('parse_status:', m.parse_status)
print('warnings:', m.warnings)
v = ScrapeVerification()
print('ScrapeVerification.match:', v.match)
print('OK')
"
```

**预期**：字段均存在，不报错。

### 验证三：JSON 解析器的 null 字符串过滤

```bash
docker compose exec manga-pipeline python3 -c "
from manga_pipeline.llm_metadata import _parse_llm_json
import json
# 模拟 LLM 返回 null 字符串
test_json = json.dumps({
    'parse_status': 'ok',
    'clean_title': 'null',  # 应被过滤
    'titles': {'traditional_chinese': '鋼之鍊金術師', 'japanese': 'null'},
    'scraping_queries': {
        'bookwalker_tw': ['鋼之鍊金術師'],
        'bookwalker_jp': ['鋼の錬金術師'],
        'bangumi': ['鋼の錬金術師'],
    },
    'authors': ['荒川弘'],
    'confidence': 0.8,
}, ensure_ascii=False)
result = _parse_llm_json(test_json)
print('title:', result.title)        # 应为 鋼之鍊金術師（tw），不是 'null'
print('title_jp:', result.title_jp)  # 应为空，'null' 被过滤
print('queries_tw:', result.queries_tw)
print('queries_jp:', result.queries_jp)
print('parse_status:', result.parse_status)
print('OK')
"
```

**预期**：`title` 为 `鋼之鍊金術師`，`title_jp` 为空，`queries_tw`/`queries_jp` 有内容。

### 验证四：配置模型能正确加载新字段

```bash
docker compose exec manga-pipeline python3 -c "
from manga_pipeline.config import load_config
cfg = load_config()
print('llm_verify_scrape_enabled:', cfg.metadata.llm_verify_scrape_enabled)
print('llm_normalize_enabled:', cfg.metadata.llm_normalize_enabled)
print('OK')
"
```

**预期**：字段存在，不报错。（`llm_verify_scrape_enabled` 默认 `False`）

### 验证五：实际 LLM 调用（需要 API key 已配置）

放入一个测试文件，观察日志：

```bash
cp /path/to/test_manga.cbz /mnt/kobo-nas/Comic/inbox/
docker compose logs -f --tail=60 manga-pipeline
```

日志中应出现：
```
LLM parse_status=ok confidence=0.X verified=False
LLM applied: title=XXX, author=XXX, vol=X
```

如果 `llm_verify_scrape_enabled: true`，还会出现：
```
BookWalker TW LLM confirmed 'XXX' (+0.XX boost): XXX
```
或者：
```
BookWalker TW LLM rejected candidate 'XXX': XXX
```

### 验证六：verify_scrape_with_llm 禁用时不影响流程

确认 `config.yaml` 中 `llm_verify_scrape_enabled: false`（或不设置），放入漫画后管线应正常完成，日志中不出现 LLM rejected/confirmed 字样。

---

## 已知问题 / 不影响功能的注意事项

1. **本地 Windows 测试的已知失败**：`test_scan_expands_collection_directory` 在 Windows 上因路径编码问题失败，Linux Docker 环境中正常。
2. **`llm_verify_scrape_enabled` 默认关闭**：需要在 `config.yaml` 显式设置 `true` 才会启用第二次 LLM 验证。
3. **fail-open 设计**：验证 LLM 出错时默认 `match=true`，不会因 LLM 故障阻断整个管线。

---

## Git 信息

```
批次一 commit: 6d233a8  fix: overhaul LLM integration with canonical prompt and per-provider query routing
批次二 commit: (本次 push)
Branch: main
Repo: https://github.com/Paucluse/manga-kobo-pipeline
```
