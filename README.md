# DDRBBOT — Linux 部署教程

面向在 Linux 服务器上自托管运行：FastAPI 服务、SQLite、Playwright 截图、NapCat（HTTP / WebSocket）投递与 QQ 入站。

## 1. 环境与假设

- **系统**：常见 x86_64 Linux（Debian / Ubuntu / Arch 等）。
- **Python**：3.10 及以上。
- **网络**：服务器能访问 NapCat、（可选）LLM API、RSS / Discord 等上游。
- **NapCat**：已单独部署，且 HTTP API 地址对 DDRBBOT 可达；若使用 QQ 事件 WebSocket，需填写 `NAPCAT_WS_URL`。

## 2. 系统依赖（Playwright / Chromium）

Playwright 的 Chromium 需要系统库。任选其一：

**方式 A（推荐）**：安装 Chromium 后由 Playwright 使用自带浏览器：

```bash
cd /path/to/DDRBBOT
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python -m playwright install chromium
python -m playwright install-deps chromium
```

若 `install-deps` 在你发行版上不可用或报错，改用 **方式 B**：按 [Playwright 文档](https://playwright.dev/docs/intro#system-requirements) 手动安装列出的系统包，再执行 `python -m playwright install chromium`。

**关闭截图**（无头环境不想装 Chromium 时）：在 `.env` 中设置 `SCREENSHOT_ENABLED=false`（仍会生成 HTML 等产物，具体行为以当前代码为准）。

## 3. 获取代码与安装

```bash
git clone <你的仓库 URL> DDRBBOT
cd DDRBBOT
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
# 可选：开发/测试
# pip install -e ".[dev]"
```

项目包位于 `src/`，`pyproject.toml` 已配置 `pytest` 的 `pythonpath`，测试时使用：

```bash
pytest tests/ -q
```

## 4. 配置环境变量

复制示例并按实际填写：

```bash
cp .env.example .env
```

编辑 `.env`。以下为 **部署时最常改** 的项（完整列表见 `src/ddrbbot/config.py` 中 `load_settings()`）：

| 变量 | 说明 |
|------|------|
| `DATABASE_PATH` | SQLite 文件绝对或相对路径（默认在 `ARTIFACTS_DIR` 下） |
| `ARTIFACTS_DIR` | HTML / PNG 等产物目录，需进程可写 |
| `NAPCAT_BASE_URL` | NapCat HTTP API 根地址，无尾部 `/` |
| `NAPCAT_ACCESS_TOKEN` | 与 NapCat 配置一致；无则留空 |
| `NAPCAT_TIMEOUT_SECONDS` | HTTP 超时秒数 |
| `NAPCAT_WS_URL` | 可选；NapCat 事件 WebSocket，填空则仅 HTTP 回调 |
| `NAPCAT_WS_RECONNECT_*` | WS 断线重连退避（秒） |
| `DEFAULT_QQ_GROUP_ID` | 自动投递默认群号；空则不在流水线里默认发群 |
| `AUTO_DELIVER_ENABLED` | 是否在处理完成后自动入队投递 |
| `DELIVERY_WORKER_ENABLED` | 是否启动后台投递 Worker（生产建议 `true`） |
| `DELIVERY_WORKER_POLL_SECONDS` | Worker 轮询间隔 |
| `DELIVERY_DEAD_LETTER_MAX_ATTEMPTS` | 失败多少次进入死信 |
| `DELIVERY_ALERT_CONSECUTIVE_FAILURES` | 连续失败告警阈值 |
| `DELIVERY_RETRY_DELAYS_SECONDS` | 重试间隔，逗号分隔，如 `10,30,120` |
| `SCREENSHOT_ENABLED` | 是否 Playwright 截图 |
| `WORKER_CONCURRENCY` / `QUEUE_MAXSIZE` | 流水线并发与队列长度 |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | OpenAI 兼容接口，用于分析等 |
| `LLM_AGENT_ENABLED` | 是否启用工具型 LLM Agent（QQ/API） |
| `LLM_AGENT_SCHEDULE_ENABLED` | 是否启用定时 Agent 任务 |
| `QQ_ADMIN_USER_IDS` / `QQ_ADMIN_GROUP_IDS` | 逗号分隔，管理员 QQ / 群 |
| `QQ_NEWS_CARD_MAX_BYTES` | 卡片图片最大字节 |
| `QQ_IMAGE_FAIL_TEXT_FALLBACK` | 图片失败是否回退纯文 |
| `RSSHUB_HOST_MARKERS` / `RSSHUB_EXTRA_HOSTS` | RSSHub URL 校验用 |

加载方式：进程环境或项目根目录 `.env`（若你使用 `python-dotenv` 或 shell `export`，需与当前启动方式一致；**若未自动加载 `.env`**，请用 `export $(grep -v '^#' .env | xargs)` 或 systemd `EnvironmentFile=`）。

## 5. 启动服务

开发（仅本机或内网调试）：

```bash
cd /path/to/DDRBBOT
source .venv/bin/activate
export PYTHONPATH=src
uvicorn ddrbbot.main:create_app --factory --host 0.0.0.0 --port 8000
```

生产建议：**不**使用 `--reload`，由 systemd 或进程管理器托管，并置于 Nginx/Caddy 反向代理之后（HTTPS、限流、鉴权由代理层补充）。

## 6. NapCat 对接要点

1. **HTTP API**：`NAPCAT_BASE_URL` 指向 NapCat 提供的 OneBot 兼容 HTTP 入口；`NAPCAT_ACCESS_TOKEN` 与 NapCat 侧 token 一致。
2. **QQ 事件上报**：将 NapCat（或反向代理）配置为把消息/事件 `POST` 到 DDRBBOT 的 `http://<ddrbbot-host>:8000/api/events/qq`（若走公网需固定 URL 与防火墙放行）。
3. **WebSocket（可选）**：设置 `NAPCAT_WS_URL` 后，应用启动时会连接并自动重连；与 HTTP 上报二选一或按 NapCat 能力组合使用，避免重复处理需以 NapCat 配置为准。
4. **投递**：业务侧多通过 `QQDeliveryService` 入队，由 `DeliveryWorker` 异步发送；审核「通过发送」等接口返回 `queued` 属正常，需 Worker 运行且 NapCat 可用。

## 7. 健康检查与冒烟

```bash
curl -sS http://127.0.0.1:8000/api/health
```

可选：检查 NapCat 适配状态（路径以实际路由为准）：

```bash
curl -sS http://127.0.0.1:8000/api/qq/adapter/status
```

## 8. systemd 示例

`/etc/systemd/system/ddrbbot.service`：

```ini
[Unit]
Description=DDRBBOT FastAPI
After=network.target

[Service]
Type=simple
User=ddrbbot
Group=ddrbbot
WorkingDirectory=/opt/DDRBBOT
Environment=PYTHONPATH=/opt/DDRBBOT/src
EnvironmentFile=/opt/DDRBBOT/.env
ExecStart=/opt/DDRBBOT/.venv/bin/uvicorn ddrbbot.main:create_app --factory --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ddrbbot.service
sudo journalctl -u ddrbbot.service -f
```

前面 Nginx 监听 443，反代到 `127.0.0.1:8000`；**不要将数据库与 artifacts 目录放在 Web 可遍历的静态路径下**。

## 9. 目录与权限

- 确保 `ARTIFACTS_DIR`、SQLite 所在目录对运行用户可写。
- 首次启动会初始化数据库与迁移；备份时复制 `DATABASE_PATH` 与 `ARTIFACTS_DIR` 即可。

## 10. 常用 API 速查

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| POST | `/api/webhook/discord` | Discord 入站 |
| POST | `/api/collect/rss` | 拉取 RSS |
| POST | `/api/collect/rsshub` | RSSHub 拉取 |
| POST | `/api/render/preview` | HTML 预览 |
| POST | `/api/render/preview-image` | 直接出 PNG（需截图开启且 Chromium 可用） |
| POST | `/api/events/qq` | QQ 事件入站 |
| POST | `/api/qq/send-news-card` | 手动发卡片（入队） |
| GET | `/review` | 审核页 |
| GET | `/api/delivery/dead-letter` | 死信列表 |
| POST | `/api/delivery/dead-letter/{id}/retry` | 重试死信 |
| POST | `/api/ai/chat` | HTTP 调试 LLM Agent（需开启 LLM 与 Agent） |

更完整的说明见 `skills/ddrbbot-api-content/references/endpoints.md`。

## 11. 故障排查

| 现象 | 排查 |
|------|------|
| 启动报 Playwright / 浏览器错误 | 执行 `playwright install chromium` 与 `install-deps`，或关 `SCREENSHOT_ENABLED` |
| 投递一直 pending / 无发送 | 确认 `DELIVERY_WORKER_ENABLED=true`，看日志与 `GET /api/delivery/dead-letter` |
| NapCat 401 / 连接失败 | 核对 `NAPCAT_BASE_URL`、`NAPCAT_ACCESS_TOKEN` 与防火墙 |
| QQ 无回调 | 核对 NapCat 上报 URL 是否指向本服务 `/api/events/qq` |
| LLM Agent 无响应 | 核对 `LLM_*` 与 `LLM_AGENT_ENABLED`，以及 QQ 是否私聊/@机器人/触发前缀（见 `qq/commands.py`） |

---

更多产品级规划与完成情况见 `PLAN/PLAN.md`、`PLAN/NAPCAT_PLAN.md`。
