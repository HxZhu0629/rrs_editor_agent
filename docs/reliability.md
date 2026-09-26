# RRS 可靠性与运行手册

这套实现提供观测、保护和验证机制。SLO 是待验证的内部目标，SLA 是对外承诺；不能由代码或单元测试证明已达到生产 SLA。上线前需要真实标注集、真实依赖压测和持续运行数据。

## 启用

1. 安装 `backend/requirements/base.txt`，执行 `python backend/manage.py migrate`。新增迁移 `agent/0004_reliability.py`，保存任务时间、请求关联与执行租约。
2. Docker 使用 `docker compose -f compose.server.yml up -d --build`。后台 API、Celery worker、单实例 beat、Redis 和 PostgreSQL 均需运行。开发部署使用 `docker-compose.yml`。
3. 在 `.env` 中配置 `AGENT_MAX_PENDING_RUNS=20`、`AGENT_QUEUE_TIMEOUT=300`、`WORKER_CONCURRENCY=2`。这些是起始保护值，不是压测得出的容量。生产使用 PostgreSQL；SQLite 不提供这里需要的行级并发锁。
4. 按实际耗时设置 Embedding、模型、MCP 超时。现有 Embedding 超时默认 120 秒，发生故障时首次降级仍需等待该超时；建议先测量正常 P99，再降低 `OLLAMA_EMBED_TIMEOUT`。现有 HTTP 重试、模型重试和任务上限需一起计算，不能只考虑单次超时。

## Tracing、指标与日志

- HTTP 接收合法 `X-Request-ID`、W3C `traceparent`，返回 `X-Request-ID` 和 `X-Trace-ID`。请求关联信息随 AgentRun 入库，worker 的开始、结束、异常和模型/工具 span 日志带上 request/trace/run/task ID。审批恢复保留原始运行关联信息。
- `rrs.events` 输出 JSON 到 stdout；新增业务观测日志不记录原始提问、文稿、工具参数或异常内容。AgentRun 中原有完整工具轨迹仍是业务审计数据，需要适当的访问控制。
- 已有 Langfuse 可选集成保留：安装 `backend/requirements/observability.txt`、配置密钥并设置 `LANGFUSE_TRACING_ENABLED=1` 后，可视化 LangGraph/模型/工具调用。Langfuse trace ID 为去横线的 run UUID；`agent_started.langfuse_trace_id` 提供 HTTP trace 的映射。只有启用时才向该服务发送模型上下文。
- `/health/live/` 只检查进程；`/health/ready/` 检查数据库和 Redis，失败返回 503。它不证明模型或 worker 正常；结合 worker 抓取状态、排队时长与停滞告警判断。
- `/metrics` 和 worker 内网 `:9101/metrics` 需要 `Authorization: Bearer <METRICS_TOKEN>`；未配置 token 时拒绝访问。默认前端 Nginx 不公开这些运维端点。
- Gunicorn/Celery 各自启动脚本在导入 Python 前创建独立 Prometheus multiprocess 目录。一个容器一个目录，不能跨容器共享；抓取会聚合该实例所有子进程。手动启动多进程也必须在导入前配置目录。容器重建后计数器正常归零，查询使用 rate/increase。
- `observe/event` 失败不阻塞业务；监控抓取失败返回 503，由 `up` 告警。模型/工具 span 在当前执行段落完成后导出；进程被强杀时可能缺失，通过超时对账发现，不能将缺失视为成功。

主要指标：

| 指标 | 含义 |
| --- | --- |
| `rrs_http_requests_total` / `rrs_http_duration_seconds` | 按路由模板、方法、状态统计 HTTP；不使用用户 ID、原始路径或查询作为标签 |
| `rrs_agent_executions_total{outcome}` | Agent 执行段结果；审批等待、预算耗尽、异常分别统计 |
| `rrs_agent_queue_seconds` / `rrs_agent_duration_seconds` | 每次初始执行或审批恢复的排队/执行耗时 |
| `rrs_agent_span_seconds` | 模型、工具已测得的调用耗时 |
| `rrs_agent_known_tokens_total` | provider 已上报的主模型/finalize token；不含压缩、嵌入、工具内部调用，不能当总账单 |
| `rrs_retrieval_duration_seconds` / `rrs_degradations_total` | 文档检索耗时及向量、reranker、队列等降级/拒绝事件 |
| `rrs_agent_active_runs` / `rrs_agent_oldest_pending_seconds` | 从业务数据库抓取当前积压、待审批数量和最老任务年龄 |

API 时间字段均针对最近一次执行段，人工审批等待不计入执行耗时。端到端任务体验通过压测客户端从提交到终态单独测量。HTTP 202 只表示接收任务，Agent `status=success` 也不等于质量通过：预算退出保留现有 API 语义，但指标/压测只把 `stop_reason=completed` 记为自然完成；语义正确仍需人工标注评测。

## 监控与 SLO

生成一个随机 token，分别填入 `.env` 的 `METRICS_TOKEN` 和 `infra/monitoring/metrics-token.txt`（只存 token 本身，勿提交）。两个位置必须相同。然后运行：

```sh
docker compose -f compose.server.yml -f compose.monitoring.yml up -d --build
```

Prometheus 仅监听本机 `http://127.0.0.1:9090`；Targets 查看 web/worker 抓取，Alerts 查看告警。规则已接入，尚未配置外部通知接收器。若要发送值班消息，需另行配置 Alertmanager 与接收目标。横向扩容后须配置每个实例的服务发现，不能对负载均衡后的随机单个实例抓取。

建议先观察一周再校准以下内部目标：

| 对象 | 建议目标与口径 |
| --- | --- |
| API 可用性 | 滚动 30 天 ≥99.9%；有效流量中的 5xx 和容量 429 都算失败，排除无效参数/不存在资源等其他 4xx；探针和 metrics 不计入 |
| Agent 排队 | 已启动执行段的 P95 ≤30 秒；未启动任务另外由最老积压年龄告警覆盖 |
| Agent 自然完成 | 合格且无需等待人工的任务 ≥95%；异常、超时、预算耗尽不算成功；业务正确率另算 |
| 检索质量 | 独立标注集 Recall@5 ≥0.8；目标值必须结合数据调整 |

99.9% 的请求错误预算是合格请求数的 0.1%，不能直接冒充运行时间可用率。HTTP 短窗错误比例超过 1.44% 会触发快速消耗预算告警；规则还有队列 P95、任务停滞、重复降级和抓取失败。当前告警阈值按默认 300 秒排队 + 900 秒执行设置；修改上限时同步修改规则。

查询示例：

```promql
histogram_quantile(0.95, sum by (le) (rate(rrs_agent_queue_seconds_bucket[10m])))
sum(rate(rrs_agent_executions_total{outcome="completed"}[15m]))
sum by (component, reason) (increase(rrs_degradations_total[15m]))
```

## 保护与故障定位

- 文档 vector/HyDE 路由异常时保留 BM25；返回 `route_status`、`degraded`、`degraded_reasons`。Embedding 连续失败后进程内熔断，冷却后探测；各 worker 各自维护熔断状态。reranker 异常回到 RRF。业务需判断降级响应是否仍满足用途。
- 排除已标明其他 embedding model、维度不匹配和非有限存储向量；旧数据未填 model 的仍兼容读取，迁移模型时需重建索引。RRF 是排序分，未校准不能直接当证据置信度；无反思/reranker 判断时返回 `retrieval_quality=unverified`。机构别名冲突识别仍是单独的已知问题。
- API 提交与审批恢复用数据库全局 admission 行锁限制 processing 数量，包含排队与执行中，不包含人工审批等待。超额返回 429 和 Retry-After；同一会话已有任务返回 409。
- 发布到 broker 失败时返回 503、保存 `dispatch_failed`，避免永远显示 processing。若发布响应不确定而 worker 已抢到租约，会保留执行状态；不要因看到客户端断线就自动重发有副作用的任务。
- 执行前获取数据库租约，重复投递不能并行调用图；执行上限由 Celery soft/hard timeout 限制。租约有额外 30 秒宽限，过期任务不自动重放外部操作。生产 worker 必须使用支持 hard timeout 的 prefork 池；Windows solo 模式不提供同等保证。
- beat 每分钟投递对账任务，将超时排队/失联执行设为 failed。worker 完全不可用时对账任务也无法执行，可由独立运维进程运行 `python backend/manage.py reconcile_agent_runs`，同时处理停滞告警。
- 不提供外部工具 exactly-once 保证。将来添加邮件、写库等 ACTION，仍需目标端幂等键或 outbox；超时后先检查检查点与实际副作用，再决定重试。

排障顺序：从响应取得 request/run ID → 查 `docker compose ... logs backend worker` 中对应 JSON 事件 → 看 queue_wait 与 execution 分布 → 看 stop_reason、失败 span 和 degraded_reason → 必要时查同 run 的 Langfuse → 修复依赖或调整容量 → 将失败输入脱敏后加入 bad_cases 与人工标注集 → 跑质量门禁。

常见情形：429 先看积压和模型限流；queue_wait 高而执行正常优先检查 worker/并发；模型 span 慢优先检查供应商延迟/超时；embedding_unavailable 检查 Ollama；dimension_mismatch 重建向量；execution_timeout 检查 worker OOM/硬超时，勿盲目重放 ACTION。

## 压测与容量估算

使用测试环境和测试文稿。`load_test --mode agent` 会创建会话、持久化任务并真实调用模型，保留 run ID 便于清理和排查；不会自动审批 ACTION。客户端超时不取消服务器任务，先查询原 run 再决定下一步。

```sh
python backend/manage.py load_test --base-url http://127.0.0.1:8000 --path "/api/extraction/runs/<run-id>/vector-search/?q=RAG&rerank=0" --requests 30 --concurrency 2 --timeout 180 --output data/evaluation/load-retrieval.json
python backend/manage.py load_test --base-url http://127.0.0.1:8000 --mode agent --manuscript-id <test-manuscript-id> --prompt "总结该稿件的研究主题" --requests 10 --concurrency 2 --timeout 600 --target-rps 0.05 --output data/evaluation/load-agent.json
python backend/manage.py capacity_plan --service-seconds 30 --arrival-rps 0.1 --utilization 0.7
```

报告包含完成吞吐量、全部请求 P50/P95、成功请求 P95、排队 P95、成功/失败/429/超时分布、降级次数与每次 run ID。默认成功率低于 99% 命令失败，可使用 `--min-success-rate`、`--max-p95-seconds` 调整。HTTP 模式只证明请求可用性；HTTP 200 不证明检索正确。Agent 模式等到终态，预算退出不算成功。

这是有并发上限的闭环负载，服务变慢会降低发压速率，不能等同于固定到达率的峰值流量。按并发 1→2→4→8 分批增加，记录模型配额、CPU/RAM、数据库连接、队列和降级比例，在错误率/延迟越界前停止；此工具不负责自动扫描硬件指标。保持请求分布、模型、数据量一致再比较版本。

容量下界：`worker_slots = ceil(目标每秒任务数 × 平均执行秒数 / 目标利用率)`。例子的 30 秒、0.1任务/秒、70% 利用率得 5 个 slot，这是公式演示，不是本项目实测。工具只用成功自然完成样本估算，故障或预算退出很多时存在选择偏差。还需满足 provider RPM/TPM、每个模型副本内存和数据库上限；长尾与突发必须再压测验证。

## 自动回归与 Bad Case

```sh
python backend/manage.py test apps.common apps.extraction apps.recommendations apps.agent
python backend/manage.py evaluate_quality --cases data/evaluation/<human-cases>.jsonl --collect --gate data/evaluation/gates.example.json --output data/evaluation/<new-report>
```

`--collect` 会运行检索并读取已有 AgentRun，不会重新执行 Agent；测试新 Agent 版本需要先生成新运行，再导出并标注对应样本。自动 CI 用可控模型/工具响应验证当前图、API、故障注入和并发行为，合成数据报告只检查评测器。

门禁配置按 kind/source/target/mode/slice/K/options 匹配，每个匹配组独立检查。指标低于阈值、没有匹配组、指标缺失、人工标注覆盖不足、样本数不足都失败并返回非零退出码；结果写入 gate.json。默认只接收 source=human，合成门禁须显式 `--allow-synthetic-gate`，不可用来声明业务质量。例子要求每个切片至少 20 条已标注样本；无答案检索应另用 no_answer_correct 指标，不与 Recall 混在一起。

`.github/workflows/reliability.yml` 包含 SQLite/PostgreSQL 测试、迁移检查、评测器烟雾报告与前端构建。PostgreSQL 用例验证并发 admission 和重复执行保护。当前工作目录没有 Git 仓库，workflow 文件须随项目纳入 GitHub 仓库后才会自动触发。

生产使用还需要项目层面的认证授权、TLS、备份/恢复演练、持久化容量和告警值班流程；本次不将现有 AllowAny API 或单机 Compose 宣称为完整生产平台。
