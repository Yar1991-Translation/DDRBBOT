# DDRBBOT 总体执行 PLAN

## 0. 工作规则

- [x] 规则：从 2026-04-06 起，所有开发任务开始前必须先阅读本文件，并同时阅读对应专项 PLAN。
- [x] 规则：开始实现前，先把目标条目标记为 `[~] 进行中`；完成并验证后，再改为 `[x] 已完成`。
- [x] 规则：每次实现完成后，必须在对应条目后补充日期和简短说明。
- [x] 规则：如果用户提出的新需求不在 PLAN 中，先补进 PLAN，再开始开发。
- [x] 规则：如果实现方案与 PLAN 不一致，必须先更新 PLAN，再继续编码。
- [x] 规则：大模型不直接触发群卡投递、重试、撤回、审核动作；资讯卡必须经审核队列由人工批准。允许 LLM 通过工具回复**私聊**与**被 at 的当前群**（`send_reply_text`），并允许通过 `render_card_for_review` 把生成的卡片落入审核队列。（2026-04-17：从 "只负责信息发现、摘要、翻译" 扩为工具化 Agent，工具边界在 `src/ddrbbot/llm_agent/tools.py`）

状态约定：

- `[x]` 已完成
- `[~]` 进行中
- `[ ]` 未开始
- `[-]` 暂缓 / 可选

建议记录格式：

- `[x] 功能名（2026-04-06：实现说明）`

## 1. 当前已完成基线

- [x] FastAPI 主服务骨架（2026-04-06：已提供 health、Discord webhook、RSS collect、render preview、QQ event、QQ send-news-card 等接口）
- [x] SQLite 存储骨架（2026-04-06：已落地 `sources`、`raw_events`、`processed_events`、`render_artifacts`、`delivery_logs`、`delivery_records`、`platform_events`）
- [x] MVP 异步流水线（2026-04-06：已实现 `asyncio.Queue` + worker 模式）
- [x] 基础分析层（2026-04-06：已支持启发式分析和 OpenAI 兼容 LLM 调用，LLM 失败时自动回退）
- [x] MD3 卡片渲染（2026-04-06：已实现单卡片模板、预览页、截图输出、游戏预设）
- [x] NapCat HTTP 适配（2026-04-06：已支持文本、图片卡片、登录信息、版本、群列表、事件归一化）
- [x] QQ 事件与命令基础链（2026-04-06：已支持 `/ping`、`/status`、`/push test`、`/review queue`、`/retry failed`）
- [x] 投递幂等与失败恢复基础（2026-04-06：已支持 `delivery_records`、请求快照持久化、失败查看、手动重试、默认群自动投递）

## 2. 总体待办总览

### 2.1 P0：当前最高优先级

- [x] 审核后台 MVP（2026-04-06：已提供 `/review` 审核页、原始消息查看、结构化结果编辑、重渲染、批准发送、拒绝与手动重发）
  说明：查看原始消息、查看 LLM 结果、手改标题/摘要、重新渲染、批准/拒绝、手动重发。
- [~] X / RSSHub 正式接入与源管理
  说明：按账号追踪、定时拉取、订阅关系、去重策略完善仍待办；**已落地** `POST /api/collect/rsshub`（feed 主机策略）、`GET/POST /api/sources`、RSS/RSSHub 采集后刷新 `sources.last_checked_at` 与 `url`（2026-04-17）。
- [x] NapCat WebSocket 事件流与重连恢复（2026-04-17：新增 `NapCatWSClient`，`NAPCAT_WS_URL` 可选启用；指数退避重连；归一化/入库/分发共用 `handle_inbound_event`）
- [x] 独立 Delivery Queue / Retry Worker / Dead Letter Queue（2026-04-17：`delivery_records.next_retry_at` + `DeliveryWorker` 轮询；pipeline/审核/手动 send 全部改为入队；超过 `DELIVERY_DEAD_LETTER_MAX_ATTEMPTS` 进入 `dead_letter`，提供查看与重入队 API）
- [x] 图片发送失败降级策略（2026-04-17：QQ 图片投递重试耗尽后自动 `send_text` 发送标题/摘要/caption/本地路径说明，投递状态 `sent_text_fallback`，可用 `QQ_IMAGE_FAIL_TEXT_FALLBACK=0` 关闭）

### 2.2 P1：增强版必须补齐

- [~] LLM 输出 JSON Schema 校验与自动修复
  说明：**已落地** LLM 返回 JSON 的 `LLMAnalysisOutput` Pydantic 校验（category/credibility 枚举收敛、忽略多余字段）；自动修复层仍待办（2026-04-17）。
- [x] LLM 工具化 Agent 与对话入口（2026-04-17：`src/ddrbbot/llm_agent/`，支持 OpenAI Tool Calling 多轮循环；工具：list_sources / list_review_items / get_processed_event / fetch_url / collect_rss / collect_rsshub / register_source / render_card_for_review / call_ddrbbot_api / send_reply_text；QQ 私聊、被 at 群消息、`/ai`、`/chat` 前缀触发；`AgentScheduler` 可选后台巡查；`POST /api/ai/chat` 调试入口；资讯卡强制 `delivery_status=review_pending` 走审核；`skills/ddrbbot-api-content` SKILL.md + endpoints.md + `ddrbbot_api.py` CLI 同步补齐 collect-rsshub / sources / dead-letter / adapter-status / ai-chat 子命令）
- [ ] 术语库 / 翻译本地化规则
- [ ] 来源可信度评分系统
- [ ] 多模板卡片体系
- [ ] 深浅色主题的完整 MD3 Token 系统
- [ ] 长图切片 / 超长内容溢出治理
- [ ] 订阅管理与路由管理
  说明：包括 `/news`、`/bind source`、目标群管理、来源绑定。
- [ ] 消息撤回 / 重发管理流程
- [~] 启动自检、连续失败告警、登录态失效恢复（2026-04-17：已落地 lifespan `_startup_selfcheck` 与 DeliveryWorker 连续失败阈值告警；登录失效自动恢复仍待办）
- [ ] 运维状态面板 / 审计视图

### 2.3 P2：生产化与扩展

- [ ] Redis 队列或 Celery / APScheduler
- [ ] PostgreSQL 迁移
- [ ] Redis 缓存层
- [ ] Prometheus + Grafana 监控
- [ ] 多平台分发（QQ / Telegram / Discord）
- [ ] 自动发现新来源并推荐订阅
- [ ] NapCat 插件桥接 / WebUI
- [ ] 灰度群 / 测试群 / 正式群分层投递
- [ ] 压测、集成测试、视觉验收、灰度测试

## 3. 模块化执行清单

### 3.1 数据采集层

- [x] Discord Webhook 接入（2026-04-06：已实现 `/api/webhook/discord`）
- [x] 通用 RSS 手动采集接口（2026-04-06：已实现 `/api/collect/rss`）
- [x] RSSHub 专用采集端点（2026-04-17：`POST /api/collect/rsshub` + `RSSHUB_HOST_MARKERS` / `RSSHUB_EXTRA_HOSTS`；X 平台仍缺）
- [x] 来源注册表 API（2026-04-17：`GET/POST /api/sources`，与采集触达 `touch_source_feed`；订阅关系表仍待办）
- [ ] 定时采集任务
- [ ] 官网 / Wiki / Trello / Notion / 视频平台扩展采集器
- [ ] 新来源自动发现与推荐

### 3.2 队列与调度层

- [x] `asyncio.Queue` MVP（2026-04-06：已上线）
- [ ] APScheduler / Celery / Redis Stream 正式调度层
- [ ] 优先级队列
- [x] 死信队列（2026-04-17：`delivery_records` 新增 `dead_letter` 状态与 API）
- [ ] 多 worker 并发策略优化
- [ ] 更完整的任务状态流转与恢复

### 3.3 LLM 分析层

- [x] 启发式分析（2026-04-06：已实现）
- [x] OpenAI 兼容接口分析（2026-04-06：已实现基础接入）
- [x] LLM JSON 输出 Pydantic 强校验（2026-04-17：`LLMAnalysisOutput`；Schema 文件/自动修复仍属增强项）
- [x] LLM 工具化 Agent（2026-04-17：`LLMAgent` 多轮 tool calling，工具边界见 `src/ddrbbot/llm_agent/tools.py`）
- [x] LLM 会话入口（2026-04-17：QQ 私聊/被 at/`/ai`|`/chat` 前缀 → agent；`AgentScheduler` 后台巡查；`POST /api/ai/chat` 调试）
- [ ] LLM 异常结果自动修复层
- [ ] 术语标准化
- [ ] 高风险内容识别与人工审核分流
- [ ] 更细粒度可信度系统
- [~] 新来源提取后的自动入库与建议订阅（2026-04-17：Agent 可经 `register_source` 工具入库；自动推荐仍待办）

### 3.4 渲染层

- [x] 单模板 MD3 卡片（2026-04-06：已实现）
- [x] 预览工作台（2026-04-06：已实现）
- [x] Playwright 截图（2026-04-06：已接入）
- [x] 游戏预设系统（2026-04-06：已支持 Roblox / DOORS / Pressure / Forsaken）
- [ ] 多模板体系
- [ ] 完整主题系统
- [ ] 长图切片
- [~] 图片缺失 / 图片失败时的渲染降级（2026-04-17：投递侧文本降级已做；模板侧占位/降级仍待办）
- [ ] 更完整的 MD3 后台界面组件库

### 3.5 分发与通知层

- [x] NapCat HTTP 发送文本与图片（2026-04-06：已实现）
- [x] 幂等投递记录（2026-04-06：已实现）
- [x] 失败重试基础能力（2026-04-06：已实现）
- [x] 失败查看与手动重试接口（2026-04-06：已实现）
- [x] 独立 Retry Worker（2026-04-17：`DeliveryWorker` 轮询 + 重试调度）
- [x] Dead Letter Queue（2026-04-17：`dead_letter` 状态 + `/api/delivery/dead-letter` API）
- [ ] 撤回消息业务流
- [ ] 多目标路由配置
- [ ] 发送限流 / 冷却 / 风控策略
- [ ] 多平台分发扩展

### 3.6 审核与后台

- [x] 审核列表页（2026-04-06：已提供 `/review` 队列列表与状态筛选）
- [x] 原始消息 / 结构化结果 / 预览图三栏视图（2026-04-06：已提供原文、结构化编辑区、实时预览与最新产物信息）
- [x] 人工编辑标题、摘要、要点（2026-04-06：已支持标题、摘要、要点、分类与游戏名编辑）
- [x] 一键重渲染（2026-04-06：已接入审核页重渲染接口并保存新产物）
- [x] 一键批准发送 / 拒绝 / 手动重发（2026-04-06：已接入 NapCat 发送、拒绝与重发最新截图）
- [ ] 标记误报
- [ ] 审计日志与操作记录

### 3.7 存储与观测

- [x] SQLite MVP（2026-04-06：已实现）
- [ ] PostgreSQL 正式版
- [ ] Redis 缓存
- [ ] Prometheus 指标
- [ ] Grafana 仪表盘
- [ ] 告警机制
- [ ] 灰度/正式环境区分

## 4. 当前建议执行顺序

### 4.1 先做

- [x] 审核后台 MVP（2026-04-06：首版审核链路已落地）
- [~] X / RSSHub 正式接入（2026-04-17：RSSHub 采集与来源 API 已接，见 §5）
- [x] NapCat WebSocket 事件流（2026-04-17）
- [x] Delivery Queue / Retry Worker / Dead Letter Queue（2026-04-17）

### 4.2 再做

- [ ] 多模板卡片
- [ ] 订阅管理
- [ ] 启动自检与告警
- [ ] 撤回 / 重发管理

### 4.3 最后做

- [ ] PostgreSQL / Redis / Prometheus / Grafana
- [ ] 多平台分发
- [ ] NapCat 插件桥接与 WebUI

## 5. 实施记录

- [x] 2026-04-06：建立可执行 PLAN 规则，后续所有开发必须先对照 PLAN，再实现，再回写状态。
- [x] 2026-04-06：完成审核后台 MVP，新增 `/review` 审核页与审核动作接口，支持人工编辑、重渲染、批准发送、拒绝和重发最新截图。
- [x] 2026-04-17：RSSHub 采集 `POST /api/collect/rsshub`；来源管理 `GET/POST /api/sources`；`POST /api/collect/rss` 采集后更新 `sources`；QQ 图片发送失败后的文本降级投递；LLM 输出 `LLMAnalysisOutput` 校验。
- [x] 2026-04-17：P0 剩余三项——NapCat WebSocket 事件流与指数退避重连（`NapCatWSClient`）；独立 `DeliveryWorker` + `delivery_records.next_retry_at` + `dead_letter` 状态（pipeline/审核/手动发送全部改为入队 fire-and-forget，新增 `GET/POST /api/delivery/dead-letter*`）；lifespan `_startup_selfcheck` 与连续失败 `logger.critical` 告警。全量 41 个单元测试通过。
- [x] 2026-04-17：LLM 工具化 Agent 接入（`src/ddrbbot/llm_agent/`）。新增 `LLMAgent`/`AgentToolRegistry`/`AgentScheduler`；工具集覆盖查询/采集/生成审核卡/私聊回复；QQ 私聊、被 at、`/ai`、`/chat` 均路由到 Agent；`POST /api/ai/chat` 调试；`QQInboundEvent.at_self` 归一化识别 at 段。PLAN §0 第 6 条规则改为「LLM 不直接发群卡/操作队列，但允许 send_reply_text 在 qq_chat 上下文回复、允许 render_card_for_review 入审核」。全量 45 个单元测试通过。
- [x] 2026-04-17：Agent 与 DDRBBOT API / `ddrbbot-api-content` skill 适配补强。工具集新增 `call_ddrbbot_api`（带 allowlist：/api/health、/api/render/*、/api/review/*、/api/collect/*、/api/sources、/api/qq/adapter/status、/api/qq/delivery/review-queue、/api/delivery/dead-letter、/api/ai/chat、/api/webhook/discord；禁止直接 send-news-card / retry-failed）；skill 文档重写（SKILL.md + references/endpoints.md 覆盖全部端点与新投递队列语义）；CLI `ddrbbot_api.py` 新增 `collect-rsshub`、`sources-list`、`sources-upsert`、`adapter-status`、`dead-letter-list`、`dead-letter-retry`、`ai-chat` 子命令。全量 46 个测试中 45 个通过（唯一失败 `test_runtime_copy_store_reloads_after_file_edit` 为 Windows 文件系统 mtime 的 pre-existing flake）。
