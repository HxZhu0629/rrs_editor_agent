<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="brand-lockup">
        <div class="brand-mark">R</div>
        <div>
          <p class="eyebrow">Reviewer Recommendation Studio</p>
          <h1>审稿人推荐工作台</h1>
        </div>
      </div>
      <div class="topbar-actions">
        <div class="service-status"><span class="status-dot"></span>AI 服务在线</div>
        <el-upload
          :show-file-list="false"
          accept=".pdf,.docx"
          :before-upload="onUploadPdf"
          :disabled="uploading"
        >
          <el-button type="primary" :loading="uploading">上传新稿件</el-button>
        </el-upload>
      </div>
    </header>

    <main class="workspace-grid">
      <aside class="sidebar panel-surface">
        <div class="sidebar-head">
          <div>
            <span class="section-kicker">Workspace</span>
            <h2>稿件队列</h2>
          </div>
          <span class="count-badge">{{ manuscripts.length }}</span>
        </div>
        <el-input v-model="searchText" clearable placeholder="搜索稿件标题或领域" class="search-input">
          <template #prefix>⌕</template>
        </el-input>
        <div class="queue-caption">选择一篇稿件开始推荐</div>
        <el-scrollbar class="manuscript-scroll" v-loading="listLoading">
          <button
            v-for="item in filteredManuscripts"
            :key="item.id"
            type="button"
            class="manuscript-row"
            :class="{ active: selectedId === item.id }"
            @click="selectManuscript(item.id)"
          >
            <span class="row-status" :class="`status-${item.status || 'draft'}`"></span>
            <span class="manuscript-row-copy">
              <strong>{{ item.title || '未命名稿件' }}</strong>
              <small>{{ item.domain || '未设置领域' }}</small>
            </span>
            <span class="row-arrow">›</span>
          </button>
          <el-empty v-if="!filteredManuscripts.length && !listLoading" description="没有匹配的稿件" />
        </el-scrollbar>
        <div class="sidebar-footer">
          <span class="legend-dot"></span>
          <span>已推荐</span>
          <span class="legend-dot muted"></span>
          <span>待处理</span>
        </div>
      </aside>

      <section class="main-column">
        <template v-if="selectedId">
          <section class="manuscript-hero panel-surface" v-loading="detailLoading">
            <div class="hero-content">
              <div class="hero-label-row">
                <span class="section-kicker">Selected manuscript</span>
                <el-tag :type="statusTagType(selectedManuscript?.status)" effect="light" round>
                  {{ formatStatus(selectedManuscript?.status) }}
                </el-tag>
              </div>
              <h2>{{ form.title || '未命名稿件' }}</h2>
              <p class="hero-domain">{{ form.domain || '未设置研究领域' }}</p>
              <div class="keyword-row">
                <span v-for="keyword in formKeywords.slice(0, 8)" :key="keyword" class="keyword-pill">{{ keyword }}</span>
                <span v-if="!formKeywords.length" class="keyword-placeholder">尚未填写关键词</span>
              </div>
            </div>
            <div class="hero-actions">
              <div class="hero-meta">
                <span>作者</span><strong>{{ form.authors.length }} 位</strong>
                <span>源文档</span><strong>{{ selectedManuscript?.source_pdf ? '已上传' : '无源文件' }}</strong>
              </div>
              <el-button plain @click="metadataOpen = ['metadata']">编辑稿件信息</el-button>
            </div>
          </section>

          <section class="workflow-bar panel-surface">
            <div class="workflow-step done"><span>1</span><div><strong>确认稿件</strong><small>标题、作者与关键词</small></div></div>
            <div class="workflow-line done"></div>
            <div class="workflow-step" :class="{ done: recommendations.length }"><span>2</span><div><strong>召回审稿人</strong><small>多路检索与重排</small></div></div>
            <div class="workflow-line" :class="{ done: recommendations.length }"></div>
            <div class="workflow-step" :class="{ done: recommendations.length }"><span>3</span><div><strong>编辑决策</strong><small>解释、筛选与邀请</small></div></div>
          </section>

          <section class="recommend-panel panel-surface">
            <div class="panel-header recommendation-header">
              <div>
                <span class="section-kicker">Hybrid retrieval</span>
                <h2>推荐审稿人</h2>
                <p>从论文证据出发，综合 sparse、dense 与 metadata 召回。</p>
              </div>
              <div class="recommend-actions">
                <label>Top K <el-input-number v-model="topK" :min="1" :max="50" size="small" /></label>
                <el-button type="primary" :disabled="!canRecommend" :loading="recommending" @click="triggerRecommendation">运行推荐</el-button>
              </div>
            </div>

            <div v-if="latestRun" class="run-summary">
              <div><span>运行状态</span><strong class="success-text">{{ formatRunStatus(latestRun.status) }}</strong></div>
              <div><span>候选池</span><strong>{{ latestRun.summary?.candidate_count ?? 0 }}</strong></div>
              <div><span>已返回</span><strong>{{ latestRun.summary?.returned_count ?? 0 }}</strong></div>
              <div><span>方法</span><strong>{{ formatMethod(latestRun.summary?.method) }}</strong></div>
            </div>
            <div v-else class="run-hint"><span class="hint-icon">✦</span><span>{{ recommendHint }}</span></div>

            <div v-if="recommendations.length" class="results-toolbar">
              <div><strong>{{ recommendations.length }} 位候选人</strong><span>按综合重排分数排序</span></div>
              <el-button type="primary" link @click="overviewVisible = true">打开结果总览 →</el-button>
            </div>
            <el-scrollbar v-if="recommendations.length" class="recommend-scroll">
              <div class="reviewer-list">
                <article v-for="item in recommendations" :key="item.id" class="reviewer-card">
                  <div class="reviewer-rank">#{{ item.rank }}</div>
                  <div class="reviewer-main">
                    <div class="reviewer-title-row">
                      <div><h3>{{ item.reviewer_name }}</h3><p>{{ item.reviewer_affiliation || '未提供单位' }}</p></div>
                      <div class="score-block"><strong>{{ Number(item.score).toFixed(3) }}</strong><span>综合得分</span></div>
                    </div>
                    <p class="reviewer-email">{{ item.reviewer_email || '未提供邮箱' }}</p>
                    <div class="route-row">
                      <span v-for="route in item.rationale?.retrieval_routes || []" :key="route" class="route-pill">{{ route }}</span>
                      <span class="rerank-label">rerank {{ formatScore(item.rerank_score) }}</span>
                    </div>
                    <p class="reviewer-reason">{{ item.rationale?.explanation || '当前结果尚未生成详细解释。' }}</p>
                    <details class="evidence-details">
                      <summary><span>查看论文证据与评分拆解</span><span>⌄</span></summary>
                      <div class="evidence-grid">
                        <div class="evidence-column">
                          <span class="evidence-label">代表论文</span>
                          <button v-for="paper in item.rationale?.representative_papers || []" :key="paper.id" type="button" class="paper-row paper-row-action" @click="openEvidence(item, paper)">
                            <strong>{{ paper.title }}</strong><small>{{ paper.publication_year || '年份未知' }} · 关键词 {{ paper.matched_keywords?.length || 0 }} 个 · 文本重叠 {{ paper.token_overlap_count || 0 }} 项</small><span>查看证据与全文入口 →</span>
                          </button>
                        </div>
                        <div class="evidence-column">
                          <span class="evidence-label">评分信号</span>
                          <div class="signal-mini-grid"><span>关键词<strong>{{ item.rationale?.matched_keywords?.length || 0 }}</strong></span><span>语义<strong>{{ formatScore(item.rationale?.vector_score) }}</strong></span><span>近年发文<strong>{{ item.rationale?.recent_publication_count ?? 0 }}</strong></span><span>RRF<strong>{{ formatScore(item.rationale?.rrf_score) }}</strong></span></div>
                          <div v-if="(item.rationale?.risk_flags || []).length" class="risk-row">⚠ {{ item.rationale.risk_flags.join('；') }}</div>
                        </div>
                      </div>
                    </details>
                  </div>
                </article>
              </div>
            </el-scrollbar>
            <div v-else class="empty-results"><div class="empty-icon">⌁</div><h3>还没有推荐结果</h3><p>{{ recommendHint }}</p><el-button type="primary" :disabled="!canRecommend" :loading="recommending" @click="triggerRecommendation">开始推荐</el-button></div>
          </section>

          <el-collapse v-model="metadataOpen" class="metadata-collapse panel-surface">
            <el-collapse-item name="metadata">
              <template #title><div class="collapse-title"><span class="collapse-icon">✎</span><div><strong>稿件元数据确认</strong><small>推荐前确认作者与单位，确保利益冲突过滤准确</small></div></div></template>
              <div class="metadata-actions"><el-button plain size="small" @click="addAuthor">新增作者</el-button><el-button v-if="canExtract" type="warning" plain size="small" :loading="extracting" @click="triggerExtraction">重新解析文档</el-button></div>
              <el-form label-position="top" class="metadata-form">
                <el-form-item label="标题"><el-input v-model="form.title" /></el-form-item>
                <el-form-item label="摘要"><el-input v-model="form.abstract" type="textarea" :rows="5" /></el-form-item>
                <div class="form-two-col"><el-form-item label="研究领域"><el-input v-model="form.domain" /></el-form-item><el-form-item label="关键词（逗号分隔）"><el-input v-model="keywordInput" /></el-form-item></div>
                <div class="authors-header"><div><strong>作者与机构</strong><span>用于冲突检测</span></div><span>{{ form.authors.length }} 位作者</span></div>
                <div v-for="(author, idx) in form.authors" :key="idx" class="author-row"><el-input v-model="author.name" placeholder="姓名" /><el-input v-model="author.email" placeholder="邮箱" /><el-input v-model="author.affiliation" placeholder="单位" /><el-button type="danger" text @click="removeAuthor(idx)">删除</el-button></div>
                <div v-if="!form.authors.length" class="empty-inline">当前没有作者，请至少补充一位作者。</div>
                <div class="metadata-footer"><span>保存后可重新运行推荐</span><el-button type="success" :loading="saving" @click="saveConfirmation">保存人工确认</el-button></div>
              </el-form>
            </el-collapse-item>
          </el-collapse>
        </template>
        <div v-else class="main-empty panel-surface"><div class="empty-icon">R</div><h2>选择一篇稿件开始</h2><p>从左侧稿件队列选择稿件，系统会在这里展示推荐、证据和编辑操作。</p></div>
      </section>

      <aside class="agent-column">
        <div class="agent-intro panel-surface"><div class="agent-intro-icon">✦</div><div><span class="section-kicker">Editor Copilot</span><h2>编辑助手</h2><p>用自然语言调整检索条件，追问推荐依据，生成邀请邮件草稿。</p></div></div>
        <EditorChat v-if="selectedId" :manuscript-id="selectedId" />
        <div v-else class="agent-empty panel-surface"><div class="empty-icon">✦</div><p>选择稿件后，编辑助手将在这里连接。</p></div>
        <div class="trust-card panel-surface"><span class="trust-icon">✓</span><div><strong>证据优先</strong><p>回答引用真实论文证据。邮件仅生成草稿，发送前必须人工确认。</p></div></div>
      </aside>
    </main>

    <el-dialog v-model="overviewVisible" fullscreen append-to-body class="overview-dialog" title="审稿人推荐结果总览">
      <section class="overview-page"><div class="overview-dialog-head"><div><span class="section-kicker">Recommendation overview</span><h2>Top {{ recommendations.length }} 推荐结果</h2><p>按综合得分快速比较候选人，点击代表论文可查看证据和全文入口。</p></div><div class="overview-stats"><div><span>候选池</span><strong>{{ latestRun?.summary?.candidate_count ?? 0 }}</strong></div><div><span>返回人数</span><strong>{{ recommendations.length }}</strong></div><div><span>检索方式</span><strong>{{ formatMethod(latestRun?.summary?.method) }}</strong></div></div></div><div class="overview-grid"><article v-for="item in recommendations" :key="`overview-${item.id}`" class="overview-card"><div class="overview-card-head"><div><span class="rank-pill">#{{ item.rank }}</span><h3>{{ item.reviewer_name }}</h3><p>{{ item.reviewer_affiliation || '未提供单位' }}</p></div><strong>{{ Number(item.score).toFixed(3) }}</strong></div><div class="route-row"><span v-for="route in item.rationale?.retrieval_routes || []" :key="route" class="route-pill">{{ route }}</span></div><p class="overview-reason">{{ item.rationale?.explanation }}</p><button v-if="item.rationale?.representative_papers?.length" type="button" class="overview-paper overview-paper-action" @click="openEvidence(item, item.rationale.representative_papers[0])"><span>代表论文</span><strong>{{ item.rationale.representative_papers[0].title }}</strong><small>查看证据与全文入口 →</small></button></article></div></section>
    </el-dialog>

    <el-drawer v-model="evidenceVisible" title="论文证据复核" size="520px" append-to-body>
      <section v-if="selectedEvidence" class="evidence-drawer" v-loading="accessLoading">
        <div class="evidence-reviewer"><span>候选审稿人</span><strong>#{{ selectedEvidence.item.rank }} {{ selectedEvidence.item.reviewer_name }}</strong><small>{{ selectedEvidence.item.reviewer_affiliation || '未提供单位' }}</small></div>
        <div class="evidence-paper-head"><span class="section-kicker">Representative paper</span><h2>{{ selectedEvidence.paper.title }}</h2><p>{{ selectedEvidence.paper.publication_year || '年份未知' }}<template v-if="paperAccess?.venue"> · {{ paperAccess.venue }}</template></p></div>
        <div class="evidence-match-grid"><div><span>关键词命中</span><strong>{{ selectedEvidence.paper.matched_keywords?.length || 0 }}</strong></div><div><span>文本重叠</span><strong>{{ selectedEvidence.paper.token_overlap_count || 0 }}</strong></div><div><span>召回证据</span><strong>{{ selectedEvidence.localEvidence?.recall_route || '—' }}</strong></div></div>
        <div v-if="selectedEvidence.paper.matched_keywords?.length" class="drawer-keywords"><span v-for="keyword in selectedEvidence.paper.matched_keywords" :key="keyword">{{ keyword }}</span></div>
        <div v-if="selectedEvidence.localEvidence?.evidence_text" class="evidence-excerpt"><span>本地证据片段</span><p>{{ selectedEvidence.localEvidence.evidence_text }}</p></div>
        <div class="access-panel">
          <div><span class="evidence-label">论文与全文入口</span><strong>{{ accessStatusText }}</strong><p>只提供 DOI、OpenAlex 和明确标记为开放获取的 PDF，不绕过出版商权限。</p></div>
          <div class="access-actions">
            <el-button :disabled="!doiUrl" @click="openExternal(doiUrl)">打开 DOI</el-button>
            <el-button :disabled="!openAlexUrl" @click="openExternal(openAlexUrl)">查看 OpenAlex</el-button>
            <el-button type="primary" :disabled="!paperAccess?.pdf_url" @click="openExternal(paperAccess.pdf_url)">打开 PDF</el-button>
          </div>
          <div v-if="paperAccess?.is_open_access" class="oa-note">开放获取 {{ paperAccess.open_access_status || '' }}<template v-if="paperAccess.open_access_license"> · {{ paperAccess.open_access_license }}</template></div>
          <div v-else-if="!accessLoading" class="no-pdf-note">未找到可验证的开放获取 PDF，可通过 DOI 或论文页面继续访问。</div>
          <el-button v-if="paperAccess?.landing_page_url" type="primary" link @click="openExternal(paperAccess.landing_page_url)">打开论文详情页 →</el-button>
        </div>
      </section>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { confirmManuscript, getManuscript, listManuscripts, resolvePaperAccess, runExtraction, runRecommendation, uploadManuscript } from './api/client'
import EditorChat from './components/EditorChat.vue'

const manuscripts = ref([])
const selectedId = ref('')
const selectedManuscript = ref(null)
const recommendations = ref([])
const latestRun = ref(null)
const keywordInput = ref('')
const searchText = ref('')
const topK = ref(20)
const listLoading = ref(false)
const detailLoading = ref(false)
const saving = ref(false)
const uploading = ref(false)
const extracting = ref(false)
const recommending = ref(false)
const overviewVisible = ref(false)
const evidenceVisible = ref(false)
const selectedEvidence = ref(null)
const paperAccess = ref(null)
const accessLoading = ref(false)
const metadataOpen = ref([])

const form = reactive({ title: '', abstract: '', domain: '', authors: [] })

const filteredManuscripts = computed(() => {
  const keyword = searchText.value.trim().toLowerCase()
  if (!keyword) return manuscripts.value
  return manuscripts.value.filter((item) => (item.title || '').toLowerCase().includes(keyword) || (item.domain || '').toLowerCase().includes(keyword))
})
const formKeywords = computed(() => keywordInput.value.split(',').map((item) => item.trim()).filter(Boolean))
const canExtract = computed(() => Boolean(selectedId.value && selectedManuscript.value?.source_pdf))
const canRecommend = computed(() => Boolean(selectedId.value && form.title.trim() && normalizeAuthors().length))
const recommendHint = computed(() => {
  if (!selectedId.value) return '请先选择稿件。'
  if (!form.title.trim()) return '标题为空，请先补全标题。'
  if (!normalizeAuthors().length) return '请至少补充一位作者，系统才能执行冲突过滤。'
  return '后端会自动过滤同单位与近五年合作者冲突。'
})
const doiUrl = computed(() => paperAccess.value?.doi_url || normalizeDoiUrl(selectedEvidence.value?.paper?.doi))
const openAlexUrl = computed(() => paperAccess.value?.openalex_id ? `https://openalex.org/${paperAccess.value.openalex_id}` : '')
const accessStatusText = computed(() => {
  if (accessLoading.value) return '正在通过 OpenAlex 查询开放获取入口…'
  if (paperAccess.value?.pdf_url) return '已找到开放获取 PDF'
  if (paperAccess.value?.landing_page_url || doiUrl.value) return '已找到论文页面，暂无开放 PDF'
  return paperAccess.value?.error ? '外部查询暂时不可用' : '未找到可访问地址'
})

function formatStatus(status) { return ({ draft: '草稿', extracting: '抽取中', review_pending: '待核对', confirmed: '已确认', recommended: '已推荐', failed: '失败' })[status] || status || '未知' }
function formatRunStatus(status) { return ({ processing: '处理中', success: '成功', failed: '失败' })[status] || status || '未知' }
function formatMethod(method) { return ({ lexical: '词面检索', vector: '向量检索', hybrid: '混合检索' })[method] || method || '默认' }
function statusTagType(status) { return ({ draft: 'info', extracting: 'warning', review_pending: 'warning', confirmed: 'success', recommended: 'primary', failed: 'danger' })[status] || 'info' }
function resetForm() { form.title = ''; form.abstract = ''; form.domain = ''; form.authors = []; keywordInput.value = '' }
function hydrateForm(data) { selectedManuscript.value = data; form.title = data.title || ''; form.abstract = data.abstract || ''; form.domain = data.domain || ''; keywordInput.value = (data.keywords || []).join(', '); form.authors = (data.authors || []).map((item) => ({ name: item.name || '', email: item.email || '', affiliation: item.affiliation || '', author_order: item.author_order || 1, is_corresponding: Boolean(item.is_corresponding), is_co_first: Boolean(item.is_co_first) })) }
function normalizeAuthors() { return form.authors.map((author, idx) => ({ name: (author.name || '').trim(), email: (author.email || '').trim(), affiliation: (author.affiliation || '').trim(), author_order: author.author_order || idx + 1, is_corresponding: Boolean(author.is_corresponding), is_co_first: Boolean(author.is_co_first) })).filter((author) => author.name) }
function addAuthor() { form.authors.push({ name: '', email: '', affiliation: '', author_order: form.authors.length + 1, is_corresponding: false, is_co_first: false }) }
function removeAuthor(idx) { form.authors.splice(idx, 1) }
function getErrorMessage(err, fallback) { return err?.response?.data?.detail || err?.response?.data?.error_message || err?.message || fallback }
function formatScore(value) { if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'; return Number(value).toFixed(3) }
function normalizeDoiUrl(value) { const doi = String(value || '').trim().replace(/^https?:\/\/(dx\.)?doi\.org\//i, '').replace(/^doi:/i, ''); return doi ? `https://doi.org/${doi}` : '' }
function openExternal(url) { if (!/^https?:\/\//i.test(String(url || ''))) return; window.open(url, '_blank', 'noopener,noreferrer') }
async function openEvidence(item, paper) {
  const localEvidence = (item.evidence || []).find((entry) => entry.paper_id === paper.id)
  selectedEvidence.value = { item, paper, localEvidence }
  paperAccess.value = null
  evidenceVisible.value = true
  accessLoading.value = true
  try { const { data } = await resolvePaperAccess(paper.id); paperAccess.value = data } catch (err) { paperAccess.value = { error: getErrorMessage(err, 'OpenAlex 查询失败') }; ElMessage.warning(`全文入口查询失败：${paperAccess.value.error}`) } finally { accessLoading.value = false }
}

async function refreshManuscripts() {
  listLoading.value = true
  try {
    const { data } = await listManuscripts()
    manuscripts.value = data
    if (!selectedId.value && data.length) { await selectManuscript(data[0].id); return }
    if (selectedId.value) {
      const current = data.find((item) => item.id === selectedId.value)
      if (!current) { selectedId.value = ''; selectedManuscript.value = null; recommendations.value = []; latestRun.value = null; resetForm() }
      else if (selectedManuscript.value) selectedManuscript.value = { ...selectedManuscript.value, ...current }
    }
  } catch (err) { ElMessage.error(`加载稿件失败：${getErrorMessage(err, '未知错误')}`) } finally { listLoading.value = false }
}
async function selectManuscript(id) {
  selectedId.value = id; detailLoading.value = true; recommendations.value = []; latestRun.value = null; metadataOpen.value = []
  try { const { data } = await getManuscript(id); hydrateForm(data) } catch (err) { ElMessage.error(`加载稿件详情失败：${getErrorMessage(err, '未知错误')}`) } finally { detailLoading.value = false }
}
async function onUploadPdf(file) {
  uploading.value = true
  try { const { data } = await uploadManuscript(file); ElMessage.success('文档上传成功'); await refreshManuscripts(); await selectManuscript(data.id) } catch (err) { ElMessage.error(`上传失败：${getErrorMessage(err, '未知错误')}`) } finally { uploading.value = false }
  return false
}
async function triggerExtraction() {
  if (!selectedId.value || !canExtract.value) return ElMessage.warning('当前稿件没有源文档，无法运行解析')
  extracting.value = true
  try { await runExtraction(selectedId.value); ElMessage.success('抽取完成，请检查并修正元数据'); await selectManuscript(selectedId.value); await refreshManuscripts() } catch (err) { ElMessage.error(`抽取失败：${getErrorMessage(err, '未知错误')}`) } finally { extracting.value = false }
}
async function saveConfirmation() {
  if (!selectedId.value) return
  const authors = normalizeAuthors()
  if (!authors.length) return ElMessage.warning('至少需要一位作者')
  saving.value = true
  try { const { data } = await confirmManuscript(selectedId.value, { title: form.title.trim(), abstract: form.abstract.trim(), domain: form.domain.trim(), keywords: formKeywords.value, authors }); hydrateForm(data); ElMessage.success('人工确认已保存'); await refreshManuscripts() } catch (err) { ElMessage.error(`保存失败：${getErrorMessage(err, '未知错误')}`) } finally { saving.value = false }
}
async function triggerRecommendation() {
  if (!canRecommend.value) return ElMessage.warning(recommendHint.value)
  recommending.value = true
  try { const { data } = await runRecommendation(selectedId.value, topK.value); latestRun.value = data; recommendations.value = data.items || []; if (selectedManuscript.value) selectedManuscript.value = { ...selectedManuscript.value, status: 'recommended' }; ElMessage.success('审稿人推荐完成'); await refreshManuscripts() } catch (err) { ElMessage.error(`推荐失败：${getErrorMessage(err, '未知错误')}`) } finally { recommending.value = false }
}
onMounted(async () => { resetForm(); await refreshManuscripts() })
</script>

<style scoped>
.topbar { display:flex; justify-content:space-between; align-items:center; gap:24px; padding:16px 20px; margin-bottom:18px; background:rgba(255,255,255,.86); border:1px solid var(--line); border-radius:18px; box-shadow:var(--shadow-sm); backdrop-filter:blur(12px); }
.brand-lockup,.topbar-actions,.hero-label-row,.recommendation-header,.results-toolbar,.reviewer-title-row,.metadata-actions,.metadata-footer,.authors-header,.collapse-title { display:flex; align-items:center; }
.brand-lockup { gap:12px; }
.brand-mark { display:grid; place-items:center; width:38px; height:38px; border-radius:12px; color:#fff; background:linear-gradient(145deg,var(--accent),#39a787); font-weight:800; font-size:20px; }
.topbar h1 { margin:2px 0 0; font-size:18px; letter-spacing:-.02em; }
.topbar-actions { gap:14px; }
.service-status { display:flex; align-items:center; gap:7px; color:var(--ink-secondary); font-size:12px; }
.status-dot { width:7px; height:7px; border-radius:50%; background:#35ad79; box-shadow:0 0 0 4px #dbf6e8; }
.workspace-grid { display:grid; grid-template-columns:260px minmax(0,1fr) 330px; gap:16px; align-items:start; }
.panel-surface { background:rgba(255,255,255,.9); border:1px solid var(--line); border-radius:18px; box-shadow:var(--shadow-sm); }
.sidebar { position:sticky; top:16px; display:flex; flex-direction:column; min-height:calc(100vh - 108px); padding:16px; }
.sidebar-head { display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:14px; }
.sidebar h2,.agent-intro h2 { margin:3px 0 0; font-size:19px; }
.section-kicker { display:block; color:var(--accent-deep); font-size:10px; font-weight:800; letter-spacing:.14em; text-transform:uppercase; }
.count-badge { display:grid; place-items:center; min-width:28px; height:24px; padding:0 7px; border-radius:8px; color:var(--accent-deep); background:var(--accent-soft); font-size:12px; font-weight:800; }
.queue-caption { margin:12px 2px 8px; color:var(--ink-muted); font-size:11px; }
.manuscript-scroll { flex:1; min-height:260px; }
.manuscript-row { display:flex; align-items:center; width:100%; gap:9px; padding:11px 8px; margin-bottom:4px; border:1px solid transparent; border-radius:12px; color:inherit; text-align:left; background:transparent; cursor:pointer; transition:.18s ease; }
.manuscript-row:hover { background:#f5faf7; border-color:var(--line); }
.manuscript-row.active { background:var(--accent-soft); border-color:#a8dece; }
.row-status { flex:0 0 auto; width:7px; height:7px; border-radius:50%; background:#b7c8c0; }
.row-status.status-recommended { background:var(--accent); }.row-status.status-confirmed { background:#53a877; }.row-status.status-failed { background:#d46161; }
.manuscript-row-copy { display:flex; flex:1; min-width:0; flex-direction:column; gap:3px; }.manuscript-row-copy strong { overflow:hidden; font-size:12px; line-height:1.35; text-overflow:ellipsis; white-space:nowrap; }.manuscript-row-copy small { overflow:hidden; color:var(--ink-muted); font-size:10px; text-overflow:ellipsis; white-space:nowrap; }.row-arrow { color:#91a59b; font-size:18px; }
.sidebar-footer { display:flex; align-items:center; gap:6px; padding-top:14px; margin-top:12px; border-top:1px solid var(--line); color:var(--ink-muted); font-size:10px; }.legend-dot { width:6px; height:6px; margin-left:4px; border-radius:50%; background:var(--accent); }.legend-dot.muted { background:#b7c8c0; }
.main-column { display:grid; gap:14px; min-width:0; }.manuscript-hero { display:flex; justify-content:space-between; gap:24px; padding:22px 24px; background:linear-gradient(120deg,#fff 0%,#f0faf5 100%); }.hero-content { min-width:0; }.hero-label-row { gap:10px; }.manuscript-hero h2 { max-width:700px; margin:10px 0 4px; font-size:25px; line-height:1.22; letter-spacing:-.03em; }.hero-domain { margin:0; color:var(--ink-secondary); font-size:13px; }.keyword-row { display:flex; flex-wrap:wrap; gap:6px; margin-top:14px; }.keyword-pill,.route-pill { display:inline-flex; align-items:center; padding:4px 8px; border-radius:6px; color:var(--accent-deep); background:#e3f5ed; font-size:10px; font-weight:700; }.keyword-placeholder { color:var(--ink-muted); font-size:11px; }.hero-actions { display:flex; flex-direction:column; justify-content:space-between; align-items:flex-end; gap:14px; }.hero-meta { display:grid; grid-template-columns:auto auto; gap:3px 12px; color:var(--ink-muted); font-size:11px; text-align:right; }.hero-meta strong { color:var(--ink-primary); }
.workflow-bar { display:flex; align-items:center; gap:10px; padding:12px 18px; }.workflow-step { display:flex; align-items:center; gap:8px; min-width:0; color:#94a79e; }.workflow-step > span { display:grid; place-items:center; width:24px; height:24px; border-radius:50%; color:#879b91; background:#edf3ef; font-size:11px; font-weight:800; }.workflow-step.done { color:var(--ink-primary); }.workflow-step.done > span { color:#fff; background:var(--accent); }.workflow-step div { display:flex; flex-direction:column; gap:2px; }.workflow-step strong { font-size:11px; }.workflow-step small { color:var(--ink-muted); font-size:9px; }.workflow-line { flex:1; height:1px; background:#dfe9e4; }.workflow-line.done { background:#9dd8c3; }
.recommend-panel { overflow:hidden; padding:20px 22px; }.panel-header { gap:16px; }.panel-header h2 { margin:3px 0 3px; font-size:21px; }.panel-header p { margin:0; color:var(--ink-secondary); font-size:12px; }.recommendation-header { justify-content:space-between; }.recommend-actions { display:flex; align-items:center; gap:9px; }.recommend-actions label { display:flex; align-items:center; gap:7px; color:var(--ink-muted); font-size:11px; }.run-summary { display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin-top:18px; padding:11px 12px; border:1px solid var(--line); border-radius:12px; background:#f7fbf9; }.run-summary div { display:flex; flex-direction:column; gap:4px; }.run-summary span { color:var(--ink-muted); font-size:10px; }.run-summary strong { font-size:14px; }.success-text { color:var(--accent-deep); }.run-hint { display:flex; align-items:center; gap:9px; margin-top:16px; padding:11px 13px; border-radius:11px; color:var(--ink-secondary); background:#fafcf9; font-size:11px; }.hint-icon,.agent-intro-icon { color:var(--accent); }.results-toolbar { justify-content:space-between; margin:20px 0 10px; }.results-toolbar div { display:flex; align-items:baseline; gap:8px; }.results-toolbar strong { font-size:13px; }.results-toolbar span { color:var(--ink-muted); font-size:10px; }.recommend-scroll { max-height:calc(100vh - 410px); min-height:260px; padding-right:4px; }.reviewer-list { display:grid; gap:9px; }.reviewer-card { display:flex; gap:13px; padding:14px; border:1px solid var(--line); border-radius:14px; background:#fff; transition:.18s ease; }.reviewer-card:hover { border-color:#acdcca; box-shadow:0 7px 18px rgba(16,42,31,.06); }.reviewer-rank { display:grid; place-items:center; flex:0 0 auto; width:28px; height:28px; border-radius:9px; color:var(--accent-deep); background:var(--accent-soft); font-size:11px; font-weight:800; }.reviewer-main { flex:1; min-width:0; }.reviewer-title-row { justify-content:space-between; gap:14px; }.reviewer-title-row h3 { margin:0 0 3px; font-size:15px; }.reviewer-title-row p,.reviewer-email { margin:0; color:var(--ink-secondary); font-size:11px; }.reviewer-email { margin-top:5px; color:var(--ink-muted); }.score-block { display:flex; flex-direction:column; align-items:flex-end; }.score-block strong { color:var(--accent-deep); font-size:20px; font-variant-numeric:tabular-nums; }.score-block span,.rerank-label { color:var(--ink-muted); font-size:9px; }.route-row { display:flex; flex-wrap:wrap; align-items:center; gap:5px; margin-top:9px; }.route-pill { padding:3px 7px; color:#43665a; background:#eef6f2; font-size:9px; text-transform:uppercase; }.rerank-label { margin-left:3px; }.reviewer-reason { margin:9px 0 0; color:#354a41; font-size:12px; line-height:1.55; }.evidence-details { margin-top:10px; border-top:1px solid #edf2ef; padding-top:8px; }.evidence-details summary { display:flex; justify-content:space-between; cursor:pointer; color:var(--accent-deep); font-size:10px; font-weight:700; list-style:none; }.evidence-details summary::-webkit-details-marker { display:none; }.evidence-grid { display:grid; grid-template-columns:1.2fr 1fr; gap:14px; margin-top:11px; }.evidence-column { display:grid; align-content:start; gap:7px; }.evidence-label { color:var(--ink-muted); font-size:10px; font-weight:700; }.paper-row { display:flex; flex-direction:column; gap:3px; padding:8px 9px; border-radius:9px; background:#fbfdfc; border:1px solid #e5eee9; }.paper-row strong { color:#31483d; font-size:10px; line-height:1.4; }.paper-row small { color:var(--ink-muted); font-size:9px; }.signal-mini-grid { display:grid; grid-template-columns:repeat(2,1fr); gap:5px; }.signal-mini-grid span { display:flex; justify-content:space-between; padding:6px 7px; border-radius:7px; color:var(--ink-muted); background:#f8faf6; font-size:9px; }.signal-mini-grid strong { color:var(--ink-primary); }.risk-row { padding:7px 8px; border-radius:7px; color:#986b23; background:#fff7e6; font-size:9px; line-height:1.4; }.empty-results,.main-empty,.agent-empty { display:flex; flex-direction:column; align-items:center; justify-content:center; min-height:290px; padding:30px; text-align:center; }.empty-icon { display:grid; place-items:center; width:44px; height:44px; margin-bottom:12px; border-radius:14px; color:var(--accent); background:var(--accent-soft); font-size:21px; font-weight:800; }.empty-results h3,.main-empty h2 { margin:0 0 5px; font-size:17px; }.empty-results p,.main-empty p,.agent-empty p { max-width:330px; margin:0 0 15px; color:var(--ink-secondary); font-size:12px; line-height:1.6; }
.metadata-collapse { overflow:hidden; }.metadata-collapse :deep(.el-collapse-item__header) { height:auto; min-height:66px; padding:0 18px; border:0; }.metadata-collapse :deep(.el-collapse-item__wrap) { border:0; }.metadata-collapse :deep(.el-collapse-item__content) { padding:0 20px 20px; }.collapse-title { gap:10px; }.collapse-icon { display:grid; place-items:center; width:28px; height:28px; border-radius:9px; color:var(--accent-deep); background:var(--accent-soft); }.collapse-title div { display:flex; flex-direction:column; gap:3px; }.collapse-title small { color:var(--ink-muted); font-size:10px; font-weight:400; }.metadata-actions { justify-content:flex-end; gap:8px; margin-bottom:12px; }.metadata-form :deep(.el-form-item) { margin-bottom:12px; }.form-two-col { display:grid; grid-template-columns:1fr 1fr; gap:12px; }.authors-header { justify-content:space-between; margin:2px 0 9px; padding-top:5px; }.authors-header div { display:flex; align-items:baseline; gap:8px; }.authors-header span { color:var(--ink-muted); font-size:10px; }.author-row { display:grid; grid-template-columns:1fr 1fr 1.2fr 54px; gap:7px; margin-bottom:7px; }.empty-inline { padding:10px; border:1px dashed var(--line); border-radius:9px; color:var(--ink-muted); font-size:11px; }.metadata-footer { justify-content:space-between; margin-top:14px; color:var(--ink-muted); font-size:10px; }
.agent-column { display:grid; align-content:start; gap:12px; position:sticky; top:16px; }.agent-intro { display:flex; gap:11px; padding:17px; background:linear-gradient(140deg,#f0faf5,#fff); }.agent-intro-icon { display:grid; place-items:center; flex:0 0 auto; width:32px; height:32px; border-radius:11px; background:var(--accent-soft); font-size:18px; }.agent-intro p { margin:7px 0 0; color:var(--ink-secondary); font-size:11px; line-height:1.5; }.agent-column :deep(.agent-card) { margin:0; padding:17px; border-radius:18px; box-shadow:var(--shadow-sm); }.trust-card { display:flex; gap:10px; padding:13px 15px; }.trust-icon { display:grid; place-items:center; flex:0 0 auto; width:21px; height:21px; border-radius:50%; color:#fff; background:var(--accent); font-size:12px; }.trust-card strong { font-size:11px; }.trust-card p { margin:4px 0 0; color:var(--ink-muted); font-size:10px; line-height:1.5; }
.overview-page { min-height:calc(100vh - 90px); padding:6px; background:linear-gradient(135deg,#f4faf7,#fff9ee); }.overview-dialog-head { display:flex; justify-content:space-between; gap:20px; align-items:stretch; margin-bottom:16px; padding:22px; border:1px solid var(--line); border-radius:18px; background:rgba(255,255,255,.9); }.overview-dialog-head h2 { margin:5px 0 3px; font-size:26px; }.overview-dialog-head p { margin:0; color:var(--ink-secondary); font-size:12px; }.overview-stats { display:grid; grid-template-columns:repeat(3,120px); gap:8px; }.overview-stats div { display:flex; flex-direction:column; justify-content:center; padding:12px; border-radius:12px; background:#f7fbf9; }.overview-stats span { color:var(--ink-muted); font-size:10px; }.overview-stats strong { margin-top:5px; color:var(--accent-deep); font-size:17px; }.overview-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); gap:12px; }.overview-card { display:flex; min-height:220px; flex-direction:column; gap:9px; padding:14px; border:1px solid var(--line); border-radius:15px; background:#fff; }.overview-card-head { display:flex; justify-content:space-between; gap:10px; }.overview-card-head h3 { margin:7px 0 3px; font-size:14px; }.overview-card-head p { margin:0; color:var(--ink-secondary); font-size:10px; }.overview-card-head > strong { color:var(--accent-deep); font-size:21px; }.rank-pill { display:inline-flex; padding:3px 7px; border-radius:5px; color:var(--accent-deep); background:var(--accent-soft); font-size:10px; font-weight:800; }.overview-reason { margin:0; color:#354a41; font-size:11px; line-height:1.55; }.overview-paper { margin-top:auto; padding:9px; border-radius:9px; background:#fffaf0; border:1px solid #f1e5c7; }.overview-paper span { display:block; color:var(--ink-muted); font-size:9px; }.overview-paper strong { display:block; margin-top:4px; color:#4c4837; font-size:10px; line-height:1.4; }
.paper-row-action { width:100%; color:inherit; font:inherit; text-align:left; cursor:pointer; transition:.16s ease; }.paper-row-action:hover { border-color:#9fd5c1; background:#f2faf6; }.paper-row-action > span { margin-top:3px; color:var(--accent-deep); font-size:9px; font-weight:700; }.overview-paper-action { width:100%; font:inherit; text-align:left; cursor:pointer; }.overview-paper-action small { display:block; margin-top:7px; color:var(--accent-deep); font-size:9px; font-weight:700; }
.evidence-drawer { display:grid; gap:16px; }.evidence-reviewer { display:flex; flex-direction:column; gap:4px; padding:12px 14px; border-radius:12px; background:var(--accent-soft); }.evidence-reviewer span,.evidence-reviewer small,.evidence-paper-head p { color:var(--ink-muted); font-size:10px; }.evidence-reviewer strong { font-size:13px; }.evidence-paper-head h2 { margin:7px 0 5px; font-size:21px; line-height:1.35; }.evidence-paper-head p { margin:0; }.evidence-match-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:7px; }.evidence-match-grid div { display:flex; flex-direction:column; gap:5px; padding:10px; border:1px solid var(--line); border-radius:10px; background:#fafcfb; }.evidence-match-grid span { color:var(--ink-muted); font-size:9px; }.evidence-match-grid strong { font-size:13px; }.drawer-keywords { display:flex; flex-wrap:wrap; gap:6px; }.drawer-keywords span { padding:4px 8px; border-radius:7px; color:var(--accent-deep); background:#e6f5ee; font-size:10px; }.evidence-excerpt,.access-panel { padding:14px; border:1px solid var(--line); border-radius:13px; }.evidence-excerpt > span { color:var(--ink-muted); font-size:10px; font-weight:700; }.evidence-excerpt p { max-height:190px; margin:8px 0 0; overflow:auto; color:#3d5148; font-size:11px; line-height:1.65; }.access-panel { display:grid; gap:12px; background:#fbfdfc; }.access-panel > div:first-child { display:grid; gap:5px; }.access-panel > div:first-child strong { font-size:13px; }.access-panel > div:first-child p { margin:0; color:var(--ink-muted); font-size:10px; line-height:1.5; }.access-actions { display:flex; flex-wrap:wrap; gap:7px; }.oa-note,.no-pdf-note { padding:8px 10px; border-radius:8px; font-size:10px; }.oa-note { color:#287252; background:#e7f6ee; }.no-pdf-note { color:#8a6527; background:#fff6df; }
@media (max-width:1200px) { .workspace-grid { grid-template-columns:220px minmax(0,1fr); }.agent-column { grid-column:1 / -1; position:static; grid-template-columns:1fr 1fr; }.agent-column .trust-card { grid-column:1 / -1; } }
@media (max-width:800px) { .app-shell { padding:12px; }.topbar { align-items:flex-start; flex-direction:column; }.topbar-actions { width:100%; justify-content:space-between; }.workspace-grid { grid-template-columns:1fr; }.sidebar { position:static; min-height:unset; }.manuscript-scroll { max-height:260px; }.manuscript-hero,.recommendation-header,.overview-dialog-head { flex-direction:column; }.hero-actions { align-items:flex-start; width:100%; }.hero-meta { text-align:left; }.workflow-bar { overflow:auto; }.workflow-step { min-width:145px; }.recommend-actions { justify-content:space-between; width:100%; }.run-summary { grid-template-columns:repeat(2,1fr); }.evidence-grid,.form-two-col { grid-template-columns:1fr; }.author-row { grid-template-columns:1fr 1fr; }.agent-column { grid-template-columns:1fr; }.overview-stats { grid-template-columns:repeat(3,1fr); }.overview-stats div { min-width:0; } }
</style>
