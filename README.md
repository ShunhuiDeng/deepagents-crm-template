# 智能 CRM（Intelligent CRM）

智能 CRM 是一个基于 FastAPI、Deep Agents 和 PostgreSQL 的可复用 CRM 模板。它提供常规 CRM 页面、角色权限、线索转客户闭环，以及带多轮记忆和人工确认机制的 AI 助手。前端与 API 由同一个服务提供，适合本机开发、局域网试用和后续生产化改造。

详细部署、使用、管理、备份和扩展说明见 [`docs/HANDOFF.md`](docs/HANDOFF.md)。

## 核心能力

- 登录、注册、注销和 HttpOnly Cookie 会话。
- `admin / manager / sales / viewer` 四级 RBAC。
- 线索、客户公司、联系人、商机和跟进活动五类业务实体。
- 线索原子转换为客户公司、联系人和可选商机。
- 客户公司 360° 视图与管理员整条客户链负责人转移。
- 主 Agent 与一个 CRM 数据子 Agent。
- 按账号和会话隔离的多轮记忆与 PostgreSQL checkpoint。
- Agent 查询即时执行；新增、更新和线索转换需人工确认。
- 写入前复检权限、负责人、外键和版本，并记录审计日志。

## 权限矩阵

| 角色 | 数据范围 | 新增 | 更新 | 删除 | 账号管理 | 整链转移 |
|---|---|---:|---:|---:|---:|---:|
| `admin` | 全部 | 是 | 是 | 是 | 是 | 是 |
| `manager` | 全部 | 是 | 是 | 是 | 否 | 否 |
| `sales` | 仅本人负责 | 是 | 是 | 否 | 否 | 否 |
| `viewer` | 全部，只读 | 否 | 否 | 否 | 否 | 否 |

权限由服务端认证上下文和数据库访问层执行，不依赖提示词。模型没有任意 SQL 工具，也不能自行指定业务负责人。

## 业务闭环

```text
线索
  └── 原子转换
      ├── 客户公司
      ├── 联系人
      ├── 可选商机
      └── 永久来源映射

客户公司 360°
  ├── 联系人
  ├── 商机
  ├── 跟进活动
  └── 来源线索
```

页面、REST API 和 Agent 共用同一套 PostgreSQL 数据契约。联系人和商机可先作为未关联记录录入；一旦关联客户公司，负责人和关系必须保持一致，商机的主要联系人也必须属于同一公司。跟进活动填写多个关联时，后端同样校验整条关系链。

## 快速启动

### 1. 准备环境

需要 Python 3.11+、PostgreSQL 兼容服务和 [uv](https://docs.astral.sh/uv/)。仓库预期托管在个人私有仓库 `ShunhuiDeng/deepagents-crm-template`。

当前模板包含 `crm_app_000_core_schema`，可在空 PostgreSQL 中创建 `users`、`leads`、`accounts`、`contacts`、`opportunities` 和 `activities` 六张核心表；后续追加迁移会创建认证、会话、审计、待确认和转换结构，LangGraph 会初始化 checkpoint 表。当前应用每次启动都会调用迁移检查，因此运行账号仍需建表、建索引和修改表权限。若要分离迁移角色与最小权限运行角色，需要先实现独立迁移入口和跳过启动迁移的开关。对已有数据库，`IF NOT EXISTS` 不会验证同名表是否发生契约漂移，接入前仍需核对 schema 并备份。

```bash
git clone git@github.com:ShunhuiDeng/deepagents-crm-template.git
cd deepagents-crm-template
cp .env.example .env.local
chmod 600 .env.local
```

### 2. 配置

编辑 `.env.local`。下面全部是占位值，不能原样用于部署：

```dotenv
DATABASE_URL=postgresql://<db-user>:<db-password>@<db-host>:<db-port>/<db-name>?sslmode=<ssl-mode>
MODEL_NAME=<provider:model-name>
OPENAI_API_KEY=<model-api-key>
LANGGRAPH_AES_KEY=<64-hex-characters>
APP_HOST=<listen-address>
APP_PORT=<listen-port>
COOKIE_SECURE=<true-or-false>
REGISTRATION_ENABLED=<true-or-false>
FIRST_USER_IS_ADMIN=<true-or-false>
FIRST_ADMIN_LOCAL_ONLY=<true-or-false>
ENABLED_SUBAGENTS=<comma-separated-subagent-names>
SUBAGENT_EXECUTION=<sync-or-async>
```

真实数据库密码、模型密钥和 AES 密钥只应保存在部署环境的机密管理系统或权限为 `0600` 的 `.env.local` 中，不得写入 Git、文档、日志或聊天记录。

### 3. 安装并启动

```bash
uv sync --frozen
./scripts/start-local.sh
```

服务电脑访问 `http://127.0.0.1:<listen-port>`。局域网设备访问 `http://<server-lan-ip>:<listen-port>`；要允许其他设备连接，监听地址应配置为所有接口，并在防火墙中仅放行可信网段。

## 首个管理员与账号开通

空账号库首次启动时，可配置为只允许从服务电脑本机注册首个管理员。后续注册账号默认是 `sales`，管理员在“账号管理”中调整角色。创建完所需账号后，应关闭公开注册并重启服务。

管理员密码不应出现在仓库。建议通过团队密码管理器交接，并在首次登录后按组织策略轮换。

维护模式下替换全部登录账号：

```bash
.venv/bin/python scripts/reset-admin.py \
  --username <new-admin-username> \
  --email <new-admin-email> \
  --display-name <new-admin-display-name> \
  --confirm RESET-DEEPAGENTS-CRM-ADMIN
```

脚本通过终端隐藏输入新密码。它不是日常开户工具；执行前必须停写、备份，并确认旧账号不再负责业务数据。

## AI 助手写入规则

- 查询当前账号有权查看的数据时立即执行。
- 新增、更新和线索转换只生成待确认动作。
- 发起人必须在前端检查字段并点击“确认执行”。
- 批准时服务端再次检查账号、角色、数据范围、外键和版本。
- 过期或冲突的动作不会写入，需重新查询后发起。
- AI 不提供业务删除工具；删除由常规 CRM 页面按角色执行。

## 测试

```bash
uv sync --frozen
uv run ruff check app tests scripts
uv run pytest -q
node --check app/static/app.js
bash -n scripts/start-local.sh
```

## 破坏性维护

以下命令保留登录账号和迁移元数据，但会清空 CRM 业务数据、会话、待确认项、审计、checkpoint，以及当前数据库中实际存在的共享知识库内容表；不存在的可选表会安全跳过。它不是备份命令。

```bash
.venv/bin/python scripts/clear-crm-data.py \
  --confirm CLEAR-DEEPAGENTS-CRM-DATA
```

执行前必须停止写入并完成可恢复备份。备份文件建议使用 `deepagents-crm-template-<timestamp>.dump` 命名并存放在仓库之外。

## Agent 扩展

```text
app/agents/
├── main_agent/       # 主 Agent、调度和会话记忆工具
├── crud_agent/       # CRM 子 Agent 与窄权限工具
├── registry.py       # 显式注册表
├── context.py        # 服务端认证后的运行上下文
└── service.py        # 会话串行和调用超时

agent_assets/skills/
├── supervisor/
└── crud-agent/
```

新增工具或 Skill 时，应使用强类型窄工具，从服务端运行上下文取得身份，在 Repository 层重复执行 RBAC，并让所有业务写入经过 pending、确认、事务和审计流程。

详见 [`docs/agent-architecture.md`](docs/agent-architecture.md) 和 [`docs/memory-architecture.md`](docs/memory-architecture.md)。

## 生产化要求

- 使用 HTTPS 反向代理并启用安全 Cookie。
- 为应用建立专用、最小权限的数据库角色。
- 限制数据库来源网络并验证 TLS 证书。
- 将机密放入机密管理系统，制定轮换和恢复流程。
- 建立 PostgreSQL 备份、恢复演练、日志和告警。
- 保持单 worker，直到实现同一会话的分布式锁或固定路由。
- 上线前完成真实数据库、浏览器、模型、并发和权限隔离验收。

## 许可

本仓库为私有专有软件，不授予开源许可。详见 [`LICENSE`](LICENSE)。
