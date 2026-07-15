# Internship 记忆源 Pull 接入设计

## 目标

只修改 DREAM，不读取或修改对方的 Redis、Elasticsearch、Mirage、S3 和代码。DREAM 通过一个可配置的只读 HTTP API，定期增量拉取已完成的原始对话，将其转换为现有 `TaskCompletedEvent`，随后复用 Background Review、AI Curator 和 User Curator，持续生成 AI 决策卡和隔离的用户画像。

第一版可在对方真实导出 API 尚未上线时，通过模拟 HTTP 服务完成 DREAM 端测试；真实联调只需要填写 `.env` 中的 URL 和服务凭据。

## 非目标

- 不修改 `MiguelJunXiang/Internship` 项目。
- 不调用其 `/recall` 作为完整做梦数据源，因为该接口会筛选和压缩历史。
- 不访问对方数据库、文件目录、Mirage mount、Embedding 或向量索引。
- 不在第一版实现 Kafka、Redis Stream、Webhook 或双向记忆同步。
- 不从 GitHub 仓库读取运行时用户记忆。

## 对方需要提供的接口

对方提供一个可从 DREAM 部署环境访问的 HTTPS URL，例如：

```http
GET https://memory.example.com/v1/memory/dream-export?after=100&limit=100
Authorization: Bearer <read-only-service-token>
Accept: application/x-ndjson
```

本地开发允许使用 `http://127.0.0.1:<port>`。Token 只通过环境变量注入，不写入日志、报告、事件账本或同步状态。

响应正文使用 NDJSON；每一行是一轮已完成对话，记录按 `cursor` 升序返回：

```jsonl
{"cursor":"101","event_id":"evt-101","user_id":"user-001","session_id":"session-01","round_id":"round-10","completed_at":"2026-07-15T10:00:00Z","messages":[{"role":"user","content":"以后回答先给结论"},{"role":"assistant","content":"明白，后续我会先给结论"}],"final_response":"明白，后续我会先给结论"}
{"cursor":"102","event_id":"evt-102","user_id":"user-002","session_id":"session-08","round_id":"round-03","completed_at":"2026-07-15T10:05:00Z","messages":[{"role":"user","content":"技术问题请讲详细一点"},{"role":"assistant","content":"好的，我会补充实现细节和原因"}],"final_response":"好的，我会补充实现细节和原因"}
```

必填字段：

- `cursor`：不透明的增量位置；DREAM 只保存并在下一次请求的 `after` 参数中原样传回。
- `event_id`：稳定、全局唯一的源事件 ID；同一轮对话重试时必须保持不变。
- `user_id`：源系统的用户 ID。
- `session_id`：源系统的会话 ID。
- `round_id`：会话内稳定的轮次 ID。
- `completed_at`：带时区的 ISO 8601 完成时间。
- `messages`：完整、有序的 user/assistant 原始文本消息；可以包含 system/tool 文本消息。
- `final_response`：这一轮 AI 的最终回答，非空。

第一版不要求对方提供 `tenant_id` 和 `agent_id`；它们由每个 DREAM 数据源实例的本地配置确定。对方若返回未知字段，DREAM 忽略未知字段，但必填字段类型错误会停止当前批次。

## DREAM 配置

在 `.env.example` 中增加以下非秘密示例：

```env
DREAM_INTERNSHIP_SOURCE_ENABLED=false
DREAM_INTERNSHIP_SOURCE_URL=http://127.0.0.1:8000/v1/memory/dream-export
DREAM_INTERNSHIP_SOURCE_API_KEY=
DREAM_INTERNSHIP_SOURCE_TENANT_ID=enterprise-a
DREAM_INTERNSHIP_SOURCE_AGENT_ID=service-agent
DREAM_INTERNSHIP_SOURCE_BATCH_SIZE=100
DREAM_INTERNSHIP_SOURCE_TIMEOUT_SECONDS=15
DREAM_INTERNSHIP_SOURCE_INTERVAL_SECONDS=300
```

数据源默认关闭，因此未配置对方 API 时不会改变 DREAM 现有行为。启用时 URL、tenant ID 和 agent ID 必填。API key 可为空以支持本机联调；生产部署必须通过网关或服务端鉴权保护接口。

## DREAM 组件边界

### `dream.sources.internship`

负责 HTTP 和源格式，不负责写事件账本：

- 定义并验证源记录模型。
- 使用 `after` 和 `limit` 调用导出 URL。
- 发送 `Accept: application/x-ndjson` 和可选的 Bearer Token。
- 逐行解析 NDJSON；忽略空行。
- 对非 2xx、超时、非法 JSON和缺失字段返回明确错误；空响应正文表示当前没有新记录，是成功的无操作同步。
- 支持注入 HTTP transport/client，测试不访问真实网络。

### `dream.source_sync`

负责增量导入和持久化边界：

- 从 DREAM_HOME 中读取当前 cursor 和上次同步时间。
- 将每条源记录映射为 `TaskCompletedEvent`。
- 使用源名和 `event_id` 生成 DREAM 事件 ID，避免不同数据源碰撞。
- 复用 `DreamService.ingest_conversation()` 写事件账本并进入待处理队列。
- 如果事件账本已经存在同一事件，将其计为 duplicate，并允许 cursor 前进。
- 每条记录成功持久化或确认重复后，原子更新 cursor。
- 遇到第一条非法或无法持久化的记录时停止，不越过错误记录。
- 返回 fetched、ingested、duplicates、cursor 和 errors，供 API 和日志展示。

同步状态位于：

```text
DREAM_HOME/source-state/internship.json
```

文件只保存 cursor、最近同步时间和最近结果，不保存 API key 或完整对话。

### `dream.api`

- 增加 `POST /v1/sources/internship/sync`，用于人工强制同步并返回同步结果。
- FastAPI lifespan worker 在现有 `run_pending()` 之前检查同步间隔；到期时先拉取，再执行 Background Review 和 Curator。
- 数据源关闭时，手动接口返回明确的 disabled 状态，后台 worker 跳过同步。

## 字段映射和隔离

每条源记录映射如下：

| 源字段 | DREAM字段 |
|---|---|
| 配置 `tenant_id` | `scope.tenant_id` |
| 配置 `agent_id` | `scope.agent_id` |
| `user_id` | `scope.user_id` |
| `internship` + `event_id` | `event_id` |
| `session_id` + `round_id` | `task_id` |
| `completed_at` | `completed_at` |
| `messages` | `transcript` |
| `final_response` | `final_response` |

如果源 `user_id` 已符合 DREAM 的安全 ID 规则，则原样使用；否则映射为 `external-` 加源 ID 的 SHA-256 短摘要。映射是确定性的，相同源用户始终进入同一个隔离目录，源 ID 本身不用于构造磁盘路径。

AI 决策卡继续位于 `tenant_id/agent_id` 作用域，可汇总不同用户对同一 AI 的行为反馈；用户画像继续位于 `tenant_id/agent_id/user_id` 作用域，不允许跨用户合并。Background Review 必须避免将某个用户的个人信息写入共享 AI 决策卡。

## 游标、幂等和失败恢复

处理顺序是：

1. 用已保存 cursor 请求下一批数据。
2. 验证记录顺序和字段。
3. 将第一条记录写入事件账本并加入待处理队列。
4. 事件落盘成功后，原子保存该记录 cursor。
5. 依次处理后续记录。
6. 批次结束后由现有 `run_pending()` 触发做梦。

如果 DREAM 在事件落盘后、cursor 更新前崩溃，下次会再次收到同一事件。同步器通过事件 ID 识别 duplicate，然后推进 cursor，不会重复学习。

HTTP 请求失败、解析失败或数据校验失败时不推进相关 cursor。错误只进入同步结果和应用日志，API key 与完整响应正文不写日志。同步失败不阻止 DREAM 处理已经进入本地事件账本的其他任务。

## 现有可靠性修正

当前 DREAM 的待处理队列在内存中，而事件账本在磁盘上。如果进程在事件落盘后、Background Review 完成前退出，重启后需要能够恢复未完成事件。该问题直接影响 Pull 接入的至少一次传输语义，因此本次实现同时增加可持久恢复的处理状态：

- 报告中成功完成的 Background Review 事件视为 processed。
- 服务启动或同步前，从事件账本重新发现尚无成功处理报告的事件并放回队列。
- 重复恢复不得生成第二份决策卡或用户画像证据。

## 测试与验收

自动化测试覆盖：

- 合法 NDJSON 多行解析。
- Bearer Token、`after`、`limit` 和 Accept Header。
- 非 2xx、超时、空行、非法 JSON 和字段缺失。
- 安全和不安全源用户 ID 的稳定隔离映射。
- 两个用户产生不同 `USER.md`，但共享同一 agent 的决策卡目录。
- cursor 只在事件持久化或确认重复后前进。
- DREAM 崩溃重试场景不会重复学习。
- 手动同步接口的 enabled、disabled、success 和 failure 响应。
- 后台定期同步后自动运行 Background Review。
- 全部现有测试继续通过，Ruff 检查通过。

验收时不要求真实外部 URL；测试使用内存 HTTP transport 模拟 JSONL API。真实联调时只需在 `.env` 中启用数据源并填写对方提供的 URL、Token、tenant ID 和 agent ID。
