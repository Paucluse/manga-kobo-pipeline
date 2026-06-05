# manga-kobo-pipeline

自动化中文漫画处理管线：**扫描 → 解析 → 归一化 → KCC 转换 → Calibre 导入**，专为 **Kobo Sage** 电子墨水屏优化。

把漫画丢进 `inbox` 文件夹，剩下的全自动完成 — 包括转换为 Kobo 专用格式、导入 Calibre 书库、通过 Calibre-Web 在线浏览，以及同步到 Kobo Sage 阅读器。

---

## 目录

1. [功能概览](#功能概览)
2. [系统要求](#系统要求)
3. [第一步：安装 Docker](#第一步安装-docker)
4. [第二步：创建目录结构](#第二步创建目录结构)
5. [第三步：下载项目](#第三步下载项目)
6. [第四步：配置项目](#第四步配置项目)
7. [第五步：启动服务](#第五步启动服务)
8. [第六步：放入测试漫画](#第六步放入测试漫画)
9. [第七步：确认 KCC 转换成功](#第七步确认-kcc-转换成功)
10. [第八步：确认 Calibre 导入成功](#第八步确认-calibre-导入成功)
11. [第九步：配置 Calibre-Web](#第九步配置-calibre-web)
12. [第十步：Kobo Sage 同步](#第十步kobo-sage-同步)
13. [CLI 命令参考](#cli-命令参考)
14. [处理流水线](#处理流水线)
15. [支持的文件格式](#支持的文件格式)
16. [文件名解析示例](#文件名解析示例)
17. [配置文件详解](#配置文件详解)
18. [日志查看](#日志查看)
19. [常见错误排查表](#常见错误排查表)
20. [项目结构](#项目结构)
21. [开发指南](#开发指南)
22. [License](#license)

---

## 功能概览

| 功能 | 说明 |
|------|------|
| 🔍 自动扫描 | 监听 inbox 目录，发现新漫画文件 |
| 📝 文件名解析 | 智能识别中文/日文漫画文件名中的作者、标题、卷号 |
| 📦 格式归一化 | ZIP / CBZ / RAR / CBR / 7Z → 统一 CBZ |
| 🔄 KCC 转换 | 通过 KCC 转为 Kobo Sage 兼容的 KEPUB |
| 📚 Calibre 导入 | 自动调用 calibredb 导入书库并设置元数据 |
| 🔒 幂等处理 | SHA-256 文件哈希去重，同一文件不重复处理 |
| 🔁 失败重试 | 自动重试 + 手动 retry 命令 |
| 👁️ 人工审核 | 低信心度文件自动移至审核目录 |

---

## 系统要求

- **操作系统**：Ubuntu 22.04 / 24.04（推荐）或任何支持 Docker 的 Linux
- **内存**：≥ 2 GB
- **硬盘**：≥ 10 GB 可用空间（视漫画量而定）
- **网络**：首次部署需联网下载 Docker 镜像

---

## 第一步：安装 Docker

> 以下命令需要在 Ubuntu 终端中以普通用户运行（部分命令需要 `sudo`）。
> 如果你的 Ubuntu 已经安装了 Docker，可以跳到 [第二步](#第二步创建目录结构)。

### 1.1 更新系统并安装必要工具

```bash
sudo apt update
sudo apt install -y ca-certificates curl gnupg
```

### 1.2 添加 Docker 官方 GPG 密钥

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
```

### 1.3 添加 Docker 软件源

```bash
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
```

### 1.4 安装 Docker Engine 和 Docker Compose

```bash
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

### 1.5 让当前用户可以免 sudo 使用 Docker

```bash
sudo usermod -aG docker $USER
```

> ⚠️ **重要**：执行完上面的命令后，你需要**退出终端并重新登录**（或重启电脑），这个改动才会生效。

### 1.6 验证 Docker 安装成功

重新登录后，运行：

```bash
docker --version
docker compose version
```

你应该看到类似输出：

```
Docker version 27.x.x, build xxxxxxx
Docker Compose version v2.x.x
```

如果两条命令都有正确输出，说明 Docker 安装成功。

---

## 第二步：创建目录结构

我们把所有数据放在 `/srv/ebooks` 下面。运行：

```bash
sudo mkdir -p /srv/ebooks/{inbox,processing,archive_cbz,kepub_ready,calibre-library,pipeline-state,manual-review,logs,calibre-web-config}
```

然后把目录的所有权改为你的用户（这样不需要 root 就能往里放文件）：

```bash
sudo chown -R $USER:$USER /srv/ebooks
```

### 验证

```bash
ls -la /srv/ebooks/
```

你应该看到 9 个子目录：

```
inbox/
processing/
archive_cbz/
kepub_ready/
calibre-library/
pipeline-state/
manual-review/
logs/
calibre-web-config/
```

---

## 第三步：下载项目

### 方式 A：从 GitHub 克隆（推荐）

```bash
cd /srv/ebooks
git clone https://github.com/你的用户名/manga-kobo-pipeline.git
cd manga-kobo-pipeline
```

### 方式 B：手动上传

如果项目代码在你的 Windows 电脑上，你可以用 `scp` 上传到 Ubuntu：

```bash
# 在 Windows PowerShell 中运行（替换 ubuntu-ip 为你的 Ubuntu IP 地址）：
scp -r d:\Antigravity\manga-kobo-pipeline 你的用户名@ubuntu-ip:/srv/ebooks/
```

然后在 Ubuntu 上：

```bash
cd /srv/ebooks/manga-kobo-pipeline
```

---

## 第四步：配置项目

### 4.1 复制配置文件模板

```bash
cp config.example.yaml config.yaml
cp .env.example .env
```

### 4.2 编辑 .env（通常不需要改）

```bash
nano .env
```

默认值已经适合大多数情况。如果你想更改数据目录位置或 Calibre-Web 端口，可以修改：

```env
# 数据根目录（默认 /srv/ebooks）
DATA_ROOT=/srv/ebooks

# Calibre-Web 网页端口（默认 8083）
CALIBRE_WEB_PORT=8083

# 日志级别（默认 INFO，调试时改为 DEBUG）
MANGA_PIPELINE_LOG_LEVEL=INFO
```

> 💡 按 `Ctrl+O` 保存，`Ctrl+X` 退出 nano 编辑器。

### 4.3 确认 config.yaml（通常不需要改）

配置文件已预设为中文翻译版日漫模式。如果你不需要特殊修改，可以跳过这步。

关键配置说明：

```yaml
kobo:
  profile: KoS            # Kobo Sage 设备配置，不要改
  manga_style: true        # 右到左阅读（日漫标准），不要改

metadata:
  default_language: zho    # 中文（已设好）
  default_tags:
    - manga
    - chinese-translation
    - kobo-sync

processing:
  enable_delete_original: false   # 安全模式：不删除原始文件
```

---

## 第五步：启动服务

### 5.1 构建并启动

```bash
cd /srv/ebooks/manga-kobo-pipeline
docker compose up -d --build
```

> ⏳ **首次构建大约需要 5-15 分钟**（需要下载 Python 基础镜像、安装 Calibre、KCC 等）。
> 后续启动不需要 `--build`，几秒就能启动。

### 5.2 查看构建进度

```bash
docker compose logs -f
```

按 `Ctrl+C` 退出日志查看（不会停止服务）。

### 5.3 确认服务正在运行

```bash
docker compose ps
```

你应该看到两个服务都是 `Up` 状态：

```
NAME              STATUS
manga-pipeline    Up (healthy)
calibre-web       Up
```

### 5.4 检查管线环境

```bash
docker compose exec manga-pipeline manga-pipeline doctor
```

这会检查所有必要的目录和工具是否就绪。你应该看到全部绿色 ✓。

### 5.5 启动 / 停止 / 重启

```bash
# 停止所有服务
docker compose down

# 重新启动（不重新构建）
docker compose up -d

# 重新构建并启动（修改了代码后才需要）
docker compose up -d --build

# 只重启管线（不动 Calibre-Web）
docker compose restart manga-pipeline
```

---

## 第六步：放入测试漫画

### 6.1 准备一个测试文件

找一个你下载好的漫画文件，比如：

```
[尾田栄一郎] 海贼王 第01卷.cbz
```

### 6.2 复制到 inbox 目录

```bash
# 方法 1：直接复制（Ubuntu 本机操作）
cp "/path/to/你的漫画文件.cbz" /srv/ebooks/inbox/

# 方法 2：从 Windows 通过 scp 上传
# 在 Windows PowerShell 中运行：
scp "你的漫画.cbz" 你的用户名@ubuntu-ip:/srv/ebooks/inbox/
```

### 6.3 手动触发处理（可选）

管线默认是持续监听模式，放入文件后会自动开始处理。如果你想立即处理，可以手动触发：

```bash
docker compose exec manga-pipeline manga-pipeline process
```

### 6.4 查看实时处理日志

```bash
docker compose logs -f manga-pipeline
```

你应该看到类似的输出：

```
[INFO] scanner: Discovered new file: [尾田栄一郎] 海贼王 第01卷.cbz
[INFO] pipeline: File stable: [尾田栄一郎] 海贼王 第01卷.cbz
[INFO] pipeline: Parsed: title=海贼王, author=尾田栄一郎, vol=1 (confidence=1.00)
[INFO] pipeline: Archived: [尾田栄一郎] 海贼王 第01卷.cbz -> [尾田栄一郎] 海贼王 v01.cbz
[INFO] kcc: Running KCC: kcc-c2e -p KoS -m -q -f EPUB -o /data/kepub_ready ...
[INFO] kcc: KCC conversion successful
[INFO] calibre: Calibre import successful (book_id=1)
```

---

## 第七步：确认 KCC 转换成功

### 7.1 检查 kepub_ready 目录

```bash
ls -la /srv/ebooks/kepub_ready/
```

你应该看到一个 `.epub` 或 `.kepub.epub` 文件，比如：

```
[尾田栄一郎] 海贼王 v01.kepub.epub
```

### 7.2 通过管线状态确认

```bash
docker compose exec manga-pipeline manga-pipeline status
```

你应该看到类似的输出：

```
Pipeline Status
┌─────────────┬───────┐
│ Status      │ Count │
├─────────────┼───────┤
│ DONE        │     1 │
└─────────────┴───────┘
```

如果看到 `CONVERTED` 或 `DONE` 状态有数字，说明 KCC 转换成功了。

### 7.3 如果看到 FAILED

```bash
# 查看失败的详细信息
docker compose logs manga-pipeline | grep -i "fail\|error"
```

常见原因见底部的 [常见错误排查表](#常见错误排查表)。

---

## 第八步：确认 Calibre 导入成功

### 8.1 检查 Calibre 书库

```bash
ls /srv/ebooks/calibre-library/
```

你应该看到一个以作者名命名的目录，比如：

```
metadata.db
尾田栄一郎/
```

### 8.2 查看书库中的书籍

```bash
docker compose exec manga-pipeline calibredb list --with-library /data/calibre-library
```

你应该看到类似的输出：

```
id   title
1    海贼王
```

### 8.3 通过管线状态确认

```bash
docker compose exec manga-pipeline manga-pipeline status
```

当状态显示 `DONE` 时，说明 Calibre 导入也完成了。

---

## 第九步：配置 Calibre-Web

Calibre-Web 是一个网页版书库管理器，让你可以在浏览器中浏览和管理你的漫画。

### 9.1 打开 Calibre-Web

在浏览器中访问：

```
http://你的Ubuntu-IP:8083
```

> 💡 如果 Ubuntu 就是你当前的电脑，用 `http://localhost:8083`

### 9.2 首次配置

首次打开时会要求你设置 Calibre 书库路径。填写：

```
/books
```

> ⚠️ 注意是 `/books`，不是 `/srv/ebooks/calibre-library`。因为在 Docker 容器内部，书库被挂载到了 `/books` 路径。

### 9.3 默认登录账号

```
用户名：admin
密码：admin123
```

> ⚠️ **登录后请立即修改密码！** 点击右上角用户名 → Edit Account → 修改密码。

### 9.4 验证书库可用

登录后你应该能看到之前导入的漫画。如果看不到：

1. 点击右上角 ⚙️ → Admin → Basic Configuration
2. 确认 "Location of Calibre Database" 是 `/books`
3. 点击 Save

### 9.5 启用 Kobo 同步功能

这是让 Kobo Sage 能自动同步书籍的关键设置：

1. 点击右上角 ⚙️ → **Admin** → **Basic Configuration**
2. 找到 **Feature Configuration** 选项卡
3. **勾选** "Enable Kobo sync"
4. 点击 **Save**

然后为你的用户启用 Kobo 同步：

1. 点击右上角 ⚙️ → **Admin** → **Users**
2. 点击你的用户名（admin）
3. 找到 **Kobo Sync Token**，点击 **Create/View**
4. 你会看到一个同步 URL，形如：

```
http://你的Ubuntu-IP:8083/kobo/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

**记下这个 URL**，下一步会用到。

---

## 第十步：Kobo Sage 同步

### 10.1 确保 Kobo Sage 连接 WiFi

把 Kobo Sage 连接到和 Ubuntu 服务器相同的局域网 WiFi。

### 10.2 修改 Kobo 设备配置

1. 用 USB 线把 Kobo Sage 连接到电脑
2. Kobo 会挂载为一个磁盘（比如 `E:\`）
3. 找到文件 `.kobo/Kobo/Kobo eReader.conf`（注意 `.kobo` 是隐藏目录）
4. 用记事本打开这个文件
5. 找到 `[OneStoreServices]` 部分（如果没有就添加一段）
6. 添加或修改以下行：

```ini
[OneStoreServices]
oneStoreEnabled=false

[KoboCloudService]
api_endpoint=http://你的Ubuntu-IP:8083/kobo/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

> ⚠️ 把 URL 替换为 [第九步](#95-启用-kobo-同步功能) 中获得的 Kobo Sync Token URL。

7. 保存文件
8. 安全弹出 USB 设备

### 10.3 在 Kobo 上同步

1. 断开 USB
2. 确保 Kobo Sage 连接 WiFi
3. 在 Kobo 主界面，点击 **Sync** 按钮（循环箭头图标 🔄）
4. 等待同步完成

### 10.4 验证同步成功

同步完成后，你应该在 Kobo 的书库中看到新导入的漫画。

> 💡 首次同步可能需要稍等一会儿。如果没有看到书籍，尝试重启 Kobo 后再同步。

---

## CLI 命令参考

在终端中运行这些命令来管理管线：

| 命令 | 说明 | 使用示例 |
|------|------|----------|
| `doctor` | 检查环境配置 | `docker compose exec manga-pipeline manga-pipeline doctor` |
| `scan` | 仅扫描 inbox，不处理 | `docker compose exec manga-pipeline manga-pipeline scan` |
| `process` | 扫描 + 处理所有待处理文件 | `docker compose exec manga-pipeline manga-pipeline process` |
| `run` | 持续监听 inbox（守护模式） | 默认启动模式，无需手动运行 |
| `status` | 查看各状态的文件数量 | `docker compose exec manga-pipeline manga-pipeline status` |
| `retry --id N` | 重试一个失败的任务 | `docker compose exec manga-pipeline manga-pipeline retry --id 3` |
| `dry-run FILE` | 预览处理流程（不执行） | `docker compose exec manga-pipeline manga-pipeline dry-run /data/inbox/test.cbz` |

---

## 处理流水线

```
inbox/
  └── [尾田栄一郎] 海贼王 第01卷.cbz
         │
         ▼
    ┌─────────────┐
    │  1. 扫描发现  │  计算 SHA-256, 注册到 SQLite
    └──────┬──────┘
           ▼
    ┌─────────────┐
    │ 2. 稳定性检查 │  等待文件下载完成（30秒无变化）
    └──────┬──────┘
           ▼
    ┌─────────────┐
    │ 3. 文件名解析 │  提取：作者=尾田栄一郎 / 标题=海贼王 / 卷号=1
    └──────┬──────┘
           │
           ├── 信心度 ≥ 0.85 ──→ 继续处理
           │
           └── 信心度 < 0.85 ──→ manual-review/ (等待人工确认)
           │
           ▼
    ┌─────────────┐
    │ 4. 格式归一化 │  RAR / 7Z / ZIP → 统一 CBZ
    └──────┬──────┘
           ▼
    ┌─────────────┐
    │  5. KCC 转换  │  CBZ → KEPUB/EPUB（Kobo Sage 专用格式）
    └──────┬──────┘
           ▼
    ┌─────────────┐
    │ 6. Calibre   │  导入书库 + 写入元数据（标题/作者/标签）
    │    导入       │
    └──────┬──────┘
           ▼
        ✅ 完成！可在 Calibre-Web 中浏览，Kobo 同步
```

---

## 支持的文件格式

| 格式 | 扩展名 | 处理方式 |
|------|--------|----------|
| CBZ / ZIP | `.cbz`, `.zip` | 直接复制为 CBZ |
| CBR / RAR | `.cbr`, `.rar` | 解压后重新打包为 CBZ |
| 7Z | `.7z` | 解压后重新打包为 CBZ |
| 图片文件夹 | 文件夹内含 `.jpg`/`.png` | 打包为 CBZ |

---

## 文件名解析示例

管线同时支持中文和日文的文件名格式：

| 文件名 | 作者 | 标题 | 卷号 | 信心度 |
|--------|------|------|------|--------|
| `[尾田栄一郎] 海贼王 第01卷.cbz` | 尾田栄一郎 | 海贼王 | 1 | 1.00 |
| `进击的巨人 第01卷.cbz` | — | 进击的巨人 | 1 | 0.70 |
| `[桜場コハル] みなみけ 第01巻.cbz` | 桜場コハル | みなみけ | 1 | 1.00 |
| `一拳超人 v01.cbz` | — | 一拳超人 | 1 | 0.70 |
| `[author] title vol.01.cbz` | author | title | 1 | 1.00 |
| `海贼王 01.cbz` | — | 海贼王 | 1 | 0.70 |

> 💡 **命名技巧**：文件名格式为 `[作者] 标题 第XX卷.cbz` 时信心度最高（1.00），可以直接自动处理。
> 如果文件名不规范导致信心度低于 0.85，文件会被移入 `manual-review` 目录等待你确认。

---

## 配置文件详解

完整的 `config.yaml` 参数说明：

```yaml
# ── 路径配置 ──────────────────────────────────────────────
# Docker 环境下不需要修改（容器内路径）
paths:
  inbox: /data/inbox                   # 放入漫画的目录
  processing: /data/processing         # 处理中的临时目录
  archive_cbz: /data/archive_cbz       # 归一化后的 CBZ 存档
  kepub_ready: /data/kepub_ready       # KCC 转换后的 KEPUB
  calibre_library: /data/calibre-library  # Calibre 书库
  state: /data/state                   # SQLite 数据库（处理状态）
  manual_review: /data/manual-review   # 低信心度文件等待人工确认
  logs: /data/logs                     # 日志文件

# ── Kobo 设备配置 ──────────────────────────────────────────
kobo:
  profile: KoS            # KoS = Kobo Sage（不要改）
  format: EPUB             # 输出格式（不要改）
  manga_style: true        # 右到左阅读（日漫标准，不要改）
  high_quality: true       # 高质量转换

# ── 元数据默认值 ──────────────────────────────────────────
metadata:
  default_language: zho              # 中文
  confidence_auto_accept: 0.85       # 低于此值进入人工审核
  default_tags:                      # 默认标签
    - manga
    - chinese-translation
    - kobo-sync

# ── 外部命令路径 ──────────────────────────────────────────
# Docker 环境下不需要修改
commands:
  kcc: kcc-c2e             # KCC 命令行工具
  calibredb: calibredb     # Calibre 数据库工具

# ── 处理参数 ──────────────────────────────────────────────
processing:
  stable_check_seconds: 30     # 文件稳定检查时间（秒）
  stable_check_interval: 5     # 检查间隔（秒）
  enable_delete_original: false  # ⚠️ 是否删除原始文件（建议 false）
  max_retries: 3               # 最大重试次数

# ── 日志 ──────────────────────────────────────────────────
logging:
  level: INFO                  # DEBUG / INFO / WARNING / ERROR
  format: "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
```

### 配置加载顺序

1. `MANGA_PIPELINE_CONFIG` 环境变量指定的路径
2. 当前目录下的 `config.yaml`
3. `/app/config.yaml`（Docker 默认）
4. 全部使用内置默认值

---

## 日志查看

### 查看实时日志

```bash
# 查看所有服务的日志
docker compose logs -f

# 只看管线的日志
docker compose logs -f manga-pipeline

# 只看 Calibre-Web 的日志
docker compose logs -f calibre-web
```

### 查看最近 100 行日志

```bash
docker compose logs --tail 100 manga-pipeline
```

### 查看日志文件

日志也会保存在 `/srv/ebooks/logs/` 目录：

```bash
ls /srv/ebooks/logs/
cat /srv/ebooks/logs/manga-pipeline.log
```

### 过滤错误信息

```bash
docker compose logs manga-pipeline | grep -i "error\|fail\|exception"
```

### 开启调试模式

如果需要更详细的日志，编辑 `.env` 文件：

```bash
nano .env
```

将 `MANGA_PIPELINE_LOG_LEVEL=INFO` 改为 `MANGA_PIPELINE_LOG_LEVEL=DEBUG`，然后重启：

```bash
docker compose restart manga-pipeline
```

---

## 常见错误排查表

### 🔴 Docker 相关

| 错误现象 | 原因 | 解决方法 |
|----------|------|----------|
| `docker: permission denied` | 当前用户没有 Docker 权限 | 运行 `sudo usermod -aG docker $USER`，然后**重新登录** |
| `Cannot connect to the Docker daemon` | Docker 服务未启动 | `sudo systemctl start docker && sudo systemctl enable docker` |
| `docker compose: command not found` | Docker Compose 未安装 | `sudo apt install docker-compose-plugin` |
| 构建时下载超时 | 网络问题 | 配置 Docker 镜像加速器（见下方说明） |

<details>
<summary>📌 配置 Docker 镜像加速器（国内网络推荐）</summary>

```bash
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json <<EOF
{
  "registry-mirrors": [
    "https://mirror.ccs.tencentyun.com",
    "https://docker.mirrors.ustc.edu.cn"
  ]
}
EOF
sudo systemctl daemon-reload
sudo systemctl restart docker
```

</details>

---

### 🟡 管线处理相关

| 错误现象 | 原因 | 解决方法 |
|----------|------|----------|
| 文件放入 inbox 后没有被处理 | 文件还在下载中 | 等待文件下载完成（管线会等 30 秒确认文件稳定） |
| 文件放入 inbox 后没有被处理 | 管线服务未运行 | `docker compose ps` 查看状态，`docker compose up -d` 启动 |
| 文件被移到 `manual-review/` | 文件名信心度太低 | 重命名文件为 `[作者] 标题 第XX卷.cbz` 格式，然后移回 inbox |
| `status` 显示 FAILED | 处理过程出错 | 查看日志 `docker compose logs manga-pipeline \| grep FAILED` |
| KCC 转换失败 | 漫画图片格式异常 | 检查 CBZ 文件是否损坏，尝试用 7-Zip 打开查看 |
| Calibre 导入失败 | 书库被锁定 | 确保没有其他程序在使用 Calibre 书库，重启管线 |

### 手动重试失败的任务

```bash
# 1. 查看状态，找到失败的任务 ID
docker compose exec manga-pipeline manga-pipeline status

# 2. 重试指定任务
docker compose exec manga-pipeline manga-pipeline retry --id 你的任务ID
```

---

### 🟡 Calibre-Web 相关

| 错误现象 | 原因 | 解决方法 |
|----------|------|----------|
| 打开 `http://ip:8083` 显示无法连接 | Calibre-Web 未启动 | `docker compose up -d calibre-web` |
| 登录后看不到任何书籍 | 书库路径错误 | 设置书库路径为 `/books`（不是主机路径） |
| 登录后看不到任何书籍 | 还没有书导入 | 先放一本漫画到 inbox 等待处理完成 |
| 新导入的书没显示 | Calibre-Web 缓存 | 点击右上角 ⚙️ → Admin → Reconnect Calibre DB |

---

### 🟡 Kobo 同步相关

| 错误现象 | 原因 | 解决方法 |
|----------|------|----------|
| Kobo 同步按钮无反应 | 配置文件路径错误 | 确认修改的是 `.kobo/Kobo/Kobo eReader.conf`（注意隐藏目录） |
| Kobo 同步失败 | WiFi 网络不通 | 确保 Kobo 和 Ubuntu 在同一局域网，尝试 `ping Ubuntu-IP` |
| Kobo 同步失败 | URL 错误 | 确认 `api_endpoint` 的值与 Calibre-Web 中的 Kobo Sync Token 完全一致 |
| Kobo 同步后看不到书 | Calibre-Web 未启用 Kobo sync | 参见 [第九步 9.5](#95-启用-kobo-同步功能) |
| Kobo 一直连接官方商店 | `oneStoreEnabled` 未关闭 | 确保配置文件中有 `oneStoreEnabled=false` |

---

### 🟡 文件权限相关

| 错误现象 | 原因 | 解决方法 |
|----------|------|----------|
| `Permission denied` 写入 inbox | 目录权限不对 | `sudo chown -R $USER:$USER /srv/ebooks` |
| Docker 容器内权限问题 | UID/GID 不匹配 | 在 `.env` 中设置 `PUID` 和 `PGID` 为你的用户 ID（用 `id` 命令查看） |

---

## 项目结构

```
manga-kobo-pipeline/
├── src/manga_pipeline/         # 源代码
│   ├── __init__.py
│   ├── main.py                 # 入口点
│   ├── cli.py                  # CLI 命令（7 个命令）
│   ├── config.py               # 配置管理（Pydantic）
│   ├── logging_config.py       # 日志配置
│   ├── models.py               # 数据模型 + 状态枚举
│   ├── database.py             # SQLite 状态管理
│   ├── utils.py                # 工具函数（SHA-256 等）
│   ├── scanner.py              # Inbox 目录扫描
│   ├── stability.py            # 文件下载完成检测
│   ├── filename_parser.py      # 文件名解析（中文/日文）
│   ├── normalizer.py           # 归一化（RAR/7Z → CBZ）
│   ├── review.py               # 人工审核处理
│   ├── kcc.py                  # KCC 命令封装
│   ├── calibre.py              # Calibre 命令封装
│   ├── pipeline.py             # 流水线编排
│   ├── watcher.py              # 文件系统监听
│   └── comicinfo.py            # ComicInfo.xml 元数据
├── tests/                      # 测试（104 个测试用例）
│   ├── conftest.py
│   ├── test_config.py
│   ├── test_filename_parser.py
│   ├── test_state_machine.py
│   ├── test_kcc_command.py
│   ├── test_calibre_command.py
│   └── test_integration.py
├── config.example.yaml         # 配置文件模板
├── .env.example                # 环境变量模板
├── Dockerfile                  # Docker 构建文件
├── docker-compose.yml          # Docker 编排文件
├── pyproject.toml              # Python 项目配置
└── README.md                   # 本文档
```

---

## 开发指南

> 以下内容面向想修改代码的开发者，普通用户可以跳过。

### 本地开发环境搭建

```bash
# 克隆代码
git clone https://github.com/你的用户名/manga-kobo-pipeline.git
cd manga-kobo-pipeline

# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装开发依赖
pip install -e ".[dev]"

# 运行测试（104 个测试用例）
pytest -v

# 代码检查
ruff check src/ tests/

# 自动修复代码风格
ruff check --fix src/ tests/
```

### 依赖包说明

| 包 | 用途 |
|----|------|
| typer | CLI 命令行框架 |
| pydantic | 配置验证 |
| pyyaml | YAML 配置文件解析 |
| rich | 终端美化输出 |
| watchdog | 文件系统变更监听 |
| rarfile | RAR/CBR 解压 |
| py7zr | 7Z 解压（可选，Docker 中自动安装） |

---

## License

MIT
