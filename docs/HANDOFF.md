# 智能 CRM 部署、使用与运维手册

> 英文名称：Intelligent CRM
>
> 仓库：`ShunhuiDeng/deepagents-crm-template`（Private）
>
> 适用范围：单组织、单 FastAPI 服务、本机或受控局域网部署
>
> 文档原则：不记录真实账号、密码、主机、IP、密钥或客户数据

## 1. 文档用途

本文是可复用模板的接手手册，覆盖以下内容：

- 从私有仓库安装并启动系统；
- 让同一局域网的员工访问和登录；
- 使用五类 CRM 实体完成销售闭环；
- 管理账号、角色和数据归属；
- 使用带多轮记忆和人工确认的 AI 助手；
- 备份、恢复、排错、升级和安全加固；
- 为主 Agent 增加工具、子 Agent 和 Skill。

任何具体部署的密码和密钥都应通过组织批准的密码管理器交接，不得补写到本文或仓库中的其他文件。

## 2. 接手检查清单

接手人应先确认：

1. 已获得私有仓库的最小必要访问权限。
2. 已获得部署环境、PostgreSQL 和模型供应商的独立凭据。
3. 已确认凭据不是示例值，并存放在机密管理系统中。
4. 已确认数据库备份位置、保留周期和恢复负责人。
5. 已确认服务域名或局域网入口、防火墙规则和 HTTPS 方案。
6. 已确认谁是管理员、经理、销售和只读用户。
7. 已在隔离环境运行自动化测试和一次完整业务验收。

机密交接记录建议只包含以下字段，并存入密码管理器：

```text
部署环境：<environment-name>
应用入口：<application-url>
管理员用户名：<admin-username>
管理员密码项：<password-manager-item>
数据库连接项：<password-manager-item>
模型密钥项：<password-manager-item>
AES 密钥项：<password-manager-item>
备份存储项：<backup-location>
值班负责人：<operator-contact>
```

## 3. 系统范围

### 3.1 已实现

- 工作台指标、状态分布和最近记录。
- 线索、客户公司、联系人、商机、跟进活动的列表、搜索、分页、详情和按权限 CRUD。
- 线索到公司、联系人和可选商机的原子转换。
- 客户公司 360° 视图和永久来源关系。
- 管理员整条客户链负责人转移。
- 注册、登录、注销、角色调整和 HttpOnly Cookie 会话。
- 主 Agent 与一个 CRM 数据子 Agent。
- 多轮会话、摘要、会话长期记忆、待确认动作和审计。
- Agent 实时查询，以及新增、更新和转换的前端人工确认。
- 响应式单页前端，无单独的 Node 构建服务。

### 3.2 模板默认不包含

- 多租户、组织树和 PostgreSQL RLS。
- SSO、MFA、密码找回、自助改密和完整的账号停用页面。
- 邮件、日历、呼叫中心或第三方 CRM 同步。
- Agent 删除业务数据。
- 默认启用的知识库 RAG。
- 容器编排、进程守护和反向代理配置。
- 多 worker、横向扩容和分布式会话锁。
- 自动备份、恢复编排和外部监控平台。

以上能力在真实生产上线前应按组织要求补齐或明确接受风险。

## 4. 系统拓扑

```text
浏览器
   │ HTTPS（生产）或受控局域网 HTTP（试用）
   ▼
FastAPI 单进程
   ├── 静态 SPA
   ├── REST API / Cookie 认证 / RBAC
   ├── CRM Repository / 事务 / 审计
   └── Deep Agents 主 Agent
          └── crud-agent（窄权限 CRM 工具）
   │                         │
   ├──────── PostgreSQL ─────┤
   │   业务、账号、会话、pending、audit、checkpoint
   │
   └──────── 模型供应商 API
```

核心运行栈包括 Python、FastAPI、Pydantic、psycopg、PostgreSQL、Deep Agents、LangGraph PostgreSQL checkpoint、Argon2 和原生 JavaScript。

## 5. 代码结构

```text
deepagents-crm-template/
├── app/
│   ├── main.py              # FastAPI 生命周期、路由和异常映射
│   ├── config.py            # 环境变量与运行时配置
│   ├── schemas.py           # REST 与 Agent 强类型契约
│   ├── database.py          # Repository、SQL、事务和审计
│   ├── migrations.py        # 追加式迁移与 checksum
│   ├── permissions.py       # RBAC 权限矩阵
│   ├── security.py          # 密码和会话安全
│   ├── dependencies.py      # Cookie 认证依赖
│   ├── static/              # 单页前端
│   └── agents/
│       ├── main_agent/      # 主 Agent 与记忆工具
│       ├── crud_agent/      # CRM 数据子 Agent
│       ├── registry.py      # 子 Agent 白名单
│       ├── context.py       # 服务端认证运行上下文
│       └── service.py       # 会话锁和超时
├── agent_assets/skills/     # 运行时加载的 Skills
├── docs/                    # 使用、架构和记忆文档
├── scripts/                 # 启动和受保护维护脚本
└── tests/                   # 单元与契约测试
```

## 6. 安装与首次启动

### 6.1 前置条件

- Python 3.11 或更高兼容版本；
- PostgreSQL 兼容服务；
- `uv`；
- 可调用工具模型的模型供应商账号；
- 对私有仓库 `ShunhuiDeng/deepagents-crm-template` 的访问权限。

数据库前置条件：本模板的 `crm_app_000_core_schema` 可在空 PostgreSQL 中创建 `users`、`leads`、`accounts`、`contacts`、`opportunities` 和 `activities` 六张核心表，后续迁移再创建认证、会话、审计、待确认和转换结构，LangGraph 初始化 checkpoint 表。当前应用每次启动都会调用迁移检查，因此运行账号仍需建表、建索引和修改表权限。分离迁移角色与最小权限运行角色是待实现的生产化工作，需要先提供独立迁移入口和跳过启动迁移的开关。对已有数据库，`IF NOT EXISTS` 不会验证同名表的列和约束，接入前必须做 schema 对照与备份。

### 6.2 克隆仓库

```bash
git clone git@github.com:ShunhuiDeng/deepagents-crm-template.git
cd deepagents-crm-template
cp .env.example .env.local
chmod 600 .env.local
```

不要把 `.env.local`、数据库 dump、日志、客户导出或密码交接文件放入 Git。

### 6.3 运行配置

编辑 `.env.local`。下面全部是占位值：

```dotenv
DATABASE_URL=postgresql://<db-user>:<db-password>@<db-host>:<db-port>/<db-name>?sslmode=<ssl-mode>
MODEL_NAME=<provider:model-name>
OPENAI_API_KEY=<model-api-key>
LANGGRAPH_AES_KEY=<64-hex-characters>

SESSION_COOKIE_NAME=<cookie-name>
SESSION_TTL_HOURS=<session-hours>
COOKIE_SECURE=<true-or-false>
REGISTRATION_ENABLED=<true-or-false>
FIRST_USER_IS_ADMIN=<true-or-false>
FIRST_ADMIN_LOCAL_ONLY=<true-or-false>

APP_HOST=<listen-address>
APP_PORT=<listen-port>
DB_POOL_MIN_SIZE=<minimum-pool-size>
DB_POOL_MAX_SIZE=<maximum-pool-size>
DB_POOL_TIMEOUT_SECONDS=<pool-timeout-seconds>
CHECKPOINT_POOL_MAX_SIZE=<checkpoint-pool-size>

ENABLED_SUBAGENTS=<comma-separated-subagent-names>
AGENT_TIMEOUT_SECONDS=<agent-timeout-seconds>
SUBAGENT_EXECUTION=<sync-or-async>
SUBAGENT_SERVER_URL=<agent-protocol-url>
```

配置原则：

- `DATABASE_URL` 使用应用专用、最小权限的数据库角色。
- 远程 PostgreSQL 应验证 TLS 证书；仅使用加密但不验证证书不是最终生产方案。
- `LANGGRAPH_AES_KEY` 使用独立生成的 256 位密钥，并与数据库备份一同纳入恢复计划。
- 局域网试用可采用 HTTP；生产必须使用 HTTPS 并启用安全 Cookie。
- 只在需要开户的受控时间窗开启注册。
- 未部署 Agent Protocol 服务时使用同步子 Agent 模式。

### 6.4 安装依赖与启动

```bash
uv sync --frozen
./scripts/start-local.sh
```

启动脚本会运行数据库迁移并启动单个 FastAPI/Uvicorn 进程。终端应保留运行；关闭终端、停止进程、主机关机或休眠都会中断访问。

本机检查：

```text
http://127.0.0.1:<listen-port>
http://127.0.0.1:<listen-port>/health
http://127.0.0.1:<listen-port>/docs
```

端口监听检查：

```bash
lsof -nP -iTCP:<listen-port> -sTCP:LISTEN
```

## 7. 局域网访问

### 7.1 获取服务主机地址

macOS 可使用：

```bash
ipconfig getifaddr <network-interface>
```

Linux 可使用：

```bash
ip -brief address
```

其他设备访问：

```text
http://<server-lan-ip>:<listen-port>
```

服务主机上的 `127.0.0.1` 只代表该主机本身。员工电脑上的 `127.0.0.1` 代表员工自己的电脑，不能用来访问服务主机。

### 7.2 网络条件

- 服务必须监听所有需要接收连接的接口。
- 员工设备与服务主机应在可互通的可信网络中。
- 防火墙只应向获批网段开放应用端口。
- 访客 Wi-Fi 的客户端隔离、VPN 路由和终端安全软件都可能阻断访问。
- DHCP 地址可能变化；稳定使用应保留固定地址或配置内部 DNS。
- 服务主机应保持供电、联网和禁止自动休眠。

### 7.3 生产入口

局域网 HTTP 不提供传输加密，只适合无真实敏感数据的受控验证。正式使用建议：

1. 使用内部 DNS 或正式域名。
2. 在 FastAPI 前部署受支持的 HTTPS 反向代理。
3. 使用组织信任的证书。
4. 启用安全 Cookie。
5. 只开放反向代理端口，限制直接访问应用端口。

## 8. 登录、注册与退出

员工在登录页使用 CRM 用户名或邮箱和自己的 CRM 密码。数据库账号不能用于网页登录。

空账号库的推荐初始化流程：

1. 临时开启注册、首用户管理员和仅本机首管理员保护。
2. 从服务主机本机注册首个管理员。
3. 管理员确认登录和账号管理页面正常。
4. 员工在受控时间窗自行注册，默认成为 `sales`。
5. 管理员在“账号管理”中调整角色。
6. 关闭注册并重启服务。

用户名、邮箱必须唯一，密码至少满足应用校验和组织密码策略。公共或共享设备使用后必须点击页面中的退出按钮；只关闭浏览器不等于撤销服务端会话。

## 9. 页面使用指南

| 页面 | 用途 | 常用动作 |
|---|---|---|
| 工作台 | 查看五类业务概况和最近记录 | 刷新、快速新增、进入 AI |
| 线索 | 管理潜在客户 | 新增、搜索、编辑、转为客户 |
| 客户公司 | 管理公司主体 | 查看 360°、关联联系人、商机和活动 |
| 联系人 | 管理公司中的自然人 | 关联公司、维护职位和联系方式 |
| 商机 | 跟踪销售机会 | 维护金额、阶段、概率和预计成交日 |
| 跟进活动 | 记录电话、会议、邮件和任务 | 关联一致的业务链 |
| AI 助手 | 自然语言查询和发起变更 | 多轮问答、审核待确认动作 |
| 账号管理 | 调整成员角色 | 仅管理员可见 |

列表支持服务端搜索、分页、详情和按权限显示的操作按钮。看不到预期记录时，先清除搜索词和关联筛选，并确认当前账号的数据范围。

普通 CRM 页面的新增、编辑、删除和转换按按钮后直接提交后端；AI 发起的写入必须进入待确认区。两种入口使用相同的后端数据契约和权限规则。

## 10. 标准 CRM 业务闭环

### 10.1 录入线索

1. 进入“线索”，点击新增。
2. 填写姓名、公司、职位、联系方式、来源、状态、评分和描述。
3. 保存后检查列表和详情。
4. 销售创建的线索归本人；根实体未指定负责人时归创建人。

已转换状态只能通过正式转换流程产生，不能靠普通编辑伪造或撤回。

### 10.2 将线索转换为客户

从未转换线索的列表或详情进入转换流程：

1. 选择现有客户公司，或创建新公司。
2. 选择现有联系人、创建联系人，或根据线索资料生成联系人。
3. 可选创建首个商机。
4. 核对公司、联系人、商机和线索处理结果。
5. 勾选确认框并提交转换。

转换会在同一个数据库事务中写入公司、联系人、可选商机、来源映射和线索状态。任何一步失败都会整体回滚，避免产生半套客户数据。

### 10.3 客户公司 360°

客户公司详情聚合：

- 公司基础资料；
- 公司联系人；
- 公司商机；
- 直接或间接关联的跟进活动；
- 由正式转换建立的来源线索。

管理员可使用“转移负责人”将公司、联系人、商机、来源线索和关联活动整条链一次性转给新负责人。不要直接在数据库中只修改公司一行，否则会破坏所有权一致性。

### 10.4 联系人、商机和活动关系

- 联系人和商机允许先作为未关联记录录入；完整客户闭环中仍建议及时关联客户公司。
- 一旦联系人或商机关联客户公司，其负责人必须与公司一致；商机填写主要联系人时，该联系人必须属于同一公司。
- 跟进活动可以独立存在，也可关联线索、公司、联系人和商机；填写多个关联时必须组成一致的业务链。
- 后端会拒绝跨负责人、跨公司或逻辑矛盾的外键组合。

## 11. AI 助手与多轮记忆

### 11.1 会话操作

1. 进入“AI 助手”并新建会话。
2. 输入问题；Enter 发送，Shift+Enter 换行。
3. 同一会话会保留上下文、摘要和长期记忆。
4. 会话可重命名或删除。

删除会话会清理该会话的消息、记忆、checkpoint 和关联待确认项，但不会删除已写入的 CRM 业务数据。

### 11.2 查询与写入

| Agent 操作 | 行为 |
|---|---|
| 查询 | 立即读取当前账号有权查看的实时业务数据 |
| 新增 | 生成待确认动作 |
| 更新 | 生成待确认动作 |
| 线索转换 | 生成一个不可拆分的原子待确认动作 |
| 删除 | 不提供 Agent 工具 |

### 11.3 人工确认

1. 打开 AI 页面的“待确认”。
2. 核对操作类型、目标记录和全部字段。
3. 点击“确认执行”或“拒绝”。
4. 批准时后端重新检查发起人、角色、行级范围、外键、版本和有效期。

每个账号只能处理自己发起的动作，管理员也不能代替其他账号批准。过期、记录已变化或权限已变化的动作会失败，应重新查询后生成新动作。

### 11.4 隔离原则

- 会话元数据按用户和会话 UUID 隔离。
- PostgreSQL checkpoint 使用用户与会话共同派生的内部 thread ID。
- CRM 子 Agent 不能读取主 Agent 的会话记忆目录。
- 结构化 CRM 事实始终来自业务表，不使用聊天记忆代替。
- 销售的 Agent 查询和写入仍只能作用于本人负责的数据。

详见 [`memory-architecture.md`](memory-architecture.md)。

## 12. 角色与管理

| 角色 | 可见数据 | 新增/编辑 | 删除 | 账号管理 | 整链转移 |
|---|---|---|---|---|---|
| `admin` | 全部 | 可以 | 可以 | 可以 | 可以 |
| `manager` | 全部 | 可以 | 可以 | 不可以 | 不可以 |
| `sales` | 仅本人负责 | 可以 | 不可以 | 不可以 | 不可以 |
| `viewer` | 全部，只读 | 不可以 | 不可以 | 不可以 | 不可以 |

管理员的日常职责：

- 在开户时间窗结束后关闭注册。
- 审核角色变更，避免授予超出职责的权限。
- 在人员变动前完成客户整链转移。
- 核对销售看不到数据是归属问题还是系统问题。
- 定期检查审计日志、失败请求和待确认动作。
- 确认备份成功并执行恢复演练。

系统会阻止降级最后一个有效管理员。当前模板不提供完整的账号停用、删除、自助改密和管理员重置单个用户密码界面；生产人员管理应补齐这些能力或接入组织身份系统。

## 13. 数据模型与一致性

| 表 | 含义 | 负责人字段 | 关键关系 |
|---|---|---|---|
| `users` | 登录账号和负责人 | — | 被业务实体引用 |
| `leads` | 线索 | `owner_id` | 可产生正式转换 |
| `accounts` | 客户公司 | `owner_id` | 联系人和商机的父实体 |
| `contacts` | 联系人 | `owner_id` | `account_id` |
| `opportunities` | 商机 | `owner_id` | `account_id`、`primary_contact_id` |
| `activities` | 跟进活动 | `assigned_user_id` | 可关联四类业务实体 |
| `lead_conversions` | 转换映射和来源快照 | `converted_by` | 连接线索、公司、联系人和商机 |

应用还维护会话、记忆、待确认、审计和 LangGraph checkpoint 表。业务记录和 Agent 记忆都在 PostgreSQL 中；本地代码目录不保存客户业务记录。

根实体默认归创建人，子实体通常继承父实体负责人。销售不能通过猜测 UUID 访问或关联其他销售的数据。

## 14. API 与错误语义

主要 API 分组：

| 分组 | 路径 |
|---|---|
| 健康检查 | `GET /health` |
| 认证 | `/api/auth/*` |
| 用户与角色 | `/api/users*` |
| 工作台 | `/api/dashboard` |
| 五类业务实体 | `/api/leads*`、`/api/accounts*`、`/api/contacts*`、`/api/opportunities*`、`/api/activities*` |
| 线索转换 | `/api/leads/{id}/convert` |
| 公司全景和转移 | `/api/accounts/{id}/overview`、`/api/accounts/{id}/transfer` |
| Agent 会话 | `/api/conversations*`、`/api/chat` |
| 待确认动作 | `/api/pending-actions*` |

常见状态码：

- `422`：请求字段格式、类型或必填校验错误；
- `401`：未登录、凭据错误或会话过期；
- `403`：角色或负责人范围不允许；
- `404`：记录不存在或对当前用户不可见；
- `409`：业务规则、唯一性、版本或并发冲突；
- `503`：数据库暂不可用；
- `502/504`：模型供应商失败或超时。

日志中的 request ID 应用于定位一次失败请求，但不得把请求正文、Cookie 或机密写入工单。

## 15. 日常运维

### 15.1 每日检查

1. 服务进程和主机运行正常。
2. `/health` 从本机和获批客户端均可访问。
3. 登录、CRM 列表读取和无写入 AI 查询正常。
4. 日志没有持续数据库或模型错误。
5. 注册开关处于预期状态。
6. 备份任务最近一次运行成功。

### 15.2 变更前检查

1. 创建可恢复备份。
2. 在隔离环境运行迁移和测试。
3. 核对 `.env.local` 与目标环境差异。
4. 记录回滚条件和负责人。
5. 选择低流量窗口并通知用户。

### 15.3 服务重启

配置和代码变更后，应优雅停止旧进程并重新运行启动脚本。重启后检查健康接口、登录、业务列表和 Agent 查询。不要同时启动两个指向同一端口的实例。

## 16. 备份与恢复

### 16.1 数据库备份

在停止写入或使用一致性备份策略后执行。以下命令不会自动读取 `.env.local`；必须先由秘密管理系统向当前维护 shell 安全注入 `DATABASE_URL`，并在不打印其值的前提下核对目标：

```bash
pg_dump \
  --format=custom \
  --file=deepagents-crm-template-<timestamp>.dump \
  --dbname="$DATABASE_URL"
```

备份必须存放在仓库之外、加密且受访问控制的位置。备份范围应同时覆盖业务表、账号、会话、审计、迁移记录和 checkpoint。

### 16.2 恢复演练

始终恢复到平台或 DBA 预先创建的隔离数据库。由秘密管理系统单独注入 `RESTORE_DATABASE_URL`，不要在命令历史中写明文连接串：

```bash
pg_restore \
  --clean \
  --if-exists \
  --no-owner \
  --dbname="$RESTORE_DATABASE_URL" \
  deepagents-crm-template-<timestamp>.dump
```

恢复后：

1. 把隔离环境的 `RESTORE_DATABASE_URL` 配置为应用的 `DATABASE_URL` 后启动应用。
2. 检查迁移状态和健康接口。
3. 验证账号、五类实体、转换关系、审计和 Agent 会话。
4. 用不同角色验证数据隔离。
5. 记录恢复耗时和缺失项。

生产恢复前应根据数据库平台调整命令，避免把测试恢复误指向运行中的数据库。

### 16.3 AES 密钥

checkpoint 加密密钥必须与数据库备份分开保管，但纳入同一恢复流程。丢失密钥可能导致历史 checkpoint 无法解密；密钥泄露则应按组织流程轮换和处置。

## 17. 受保护维护脚本

### 17.1 清空业务数据

```bash
.venv/bin/python scripts/clear-crm-data.py \
  --confirm CLEAR-DEEPAGENTS-CRM-DATA
```

该脚本保留登录账号和迁移元数据，但清空 CRM 业务、会话、待确认、审计、checkpoint，以及当前数据库中实际存在的共享知识库内容表；不存在的可选表会安全跳过。执行前必须停写、备份、核对目标数据库并获得批准。

### 17.2 重建唯一管理员

```bash
.venv/bin/python scripts/reset-admin.py \
  --username <new-admin-username> \
  --email <new-admin-email> \
  --display-name <new-admin-display-name> \
  --confirm RESET-DEEPAGENTS-CRM-ADMIN
```

脚本通过隐藏输入读取密码，并在旧账号仍负责业务数据时拒绝执行。它会替换凭据用户及其会话相关数据，只适用于停服维护或灾难恢复，不适用于日常员工开户。

## 18. 故障排查

| 现象 | 常见原因 | 处理顺序 |
|---|---|---|
| 局域网打不开 | 服务停止、主机休眠、地址变化、防火墙、网络隔离 | 查本机健康接口、监听端口、当前地址和网络策略 |
| 登录失败 | 用户名/邮箱或密码错误、会话过期 | 核对 CRM 账号并重新登录 |
| 返回 403 | 角色不足或销售访问他人数据 | 核对角色和负责人归属 |
| 销售列表为空 | 数据属于其他账号或筛选未清除 | 清除筛选并由管理员核对归属 |
| 返回 503 | PostgreSQL 网络、TLS、连接池或容量问题 | 检查数据库可达性、证书、连接数和 request ID |
| AI 返回 502/504 | 模型密钥、出口、额度或超时 | 检查供应商状态和配置；CRM 页面可独立使用 |
| 待确认动作失败 | 动作过期、记录版本或权限变化 | 重新查询并生成新动作 |
| 浏览器图标 404 | 未提供对应静态图标 | 不影响 CRM、数据库或 AI 功能 |

### 18.1 数据库连接检查

以下命令不会自动读取 `.env.local`。确认秘密管理系统已向当前 shell 注入 `DATABASE_URL` 后再执行：

```bash
psql "$DATABASE_URL" -c "select current_database(), current_user;"
```

不得把展开后的连接串复制到日志、截图或工单。远程连接失败时依次检查 DNS、端口、来源网段、TLS、角色权限、连接上限和数据库服务状态。

### 18.2 模型检查

普通 CRM 页面不依赖模型调用。若只有 AI 失败，优先核对模型名称、供应商密钥、网络出口、额度和超时配置。任何诊断输出都应隐藏请求内容和密钥。

## 19. 安全基线

- 真实凭据只进入机密管理系统或受保护的运行环境。
- 数据库使用最小权限角色和可信网络来源限制。
- 生产入口使用 HTTPS、安全 Cookie 和合理会话时长。
- 管理员账号单独分配，不共享密码。
- 默认关闭不需要的注册入口。
- 定期轮换数据库、模型和加密密钥。
- 审计账号变更、负责人转移和 Agent 批准写入。
- 备份加密、限制访问并定期恢复演练。
- 日志不得记录密码、Cookie、Authorization、连接串或完整客户敏感字段。
- 依赖升级先在隔离环境完成测试。

## 20. Agent、工具与 Skill 扩展

### 20.1 当前边界

主 Agent 负责对话、记忆和调度；`crud-agent` 负责五类实体查询、新增、更新、公司全景和线索转换。模型不接收任意 SQL、宿主文件系统或环境变量读取能力。

### 20.2 新增工具规则

1. 创建单一用途、强类型工具，不添加通用 SQL。
2. 身份、角色、会话和 request ID 只从服务端运行上下文取得。
3. Repository 层重复校验 RBAC、负责人和关联关系。
4. 写入先生成 pending，再由发起人确认。
5. 在事务中处理幂等、并发版本、业务写入和审计。
6. 为跨账号访问、无权限角色、无效外键和并发冲突增加测试。

### 20.3 新增子 Agent

1. 在 `app/agents/<subagent_name>/` 定义提示词和工具。
2. 在显式注册表中登记，不依赖自动发现。
3. 为虚拟文件系统定义最小只读路径。
4. 禁止读取主 Agent 记忆和其他 Skill 根目录。
5. 配置启用名单并运行权限边界测试。

### 20.4 新增 Skill

Skill 放在 `agent_assets/skills/<skill-name>/`，内容应描述流程和工具选择，不承载认证、授权或数据库完整性。安全规则必须由代码和数据库层执行。

详见 [`agent-architecture.md`](agent-architecture.md)。

## 21. 测试与发布

```bash
uv sync --frozen
uv run ruff check app tests scripts
uv run pytest -q
node --check app/static/app.js
bash -n scripts/start-local.sh
```

发布前至少人工验证：

1. 首个管理员限制和后续账号角色调整。
2. 四个角色的列表、详情、新增、更新和删除边界。
3. 销售无法访问其他销售的记录。
4. 线索转换全部成功或全部回滚。
5. 公司 360° 和管理员整链转移。
6. Agent 多轮记忆和跨账号隔离。
7. Agent 写入必须确认，且只有发起人可批准。
8. 数据库短暂断线后的恢复行为。
9. 局域网客户端和 HTTPS 入口。
10. 备份能够恢复到隔离环境。

## 22. 已知限制与扩展顺序

建议按以下顺序生产化：

1. HTTPS、机密管理、最小权限数据库和自动备份。
2. 完整用户生命周期、MFA 或 SSO。
3. 监控、告警、审计查询和管理员会话撤销。
4. 多租户与 PostgreSQL RLS。
5. 分布式锁和多实例部署。
6. 知识库 RAG、邮件、日历和其他业务集成。

在完成相应设计和测试前，不要直接增加多 Uvicorn worker，也不要让任何 Agent 获得任意 SQL 或宿主文件系统访问权限。

## 23. 移交验收记录模板

```text
仓库访问已验证：<yes-or-no>
隔离环境启动成功：<yes-or-no>
健康检查通过：<yes-or-no>
四角色验收通过：<yes-or-no>
CRM 闭环验收通过：<yes-or-no>
AI 查询和待确认通过：<yes-or-no>
局域网或 HTTPS 入口通过：<yes-or-no>
备份恢复演练通过：<yes-or-no>
机密已进入密码管理器：<yes-or-no>
上线阻断项：<remaining-blockers>
接手负责人：<name-or-team>
审批记录：<change-record>
```

本文只描述模板的部署和运维方法，不是凭据载体。任何具体部署的机密都必须在仓库之外交接。
