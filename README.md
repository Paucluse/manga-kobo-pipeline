# manga-kobo-pipeline

自动化日本漫画处理管线：扫描 → 解析 → 归一化 → 转换 (KCC) → 导入 (Calibre)，专为 **Kobo Sage** 优化。

## 功能特性

- **自动扫描** — 监听 inbox 目录，发现新漫画文件
- **文件名解析** — 智能识别日本漫画文件名中的作者、标题、卷号
- **格式归一化** — 支持 ZIP/CBZ/RAR/CBR/7Z 自动转换为统一 CBZ
- **KCC 转换** — 通过 [KCC (Kindle Comic Converter)](https://github.com/ciromattia/kcc) 转换为 Kobo Sage 兼容的 KEPUB
- **Calibre 导入** — 自动调用 `calibredb` 导入书库并设置元数据
- **幂等处理** — SHA-256 文件哈希去重，同一文件不会重复处理
- **失败重试** — 自动重试 + 手动 `retry` 命令
- **人工审核** — 低信心度文件自动移至审核目录

## 快速开始

### Docker 部署（推荐）

```bash
# 1. 复制配置
cp config.example.yaml config.yaml
cp .env.example .env

# 2. 编辑 config.yaml，设置你的路径

# 3. 启动
docker compose up -d

# 4. 检查环境
docker compose exec pipeline manga-pipeline doctor

# 5. 手动扫描+处理
docker compose exec pipeline manga-pipeline process
```

### 本地开发

```bash
# 安装
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows
pip install -e ".[dev]"

# 运行测试
pytest -v

# 代码检查
ruff check src/ tests/

# 查看帮助
manga-pipeline --help
```

## CLI 命令

| 命令 | 说明 |
|------|------|
| `manga-pipeline doctor` | 检查环境配置（目录、KCC、Calibre） |
| `manga-pipeline scan` | 扫描 inbox 发现新文件 |
| `manga-pipeline process` | 扫描并处理所有待处理文件 |
| `manga-pipeline run` | 持续监听 inbox 并自动处理（守护模式） |
| `manga-pipeline status` | 显示各状态的文件数量 |
| `manga-pipeline retry --id N` | 重试一个失败的任务 |
| `manga-pipeline dry-run FILE` | 预览处理流程（不执行） |

## 处理流水线

```
inbox/
  └── [桜場コハル] みなみけ 第01巻.cbz
         │
         ▼
    ┌─────────────┐
    │  1. 扫描发现  │  scan_inbox() — 计算 SHA-256, 注册到 SQLite
    └──────┬──────┘
           ▼
    ┌─────────────┐
    │ 2. 稳定性检查 │  check_file_stable_quick() — 确认下载完成
    └──────┬──────┘
           ▼
    ┌─────────────┐
    │ 3. 文件名解析 │  parse_filename() — 提取作者/标题/卷号
    └──────┬──────┘
           │
           ├── 信心度 ≥ 0.85 ──→ 继续
           │
           └── 信心度 < 0.85 ──→ manual-review/ (人工审核)
           │
           ▼
    ┌─────────────┐
    │ 4. 格式归一化 │  normalize_to_cbz() — RAR/7Z → CBZ
    └──────┬──────┘
           ▼
    ┌─────────────┐
    │  5. KCC 转换  │  run_kcc() — CBZ → KEPUB/EPUB (Kobo Sage)
    └──────┬──────┘
           ▼
    ┌─────────────┐
    │ 6. Calibre   │  run_calibredb_add() — 导入书库 + 元数据
    │    导入       │
    └──────┬──────┘
           ▼
        ✅ DONE
```

## 支持的文件格式

| 格式 | 扩展名 | 处理方式 |
|------|--------|----------|
| CBZ/ZIP | `.cbz`, `.zip` | 直接复制（已是目标格式） |
| CBR/RAR | `.cbr`, `.rar` | 解压后重新打包为 CBZ |
| 7Z | `.7z` | 解压后重新打包为 CBZ |

## 文件名解析示例

| 文件名 | 作者 | 标题 | 卷号 | 信心度 |
|--------|------|------|------|--------|
| `[桜場コハル] みなみけ 第01巻.cbz` | 桜場コハル | みなみけ | 1 | 1.00 |
| `みなみけ 第01巻.zip` | — | みなみけ | 1 | 0.70 |
| `みなみけ v01.cbz` | — | みなみけ | 1 | 0.70 |
| `[author] title vol.01.cbz` | author | title | 1 | 1.00 |
| `ダンジョン飯 01.cbz` | — | ダンジョン飯 | 1 | 0.70 |
| `よつばと! 第001巻.cbz` | — | よつばと! | 1 | 0.70 |

## 配置

配置文件 `config.yaml` 示例：

```yaml
paths:
  inbox: /data/inbox
  processing: /data/processing
  archive_cbz: /data/archive_cbz
  kepub_ready: /data/kepub_ready
  calibre_library: /data/calibre-library
  state: /data/state
  manual_review: /data/manual-review
  logs: /data/logs

kobo:
  profile: KoS          # Kobo Sage
  format: EPUB
  manga_style: true      # 右到左
  high_quality: true

metadata:
  default_language: jpn
  confidence_auto_accept: 0.85
  default_tags:
    - manga
    - japanese
    - kobo-sync

processing:
  stable_check_seconds: 30
  enable_delete_original: false   # 安全第一
  max_retries: 3
```

### 配置加载顺序

1. `MANGA_PIPELINE_CONFIG` 环境变量指定的路径
2. 当前目录下的 `config.yaml`
3. `/app/config.yaml` (Docker 默认)
4. 全部使用默认值

## 目录结构

```
manga-kobo-pipeline/
├── src/manga_pipeline/
│   ├── __init__.py
│   ├── main.py              # 入口点
│   ├── cli.py               # Typer CLI (7 个命令)
│   ├── config.py            # Pydantic 配置模型
│   ├── logging_config.py    # 日志配置
│   ├── models.py            # 数据模型 + 状态枚举
│   ├── database.py          # SQLite 状态管理
│   ├── utils.py             # SHA-256 哈希等工具
│   ├── scanner.py           # Inbox 扫描器
│   ├── stability.py         # 文件稳定性检查
│   ├── filename_parser.py   # 文件名解析 (日本漫画)
│   ├── normalizer.py        # 格式归一化
│   ├── review.py            # 人工审核
│   ├── kcc.py               # KCC 命令封装
│   ├── calibre.py           # calibredb 命令封装
│   ├── pipeline.py          # 流水线编排
│   ├── watcher.py           # Watchdog 文件监听
│   └── comicinfo.py         # ComicInfo.xml 生成
├── tests/
│   ├── conftest.py
│   ├── test_config.py       # 13 tests
│   ├── test_filename_parser.py  # 35 tests
│   ├── test_state_machine.py    # 11 tests
│   ├── test_kcc_command.py      # 10 tests
│   └── test_calibre_command.py  # 13 tests
├── config.example.yaml
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
└── README.md
```

## 开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest -v

# 代码检查
ruff check src/ tests/

# 自动修复
ruff check --fix src/ tests/
```

## 依赖

| 包 | 用途 |
|----|------|
| typer | CLI 框架 |
| pydantic | 配置验证 |
| pyyaml | YAML 配置文件 |
| rich | 美化终端输出 |
| watchdog | 文件系统监听 |
| rarfile | RAR/CBR 解压 |
| py7zr | 7Z 解压（可选） |

## License

MIT
