# manga-kobo-pipeline

面向 Kobo 阅读器的中文漫画自动处理管线。

把 ZIP / CBZ / RAR / CBR / 7Z 漫画放入 `inbox` 后，管线会自动完成：

1. 提取原始文件名；合集目录会先把父目录名和目录内文件名列表交给 LLM，生成整套书共用的系列锚点。
2. 使用 LLM 对单本文件名做前置规范化，并生成台版/日版/Bangumi 检索标题候选。
3. 使用 BookWalker 台湾检索繁体中文元数据、封面、作者、出版社、ISBN、简介。
   BookWalker 台湾无可接受匹配时尝试 BookWalker 日本；两者都失败时使用 Bangumi 兜底。
4. 开启刮削验证时，由 LLM 判断候选是否确实是同一部作品；确认后才接受候选。
5. 归一化为 CBZ，并写入 `ComicInfo.xml`。
6. 使用 KCC 转换为 Kobo 适用的 `*.kepub.epub`。
7. 写入 EPUB/KEPUB 内置 OPF 元数据。
8. 导入 Komga 书库目录并触发 Komga 扫描。

项目不依赖 Komf。元数据来源链路为 BookWalker 台湾 -> BookWalker 日本 -> Bangumi。

## 元数据规则

- **合集系列锚点**：当文件来自 `inbox` 下的合集目录时，管线会先把父目录名和最多 80 个子文件名交给 LLM，生成 `series_anchors` 记录。后续同目录所有卷都会继承这个锚点，避免同一套书因为中日文别名、Bangumi/BookWalker 返回差异而拆成多个 Komga 系列。
- **系列名称**：合集目录优先使用系列锚点，例如 `五星物語`；刮削结果只补充作者、出版社、简介、封面、ISBN 和来源 URL，不再覆盖锚点系列名。单本散放文件则使用 LLM 文件名解析和刮削结果确定系列名。
- **系列封面**：使用外部元数据封面生成 Komga 本地 `cover.jpg`。如果先导入的不是第 1 卷，后续第 1 卷进入时会覆盖系列封面；非第 1 卷不会覆盖已有系列封面。
- **系列简介**：不强制写入。Komga 中系列介绍可以为空，避免把某一卷简介误当成系列简介。
- **单本封面**：每本书使用外部元数据封面，写成 Komga 可识别的同名 `.jpg` sidecar。
- **单本信息**：每本书的标题、卷号、作者、出版社、简介、ISBN、来源 URL 等以第一个达标来源为准：BookWalker 台湾优先，其次 BookWalker 日本，最后 Bangumi。
- **卷号**：卷号以源文件名/单本 LLM 解析为准。外部平台返回的卷号不会覆盖源文件卷号，避免整套书刮到同一个平台条目时全部变成第 1 卷。
- **命名**：导入 Komga 的文件名使用 `系列名 v001.kepub.epub` 这种稳定排序格式；显示标题写入元数据为 `系列名 卷1`。
- **检索候选**：BookWalker 台湾查询前会把中文标题转换为台繁；LLM 开启时会额外提供台版/日版正式名和查询别名，例如 `DNA` -> `D・N・A2`。
- **同卷替换**：如果已入库同系列同卷，再放入更大的源文件，管线会把旧 CBZ/KEPUB/封面 sidecar 移到 `processing/replacement-backups` 后重新生成并导入。新源文件不大于旧归档时会拒绝替换，避免低质量文件覆盖高质量版本。

## 目录约定

默认数据根目录由 `.env` 中的 `DATA_ROOT` 控制，默认是 `/srv/ebooks`。

| 宿主机目录 | 容器目录 | 用途 |
|---|---|---|
| `${DATA_ROOT}/inbox` | `/data/inbox` | 放入待处理漫画 |
| `${DATA_ROOT}/archive_cbz` | `/data/archive_cbz` | 归一化后的 CBZ 存档 |
| `${DATA_ROOT}/kepub_ready` | `/data/kepub_ready` | KCC 临时输出目录 |
| `${DATA_ROOT}/komga-library` | `/data/komga-library` | Komga 书库根目录 |
| `${DATA_ROOT}/pipeline-state` | `/data/state` | 管线 SQLite 状态库 |
| `${DATA_ROOT}/manual-review` | `/data/manual-review` | 低置信度文件人工确认 |
| `${DATA_ROOT}/logs` | `/data/logs` | 管线日志 |
| `${DATA_ROOT}/komga-config` | `/config` | Komga 配置和数据库 |

## 快速开始

### 1. 准备目录

```bash
sudo mkdir -p /srv/ebooks/{inbox,processing,archive_cbz,kepub_ready,komga-library,pipeline-state,manual-review,logs,komga-config}
sudo chown -R "$USER:$USER" /srv/ebooks
```

### 2. 克隆项目

```bash
git clone https://github.com/Paucluse/manga-kobo-pipeline.git
cd manga-kobo-pipeline
```

### 3. 创建配置

```bash
cp .env.example .env
cp config.example.yaml config.yaml
```

默认配置已经适合 Komga + Kobo Sage。如果需要修改数据根目录，编辑 `.env`：

```env
DATA_ROOT=/srv/ebooks
KOMGA_PORT=25600
MANGA_PIPELINE_LOG_LEVEL=INFO
```

Compose 同时暴露 Komga 的 Kobo Sync 端口 `25601`。如果 Kobo 阅读器需要同步，请确认设备访问的是宿主机的 `25601` 端口，并且 Komga 端已启用 Kobo 代理/API key。

## Web 控制台

从最新版本开始，管线内置了功能完整的 React 前端控制台。你可以在网页端直观地完成原本依赖 CLI 的手动干预和重刮削操作。

主要功能：
- **实时概览**：管线各状态分布统计，随时掌握任务进度。
- **检索与管理**：搜索所有文件的处理记录，查看详细元数据、归档路径和置信度。
- **可视化重刮削**：如果自动解析出错（如 D.N.A2 被拆分或识别错误），可在详情页中手工输入关键词搜索外部平台（BookWalker/Bangumi），选中正确候选后一键重新提取并应用。
- **快捷纠错与入库**：支持页面内直接修改字段并重新推进入库，或重置状态让管线彻底从头处理。
- **运行设置**：切换全自动模式或人工审核模式，支持执行批量重新刮削。

**访问方法**：
控制台默认暴露在 `8080` 端口（可在 `.env` 修改 `PIPELINE_WEB_PORT`）。启动服务后在浏览器中访问：
```text
http://<你的服务器IP>:8080
```
首次访问需根据提示创建管理员账号，之后方可登录操作。

## LLM 功能说明

管线提供三类 LLM 调用，均使用同一个 OpenAI 兼容接口（推荐 Gemini Flash）。

### 功能一：合集系列锚点

当待处理文件位于 `inbox/<合集目录>/` 下时，管线会先对合集目录做一次 LLM 解析：

- 输入：父目录名和该目录下的文件名列表。
- 输出：整套书共用的正式系列名、繁中/日文标题、别名、作者、出版社提示和分平台检索词。
- 存储：写入 SQLite 的 `series_anchors` 表，同一个合集目录后续卷复用同一个锚点。

如果开启了 `llm_normalize_enabled`，但合集锚点无法生成，管线会把任务置为 `needs_review`，不会退回本地正则解析继续刮削。

### 功能二：文件名归一化（`llm_normalize_enabled`）

LLM 会直接理解原始文件名，输出：

- `clean_title`：剔除扫描组、出版社、格式标记后的纯系列名
- `titles.traditional_chinese`：台版繁体正式书名
- `titles.japanese`：日文原名
- `scraping_queries.bookwalker_tw`：专门用于 BookWalker 台湾的检索词列表
- `scraping_queries.bookwalker_jp`：专门用于 BookWalker 日本的检索词列表
- `scraping_queries.bangumi`：专门用于 Bangumi 的检索词列表
- `parse_status`：`ok / ambiguous / insufficient`
- `warnings`：解析风险提示

这些结果会直接喂给下游刮削器作为搜索词。开启 LLM 时，管线不会把本地正则解析当作兜底刮削依据；LLM 连接失败或无法解析会进入 `needs_review`。

### 功能三：刮削结果 LLM 验证（`llm_verify_scrape_enabled`）

在每个刮削平台（BookWalker TW / JP / Bangumi）返回结果之后，LLM 会额外判断：
**刮削结果描述的是否与原始文件同一部作品？**

- 如果 LLM 认为不匹配（如系列名换了、卷号差异过大），该候选会被拒绝，管线继续尝试下一个平台。
- 如果开启了验证但候选无法得到 LLM 确认，该候选会被拒绝。
- 如果 LLM 高置信确认匹配，候选置信度可以提升到平台阈值以上，避免代码相似度评分误伤跨语言正式标题。

此功能会额外消耗 API 调用次数（每本书最多 3 次），但可显著减少错误刮削。
Gemini Flash Lite 免费层通常有充裕余量。

LLM 请求支持重试；默认最多 3 次，按线性 backoff 等待。

### 配置 LLM

先创建本地密钥文件：

```bash
mkdir -p secrets
chmod 700 secrets
echo "your_api_key_here" > secrets/gemini_api_key
chmod 600 secrets/gemini_api_key
```

`secrets/gemini_api_key` 只写 API key 本身，不要写 `GEMINI_API_KEY=`。然后在
`config.yaml` 中设置：

```yaml
metadata:
  # 功能一：文件名归一化（推荐开启）
  llm_normalize_enabled: true

  # 功能二：刮削后验证（可选，会额外消耗 API 调用）
  llm_verify_scrape_enabled: false

  llm_base_url: https://generativelanguage.googleapis.com/v1beta/openai
  llm_model: gemini-3.1-flash-lite
  llm_api_key_file: /run/secrets/gemini_api_key
  llm_api_key_env: GEMINI_API_KEY  # 备用环境变量，优先使用文件
  llm_timeout_seconds: 30
  llm_max_retries: 3
  llm_retry_backoff_seconds: 2.0
  llm_verify_accept_confidence: 0.7
```

密钥文件会只读挂载进容器。不要把密钥写进仓库。

### 4. 启动

```bash
docker compose up -d --build
```

查看服务：

```bash
docker compose ps
```

Komga 默认访问地址：

```text
http://localhost:25600
```

首次进入 Komga 后创建账号和库，库根目录选择容器内的：

```text
/data
```

这个路径对应宿主机的：

```text
${DATA_ROOT}/komga-library
```

### 5. 导入漫画

把漫画放入：

```text
${DATA_ROOT}/inbox
```

管线默认持续监听。也可以手动触发：

```bash
docker compose exec manga-pipeline manga-pipeline process
```

也可以把一个合集目录放入 `inbox`。父目录名会作为系列名，目录下每个一层子项会作为一本书处理：

```text
inbox/
  蒼藍鋼鐵戰艦/
    1.zip
    2.cbz
    蒼藍鋼鐵戰艦 第03卷.pdf
    4/
      001.jpg
      002.jpg
```

其中 `1.zip`、`2.cbz` 这种纯数字文件名会按卷号处理；`4/` 这种直接包含图片的子目录会先打包成单本 CBZ。只处理这一层，不处理 `卷号/章节/图片` 这种多层目录。

合集目录名是系列锚点的主要依据。建议一套书放在同一个父目录下，即使单本文件名不规范，也让 LLM 通过目录名和整套文件列表判断系列。刮削完成后，管线才会按锚点和卷号生成规范文件名。

查看日志：

```bash
docker compose logs -f manga-pipeline
```

查看状态：

```bash
docker compose exec manga-pipeline manga-pipeline status
```

## 支持格式

| 输入格式 | 说明 |
|---|---|
| `.cbz`, `.zip` | 直接归一化为 CBZ |
| `.cbr`, `.rar` | 解压后重新打包为 CBZ |
| `.7z` | 解压后重新打包为 CBZ |
| `.pdf` | 默认使用 `pdfimages` 抽取内嵌图片，再打包为 CBZ |

输出到 Komga 的文件为：

```text
系列名/系列名 v001.kepub.epub
系列名/系列名 v001.kepub.jpg
系列名/cover.jpg
```

其中 `cover.jpg` 是系列封面，优先第 1 卷；`*.kepub.jpg` 是单本封面。

## 文件名建议

推荐格式：

```text
[作者] 系列名 第01卷.cbz
系列名 v001.cbz
系列名 卷1.zip
```

元数据源命中后会覆盖文件名里的简体/非官方标题。例如 `苍蓝钢铁战舰 卷1.cbz` 命中 BookWalker 台湾后会规范成 `蒼藍鋼鐵戰艦 v001.kepub.epub`，显示标题为 `蒼藍鋼鐵戰艦 卷1`。

## 配置说明

核心配置在 `config.yaml`：

```yaml
kobo:
  profile: KoS
  format: KEPUB
  manga_style: true
  high_quality: true

pdf:
  enabled: true
  strategy: extract_first
  render_fallback: false
  preserve_original: true
  dpi: 180
  image_format: jpg
  jpeg_quality: 92

metadata:
  default_language: zho
  confidence_auto_accept: 0.4
  bookwalker_tw_enabled: true
  bookwalker_tw_min_confidence: 0.65
  bookwalker_tw_max_candidates: 8
  bookwalker_jp_enabled: true
  bookwalker_jp_min_confidence: 0.65
  bookwalker_jp_max_candidates: 8
  bangumi_enabled: true
  bangumi_min_confidence: 0.65
  bangumi_max_candidates: 8
  download_bookwalker_covers: true
  llm_normalize_enabled: true
  llm_base_url: https://generativelanguage.googleapis.com/v1beta/openai
  llm_model: gemini-3.1-flash-lite
  llm_api_key_file: /run/secrets/gemini_api_key
  llm_api_key_env: GEMINI_API_KEY
  llm_timeout_seconds: 30
  llm_max_retries: 3
  llm_retry_backoff_seconds: 2.0
  llm_verify_accept_confidence: 0.7

komga:
  base_uri: http://komga:25600
  user: admin@manga.local
  password: changeme
  library_id: ""
```

`komga.user` 和 `komga.password` 只用于触发 Komga 扫描。实际部署时请在本地 `config.yaml` 中改成你的 Komga 账号，不要提交该文件。

## CLI

| 命令 | 用途 |
|---|---|
| `manga-pipeline doctor` | 检查目录和工具 |
| `manga-pipeline scan` | 只扫描 inbox |
| `manga-pipeline process` | 扫描并处理所有待处理文件 |
| `manga-pipeline run` | 持续监听 inbox |
| `manga-pipeline status` | 查看处理状态 |
| `manga-pipeline retry --id N` | 重试失败任务 |
| `manga-pipeline rescrape --id N` | 重新刮削已入库记录 |
| `manga-pipeline rescrape --all` | 重新刮削全库已入库记录 |
| `manga-pipeline dry-run FILE` | 预览文件名解析结果 |

Docker 中运行示例：

```bash
docker compose exec manga-pipeline manga-pipeline status
```

重新刮削示例：

```bash
# 先预览固定几本，不写数据库和文件
docker compose exec manga-pipeline manga-pipeline rescrape --id 11 --id 12 --dry-run

# 更新固定几本的数据库、CBZ ComicInfo、KEPUB OPF 和封面 sidecar
docker compose exec manga-pipeline manga-pipeline rescrape --id 11 --id 12

# 按标题/系列模糊匹配
docker compose exec manga-pipeline manga-pipeline rescrape --title 三只眼

# 全库重新刮削
docker compose exec manga-pipeline manga-pipeline rescrape --all
```

默认只处理状态为 `done` 的已入库记录。`--relocate` 会把已入库的 KEPUB 移动到重新命中的规范系列目录和文件名；不加这个参数时只改元数据，不移动文件。

## 开发

安装依赖：

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[full]"
```

运行测试：

```bash
pytest
ruff check src tests
```

## 注意事项

- `data/`、`.env`、`config.yaml`、数据库和日志不会提交到仓库。
- BookWalker 台湾没有可接受条目时，管线会尝试 BookWalker 日本；日本站也没有达标时再尝试 Bangumi。三层都没有达标时，未开启 LLM 验证的任务会保留文件名/LLM 解析结果；开启 LLM 验证的任务会进入 `needs_review`。
- 开启 LLM 文件名归一化时，LLM 是刮削参数的来源。LLM 不可用时任务会进入 `needs_review`，不会用本地正则结果继续自动入库。
- 合集目录会生成持久化系列锚点；如果需要让同一个目录重新学习系列名，需要清理 `series_anchors` 中对应 `collection_title` 后再重跑。
- 刮削结果是否接受由 LLM 验证和平台阈值共同决定；代码相似度只负责平台候选排序和基础阈值，不再强行判断跨语言标题是否同一作品。
- PDF 默认用 `pdfimages` 直接抽取内嵌图片，避免整页重渲染导致速度慢和体积暴涨。
- `pdftoppm` 只作为显式启用的渲染 fallback；默认不自动回退。
- LLM 负责文件名/目录归一化、检索候选生成和刮削结果验证；外部书籍元数据仍来自 BookWalker 台湾、BookWalker 日本或 Bangumi。
- 同一个文件内容会按 SHA-256 去重，重复放入不会再次处理。若要替换已入库同卷，请放入更大的新源文件。

## License

MIT
