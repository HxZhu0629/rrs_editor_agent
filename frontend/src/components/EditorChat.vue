<template>
  <section class="agent-card">
    <div class="agent-head">
      <div>
        <p class="eyebrow">Editor Copilot</p>
        <h2>编辑推荐助手</h2>
        <p>用自然语言调整主题、年份、机构和冲突约束。</p>
      </div>
      <el-tag v-if="conversationId" type="success" effect="plain">对话已连接</el-tag>
    </div>

    <div class="agent-messages">
      <div v-if="!messages.length" class="agent-empty">
        例如：推荐 5 位近三年做 GraphRAG 的审稿人，尽量来自不同机构。
      </div>
      <div v-for="message in messages" :key="message.id || message.created_at" class="agent-message" :class="message.role">
        <span class="agent-role">{{ message.role === 'user' ? '编辑' : '助手' }}</span>
        <p>{{ message.content }}</p>
      </div>
      <div v-if="recommendations.length || emailDrafts.length" class="agent-answer">
        <div v-if="recommendations.length" class="agent-results">
          <div v-for="item in recommendations" :key="item.reviewer_id" class="agent-result">
            <strong>#{{ item.rank }} {{ item.reviewer_name }}</strong>
            <span>{{ item.reviewer_affiliation || '未知机构' }}</span>
            <small>{{ item.rationale?.explanation }}</small>
            <details v-if="item.rationale?.representative_papers?.length">
              <summary>查看推荐依据</summary>
              <div v-for="paper in item.rationale.representative_papers" :key="paper.id || paper.title" class="agent-evidence">
                <span>{{ paper.title }} · {{ paper.publication_year || '年份未知' }}</span>
                <a v-if="paper.doi" :href="doiLink(paper.doi)" target="_blank" rel="noopener noreferrer">核对论文 DOI</a>
              </div>
            </details>
          </div>
        </div>
        <div v-if="emailDrafts.length" class="agent-results">
          <div v-for="draft in emailDrafts" :key="draft.reviewer_email" class="agent-result">
            <strong>{{ draft.subject }}</strong>
            <span>{{ draft.reviewer_name }} · {{ draft.reviewer_email }}</span>
            <small>待编辑人工确认，系统不会自动发送。</small>
            <details>
              <summary>查看邮件草稿</summary>
              <pre>{{ draft.body }}</pre>
            </details>
          </div>
        </div>
      </div>
    </div>

    <div v-if="run" class="agent-status" role="status" aria-live="polite">
      <strong>{{ statusText }}</strong>
      <span v-if="run.execution_seconds != null">执行 {{ run.execution_seconds.toFixed(1) }} 秒</span>
      <span v-if="run.queue_wait_seconds != null">排队 {{ run.queue_wait_seconds.toFixed(1) }} 秒</span>
      <small v-if="run.status === 'failed'">本次未完成。请核对已有结果后再提交调整后的任务。</small>
    </div>
    <div v-if="connectionError" class="agent-status" role="alert">
      <span>{{ connectionError }}</span>
      <el-button size="small" :loading="loading" @click="refresh">重新获取状态</el-button>
    </div>
    <div v-if="run?.status === 'waiting_approval'" class="agent-approval">
      <strong>以下操作需要你确认</strong>
      <div v-for="request in approvalRequests" :key="request.call_id">
        <b>{{ request.tool }}</b>
        <pre>{{ JSON.stringify(request.arguments, null, 2) }}</pre>
      </div>
      <el-button :disabled="loading || !!connectionError" @click="approve(false)">拒绝并继续</el-button>
      <el-button type="warning" :disabled="loading || !!connectionError || !approvalRequests.length" @click="approve(true)">同意上述操作</el-button>
    </div>
    <div class="agent-shortcuts">
      <el-button v-for="preset in presets" :key="preset.label" size="small" :disabled="busy || loading" @click="input = preset.prompt">{{ preset.label }}</el-button>
      <el-button v-if="run?.status === 'success' && answer" size="small" @click="downloadReport">导出本次结果</el-button>
    </div>
    <div class="agent-input">
      <el-input
        v-model="input"
        type="textarea"
        :rows="3"
        resize="vertical"
        :disabled="busy || loading || !conversationId || !!connectionError"
        placeholder="告诉编辑助手你希望如何筛选审稿人"
        @keydown.ctrl.enter="send"
      />
      <el-button type="primary" :loading="loading" :disabled="busy || !!connectionError || !input.trim() || !conversationId" @click="send">
        发送
      </el-button>
    </div>
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { approveAgentRun, createAgentConversation, getAgentConversation, getAgentRun, sendAgentMessage } from '../api/client'

const props = defineProps({ manuscriptId: { type: String, default: '' } })

const conversationId = ref('')
const messages = ref([])
const recommendations = ref([])
const emailDrafts = ref([])
const answer = ref('')
const input = ref('')
const loading = ref(false)
const run = ref(null)
const connectionError = ref('')
const busy = computed(() => ['processing', 'waiting_approval'].includes(run.value?.status))
const approvalRequests = computed(() => (run.value?.pending_approval?.interrupts || []).flatMap(item => item.requests || []))
const statusText = computed(() => {
  if (run.value?.status === 'processing') return run.value.started_at ? '正在处理，可稍后回来查看' : '已提交，等待执行'
  if (run.value?.status === 'waiting_approval') return '等待人工确认'
  if (run.value?.status === 'failed') return '任务失败'
  return run.value?.stop_reason === 'completed' ? '本次执行已结束，请复核结果' : '执行预算已用完，结果可能不完整'
})
const presets = [
  { label: '投稿检查', prompt: '检查稿件投稿信息和解析质量，列出缺失信息、证据位置及需要人工复核的事项。' },
  { label: '推荐并解释', prompt: '先根据稿件证据确认研究主题，再推荐5位审稿人，尽量来自不同机构；逐一解释推荐依据和冲突检查的局限。' },
  { label: '准备邀请', prompt: '根据当前候选名单，为前3位审稿人准备邀请邮件草稿，只使用已有证据，不发送邮件。' },
]
let generation = 0
let pollTimer = null

function sessionKey() { return `rrs-conversation-${props.manuscriptId}` }

function savedConversation() {
  try { return sessionStorage.getItem(sessionKey()) || '' } catch { return '' }
}

function saveConversation(value) {
  try {
    if (value) sessionStorage.setItem(sessionKey(), value)
    else sessionStorage.removeItem(sessionKey())
  } catch {}
}

function applyConversation(data) {
  messages.value = data.messages || []
  run.value = data.latest_run || null
  const last = [...messages.value].reverse().find(message => message.role === 'assistant' && message.metadata?.run_id === run.value?.id)
  answer.value = last?.content || run.value?.answer || ''
  recommendations.value = last?.metadata?.recommendations || []
  emailDrafts.value = last?.metadata?.email_drafts || []
}

function schedulePoll(token) {
  clearTimeout(pollTimer)
  if (token === generation && run.value?.status === 'processing') {
    pollTimer = setTimeout(() => poll(token), 2000)
  }
}

async function poll(token) {
  try {
    const { data } = await getAgentRun(run.value.id)
    if (token !== generation) return
    run.value = data
    if (data.status === 'processing') schedulePoll(token)
    else await refresh()
  } catch {
    if (token === generation) connectionError.value = '暂时无法获取状态，后台任务可能仍在执行。请先刷新状态。'
  }
}

async function connect() {
  const token = ++generation
  clearTimeout(pollTimer)
  loading.value = false
  connectionError.value = ''
  run.value = null
  conversationId.value = ''
  messages.value = []
  recommendations.value = []
  emailDrafts.value = []
  answer.value = ''
  input.value = ''
  if (!props.manuscriptId) return
  try {
    const saved = savedConversation()
    if (saved) {
      try {
        const { data } = await getAgentConversation(saved)
        if (token !== generation) return
        conversationId.value = saved
        applyConversation(data)
        schedulePoll(token)
        return
      } catch (error) {
        if (token !== generation) return
        if (error.response?.status !== 404) throw error
        saveConversation('')
      }
    }
    const { data } = await createAgentConversation(props.manuscriptId)
    if (token !== generation) return
    conversationId.value = data.id
    saveConversation(data.id)
  } catch (error) {
    if (token === generation) connectionError.value = '编辑助手连接失败，请重试。'
  }
}

async function refresh() {
  if (!conversationId.value) return connect()
  const token = generation
  clearTimeout(pollTimer)
  loading.value = true
  try {
    const { data } = await getAgentConversation(conversationId.value)
    if (token !== generation) return
    applyConversation(data)
    connectionError.value = ''
    schedulePoll(token)
  } catch {
    if (token === generation) connectionError.value = '无法同步会话，请先重新获取状态，避免重复提交任务。'
  } finally {
    if (token === generation) loading.value = false
  }
}

async function send() {
  const content = input.value.trim()
  if (!content || !conversationId.value || loading.value || busy.value || connectionError.value) return
  const token = generation
  input.value = ''
  messages.value.push({ id: `user-${Date.now()}`, role: 'user', content })
  loading.value = true
  try {
    const { data } = await sendAgentMessage(conversationId.value, content)
    if (token !== generation) return
    run.value = data
    recommendations.value = []
    emailDrafts.value = []
    answer.value = ''
    await refresh()
  } catch (error) {
    if (token !== generation) return
    input.value = content
    ElMessage.error(error.response?.status === 429 ? '当前任务较多，请稍后再试。' : '提交未确认，正在同步任务状态。')
    await refresh()
  } finally {
    if (token === generation) loading.value = false
  }
}

async function approve(approved) {
  if (loading.value || connectionError.value || run.value?.status !== 'waiting_approval') return
  const token = generation
  loading.value = true
  try {
    await approveAgentRun(run.value.id, approved, approved ? approvalRequests.value.map(request => request.call_id) : [])
  } catch {
    if (token === generation) ElMessage.error('审批结果未确认，正在同步状态。')
  } finally {
    if (token === generation) await refresh()
  }
}

function doiLink(doi) {
  const identifier = String(doi).replace(/^https?:\/\/(dx\.)?doi\.org\//i, '')
  return `https://doi.org/${encodeURIComponent(identifier)}`
}

function downloadReport() {
  const lines = ['# 审稿辅助结果', '', answer.value, '', '## 推荐依据']
  for (const item of recommendations.value) {
    lines.push(`\n### ${item.rank}. ${item.reviewer_name}`, item.reviewer_affiliation || '', item.rationale?.explanation || '')
    for (const paper of item.rationale?.representative_papers || []) {
      lines.push(`- ${paper.title} (${paper.publication_year || '年份未知'}) ${paper.doi ? doiLink(paper.doi) : ''}`)
    }
  }
  for (const draft of emailDrafts.value) lines.push('\n## 邀请草稿（需人工复核，未发送）', draft.subject, draft.body)
  lines.push(`\n运行记录：${run.value.id}`, `停止原因：${run.value.stop_reason}`)
  const url = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' }))
  const link = document.createElement('a')
  link.href = url
  link.download = `editor-result-${run.value.id}.md`
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

watch(() => props.manuscriptId, connect, { immediate: true })
onBeforeUnmount(() => { generation += 1; clearTimeout(pollTimer) })
</script>

<style scoped>
.agent-card { display: grid; gap: 16px; margin: 18px 0; padding: 22px; border: 1px solid #d6e4de; border-radius: 22px; background: rgba(255, 255, 255, 0.94); box-shadow: 0 12px 26px rgba(16, 42, 31, 0.07); }
.agent-head, .agent-input { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }
.agent-head h2 { margin: 4px 0; }
.agent-head p, .agent-message p, .agent-answer p { margin: 0; line-height: 1.6; }
.agent-messages { display: grid; gap: 10px; max-height: 360px; overflow: auto; }
.agent-empty { padding: 16px; border-radius: 12px; color: #52665d; background: #f8fcfa; }
.agent-message, .agent-answer { padding: 12px 14px; border-radius: 14px; background: #f5f7ea; }
.agent-message.user { background: #e9f5ff; }
.agent-role { display: block; margin-bottom: 4px; color: #52665d; font-size: 12px; font-weight: 700; }
.agent-input { align-items: flex-end; }
.agent-input .el-textarea { flex: 1; }
.agent-results { display: grid; gap: 8px; margin-top: 12px; }
.agent-result { display: grid; gap: 3px; padding: 10px; border: 1px solid #dce9e3; border-radius: 10px; background: #fff; }
.agent-result span, .agent-result small { color: #60756c; }
.agent-result pre { white-space: pre-wrap; font-family: inherit; line-height: 1.6; }
.agent-message p { white-space: pre-wrap; }
.agent-status, .agent-approval { display: grid; gap: 8px; padding: 12px; border-radius: 12px; background: #fff7e6; }
.agent-status span, .agent-status small { color: #60756c; font-size: 12px; }
.agent-approval pre { white-space: pre-wrap; overflow-wrap: anywhere; }
.agent-shortcuts { display: flex; flex-wrap: wrap; gap: 6px; }
.agent-shortcuts .el-button { margin: 0; }
.agent-evidence { display: grid; gap: 4px; margin: 8px 0; font-size: 12px; }
</style>
