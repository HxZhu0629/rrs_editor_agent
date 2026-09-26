import axios from 'axios'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000/api'

export const api = axios.create({
  baseURL: API_BASE,
  timeout: 120000,
})

export function listManuscripts() {
  return api.get('/manuscripts/')
}

export function uploadManuscript(file) {
  const form = new FormData()
  form.append('file', file)
  return api.post('/manuscripts/upload/', form)
}

export function getManuscript(id) {
  return api.get(`/manuscripts/${id}/`)
}

export function runExtraction(manuscriptId) {
  return api.post(`/extraction/manuscripts/${manuscriptId}/run/`)
}

export function confirmManuscript(manuscriptId, payload) {
  return api.patch(`/manuscripts/${manuscriptId}/confirm/`, payload)
}

export function runRecommendation(manuscriptId, topK) {
  return api.post('/recommendations/run/', {
    manuscript_id: manuscriptId,
    top_k: topK,
  })
}

export function resolvePaperAccess(paperId) {
  return api.get(`/recommendations/papers/${paperId}/access/`)
}

export function bulkUpsertReviewers(items) {
  return api.post('/reviewers/bulk-upsert/', { items })
}

export function createAgentConversation(manuscriptId) {
  return api.post('/agent/conversations/', { manuscript_id: manuscriptId })
}

export function getAgentConversation(conversationId) {
  return api.get(`/agent/conversations/${conversationId}/`)
}

export function sendAgentMessage(conversationId, content) {
    return api.post(`/agent/conversations/${conversationId}/messages/`, { content }, { timeout: 180000 })
}

export function getAgentRun(runId) {
  return api.get(`/agent/runs/${runId}/`, { timeout: 15000 })
}

export function approveAgentRun(runId, approved, approvedCallIds = []) {
  return api.post(`/agent/runs/${runId}/approval/`, { approved, approved_call_ids: approvedCallIds })
}
