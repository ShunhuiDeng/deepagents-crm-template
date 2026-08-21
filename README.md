# CRM 与企业知识库检索系统

基于 FastAPI、PostgreSQL 和 Deep Agents 的 CRM 项目。系统提供基础客户管理、角色权限、Agent 对话和企业知识库检索能力。

## 功能

- 线索、客户公司、联系人、商机和活动管理。
- 登录、HttpOnly Cookie 会话和角色权限控制。
- `admin / manager / sales / viewer` 四级角色。
- Agent 查询 CRM 业务数据和企业知识库。
- PostgreSQL + pgvector 文档检索，支持共享和私有文档。
- 基于 PostgreSQL 的会话状态和多轮记忆。
- CRM 新增、更新和线索转换须由用户确认后执行。
- 写入前执行权限、外键和版本校验，并保留审计记录。

## 架构

```text
浏览器
  ↓
FastAPI
  ├── CRM REST API
  ├── 登录、会话和权限检查
  ├── Agent 对话接口
  └── 知识库文档管理接口
  ↓
PostgreSQL
  ├── CRM 业务表
  ├── 会话、记忆、审计和待确认操作
  ├── LangGraph checkpoint
  └── pgvector 知识库向量
```

Agent 分工：

```text
主 Agent
├── crud-agent
│   └── 查询线索、客户、联系人、商机和活动
└── knowledge-agent
    └── 检索产品资料、销售手册和制度文档
```

`crud-agent` 只处理结构化 CRM 数据；`knowledge-agent` 只处理文档知识检索。两者均通过服务端注入的用户身份和权限运行。

## 技术栈

- Python 3.11+
- FastAPI
- PostgreSQL、psycopg、pgvector
- Deep Agents、LangChain、LangGraph
- OpenAI Embedding
- Pydantic
- 原生 JavaScript

## 目录

```text
app/
├── main.py                 # FastAPI 应用、路由和生命周期
├── database.py             # CRM 数据访问与权限校验
├── migrations.py           # PostgreSQL 迁移
├── knowledge.py            # 文档分块、向量生成和检索
├── permissions.py          # RBAC
├── schemas.py              # API 请求/响应模型
└── agents/
    ├── main_agent/         # 主 Agent
    ├── crud_agent/         # CRM 数据 Agent
    └── knowledge_agent/    # 知识库检索 Agent

scripts/
├── start-local.sh
├── ingest-knowledge.py     # 导入 Markdown/TXT 文档
└── clear-crm-data.py
```

## 环境要求

- Python 3.11+
- PostgreSQL
- uv
- 启用知识库功能时，PostgreSQL 需要安装 pgvector 扩展
- OpenAI API Key

## 配置与启动

复制配置文件：

```bash
cp .env.example .env.local
```

至少配置以下变量：

```dotenv
DATABASE_URL=postgresql://<user>:<password>@<host>:5432/<database>
MODEL_NAME=openai:gpt-5.4-mini
OPENAI_API_KEY=<your-api-key>
LANGGRAPH_AES_KEY=<64-hex-characters>

ENABLED_SUBAGENTS=crud-agent,knowledge-agent
KNOWLEDGE_EMBEDDING_MODEL=text-embedding-3-small
KNOWLEDGE_CHUNK_SIZE=1800
KNOWLEDGE_CHUNK_OVERLAP=240
```

启动服务：

```bash
uv sync --frozen
./scripts/start-local.sh
```

默认访问地址为 `http://127.0.0.1:8000`。

## 知识库 RAG

知识库使用两张表：

```text
knowledge_documents  # 文档标题、来源、可见范围和元数据
knowledge_chunks     # 文本分块、Embedding 和向量索引
```

处理流程：

```text
Markdown / TXT
  ↓
文本清洗和分块
  ↓
OpenAI Embedding
  ↓
写入 pgvector
  ↓
HNSW 余弦相似度检索
```

管理员可通过 API 写入文本，也可用脚本导入 Markdown/TXT：

```bash
uv run python scripts/ingest-knowledge.py ./docs/product.md \
  --owner-username <admin-username>
```

默认导入为共享文档；使用 `--private` 时，文档仅对导入账号可见。

## 权限与写入规则

| 角色 | 数据范围 | 写入 | 账号与知识库管理 |
|---|---|---|---|
| `admin` | 全部 | 是 | 是 |
| `manager` | 全部 | 是 | 否 |
| `sales` | 仅本人负责 | 是 | 否 |
| `viewer` | 全部，只读 | 否 | 否 |

Agent 不具备任意 SQL、宿主文件系统或环境变量访问能力。CRM 写入不会直接执行：Agent 只生成待确认操作，用户确认后由后端事务完成权限复检、关系校验、乐观锁校验和审计记录。

## 测试

```bash
uv sync --frozen
uv run ruff check app tests scripts
uv run pytest -q
node --check app/static/app.js
bash -n scripts/start-local.sh
```

## 说明

- 迁移在应用启动时执行；数据库账号需要具备建表、建索引权限。启用 RAG 时还需要创建 `vector` 扩展的权限。
- 当前会话串行执行。增加多个 Uvicorn worker 前，需要补充分布式锁或固定会话路由。
- 生产环境应通过 HTTPS 反向代理运行，并妥善管理数据库、模型和 LangGraph 加密密钥。

## 许可

本项目使用 [MIT License](LICENSE)。
