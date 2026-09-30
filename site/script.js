const repositoryRoot = 'https://github.com/HxZhu0629/rrs_editor_agent/blob/main/'

const workflowSteps = [
  {
    title: '让文件成为可复核的输入',
    description: '编辑上传 PDF 或 DOCX，再手动触发文档解析：Unstructured 生成 Markdown、文档块和质量信号，DeepSeek 提取标题、摘要、关键词及作者信息。',
    flow: ['PDF / DOCX', '结构化内容', '待确认元数据'],
    output: 'Manuscript、ExtractionRun、父子 DocumentChunk；解析结果和模型输出可追溯。',
    boundary: '解析在 HTTP 请求内同步完成；分块后不自动批量生成向量，可用回填命令建立索引。',
    source: 'backend/apps/extraction/views.py',
    label: 'extraction/views.py',
  },
  {
    title: '人工校正，让筛选条件有可靠基础',
    description: '编辑核对标题、摘要、领域、关键词与作者机构，修正模型抽取错误，再点击保存人工确认。作者和机构信息将影响利益冲突过滤；运行推荐不会自动保存尚未提交的表单修改。',
    flow: ['抽取结果', '编辑修正', '确认后的稿件'],
    output: '稿件字段、作者关系、顺序、机构和联系邮箱；确认接口更新稿件状态。',
    boundary: '模型抽取不等于事实正确；同机构名称的简单归一化不能识别全部别名。',
    source: 'backend/apps/manuscripts/views.py',
    label: 'manuscripts/views.py',
  },
  {
    title: '从合格候选人中寻找主题匹配',
    description: '推荐服务读取本地候选库，剔除已知同机构与近年合作者冲突，再进行稀疏、向量和元数据召回、融合与重排。界面默认 Top K 为 20，可以调整到 1–50。',
    flow: ['候选资格', '召回与重排', 'Top-K 名单'],
    output: '直接推荐接口保存 RecommendationRun、RecommendationItem 及存在的论文证据；返回候选数量与评分拆解。',
    boundary: '需要预先导入审稿人数据。身份、冲突和证据覆盖取决于数据完整性，排名不是质量承诺。',
    source: 'backend/apps/recommendations/retrieval.py',
    label: 'recommendations/retrieval.py',
  },
  {
    title: '沿着推荐理由，追到原始论文',
    description: '编辑展开候选人卡片，检查匹配关键词、召回路线、代表论文与评分组成。点击论文后，后端通过学术 MCP 尝试解析 DOI、OpenAlex 与开放获取全文入口。',
    flow: ['推荐理由', '代表论文', '编辑复核'],
    output: '论文标题、年份、可用证据片段及可验证的访问链接；未找到全文时明确显示不可用。',
    boundary: '没有开放获取 PDF 时，不会绕过出版商权限。仅由画像召回的候选人可能缺少论文证据。',
    source: 'backend/apps/recommendations/views.py',
    label: 'recommendations/views.py',
  },
  {
    title: '把编辑意图交给受限工具执行',
    description: '创建与稿件绑定的对话，发送筛选需求。接口接收后返回 202，Celery 执行 LangGraph；前端每 2 秒轮询。成功结果支持导出 Markdown，当前标签页可恢复保存的会话。',
    flow: ['编辑需求', 'Agent 工具循环', '答案与草稿'],
    output: 'AgentRun、消息、工具轨迹和检查点；推荐结果及邮件草稿写入运行结果与消息元数据。',
    boundary: '默认工具没有发信能力。邀请草稿由模板拼装，仍需编辑确认候选人、联系方式和内容。',
    source: 'backend/apps/agent/graph.py',
    label: 'agent/graph.py',
  },
]

const agentStages = [
  { label: 'STEP 01 · 加载流程', title: '先知道这项任务怎么完成', tool: 'load_editor_skill', description: '加载 reviewer_selection 的可信流程指引，明确约束和完成检查。这是单 Agent 中的技能加载，不是另起一个 Agent。' },
  { label: 'STEP 02 · 理解主题', title: '用稿件证据补充任务背景', tool: 'search_manuscript_evidence', description: '在已有成功解析的稿件中检索主题和方法依据。没有解析记录或证据不足时，应报告缺口；本页不伪造检索结果。' },
  { label: 'STEP 03 · 推荐候选人', title: '把编辑条件传入结构化工具', tool: 'recommend_reviewers', description: '传入 top_k=5、diversify_affiliations=true，应用冲突过滤、召回与排序。机构多样性优先，但不同机构人数不足时允许补齐。' },
  { label: 'STEP 04 · 准备草稿', title: '依据当前名单生成邀请文案', tool: 'draft_reviewer_emails', description: 'count=3 时最多为当前名单前 3 位生成英文模板草稿；使用已有姓名、稿件标题和代表论文，不执行发送，返回 requires_human_approval=true。' },
  { label: 'STEP 05 · 汇总交付', title: '返回结论、依据与待复核事项', tool: 'AgentRun / AgentMessage', description: '模型基于工具结果输出答案，后台保存结果与轨迹。编辑核验候选人、利益冲突和草稿。成功状态仍需结合停止原因和内容判断。' },
]

function enableTabs(tabList, activate) {
  const tabs = [...tabList.querySelectorAll('[role="tab"]')]
  const selectTab = (selected, focus = false) => {
    tabs.forEach(tab => {
      const active = tab === selected
      tab.setAttribute('aria-selected', String(active))
      tab.tabIndex = active ? 0 : -1
    })
    activate(selected)
    if (focus) selected.focus()
  }
  tabs.forEach(tab => {
    tab.addEventListener('click', () => selectTab(tab))
    tab.addEventListener('keydown', event => {
      const vertical = tabList.getAttribute('aria-orientation') === 'vertical'
      const previousKey = vertical ? 'ArrowUp' : 'ArrowLeft'
      const nextKey = vertical ? 'ArrowDown' : 'ArrowRight'
      const current = tabs.indexOf(tab)
      let next = current
      if (event.key === previousKey) next = (current - 1 + tabs.length) % tabs.length
      else if (event.key === nextKey) next = (current + 1) % tabs.length
      else if (event.key === 'Home') next = 0
      else if (event.key === 'End') next = tabs.length - 1
      else return
      event.preventDefault()
      selectTab(tabs[next], true)
    })
  })
  selectTab(tabs[0])
}

enableTabs(document.querySelector('.step-list'), selected => {
  const index = Number(selected.dataset.step)
  const step = workflowSteps[index]
  document.getElementById('workflow-panel').setAttribute('aria-labelledby', selected.id)
  document.getElementById('step-counter').textContent = `${String(index + 1).padStart(2, '0')} / 05`
  document.getElementById('step-title').textContent = step.title
  document.getElementById('step-description').textContent = step.description
  document.getElementById('step-output').textContent = step.output
  document.getElementById('step-boundary').textContent = step.boundary
  const source = document.getElementById('step-source')
  source.href = repositoryRoot + step.source
  source.textContent = step.label + ' ↗'
  const flow = document.getElementById('step-art')
  flow.replaceChildren()
  step.flow.forEach((label, position) => {
    if (position > 0) {
      const arrow = document.createElement('b')
      arrow.textContent = '→'
      arrow.setAttribute('aria-hidden', 'true')
      flow.append(arrow)
    }
    const node = document.createElement('span')
    node.textContent = label
    flow.append(node)
  })
})

enableTabs(document.querySelector('.tab-bar'), selected => {
  document.querySelectorAll('.retrieval-panel').forEach(panel => {
    panel.hidden = panel.id !== selected.dataset.panel
  })
})

let agentStage = 0
function renderAgentStage() {
  const stage = agentStages[agentStage]
  document.getElementById('trace-label').textContent = stage.label
  document.getElementById('trace-title').textContent = stage.title
  document.getElementById('trace-tool').textContent = stage.tool
  document.getElementById('trace-description').textContent = stage.description
  document.getElementById('demo-position').textContent = `${agentStage + 1} / ${agentStages.length}`
  document.getElementById('demo-prev').disabled = agentStage === 0
  document.getElementById('demo-next').disabled = agentStage === agentStages.length - 1
}
document.getElementById('demo-prev').addEventListener('click', () => {
  agentStage = Math.max(0, agentStage - 1)
  renderAgentStage()
})
document.getElementById('demo-next').addEventListener('click', () => {
  agentStage = Math.min(agentStages.length - 1, agentStage + 1)
  renderAgentStage()
})

const menu = document.querySelector('.menu-toggle')
const navigation = document.getElementById('navigation')
function closeMenu() {
  menu.setAttribute('aria-expanded', 'false')
  navigation.classList.remove('is-open')
}
menu.addEventListener('click', () => {
  const expanded = menu.getAttribute('aria-expanded') !== 'true'
  menu.setAttribute('aria-expanded', String(expanded))
  navigation.classList.toggle('is-open', expanded)
})
navigation.querySelectorAll('a').forEach(link => link.addEventListener('click', closeMenu))
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && menu.getAttribute('aria-expanded') === 'true') {
    closeMenu()
    menu.focus()
  }
})
document.documentElement.classList.add('js-ready')

document.querySelectorAll('[data-copy]').forEach(button => {
  button.setAttribute('aria-live', 'polite')
  button.addEventListener('click', async () => {
    const code = document.getElementById(button.dataset.copy)
    try {
      await navigator.clipboard.writeText(code.textContent)
      button.textContent = '已复制'
    } catch {
      const range = document.createRange()
      range.selectNodeContents(code)
      const selection = window.getSelection()
      selection.removeAllRanges()
      selection.addRange(range)
      button.textContent = '已选中，请手动复制'
    }
    setTimeout(() => { button.textContent = '复制' }, 3000)
  })
})

if ('IntersectionObserver' in window) {
  const navLinks = [...navigation.querySelectorAll('a')]
  const observer = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return
      navLinks.forEach(link => {
        if (link.hash === `#${entry.target.id}`) link.setAttribute('aria-current', 'location')
        else link.removeAttribute('aria-current')
      })
    })
  }, { rootMargin: '-15% 0px -55% 0px' })
  navLinks.forEach(link => observer.observe(document.querySelector(link.hash)))
}
