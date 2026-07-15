# 短期记忆服务 → DREAM API 契约

## 1. 服务边界

短期记忆服务由其他团队维护，负责：

- Redis 会话缓存；
- 最近对话选择；
- Headroom 压缩；
- 保留足够的最近原始消息；
- 决定何时把一个已完成的对话批次推送给 DREAM。

DREAM 不连接或扫描短期记忆服务的 Redis。短期记忆服务通过 HTTP 推送已经整理好的输入，DREAM 负责：

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

## 3. 获取下一任务上下文

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

## 4. 做梦触发

生产服务的 FastAPI lifespan worker 周期执行两层做梦：

1. 处理等待队列，运行 Background Review，写入 AI 决策卡和用户画像；
2. 从事件账本发现活跃隔离作用域，运行到期的 AI/User Curator。

运维和测试也可以显式调用：

```http
POST /v1/dream/run-pending
POST /v1/dream/run-due-curators
POST /v1/dream/run-curators
```

`run-curators` 是忽略间隔的人工强制运行接口；正常生产调度使用 `run-due-curators` 对应的内部方法。

## 5. 隔离和安全要求

- 三个 ID 只允许字母、数字、下划线和连字符，最长 64 字符。
- 用户画像严格位于 `tenant_id/agent_id/user_id` 作用域。
- AI 决策卡和决策规则位于 `tenant_id/agent_id` 共享作用域。
- 调用方不得提交磁盘路径。
- API key 只通过 `DREAM_LLM_API_KEY` 或配置指定的环境变量注入。
- 当前实现应只绑定 `127.0.0.1`；企业网络暴露必须在网关增加认证、授权、TLS、限流和审计。

