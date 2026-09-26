# 检索与 Agent 评测

新增 `evaluate_quality --gate gates.example.json`：按组检查指标阈值、最少样本数和标注覆盖率；未匹配、未标注或未达标均非零退出，并生成 `gate.json`。默认只允许 human 来源，合成门禁必须显式开启 `--allow-synthetic-gate`。示例阈值需要用真实数据校准。

运行保护、压测、容量估算、SLO 监控与 CI 使用方法见 [可靠性手册](../../docs/reliability.md)。`--collect` 对 Agent 读取现有运行，不会自动重跑新版本。

这套工具包含独立标注、真实结果采集、离线评分、逐样本 bad case 导出。评测器不使用被测模型给自己打分，不修改推荐策略，不自动重跑 Agent 或批准动作。

## 1. 指标口径

### 检索

文档检索评估 child chunk ID，审稿人推荐评估 reviewer ID；两个任务分开统计。当前文档指标没有评估返回父块的答案覆盖，需要另行复核上下文和引用。

| 指标 | 口径 | 前提 |
| --- | --- | --- |
| Precision@K | Top-K 内不同相关结果数 / K；不足 K 不缩小分母 | Top-K 均已标注，或声明标注完整 |
| Recall@K | Top-K 命中的不同相关结果数 / 独立金标准相关结果总数 | 完整 qrels，至少一项相关结果 |
| MRR@K / Hit@K | 第一项相关结果倒数排名 / 是否命中 | 完整 qrels；相关定义为 grade > 0 |
| nDCG@K | DCG / 独立 qrels 的理想 DCG；gain=2^grade-1 | 完整 qrels，理想排序包含没被召回的相关结果 |
| No-answer Correct | 人工确认无答案时是否返回空列表 | qrels={} 且 judgments_complete=true |
| Conflict Leak | 返回结果是否与人工标记的 forbidden_ids 相交 | 独立冲突标注，不能用现有冲突算法当标准 |
| P50/P95 | 采集端单次检索耗时，包含 DB 与模型调用 | observation.latency_ms 实测值 |

每个 query 等权宏平均，并输出 n/总样本数及 coverage。重复结果占排名位置但不重复得分；空召回在有答案样本中计 0。缺少标注的指标为 null，不能当作满分。默认 bad case 门槛为 recall<1、nDCG<0.8，可按 case.thresholds 修改，这只是排查阈值而非经过验证的生产标准。

**标注不能只看当前 Top-K。** 建议小文档逐块标注；大候选库用各方法召回结果的并集加人工补充，形成固定 pooled qrels，并明确 Recall 是“相对于标注池”的 Recall。真实全库 Recall 只有完整相关集合才能报告。

`scripts/compute_experiment_metrics.py` 仍保留旧实验口径：它只把当前 Top-K 标签排序作为 IDCG，无法衡量漏召回。旧指标和本评测器的 nDCG 不可直接横比，旧实验不能作为新架构的效果证明。

### Agent

| 指标 | 口径 |
| --- | --- |
| Natural Completion | 终态 status=success 且 stop_reason=completed；不代表任务完成 |
| Task Success | 人工复核最终答案/业务结果后填写 judgments.task_success |
| Tool Selection Precision | executor 轨迹中属于 allowed_tools 的请求数 / 全部工具请求数 |
| Required Call Recall | 成功执行且参数符合 required_calls 的次数 / 必须调用次数；同一次执行不能匹配两个要求 |
| Argument Accuracy | 对有参数契约的成功调用，参数满足任一同名契约的比例；字典按子集、列表和标量按精确值匹配 |
| Tool Execution Success | 未抛出异常的调用 / (成功+error)；拒绝、预算阻断单列，不计作执行失败 |
| Tool Order Correct | 成功调用序列是否包含 expected_order 子序列 |
| Forbidden Tool Executed | forbidden_tools 中的工具是否出现 status=success；被拒绝不算执行 |
| Rule Pass | 期望状态、停止原因、必需调用、允许工具、禁止执行与顺序等规则是否通过 |
| Grounded / Constraints Preserved | 人工核验证据支持与约束保留；只填 true/false/null |
| Approval Correct / Recovery Success | 在适用案例中人工核验审批与恢复是否正确；未测填 null |
| Budget Exhausted / Repeated Call | 预算终止率 / 相同工具与参数的重复成功调用比例；重复可能合理，只作为复核线索 |

required_calls 匹配允许失败后重试成功；rule_pass 不是任务成功率，不能代替人工验收。参数准确率只检查工具入参，不能证明最终推荐满足年份和机构约束，还须复核返回结果。

新执行器将 status=disabled/unavailable/not_found/error/failed 记录为工具错误，并区分 invalid_arguments、timeout、unavailable、business_error。旧轨迹没有这些分类，不回填历史成功状态。Tool Execution Success 仍不能代替 task_success/grounded。工具选择指标以 executor 轨迹为范围，不含模型在最大轮次最后提出但未交给 executor 的调用。现有注册表只有 READ/DRAFT 工具，真实外部 ACTION 的审批/恢复需要受控测试工具，不能宣称已完成线上发信验证。

新运行的 model/finalize 事件记录 latency_ms 与 provider usage，成功/异常的实际工具执行记录 latency_ms。已知 Token 总数和覆盖率一起展示：不包含摘要压缩、Embedding、工具内部 LLM，不能当整次任务总成本。历史缺失值不补 0。Agent observation.latency_ms 使用最近执行段的 queue_wait_seconds + execution_seconds；不包含更早执行段和人工审批等待，两者缺失时为 null。历史终态缺少 stop_reason 时，natural_completion 与依赖它的 rule_pass 保持未评，并标记 missing_stop_reason。

## 2. 跑通演示（无数据库读写、无外部模型调用）

在项目根目录的 PowerShell 执行：

```powershell
$env:DB_ENGINE='sqlite'
& .venv/Scripts/python.exe backend/manage.py evaluate_quality --cases data/evaluation/demo_cases.jsonl --output data/evaluation/demo_report
```

demo_cases.jsonl 是显式 synthetic 的手工数据，用来验证分数和报告格式，不代表项目效果。不同 source、kind、target、mode、slice、options、K 分组展示，不把演示和真实数据混算。输出目录必须是新目录，避免覆盖历史实验。

输出：

- report.md：指标与待排查样本列表。
- summary.json：指标值、覆盖率、源代码/输入数据 SHA256、当前非敏感配置。
- cases.jsonl：本次输入和采集快照，方便离线重算。
- per_case.jsonl：每个样本的期望、实际结果、分数和 flags。
- bad_cases.jsonl：待排查样本，预留 root_cause、fix、regression_case_id。

保存的配置是评测时配置，不代表旧 AgentRun 执行时配置。真实对比应在冻结的数据、代码和模型版本下重新运行并保留版本记录。不同 options（例如 rerank/rewrite）自动分组，可配合有意义的 slice 名称区分故障类型。

## 3. 导出真实案例并标注

使用应用实际数据库执行（例如容器内，启动方式沿用项目部署文档）：

```text
python manage.py export_quality_cases --limit 20 --output /tmp/rrs_quality_cases.jsonl
```

导出最新成功解析的文档块及现有 AgentRun。文件含本地稿件文本与对话，作为内部评测资料保存。修改 JSONL 的每一行：

1. 文档：写独立 query，逐块核对 source_context，将 qrels 设为 {"chunk UUID":0/1/2}；0=无关，1=部分相关，2=直接支持。确认标注池完整后才设置 judgments_complete=true。
2. Agent：填写 required_calls/allowed_tools/expected_order/forbidden_tools，审阅输入、轨迹与答案填写 judgments；不要因状态 success 就标 task_success=true。
3. 复核完成后将 source 从 unreviewed 改为 human，并记录标注人、日期和解释（可附加 annotation 对象）。
4. run_id 指向被标注的实际 AgentRun；重采集会校验 query 与 user_input 一致，不会重新调用 Agent。
5. 等待审批任务如果以“正确停在审批点”为验收，设置 expected_status="waiting_approval"、expected_stop_reason=""，task_success 仍按完整任务定义填写；processing 不计终态质量。

然后采集并评分：

```text
python manage.py evaluate_quality --cases /tmp/rrs_quality_cases.jsonl --collect --output /tmp/rrs_quality_run_01
```

`--collect` 文档支持 mode=bm25/vector/hybrid，审稿人支持 target=reviewer、manuscript_id、mode=lexical/vector/hybrid、reviewer ID qrels；审稿人评分输入为数据库稿件，报告保存 title/abstract/keywords。对照时复制同一 query/qrels 并设置不同唯一 id 和 mode。

文档 hybrid 默认关闭 query_rewrite、hyde、rerank、reflect 和 CRAG 外网兜底；可用 options 中四个同名布尔开关逐项开展消融。bm25 不调用模型，其余模式可能调用配置的 Ollama/DeepSeek 或下载配置的 reranker；本次验证没有发起这些真实调用。

采集失败仍生成报告并以非零状态退出；失败样本保留在分母/覆盖率中，质量分数为 null，不能悄悄剔除后宣称全量高分。

## 4. 建议首批样本

先固定 30 个检索问题、20 个 Agent 任务作为起始回归集，后续按失败类型扩充；小样本分数只用于开发，不代表线上整体效果。

| 切片 | 检索样本目标 | Agent 样本目标 |
| --- | --- | --- |
| 正常任务 | 术语定位、方法、实验条件 | 检查投稿、推荐、解释 |
| 语义差异 | 中文问题查英文段落、缩写、同义表达 | 口语条件转结构化参数 |
| 证据不足 | 不存在的结论、库外主题、空文档 | 承认不足、拒绝无依据结论 |
| 多约束 | 年份、机构、作者与冲突 | 排除机构、数量、机构多样性 |
| 工具故障 | Embedding 超时、重排失败、维度变化 | MCP 超时、非法参数、预算耗尽 |
| 多轮与权限 | 文档中恶意指令 | 压缩后约束保留、拒绝动作、恢复任务 |

每个 bad case 记录：样本 ID → 输入/金标准 → 实际结果与轨迹 → 失败类别 → 根因证据 → 修改 → 相同样本的回归结果。自动 flags 仅用于定位，不能将推测写成已确认根因。

## 5. 验证

```powershell
$env:DB_ENGINE='sqlite'
& .venv/Scripts/python.exe backend/manage.py test apps.common.test_quality apps.common.test_quality_bad_cases --verbosity 2
```

test_quality 验证指标、边界、采集、导出和调用记录；test_quality_bad_cases 中机构别名 expectedFailure 是已复现但未修复的问题，不能算功能通过。其余三条历史失败已成为普通回归测试。详情见项目根目录 bad_case_analysis.md 的最新实测章节。

## 6. 冻结样本与版本对比

导出后每条样本保留 manuscript_id。同稿件的检索与 Agent 任务必须放在同一 split，避免相似问题同时进入开发和验收集。没有稿件 ID 的数据须填写代表独立文档/任务族的 group_id，不能给每个相似问题单独分组。

```text
python backend/manage.py split_quality_cases --cases data/evaluation/labeled.jsonl --output data/evaluation/frozen.jsonl
python backend/manage.py compare_quality --baseline data/evaluation/v1/cases.jsonl --candidate data/evaluation/v2/cases.jsonl --output data/evaluation/comparison-v2 --fail-on-regression
```

split 使用稳定哈希，默认约 20% 独立稿件进入 test；不保证小样本精确比例。已经冻结的 split 不允许再次划分。报告与门禁按 dev/test 分组；门禁可增加 `"split":"test"`。不要用 test bad case 反复调参后继续宣称独立验收效果。

对比要求两边样本 ID 集合、输入和验收规则完全一致，允许更改检索 mode/options。新采集的文档记录内容指纹；两次采集内容指纹或稿件输入不同会拒绝横比。旧数据缺少指纹时只能验证保存的输入契约，不能证明底层语料未变化。人工 Agent 判断必须针对新 run 重新复核；重复读取旧 run 会被标记 same_agent_run，不能证明新运行时更好。

comparison.md/json 展示配对指标差值、覆盖率，以及 improved/regressed/persistent/inconclusive/unchanged 样本。缺少观测或原有指标时不能将 bad case 判为修复。`--fail-on-regression` 检查新增 flags 和缺失证据，数值阈值仍使用 evaluate_quality --gate；对比不提供统计显著性结论。

bad_cases.jsonl 增加症状分类，以及 analysis.status/evidence/root_cause/fix/regression_case_id。保留分析记录到独立文件，再把 regression_case_id 对应样本放入下一轮固定评测集；对比报告验证观测变化，不自动把根因推断变成已确认结论。

首轮真实数据盘点、API 压测和产品验证步骤见 [本轮改进记录](../../docs/project-iteration.md)。
