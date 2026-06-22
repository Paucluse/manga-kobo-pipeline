# manga-kobo-pipeline

面向 Kobo 阅读器的中文漫画自动处理管线。

把 ZIP / CBZ / RAR / CBR / 7Z 漫画放入 `inbox` 后，管线会自动完成：

1. 解析文件名，识别系列、卷号、作者。
2. 可选用 LLM 对文件名结果做前置规范化，并生成台版/日版检索标题候选。
3. 使用 BookWalker 台湾检索繁体中文元数据、封面、作者、出版社、ISBN、简介。
   BookWalker 台湾无可接受匹配时尝试 BookWalker 日本；两者都失败时使用 Bangumi 兜底。
4. 归一化为 CBZ，并写入 `ComicInfo.xml`。
5. 使用 KCC 转换为 Kobo 适用的 `*.kepub.epub`。
6. 写入 EPUB/KEPUB 内置 OPF 元数据。
7. 导入 Komga 书库目录并触发 Komga 扫描。

项目不依赖 Komf。元数据来源链路为 BookWalker 台湾 -> BookWalker 日本 -> Bangumi。

## 元数据规则

- **系列名称**：优先使用达标元数据源返回的正式系列名，例如 `蒼藍鋼鐵戰艦` 或日版正式名。合集目录名只作为检索和无命中时的兜底解析来源。
- **系列封面**：使用外部元数据封面生成 Komga 本地 `cover.jpg`。如果先导入的不是第 1 卷，后续第 1 卷进入时会覆盖系列封面；非第 1 卷不会覆盖已有系列封面。
- **系列简介**：不强制写入。Komga 中系列介绍可以为空，避免把某一卷简介误当成系列简介。
- **单本封面**：每本书使用外部元数据封面，写成 Komga 可识别的同名 `.jpg` sidecar。
- **单本信息**：每本书的标题、卷号、作者、出版社、简介、ISBN、来源 URL 等以第一个达标来源为准：BookWalker 台湾优先，其次 BookWalker 日本，最后 Bangumi。
- **命名**：导入 Komga 的文件名使用 `系列名 v001.kepub.epub` 这种稳定排序格式；显示标题写入元数据为 `系列名 卷1`。
- **检索候选**：BookWalker 台湾查询前会把中文标题转换为台繁；LLM 开启时会额外提供台版/日版正式名和查询别名，例如 `DNA` -> `D・N・A2`。

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

如果要开启 LLM 文件名规范化，推荐使用 Google AI Studio / Gemini API。
先创建本地密钥文件：

```bash
mkdir -p secrets
chmod 700 secrets
nano secrets/gemini_api_key
chmod 600 secrets/gemini_api_key
```

`secrets/gemini_api_key` 只写 API key 本身，不要写 `GEMINI_API_KEY=`。然后在
`config.yaml` 中设置：

```yaml
metadata:
  llm_normalize_enabled: true
  llm_base_url: https://generativelanguage.googleapis.com/v1beta/openai
  llm_model: gemini-3.1-flash-lite
  llm_api_key_file: /run/secrets/gemini_api_key
  llm_api_key_env: GEMINI_API_KEY
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
- BookWalker 台湾没有可接受条目时，管线会尝试 BookWalker 日本；日本站也没有达标时再尝试 Bangumi；都没有达标时才保留文件名解析结果。
- PDF 默认用 `pdfimages` 直接抽取内嵌图片，避免整页重渲染导致速度慢和体积暴涨。
- `pdftoppm` 只作为显式启用的渲染 fallback；默认不自动回退。
- LLM 只做文件名前置规范化和检索候选生成，不直接替代外部书籍元数据。
- 同一个文件内容会按 SHA-256 去重，重复放入不会再次处理。

## License

MIT
