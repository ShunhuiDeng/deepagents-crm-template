# 智能 CRM 会话隔离与记忆架构

## 四类数据

### 1. 会话目录

`crm_conversations` 保存标题、消息数和时间等用户可见元数据。每行属于一个 `owner_user_id`；所有会话 API 都同时匹配当前登录用户和会话 UUID。

### 2. 工作记忆

公开会话 UUID 与认证用户 UUID 一起哈希为内部 LangGraph `thread_id`。`AsyncPostgresSaver` 保存完整消息、Agent 状态、工具调用和摘要状态；配置有效 `LANGGRAPH_AES_KEY` 时，checkpoint payload 使用 AES 加密。生产环境必须配置并妥善备份该密钥；未配置时 checkpoint 会使用默认序列化方式保存。

同一会话每轮只提交新增消息，checkpointer 在进程重启后恢复先前历史。不同用户即使使用相同公开 UUID，也会得到不同 checkpoint key。

删除会话时，应用先调用 `adelete_thread()` 清理 checkpoint、blob 和 tool writes，再删除会话元数据。

### 3. 会话长期记忆

`crm_conversation_memories` 以 `(owner_user_id, conversation_id)` 分区，保存当前会话未来轮次需要继续使用的目标、约束、决策和待办。外键引用 `crm_conversations(owner_user_id, id) ON DELETE CASCADE`。

只有主 Agent 的 memory tools 可管理这层数据。CRM 子 Agent 的虚拟文件权限明确拒绝 `/memory/**`，因此无法读取主 Agent 注入的会话记忆。

### 4. CRM 业务事实

业务事实使用服务器现有 `leads / accounts / contacts / opportunities / activities` 五张主表，以及应用迁移创建的 `lead_conversions` 转换关系。它们不是 Agent memory；每次查询都通过 Repository 和角色/负责人范围读取真实业务行。

- `sales` 仅能访问 `owner_id = 当前用户` 或 `assigned_user_id = 当前用户` 的业务行，关联外键也必须属于自己；
- `admin / manager / viewer` 可读取全部，其中 viewer 无写权限；
- 会话删除不影响业务数据；
- Agent 对五类实体的 insert/update，以及线索到公司、联系人和商机的原子转换，都必须经 `crm_pending_actions` 确认后才修改业务表。

## 并发与压缩

同一 `(user_id, conversation_id)` 的 Agent 轮次在进程内串行，防止两个请求同时推进同一 checkpoint。每轮还有总超时。
当前启动脚本固定单进程；不要直接增加多个 Uvicorn worker。横向扩容前需将同一 thread
路由到固定 worker，或增加 PostgreSQL/Redis 分布式锁。

Deep Agents 使用模型感知的摘要中间件，在上下文接近模型上限时压缩较早消息并保留近期上下文。摘要状态属于当前 thread，不会跨账号或会话共享。

## 后续扩展

- 跨会话用户偏好应放入独立 Store，并以 `(tenant_id, user_id)` namespace 隔离。
- 客户、联系人、商机和活动等结构化事实继续使用业务表，不能用向量记忆替代。
- 会话长期记忆量明显增加后，可利用现有 pgvector 做同一会话范围内的语义召回；禁止跨用户或跨会话召回。
- 多公司部署时，在现有 user owner 边界之上增加 `tenant_id`，并用 PostgreSQL RLS 作为第二道隔离。
