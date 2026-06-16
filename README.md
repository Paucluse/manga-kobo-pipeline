# manga-kobo-pipeline

面向 Kobo 阅读器的中文漫画自动处理管线。

把 ZIP / CBZ / RAR / CBR / 7Z 漫画放入 `inbox` 后，管线会自动完成：

1. 解析文件名，识别系列、卷号、作者。
2. 可选用 LLM 对文件名结果做前置规范化。
3. 使用 BookWalker 台湾检索繁体中文元数据、封面、作者、出版社、ISBN、简介。
4. 归一化为 CBZ，并写入 `ComicInfo.xml`。
5. 使用 KCC 转换为 Kobo 适用的 `*.kepub.epub`。
6. 写入 EPUB/KEPUB 内置 OPF 元数据。
7. 导入 Komga 书库目录并触发 Komga 扫描。

项目不依赖 Komf。元数据来源以 BookWalker 台湾为准。

## 元数据规则

- **系列名称**：优先使用 BookWalker 台湾返回的系列名，例如 `蒼藍鋼鐵戰艦`。
- **系列封面**：使用 BookWalker 封面生成 Komga 本地 `cover.jpg`。如果先导入的不是第 1 卷，后续第 1 卷进入时会覆盖系列封面；非第 1 卷不会覆盖已有系列封面。
- **系列简介**：不强制写入。Komga 中系列介绍可以为空，避免把某一卷简介误当成系列简介。
- **单本封面**：每本书使用它自己在 BookWalker 台湾页面上的封面，写成 Komga 可识别的同名 `.jpg` sidecar。
- **单本信息**：每本书的标题、卷号、作者、出版社、简介、ISBN、来源 URL 等以实际 BookWalker 台湾条目为准。
- **命名**：导入 Komga 的文件名使用 `系列名 v001.kepub.epub` 这种稳定排序格式；显示标题写入元数据为 `系列名 卷1`。

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

如果要开启 LLM 文件名规范化，在 `config.yaml` 中设置：

```yaml
metadata:
  llm_normalize_enabled: true
  llm_model: gpt-4.1-mini
  llm_api_key_env: OPENAI_API_KEY
```

然后在运行环境中提供对应 API key。不要把密钥写进仓库。

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

BookWalker 台湾命中后会覆盖文件名里的简体/非官方标题。例如 `苍蓝钢铁战舰 卷1.cbz` 会规范成 `蒼藍鋼鐵戰艦 v001.kepub.epub`，显示标题为 `蒼藍鋼鐵戰艦 卷1`。

## 配置说明

核心配置在 `config.yaml`：

```yaml
kobo:
  profile: KoS
  format: KEPUB
  manga_style: true
  high_quality: true

metadata:
  default_language: zho
  confidence_auto_accept: 0.4
  bookwalker_tw_enabled: true
  bookwalker_tw_min_confidence: 0.65
  bookwalker_tw_max_candidates: 8
  download_bookwalker_covers: true
  llm_normalize_enabled: false
  llm_base_url: https://api.openai.com/v1
  llm_model: ""
  llm_api_key_env: OPENAI_API_KEY

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
| `manga-pipeline dry-run FILE` | 预览文件名解析结果 |

Docker 中运行示例：

```bash
docker compose exec manga-pipeline manga-pipeline status
```

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
- BookWalker 台湾没有对应条目时，管线会保留文件名解析结果。
- LLM 只做文件名前置规范化，不直接替代 BookWalker 台湾的书籍元数据。
- 同一个文件内容会按 SHA-256 去重，重复放入不会再次处理。

## License

MIT
