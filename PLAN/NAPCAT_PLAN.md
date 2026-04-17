# NapCat 专项执行 PLAN

## 0. 使用规则

- [x] 规则：所有 NapCat 相关工作开始前，必须先阅读本文件和总 PLAN。
- [x] 规则：开始实现前，把目标条目标记为 `[~] 进行中`；完成并验证后，改为 `[x] 已完成`。
- [x] 规则：完成后必须补充日期和简短说明。
- [x] 规则：如果新增 NapCat 需求不在本文件中，先补入本文件，再开发。
- [x] 规则：大模型不直接操作 NapCat；NapCat 只接受系统层、命令层和明确的管理接口调用。

状态约定：

- `[x]` 已完成
- `[~]` 进行中
- `[ ]` 未开始
- `[-]` 暂缓 / 可选

## 1. 当前已完成

### 1.1 Phase 1：基础连通

- [x] `BotAdapter` 抽象（2026-04-06：已建立统一接口）
- [x] `NapCatAdapter` HTTP Action 封装（2026-04-06：已支持 `send_msg`、`delete_msg`、`get_login_info`、`get_version_info`、`get_group_list`）
- [x] 文本消息发送（2026-04-06：已实现）
- [x] 图片卡片发送（2026-04-06：已实现）
- [x] 健康检查基础能力（2026-04-06：已实现）

### 1.2 Phase 2：事件接收与命令控制

- [x] OneBot / NapCat 原始事件归一化（2026-04-06：已实现 `normalize_inbound_event`）
- [x] `/api/events/qq` 事件入口（2026-04-06：已实现）
- [x] 管理员白名单（2026-04-06：已支持用户白名单和群白名单）
- [x] `/ping`（2026-04-06：已实现）
- [x] `/status`（2026-04-06：已实现）
- [x] `/push test`（2026-04-06：已实现）
- [x] `/review queue`（2026-04-06：已实现）
- [x] `/retry failed`（2026-04-06：已实现）

### 1.3 Phase 3：正式接入主链路（已完成部分）

- [x] 自动投递到默认群（2026-04-06：已实现）
- [x] `delivery_records` 幂等记录（2026-04-06：已实现）
- [x] 失败请求快照持久化（2026-04-06：已实现）
- [x] 手动失败重试（2026-04-06：已实现）
- [x] 失败查看接口（2026-04-06：已实现）
- [x] 图片存在 / 大小校验（2026-04-06：已实现）

### 1.4 Phase 4：运维增强（已完成部分）

- [x] `/api/qq/adapter/status`（2026-04-06：已实现）
- [x] 默认群存在性检查（2026-04-06：已实现）

## 2. NapCat 未实现功能

### 2.1 Phase 3：正式接入主链路缺口

- [x] 独立 Delivery Queue（2026-04-17：`delivery_records` 表增加 `next_retry_at`，`enqueue_delivery` 入队后由 `DeliveryWorker` 异步消费；pipeline、审核 approve-send、`/api/qq/send-news-card` 均改为入队）
- [x] 独立 Retry Worker（2026-04-17：`src/ddrbbot/delivery_worker.py`，`DELIVERY_WORKER_POLL_SECONDS`、`DELIVERY_WORKER_ENABLED` 可配）
- [x] Dead Letter Queue / 人工处理队列（2026-04-17：重试耗尽自动转 `status=dead_letter`；`GET /api/delivery/dead-letter` 列表 + `POST /api/delivery/dead-letter/{record_id}/retry` 重入队；`DELIVERY_DEAD_LETTER_MAX_ATTEMPTS` 控制上限）
- [x] 图片发送失败降级（2026-04-17：`QQDeliveryService` 重试耗尽后自动 `send_text` 发送标题/摘要/caption/本地路径说明，状态记为 `sent_text_fallback`，可用 `QQ_IMAGE_FAIL_TEXT_FALLBACK=0` 关闭）
- [ ] 多群 / 多目标路由管理
  说明：当前主要还是默认群和显式 target，没有正式路由配置系统。
- [ ] 消息撤回业务接口
  说明：适配器层已有 `recall_message`，但还没进入管理流。
- [ ] 发送频控 / 冷却 / 防刷屏策略
- [ ] 灰度群 / 正式群分层投递策略

### 2.2 Phase 4：运维与插件扩展缺口

- [x] WebSocket 事件流接入（2026-04-17：`src/ddrbbot/qq/ws_client.py`，`NAPCAT_WS_URL` 可选；事件归一化/入库/分发复用 `handle_inbound_event`）
- [x] WebSocket 断线重连恢复（2026-04-17：指数退避 `NAPCAT_WS_RECONNECT_BASE_SECONDS` / `NAPCAT_WS_RECONNECT_MAX_SECONDS`）
- [x] 启动自检（2026-04-17：lifespan `_startup_selfcheck`：`health_check` + `get_login_info` + `get_version_info` + 默认群存在性 + DB stats 汇总到 logger.info）
- [x] 连续失败告警（2026-04-17：`DeliveryWorker._consecutive_failures` 超过 `DELIVERY_ALERT_CONSECUTIVE_FAILURES` 触发 `logger.critical`，每 10 次去重）
- [ ] 登录失效恢复流程
- [ ] 发送回执 / callback bridge
- [ ] NapCat 插件桥接层
- [ ] NapCat 本地 WebUI / 状态页
- [ ] 运维报表 / 审计视图

### 2.3 命令缺口

- [ ] `/news <game>`
- [ ] `/bind source <game> <account>`
- [x] `/ai`、`/chat` 会话前缀与被 at 自动路由 LLM Agent（2026-04-17）
- [ ] 更完整的订阅管理命令
- [ ] 更完整的重发 / 撤回命令

## 3. 测试缺口

- [ ] 对真实 NapCat 实例的集成测试
- [ ] WebSocket 接入测试
- [ ] 断线重连恢复测试
- [ ] 登录失效测试
- [ ] 多群路由测试
- [ ] 发送频控测试
- [ ] 灰度群 / 正式群链路测试
- [ ] 大量事件压测

## 4. 当前建议执行顺序

### 4.1 先做

- [x] WebSocket 事件流接入（2026-04-17）
- [x] 独立 Delivery Queue / Retry Worker / Dead Letter Queue（2026-04-17）
- [x] 图片失败降级策略（2026-04-17）
- [x] 启动自检与连续失败告警（2026-04-17）

### 4.2 再做

- [ ] 多群路由管理
- [ ] 撤回 / 重发管理
- [ ] `/news` 和 `/bind source` 命令

### 4.3 最后做

- [ ] NapCat 插件桥接
- [ ] WebUI / 状态面板
- [ ] 灰度发布体系

## 5. 实施记录

- [x] 2026-04-06：重构为可执行专项 PLAN，后续 NapCat 开发必须先改状态，再开发，再回写结果。
- [x] 2026-04-17：补齐 P0 缺口——NapCat WebSocket 事件流与指数退避重连；独立 `DeliveryWorker` 轮询 `delivery_records` 异步发送，支持 retry/dead_letter 状态机与文本降级；新增 `GET/POST /api/delivery/dead-letter*` 死信查看与重入队；lifespan 启动自检（连通/登录/版本/默认群/DB）；连续失败阈值告警。
- [x] 2026-04-17：`QQInboundEvent.at_self` 归一化识别；`QQCommandRouter` 增加 AI 入口——私聊任意文本、群内被 at、`/ai`/`/chat` 前缀都会路由到 `LLMAgent`，由 `send_reply_text` 工具回到当前会话；群卡仍需人工审核 `/review`。
