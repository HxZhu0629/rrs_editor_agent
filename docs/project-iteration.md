# RRS 首轮项目优化与验证记录

日期：2026-09-26。顺序为评测闭环 → 审稿辅助场景 → 运行验证 → 工具治理。复用 Django、Celery、现有 Agent Loop 和评测命令，不增加业务服务、模型供应商或数据库表。

## 1. 已完成的改动

| 目标 | 实现 | 验证方式 |
| --- | --- | --- |
| 避免评测泄漏 | 按稿件/group_id 冻结 dev/test，指标和门禁分开统计 | 同稿件跨任务类型不跨 split，输入顺序不影响划分 |
| 形成改进证据 | 同 ID 配对版本对比，输入/金标准/已知语料指纹校验，覆盖率和逐条变化 | 不允许删除失败样本、变更标签冒充提升或把缺失观测当修复 |
| 定位 bad case | 症状分类、根因证据/修复/回归记录，缺失历史停止原因单独处理 | 未评不填 0 或满分 |
| 跑通产品交互 | 202 后轮询、排队/执行/审批/失败状态、同标签页会话恢复、候选论文依据与 Markdown 导出 | 后端 API→Agent Loop→结果持久化→恢复读取集成测试；前端构建 |
| 实测运行情况 | P99、并发 1/2/4 的真实 HTTP 压测、Prometheus 抓取与鉴权 | 300 次 API 请求，监控 up=1，匿名 metrics 返回 403 |
| 工具可控性 | Skills/工具版本、技能内容摘要、禁用配置、严格参数、多类错误、MCP 来源不可覆盖 | 非法参数不执行；禁用工具不交给模型也不能被直接调用 |

浏览器自动化连接不可用，本轮未完成浏览器点击验收。后端集成测试使用可控模型响应和候选结果，不代表真实模型任务成功率。

最终 PostgreSQL 回归：136 项中 135 项通过、1 项机构别名为已知预期失败；SQLite 全量验证通过（2 项 PostgreSQL 专属并发用例跳过）。前端 build、迁移漂移检查、Compose 配置及 Prometheus 7 条规则校验通过。前端仍有既有的大包体积提示。测试输出保存于 `data/evaluation/local_20260926/regression-*.log`。

## 2. 真实数据盘点与 bad case

原 `rrs-db` 容器处于停止状态。启动后复制数据库 `rrs` 为 `rrs_eval_20260926`，只对副本执行 `agent.0004_reliability` 迁移。原业务库未迁移、未写入测试记录。副本及导出文件保留，供后续标注。

- 1200 条稿件、13 条历史 AgentRun，文档分块 0 条。
- 13 条运行都标记 success，但都缺少 stop_reason，turn_count 为历史默认值 0，不能报告自然完成率、模型任务成功率或历史执行耗时。
- 历史轨迹混有 classify_intent、recommendation_tool、native_tool_answer 等旧执行路径，不能直接当作当前 Agent Loop 的评测结果。
- `agent-c05fe611-6264-4b0a-8ed2-8959a72b036c` 和 `agent-876952bf-1229-4a6c-9483-41785aa51abb` 输入含大量字面问号。原意无法从当前记录恢复，是否为当时编码/调用方式问题尚待追查；不能按猜测补标签。
- 目前没有独立 qrels 和人工任务验收，source 保持 unreviewed。

文件：`data/evaluation/local_20260926/unreviewed.jsonl` 为原始导出，`audit/` 为修正缺失停止原因口径后的报告。`baseline/` 是修正前的评测器诊断，不是模型基线，不能拿两者分数变化声称模型提升。

后续根因记录按：样本 ID → 输入与预期 → 轨迹证据 → 根因（已确认/待验证）→ 修改 → 同样本新运行结果。既有机构中英文别名冲突仍为已知问题；没有完整别名数据时不做硬编码名单“修复”。

## 3. 本地性能实测

Windows、本机 Docker PostgreSQL、Django 开发服务器，评测库包含上述真实稿件数据。请求固定为 `GET /api/manuscripts/bac657d4-f529-477d-b689-0c60ed9b127f/`，不调用模型。每档 100 次，闭环并发，无独立预热阶段；同机其他应用仍在运行。

| 并发 | HTTP 成功率 | 完成请求/秒 | P95 毫秒 | P99 毫秒 |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 100/100 | 28.19 | 63.88 | 82.68 |
| 2 | 100/100 | 54.14 | 65.76 | 79.71 |
| 4 | 100/100 | 104.34 | 59.77 | 65.36 |

数据文件为 `http-c1.json`、`http-c2.json`、`http-c4.json`。这只是短时 API 基线，100 样本的 P99 不稳定，也未到达饱和点；不能推出生产容量、LLM 端到端延迟或 SLA，更不能宣称优化了上述指标。

`prometheus-verification.json` 保存 Targets 和累计请求查询证据：`rrs-local-web` 健康、观测到 300 次请求。临时 Prometheus 使用 19090 端口，避开其他项目的 9090；本轮只验证 web 指标，未启动 RRS worker 抓取。

本地复测用 `infra/monitoring/prometheus.local.yml`，指向主机 18060，token 文件沿用 `infra/monitoring/metrics-token.txt`。生产 Compose 可用 `PROMETHEUS_PORT=19090` 改监听端口。生成的 token 未写入现有 `.env`，部署时需按可靠性手册同步后端/worker 的 METRICS_TOKEN。测试结束停止本轮临时服务，保留报告和评测库。

## 4. Skills / 工具使用规则

- Skills 仍由 Python 注册表维护，包含步骤、推荐工具、完成检查、版本及内容 SHA256。工具仍使用统一的 LangChain StructuredTool / Function Calling 入口。
- `AGENT_DISABLED_TOOLS=search_external_papers,lookup_author_identity` 可关闭指定能力；配置拼写错误会报错。技能加载结果列出可用/不可用工具。它是部署级工具开关，不是用户 RBAC；推荐工具清单不是安全边界。
- 新工具 trace 带 tool_version/source_protocol，加载技能带 skill_version/skill_sha256；治理规则仍由执行器和审批机制执行。
- 错误类型区分 invalid_arguments、timeout、unavailable、business_error、execution_error；不把“禁用/未找到结果”算成功执行。合法的“信息不完整”检查结果仍是正常工具响应。
- MCP 使用现有实际调用超时、工具白名单，原生工具依赖内部请求超时和 Celery 执行上限；没有用无法中止的后台线程伪造通用工具超时。
- 默认仍只有 READ/DRAFT，邀请草稿不发信，不增加 SubAgent、长期记忆服务或插件市场。

## 5. 下一轮最小验证任务

1. 选取有使用权限、研究主题不同的真实稿件 PDF，完成解析/分块；当前导入的元数据不能代替全文证据库。
2. 准备 50 条检索问题和 30 个 Agent 任务，优先覆盖正常任务、证据不足、多约束、故障与多轮。数量是采样目标，尚未完成，不能写成已有评测集。
3. 逐块/按标注池独立标注 qrels，写明验收标准、标注人和日期；按稿件冻结 split。Agent 的“解释推荐/准备邀请”必须保留前置会话，不能当无上下文任务重放。
4. 基线与候选版本固定同样本、语料和供应商配置，真实执行后重新人工判断 task_success/grounded；保留各次 run_id。用 compare_quality 对比，再用 gate 检查验收指标。
5. 邀请少量真实使用者分别完成“手动筛选→查看依据→起草邀请”和产品内同任务，记录成功率、总耗时、人工修改次数、信任/疑问和放弃位置。小样本用于发现问题，不包装为统计显著 A/B 实验。

用户验证记录可直接用表格：参与者匿名编号｜任务/条件｜开始/结束｜是否完成｜修改次数｜证据是否够用｜卡住步骤｜原话反馈。先手动记录，确认确有持续使用需求后再接埋点平台。
