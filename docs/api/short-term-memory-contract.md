# 短期记忆服务 → DREAM API 契约

## 1. 服务边界

短期记忆服务由其他团队维护，负责：

- Redis 会话缓存；
- 最近对话选择；
- Headroom 压缩；
- 保留足够的最近原始消息；
- 决定何时把一个已完成的对话批次推送给 DREAM。

DREAM 不连接或扫描短期记忆服务的 Redis。短期记忆服务可以通过 HTTP
推送输入，也可以提供只读导出 API 供 DREAM 定期拉取。DREAM 负责：

- 追加到不可变事件账本；
- 后台提炼 AI 决策卡；
- 后台提炼当前用户画像；
- 定期运行 AI Curator 和 User Curator；
- 为下一次任务提供冻结上下文。

## 2. 推送对话

```http
POST /v1/dream/conversations
Content-Type: application/json
```

```json
{
  "tenant_id": "enterprise-a",
  "agent_id": "service-agent",
  "user_id": "user-001",
  "event_id": "evt-20260715-001",
  "conversation_id": "conversation-001",
  "completed_at": "2026-07-15T10:00:00+08:00",
  "interrupted": false,
  "tool_iterations": 12,
  "headroom_summary": "The user prefers decisions to be explained briefly.",
  "messages": [
    {"role": "user", "content": "以后结论放在前面。"},
    {"role": "assistant", "content": "明白，后续先给结论。"}
  ],
  "final_response": "已按要求调整。"
}
```

成功入队返回 HTTP 202：

```json
{"event_id": "evt-20260715-001", "status": "queued"}
```

`event_id` 是幂等键。重复推送同一事件返回 HTTP 409，不会重复学习。

`headroom_summary` 是辅助证据，不是 Background Review 的系统指令。为了降低压缩遗漏，`messages` 应保留产生偏好或决策信号的最近原始消息。

## 3. DREAM 定期拉取 NDJSON

当无法修改上游项目主动推送时，上游只需提供一个只读导出接口：

```http
GET /v1/memory/dream-export?after=100&limit=100
Accept: application/x-ndjson
Authorization: Bearer <source-api-key>
```

响应的每一行是一条完整 JSON 记录：

```json
{"cursor":"101","event_id":"evt-101","user_id":"user-001","session_id":"session-001","round_id":"round-01","completed_at":"2026-07-15T10:00:00+08:00","messages":[{"role":"user","content":"以后回答简洁一些。"},{"role":"assistant","content":"明白。高风险操作前我会先验证。"}],"final_response":"明白，后续先给简洁结论。"}
```

字段要求：

- `cursor`：可排序且不回退的读取位置；DREAM 成功持久化记录后才保存它。
- `event_id`：稳定且唯一，用于防止重复学习。
- `user_id`：用户隔离键；不同用户的画像不会互相读取。
- `session_id`、`round_id`：定位原始会话轮次。
- `completed_at`：带时区的 ISO 8601 完成时间。
- `messages`：完整的 user/assistant 消息，供 AI 决策卡和用户画像共同提炼。
- `final_response`：该轮最终答复；未完成的中间状态不应导出。

没有新增记录时返回 HTTP 200 和空响应体。DREAM 不需要 Redis、Mirage、
ES、Embedding 或向量数据，只需要上述 URL、API Key 和字段契约。

## 4. 获取下一任务上下文

```http
POST /v1/tasks/start
Content-Type: application/json
```

```json
{
  "tenant_id": "enterprise-a",
  "agent_id": "service-agent",
  "user_id": "user-001"
}
```

响应包括：

```json
{
  "snapshot_id": "sha256...",
  "user_profile": "...",
  "decision_rules": "...",
  "decision_cards": ["..."]
}
```

调用方必须在一次前台任务生命周期内固定使用该响应。DREAM 后台发生的写入不会改变已经返回的快照；下一次调用 `/v1/tasks/start` 才会获得新版本。

## 5. 做梦触发

生产服务的 FastAPI lifespan worker 周期执行做梦链路：

1. 到期时携带上次 `cursor` 拉取新增 NDJSON，并转换成 DREAM 事件；
2. 处理等待队列，运行一次 Background Review，按证据写入 AI 决策卡、用户画像或两者；
3. 从事件账本发现活跃隔离作用域，运行到期的 AI/User Curator。

运维和测试也可以显式调用：

```http
POST /v1/dream/run-pending
POST /v1/dream/run-due-curators
POST /v1/dream/run-curators
```

`run-curators` 是忽略间隔的人工强制运行接口；正常生产调度使用 `run-due-curators` 对应的内部方法。

## 6. 隔离和安全要求

- 三个 ID 只允许字母、数字、下划线和连字符，最长 64 字符。
- 用户画像严格位于 `tenant_id/agent_id/user_id` 作用域。
- AI 决策卡和决策规则位于 `tenant_id/agent_id` 共享作用域。
- AI 决策卡只能保存与用户无关的通用决策经验，不得复制用户身份、偏好或秘密。
- 调用方不得提交磁盘路径。
- API key 只通过 `DREAM_LLM_API_KEY` 或配置指定的环境变量注入。
- 当前实现应只绑定 `127.0.0.1`；企业网络暴露必须在网关增加认证、授权、TLS、限流和审计。
