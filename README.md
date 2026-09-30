# RRS：审稿人推荐与编辑 Agent 工作台

项目介绍网页：[在线导览](https://HxZhu0629.github.io/rrs_editor_agent/)（需首次启用 GitHub Pages 并完成部署）。包含产品流程、两类检索原理、Agent 交互演示、工程架构和运行说明。[网站源码与发布步骤](site/README.md)。

RRS（Reviewer Recommendation System）是一套面向期刊编辑的开源原型。它把“稿件导入、元数据确认、证据检索、审稿人推荐、结果解释、邀请草稿”串成一个可追溯的工作流，并提供一个带工具治理和人工审批的编辑 Agent。

项目定位是**编辑决策辅助**：系统返回候选人、证据和草稿，最终的审稿人选择、身份确认、利益冲突判断和邮件发送仍由编辑负责。当前 API 默认允许匿名访问，适合本地开发和演示；正式部署前需要补充认证、权限、TLS、备份和媒体访问控制。

## 能做什么

- 上传 PDF 或 DOCX，并使用 Unstructured 解析文本、页码和文档质量信号。
- 使用 DeepSeek 从结构化 Markdown 中提取标题、摘要、关键词、作者和机构，编辑可以逐项修改后确认。
- 按 Markdown 标题建立父子分层切块，支持 BM25、向量、元数据和 RRF 混合召回；可选启用 Query Rewrite、HyDE、Cross-Encoder 重排和 Self-RAG 反思。
- 基于稿件主题和审稿人论文库生成 Top-K 推荐，过滤同机构、近年合作者等已知冲突，并保存每个推荐的论文证据、召回路线和评分拆解。
- 通过 OpenAlex、Crossref 和 ORCID 的只读 MCP 服务检索外部学术信息，外部内容会标记为不可信证据并要求保留 DOI、ORCID 或 OpenAlex 标识。
- 通过 LangGraph + Celery 运行编辑 Agent：检查投稿完整性、检索稿件证据、推荐并解释审稿人、生成邀请邮件草稿。
- Agent 运行状态、工具轨迹、检查点、上下文压缩和审批中断持久化到数据库；长任务可轮询、恢复和重试。
- 邮件工具只生成草稿并返回 requires_human_approval=true，当前不会自动发送邮件。
- 提供 Prometheus 指标、健康检查、质量评测命令和 SQLite/PostgreSQL 回归测试。

更完整的产品和 Agent 文案见 docs/product-copy.md，架构说明见 docs/system-design.md，运行手册见 docs/reliability.md。

## 技术栈

| 层 | 实现 |
| --- | --- |
| 前端 | Vue 3、Element Plus、Vite、Axios |
| API | Django 5、Django REST Framework |
| 数据库 | PostgreSQL + pgvector；测试可使用 SQLite 的 JSON 向量回退 |
| 异步任务 | Celery + Redis |
| 文档解析 | Unstructured、pypdf、python-docx |
| 生成式模型 | DeepSeek OpenAI 兼容接口 |
| Embedding | Ollama，默认 nomic-embed-text:latest |
| Agent | LangChain StructuredTool、LangGraph、Django 持久化 Checkpoint |
| 外部学术数据 | stdio MCP 服务，连接 OpenAlex、Crossref、ORCID |
| 部署与运维 | Docker Compose、Nginx、Prometheus |

## 目录结构

~~~text
rrs/
├── backend/
│   ├── config/                 # Django、Celery、环境配置
│   ├── apps/common/            # 健康检查、日志、指标、质量评测
│   ├── apps/manuscripts/       # 稿件、作者、机构与确认接口
│   ├── apps/extraction/        # 文档解析、分层切块与 Advanced RAG
│   ├── apps/reviewers/         # 审稿人画像、论文与合作者关系
│   ├── apps/recommendations/   # 召回、重排、推荐结果与证据
│   ├── apps/agent/             # Agent 图、工具、策略、记忆、审批与任务
│   └── mcp_servers/            # 独立的只读学术 MCP 服务
├── frontend/src/               # 编辑工作台、编辑助手和 API 客户端
├── scripts/                    # OpenAlex 数据获取与实验数据处理脚本
├── docs/                       # 架构、可靠性、迭代记录和产品文案
├── data/evaluation/            # 可提交的评测样例与门禁配置
├── infra/monitoring/           # Prometheus 抓取配置与告警规则
└── docker-compose*.yml         # 开发、服务器和监控拓扑
~~~

本地稿件、SQLite 数据库、静态文件、虚拟环境、实验原始数据、备份和监控 token 不属于源码发布内容，已在 .gitignore 中排除。data/README.md 说明了评测样例与本地产物的边界。

## 快速开始

### 1. 准备环境

需要 Docker Desktop，以及 DeepSeek API Key。启用向量检索时，还需要在宿主机运行 Ollama 并准备 embedding 模型：

~~~bash
ollama pull nomic-embed-text:latest
copy .env.example .env        # Windows PowerShell
# cp .env.example .env        # Linux/macOS
~~~

编辑 .env，至少设置：

~~~dotenv
DJANGO_SECRET_KEY=请替换为随机长字符串
DEEPSEEK_API_KEY=你的 DeepSeek API Key
OLLAMA_BASE_URL=http://host.docker.internal:11434
~~~

如果只运行不调用模型的检查，可以把 DB_ENGINE=sqlite 交给 Django 测试命令；容器编排默认使用 PostgreSQL + pgvector。

### 2. 启动服务

~~~bash
docker compose up -d --build
docker compose exec backend python manage.py migrate
~~~

打开 http://localhost。后端 API 在 http://localhost:8000，健康检查为 /health/live/ 和 /health/ready/。

服务器部署使用更收敛的拓扑：

~~~bash
docker compose -f compose.server.yml up -d --build
~~~

该配置只公开前端端口，数据库、Redis、Django 和 Celery worker 留在 Compose 私有网络中。

### 3. 导入审稿人数据

推荐接口需要 ReviewerProfile 和 ReviewerPaper 数据。可以使用管理命令导入 OpenAlex 导出的 JSON，也可以调用 /api/reviewers/bulk-upsert/ 写入自己的数据。已有向量数据时，按需执行：

~~~bash
docker compose exec backend python manage.py backfill_reviewer_papers
docker compose exec backend python manage.py backfill_reviewer_paper_embeddings --only-missing
docker compose exec backend python manage.py backfill_document_chunk_embeddings --only-missing
~~~

## 核心 API

### 稿件与解析

~~~text
POST  /api/manuscripts/upload/
GET   /api/manuscripts/
GET   /api/manuscripts/{manuscript_id}/
PATCH /api/manuscripts/{manuscript_id}/confirm/
POST  /api/extraction/manuscripts/{manuscript_id}/run/
GET   /api/extraction/runs/{run_id}/chunks/?kind=parent|child
GET   /api/extraction/runs/{run_id}/vector-search/?q=...&top_k=8
~~~

上传后先执行解析，再由编辑确认元数据。vector-search 会返回召回路线、父级上下文和降级状态；rewrite=1、hyde=1、rerank=1、reflect=1 可按请求打开对应能力。

### 推荐与证据

~~~text
POST /api/recommendations/run/       { manuscript_id, top_k, method }
GET  /api/recommendations/runs/{run_id}/
GET  /api/recommendations/papers/{paper_id}/access/
~~~

推荐记录包含候选池大小、返回数量、综合分、召回路线、代表论文、匹配关键词、冲突风险和证据片段。全文入口只使用 DOI、OpenAlex 和已验证的开放获取链接。

### 编辑 Agent

~~~text
POST /api/agent/conversations/                              { manuscript_id }
GET  /api/agent/conversations/{conversation_id}/
POST /api/agent/conversations/{conversation_id}/messages/   -> 202 AgentRun
GET  /api/agent/runs/{run_id}/                              -> 轮询状态
POST /api/agent/runs/{run_id}/approval/                     -> 恢复审批中断
~~~

Agent 的工具包括：

- check_submission_completeness：检查投稿字段和文档质量。
- search_manuscript_evidence：从稿件父子切块中检索支持证据。
- search_external_papers、verify_paper_metadata、lookup_author_identity、check_retraction_status：通过学术 MCP 访问外部只读数据。
- recommend_reviewers、explain_recommendation：生成和解释冲突感知的候选名单。
- draft_reviewer_emails：生成邀请草稿，明确标记人工审批，不执行外部发送。

Agent 通过 READ、DRAFT、ACTION 风险等级治理工具。当前注册工具以读取和草稿为主；执行潜在外部副作用的工具会先进入 waiting_approval，通过 approved 和 approved_call_ids 恢复。

## Agent 开发说明

Agent 主循环位于 backend/apps/agent/graph.py，工具定义位于 backend/apps/agent/tools.py，策略位于 backend/apps/agent/policy.py，上下文压缩位于 backend/apps/agent/compaction.py。

每个运行使用 AgentRun.id 作为 LangGraph thread_id，检查点和 pending writes 存在 Django 数据库。Celery 负责长任务，Redis 只承担 broker/result backend；租约、late ack、定时对账和幂等终态写入用于控制重复投递和失联任务。系统不宣称外部副作用 exactly-once，未来加入真实发送动作时仍需目标端幂等键或 outbox。

上下文按 managed → user → project → local → auto → team 分层加载，支持 @relative-file.md include 和 paths frontmatter。上下文超限时依次使用 Tool 结果预算、Snip、Microcompact、Context Collapse 和可选的 JSON Auto Compact；工具调用与对应结果不会被拆开。

Langfuse 集成默认关闭。需要时安装 backend/requirements/observability.txt，设置 LANGFUSE_PUBLIC_KEY、LANGFUSE_SECRET_KEY 和 LANGFUSE_TRACING_ENABLED=1。追踪失败不会阻塞编辑工作流。

## 测试与质量评测

前端构建：

~~~bash
cd frontend
npm ci
npm run build
~~~

后端检查与回归测试：

~~~bash
$env:DB_ENGINE='sqlite'
python backend/manage.py check
python backend/manage.py test apps.common apps.extraction apps.recommendations apps.agent --verbosity 1
~~~

评测器使用明确标记为 synthetic 的样例，不代表线上质量：

~~~bash
python backend/manage.py evaluate_quality --cases data/evaluation/demo_cases.jsonl --output data/evaluation/demo_report
~~~

真实评测应先导出案例、人工标注证据和 Agent 任务，再使用固定 split、Recall/MRR/nDCG、工具调用指标和 bad case 回归。评测流程详见 data/evaluation/README.md。

## 配置索引

.env.example 按功能列出数据库、DeepSeek、Ollama、RAG、Agent、MCP、Celery、监控和超时参数。几个常用开关：

| 变量 | 用途 |
| --- | --- |
| DB_ENGINE | postgresql（默认）或 sqlite（测试） |
| DEEPSEEK_API_KEY | 元数据抽取和 Agent 推理 |
| OLLAMA_BASE_URL / OLLAMA_EMBED_MODEL | 向量生成 |
| RAG_QUERY_REWRITE_ENABLED / RAG_HYDE_ENABLED | 默认关闭的查询增强 |
| RERANKER_MODEL | 可选 Cross-Encoder 模型 |
| ACADEMIC_MCP_ENABLED | 是否启用外部学术 MCP |
| AGENT_MAX_TURNS / AGENT_MAX_TOOL_CALLS | Agent 运行预算 |
| LANGFUSE_TRACING_ENABLED | 可选 Agent tracing |
| METRICS_TOKEN | /metrics 和 worker 指标鉴权 |

## 已知边界

- 默认权限是 AllowAny，没有多租户隔离。
- 解析、推荐和 Agent 在本地原型中仍有同步 API 入口；Agent 对话执行通过 Celery 异步化，前端以轮询读取。
- Embedding、重排、MCP 或 DeepSeek 不可用时会降级或失败，响应中的 degraded、degraded_reasons 和错误字段需要由调用方处理。
- 同机构别名、作者身份消歧和外部“无撤稿记录”都不能被解释为绝对证明。
- RRF、向量相似度和重排分数是排序信号，不是经过校准的概率。

## 开源发布

首次发布前确认 .env、backend/media/、backend/db.sqlite3、data/backups/、data/experiments/ 和 infra/monitoring/metrics-token.txt 没有被 Git 跟踪。项目当前未附带许可证文件；如需允许他人复用，请在公开仓库中补充适合的许可证。

~~~bash
git init
git add .
git commit -m "整理 RRS 编辑 Agent 工作台"
git branch -M main
git remote add origin https://github.com/HxZhu0629/rrs-editor-agent.git
git push -u origin main
~~~
