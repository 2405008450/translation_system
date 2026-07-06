<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Upload, FileText, Trash2, Play, CheckCircle, AlertTriangle, Loader2, Sparkles } from 'lucide-vue-next'

import type {
  ReferenceFile,
  ReferenceProfile,
  ReferenceAnalyzeResponse,
} from '../types/api'
import { http } from '../api/http'

const props = defineProps<{
  fileRecordId: string | null
}>()

const emit = defineEmits<{
  (e: 'aiTranslateComplete', result: { updated_count: number; error_count: number }): void
}>()

const { t } = useI18n()

// 状态
const loading = ref(false)
const analyzing = ref(false)
const error = ref<string | null>(null)

// AI翻译状态
const aiTranslating = ref(false)
const aiTranslateProgress = ref<{
  updated_count: number
  error_count: number
  total: number
  current_text?: string
} | null>(null)

// 进度状态
const analysisProgress = ref<{
  stage: string
  stage_label: string
  progress: number
  message: string
  detail?: string
} | null>(null)
let progressEventSource: EventSource | null = null

const referenceFiles = ref<ReferenceFile[]>([])
const profile = ref<ReferenceProfile | null>(null)

// 上传状态
const uploadingFiles = ref(false)
const bilingualPairId = ref<string>('')

// 计算属性
const hasProfile = computed(() => profile.value !== null)
const hasFiles = computed(() => referenceFiles.value.length > 0)
const canAnalyze = computed(() => hasFiles.value && !analyzing.value)
const canAITranslate = computed(() => hasProfile.value && !aiTranslating.value && !analyzing.value)

const analysisReport = computed(() => profile.value?.analysis_report)
const styleGuide = computed(() => profile.value?.style)

// 术语和翻译记忆列表
const terminologyList = ref<Array<{source: string, target: string, context?: string, category?: string}>>([])
const tmList = ref<Array<{source: string, target: string, similarity?: number}>>([])
const loadingTerms = ref(false)
const showAllTerms = ref(false)
const showAllTm = ref(false)

const industryLabel = computed(() => {
  const map: Record<string, string> = {
    legal: '法律',
    finance: '金融',
    medical: '医疗',
    tech: '科技',
    marketing: '营销',
    general: '通用',
  }
  return map[analysisReport.value?.industry || ''] || analysisReport.value?.industry || ''
})

const strategyLabel = computed(() => {
  const map: Record<string, string> = {
    literal: '直译为主',
    free: '意译为主',
    balanced: '灵活处理',
  }
  return map[analysisReport.value?.strategy || ''] || analysisReport.value?.strategy || ''
})

// 加载数据
async function loadData() {
  if (!props.fileRecordId) return
  
  loading.value = true
  error.value = null
  
  try {
    // 加载参考文件列表
    const filesRes = await http.get<ReferenceFile[]>(
      `/reference/file-records/${props.fileRecordId}/files`
    )
    referenceFiles.value = filesRes.data
    
    // 尝试加载分析结果
    try {
      const profileRes = await http.get<ReferenceProfile>(
        `/reference/file-records/${props.fileRecordId}/profile`
      )
      profile.value = profileRes.data
      
      // 如果有分析结果，加载术语和TM列表
      if (profileRes.data) {
        await loadTerminologyAndTM()
      }
    } catch {
      profile.value = null
      terminologyList.value = []
      tmList.value = []
    }
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

// 加载术语和翻译记忆列表
async function loadTerminologyAndTM() {
  if (!props.fileRecordId) return
  
  loadingTerms.value = true
  try {
    const [termsRes, tmRes] = await Promise.all([
      http.get<Array<{source: string, target: string, context?: string, category?: string}>>(
        `/reference/file-records/${props.fileRecordId}/terminology`
      ),
      http.get<Array<{source: string, target: string, similarity?: number}>>(
        `/reference/file-records/${props.fileRecordId}/tm`
      ),
    ])
    terminologyList.value = termsRes.data || []
    tmList.value = tmRes.data || []
  } catch (err) {
    console.error('加载术语/TM列表失败:', err)
    terminologyList.value = []
    tmList.value = []
  } finally {
    loadingTerms.value = false
  }
}

// 上传文件
async function handleFileUpload(event: Event) {
  const input = event.target as HTMLInputElement
  const files = input.files
  if (!files?.length || !props.fileRecordId) return
  
  uploadingFiles.value = true
  error.value = null
  
  try {
    for (const file of files) {
      const formData = new FormData()
      formData.append('file', file)
      
      await http.post(`/reference/file-records/${props.fileRecordId}/upload`, formData)
    }
    
    await loadData()
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : '上传失败'
  } finally {
    uploadingFiles.value = false
    input.value = ''
  }
}

// 更新文件角色
async function updateFileRole(fileId: string, role: 'source' | 'target' | 'none') {
  try {
    // 生成或获取配对ID
    let pairId = bilingualPairId.value
    if (role !== 'none' && !pairId) {
      pairId = crypto.randomUUID().slice(0, 8)
      bilingualPairId.value = pairId
    }
    
    await http.patch(`/reference/files/${fileId}/role`, {
      role,
      bilingual_pair_id: role !== 'none' ? pairId : null,
    })
    
    await loadData()
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : '更新失败'
  }
}

// 获取文件当前角色
function getFileRole(file: ReferenceFile): 'source' | 'target' | 'none' {
  if (file.is_bilingual_source) return 'source'
  if (file.is_bilingual_target) return 'target'
  return 'none'
}

// 删除文件
async function deleteFile(fileId: string) {
  try {
    await http.delete(`/reference/files/${fileId}`)
    await loadData()
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : '删除失败'
  }
}

// 分析参考文件
async function analyzeFiles() {
  if (!props.fileRecordId || !canAnalyze.value) return
  
  analyzing.value = true
  error.value = null
  // 重置展示状态
  showAllTerms.value = false
  showAllTm.value = false
  
  // 立即显示初始进度（0%），不等待轮询
  analysisProgress.value = {
    stage: 'init',
    stage_label: '初始化',
    progress: 0,
    message: '准备开始分析...',
    detail: undefined,
  }
  
  // 启动进度轮询
  const progressInterval = startProgressPolling()
  
  try {
    const formData = new FormData()
    formData.append('enable_deep_analysis', 'true')
    
    const res = await http.post<ReferenceAnalyzeResponse>(
      `/reference/file-records/${props.fileRecordId}/analyze`,
      formData
    )
    
    // 重新加载 profile
    await loadData()
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : '分析失败'
  } finally {
    analyzing.value = false
    stopProgressPolling(progressInterval)
  }
}

// 启动进度轮询
function startProgressPolling(): number {
  if (!props.fileRecordId) return 0
  
  console.log('[ReferencePanel] 启动进度轮询')
  
  const pollProgress = async () => {
    if (!analyzing.value || !props.fileRecordId) return
    
    try {
      const response = await http.get<{
        stage: string
        stage_label: string
        progress: number
        message: string
        detail?: string
      }>(`/reference/file-records/${props.fileRecordId}/analyze/progress`)
      
      console.log('[ReferencePanel] 进度响应:', response.data)
      
      if (response.data && response.data.stage !== 'idle') {
        analysisProgress.value = response.data
      }
    } catch (err) {
      console.error('[ReferencePanel] 进度轮询错误:', err)
    }
  }
  
  // 使用轮询方式获取进度（间隔300ms）
  const intervalId = window.setInterval(pollProgress, 300)
  
  return intervalId
}

// 停止进度轮询
function stopProgressPolling(intervalId: number) {
  if (intervalId) {
    clearInterval(intervalId)
  }
  // 短暂延迟后清除进度显示
  setTimeout(() => {
    analysisProgress.value = null
  }, 1000)
}

// 匹配句段
async function matchSegments() {
  // 已废弃：参考资料分析后会自动同步到项目级 TM/术语库，匹配通过原生通道命中。
}

// 应用精确匹配
async function applyExactMatches() {
  // 已废弃：见 matchSegments 注释。
}

// 删除分析结果
async function deleteProfile() {
  if (!props.fileRecordId) return
  
  try {
    await http.delete(`/reference/file-records/${props.fileRecordId}/profile`)
    profile.value = null
    referenceFiles.value = []  // 同时清空文件列表
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : '删除失败'
  }
}

// AI翻译 - 基于参考文件
async function startAITranslate() {
  if (!props.fileRecordId || !canAITranslate.value) return
  
  aiTranslating.value = true
  error.value = null
  aiTranslateProgress.value = {
    updated_count: 0,
    error_count: 0,
    total: 0,
  }
  
  try {
    const baseUrl = http.defaults.baseURL || ''
    const url = `${baseUrl}/reference/file-records/${props.fileRecordId}/ai-translate`
    
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${localStorage.getItem('token') || ''}`,
      },
      body: JSON.stringify({
        scope: 'empty_target_only',
        provider: 'auto',
        translation_unit: 'paragraph',
      }),
    })
    
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ detail: '请求失败' }))
      throw new Error(errorData.detail || '翻译请求失败')
    }
    
    const reader = response.body?.getReader()
    if (!reader) throw new Error('无法读取响应流')
    
    const decoder = new TextDecoder()
    let buffer = ''
    
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      
      for (const line of lines) {
        if (line.startsWith('event:')) {
          const eventType = line.slice(6).trim()
          continue
        }
        if (line.startsWith('data:')) {
          try {
            const data = JSON.parse(line.slice(5).trim())
            
            if (data.total !== undefined) {
              aiTranslateProgress.value = {
                updated_count: data.updated_count || 0,
                error_count: data.error_count || 0,
                total: data.total,
                current_text: data.translated_text?.slice(0, 50),
              }
            }
            
            // 处理完成事件
            if (data.updated_count !== undefined && data.error_count !== undefined && !data.sentence_id) {
              emit('aiTranslateComplete', {
                updated_count: data.updated_count,
                error_count: data.error_count,
              })
            }
          } catch {
            // 忽略解析错误
          }
        }
      }
    }
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : 'AI翻译失败'
  } finally {
    aiTranslating.value = false
    // 延迟清除进度显示
    setTimeout(() => {
      aiTranslateProgress.value = null
    }, 2000)
  }
}

// 监听 fileRecordId 变化
watch(() => props.fileRecordId, () => {
  if (props.fileRecordId) {
    loadData()
  } else {
    referenceFiles.value = []
    profile.value = null
    terminologyList.value = []
    tmList.value = []
    showAllTerms.value = false
    showAllTm.value = false
  }
}, { immediate: true })

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
</script>

<template>
  <div class="reference-panel">
    <div class="reference-panel__header">
      <h3 class="reference-panel__title">参考文件分析</h3>
    </div>

    <div class="reference-panel__body">
      <!-- 错误提示 -->
      <div v-if="error" class="reference-alert reference-alert--error">
        <AlertTriangle :size="16" />
        <span>{{ error }}</span>
      </div>

      <!-- 加载状态 -->
      <div v-if="loading" class="reference-loading">
        <Loader2 :size="20" class="spin" />
        <span>加载中...</span>
      </div>

      <template v-else>
        <!-- 上传区域 -->
        <section class="reference-section">
          <h4 class="reference-section__title">上传参考文件</h4>
          
          <div class="reference-upload">
            <label class="reference-upload__dropzone">
              <input
                type="file"
                multiple
                accept=".txt,.doc,.docx,.xlsx,.pdf"
                :disabled="uploadingFiles"
                @change="handleFileUpload"
              />
              <Upload :size="24" />
              <span>点击或拖放文件</span>
              <span class="reference-upload__hint">支持 TXT、DOC、DOCX、XLSX、PDF</span>
            </label>
          </div>

          <!-- 已上传文件列表 -->
          <div v-if="hasFiles" class="reference-file-list">
            <div
              v-for="file in referenceFiles"
              :key="file.id"
              class="reference-file-item"
            >
              <FileText :size="16" />
              <span class="reference-file-item__name">{{ file.filename }}</span>
              <span class="reference-file-item__size">{{ formatFileSize(file.file_size) }}</span>
              <select
                class="reference-file-item__role"
                :value="getFileRole(file)"
                @change="(e) => updateFileRole(file.id, (e.target as HTMLSelectElement).value as 'source' | 'target' | 'none')"
              >
                <option value="none">普通</option>
                <option value="source">原文</option>
                <option value="target">译文</option>
              </select>
              <button
                class="reference-file-item__delete"
                type="button"
                @click="deleteFile(file.id)"
              >
                <Trash2 :size="14" />
              </button>
            </div>
          </div>

          <!-- 分析按钮 -->
          <div class="reference-actions">
            <button
              class="button button--primary"
              type="button"
              :disabled="!canAnalyze"
              @click="analyzeFiles"
            >
              <Loader2 v-if="analyzing" :size="16" class="spin" />
              <Play v-else :size="16" />
              <span>{{ analyzing ? '分析中...' : '开始分析' }}</span>
            </button>
          </div>
          
          <!-- 进度条 -->
          <div v-if="analyzing && analysisProgress" class="reference-progress">
            <div class="reference-progress__header">
              <span class="reference-progress__stage">{{ analysisProgress.stage_label }}</span>
              <span class="reference-progress__percent">{{ Math.round(analysisProgress.progress) }}%</span>
            </div>
            <div class="reference-progress__bar">
              <div 
                class="reference-progress__fill" 
                :style="{ width: `${analysisProgress.progress}%` }"
              ></div>
            </div>
            <div class="reference-progress__info">
              <span class="reference-progress__message">{{ analysisProgress.message }}</span>
              <span v-if="analysisProgress.detail" class="reference-progress__detail">{{ analysisProgress.detail }}</span>
            </div>
          </div>
        </section>

        <!-- 分析结果 -->
        <template v-if="hasProfile">
          <section class="reference-section">
            <div class="reference-section__header">
              <h4 class="reference-section__title">分析结果</h4>
              <button
                class="reference-section__action"
                type="button"
                title="删除分析结果"
                @click="deleteProfile"
              >
                <Trash2 :size="14" />
              </button>
            </div>

            <!-- 统计概览 -->
            <div class="reference-stats">
              <div class="reference-stat">
                <span class="reference-stat__value">{{ profile?.terminology_count || 0 }}</span>
                <span class="reference-stat__label">提取术语</span>
              </div>
              <div class="reference-stat">
                <span class="reference-stat__value">{{ profile?.tm_count || 0 }}</span>
                <span class="reference-stat__label">翻译记忆</span>
              </div>
              <div class="reference-stat">
                <span class="reference-stat__value">{{ Math.round((profile?.overall_confidence || 0) * 100) }}%</span>
                <span class="reference-stat__label">置信度</span>
              </div>
            </div>

            <!-- 深度分析报告 -->
            <template v-if="analysisReport">
              <div class="reference-analysis">
                <div class="reference-analysis__row">
                  <span class="reference-analysis__label">行业领域</span>
                  <span class="reference-analysis__value">{{ industryLabel || '通用' }}</span>
                </div>
                <div class="reference-analysis__row">
                  <span class="reference-analysis__label">翻译策略</span>
                  <span class="reference-analysis__value">{{ strategyLabel || '灵活处理' }}</span>
                </div>
                <div v-if="analysisReport.client_profile" class="reference-analysis__row reference-analysis__row--full">
                  <span class="reference-analysis__label">客户风格</span>
                  <p class="reference-analysis__text">{{ analysisReport.client_profile }}</p>
                </div>
                <div v-if="analysisReport.strategy_reasoning" class="reference-analysis__row reference-analysis__row--full">
                  <span class="reference-analysis__label">策略建议</span>
                  <p class="reference-analysis__text">{{ analysisReport.strategy_reasoning }}</p>
                </div>
              </div>

              <!-- 品牌术语 -->
              <div v-if="analysisReport.brand_terms?.length" class="reference-terms">
                <h5 class="reference-terms__title">品牌/专有名词</h5>
                <div class="reference-terms__list">
                  <div
                    v-for="(term, idx) in analysisReport.brand_terms.slice(0, 10)"
                    :key="idx"
                    class="reference-term-item"
                  >
                    <span class="reference-term-item__source">{{ term.source }}</span>
                    <span class="reference-term-item__arrow">→</span>
                    <span class="reference-term-item__target">{{ term.target }}</span>
                  </div>
                </div>
              </div>

              <!-- 风险提示 -->
              <div v-if="analysisReport.risk_points?.length" class="reference-risks">
                <h5 class="reference-risks__title">易错点提示</h5>
                <div
                  v-for="(risk, idx) in analysisReport.risk_points.slice(0, 5)"
                  :key="idx"
                  class="reference-risk-item"
                >
                  <span class="reference-risk-item__category">{{ risk.category }}</span>
                  <span class="reference-risk-item__desc">{{ risk.description }}</span>
                </div>
              </div>
            </template>

            <!-- 提取的术语列表 -->
            <div v-if="terminologyList.length" class="reference-terms">
              <div class="reference-terms__header">
                <h5 class="reference-terms__title">提取的术语 ({{ terminologyList.length }})</h5>
                <button
                  v-if="terminologyList.length > 5"
                  class="reference-terms__toggle"
                  type="button"
                  @click="showAllTerms = !showAllTerms"
                >
                  {{ showAllTerms ? '收起' : `展开全部` }}
                </button>
              </div>
              <div class="reference-terms__list">
                <div
                  v-for="(term, idx) in (showAllTerms ? terminologyList : terminologyList.slice(0, 5))"
                  :key="idx"
                  class="reference-term-item"
                >
                  <span class="reference-term-item__source">{{ term.source }}</span>
                  <span class="reference-term-item__arrow">→</span>
                  <span class="reference-term-item__target">{{ term.target }}</span>
                  <span v-if="term.category" class="reference-term-item__category">{{ term.category }}</span>
                </div>
              </div>
            </div>

            <!-- 提取的翻译记忆列表 -->
            <div v-if="tmList.length" class="reference-tm">
              <div class="reference-tm__header">
                <h5 class="reference-tm__title">翻译记忆 ({{ tmList.length }})</h5>
                <button
                  v-if="tmList.length > 3"
                  class="reference-tm__toggle"
                  type="button"
                  @click="showAllTm = !showAllTm"
                >
                  {{ showAllTm ? '收起' : `展开全部` }}
                </button>
              </div>
              <div class="reference-tm__list">
                <div
                  v-for="(pair, idx) in (showAllTm ? tmList : tmList.slice(0, 3))"
                  :key="idx"
                  class="reference-tm-item"
                >
                  <div class="reference-tm-item__source">{{ pair.source }}</div>
                  <div class="reference-tm-item__target">{{ pair.target }}</div>
                </div>
              </div>
            </div>

            <!-- 风格指南 -->
            <div v-if="styleGuide" class="reference-style">
              <h5 class="reference-style__title">风格指南</h5>
              <div v-if="styleGuide.tone" class="reference-style__row">
                <span class="reference-style__label">语气</span>
                <span class="reference-style__value">{{ styleGuide.tone }}</span>
              </div>
              <div v-if="styleGuide.person" class="reference-style__row">
                <span class="reference-style__label">人称</span>
                <span class="reference-style__value">{{ styleGuide.person }}</span>
              </div>
              <div v-if="styleGuide.preferences?.length" class="reference-style__row">
                <span class="reference-style__label">偏好</span>
                <span class="reference-style__value">{{ styleGuide.preferences.join('、') }}</span>
              </div>
              <div v-if="styleGuide.avoid?.length" class="reference-style__row">
                <span class="reference-style__label">避免</span>
                <span class="reference-style__value reference-style__value--warn">{{ styleGuide.avoid.join('、') }}</span>
              </div>
            </div>
          </section>

          <!-- AI翻译 -->
          <section class="reference-section">
            <h4 class="reference-section__title">
              <Sparkles :size="16" class="reference-section__icon" />
              AI 智能翻译
            </h4>
            <p class="reference-section__desc">
              基于上方的参考分析结果（术语、翻译记忆、风格指南）进行智能翻译
            </p>
            
            <div class="reference-actions">
              <button
                class="button button--primary button--ai"
                type="button"
                :disabled="!canAITranslate"
                @click="startAITranslate"
              >
                <Loader2 v-if="aiTranslating" :size="16" class="spin" />
                <Sparkles v-else :size="16" />
                <span>{{ aiTranslating ? '翻译中...' : '开始AI翻译' }}</span>
              </button>
            </div>
            
            <!-- AI翻译进度 -->
            <div v-if="aiTranslating && aiTranslateProgress" class="reference-progress">
              <div class="reference-progress__header">
                <span class="reference-progress__stage">AI翻译进行中</span>
                <span class="reference-progress__percent">
                  {{ aiTranslateProgress.updated_count }} / {{ aiTranslateProgress.total }}
                </span>
              </div>
              <div class="reference-progress__bar">
                <div 
                  class="reference-progress__fill reference-progress__fill--ai" 
                  :style="{ width: `${aiTranslateProgress.total > 0 ? (aiTranslateProgress.updated_count / aiTranslateProgress.total * 100) : 0}%` }"
                ></div>
              </div>
              <div class="reference-progress__info">
                <span class="reference-progress__message">
                  已翻译 {{ aiTranslateProgress.updated_count }} 条
                  <template v-if="aiTranslateProgress.error_count > 0">
                    ，{{ aiTranslateProgress.error_count }} 条失败
                  </template>
                </span>
                <span v-if="aiTranslateProgress.current_text" class="reference-progress__detail">
                  {{ aiTranslateProgress.current_text }}...
                </span>
              </div>
            </div>
            
            <!-- 翻译完成提示 -->
            <div v-if="!aiTranslating && aiTranslateProgress && aiTranslateProgress.updated_count > 0" class="reference-alert reference-alert--success">
              <CheckCircle :size="16" />
              <span>翻译完成：成功 {{ aiTranslateProgress.updated_count }} 条<template v-if="aiTranslateProgress.error_count > 0">，失败 {{ aiTranslateProgress.error_count }} 条</template></span>
            </div>
          </section>
        </template>
      </template>
    </div>
  </div>
</template>


<style scoped>
.reference-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: linear-gradient(180deg, #ffffff 0%, #f7fbfb 100%);
  border: 0;
}

.reference-panel__header {
  padding: 12px 16px;
  border-bottom: 1px solid var(--line-soft);
}

.reference-panel__title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.reference-panel__body {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  scrollbar-gutter: stable;
  scrollbar-width: thin;
  scrollbar-color: #9fb8bd transparent;
}

.reference-panel__body::-webkit-scrollbar {
  width: 8px;
}

.reference-panel__body::-webkit-scrollbar-track {
  background: transparent;
}

.reference-panel__body::-webkit-scrollbar-thumb {
  border: 2px solid transparent;
  border-radius: 999px;
  background-color: #9fb8bd;
  background-clip: content-box;
}

.reference-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 24px;
  color: var(--text-tertiary);
}

.reference-alert {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 8px;
  font-size: 13px;
}

.reference-alert--error {
  background: rgba(194, 59, 63, 0.1);
  color: #a43a3d;
  border: 1px solid rgba(194, 59, 63, 0.2);
}

.reference-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.reference-section__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.reference-section__title {
  margin: 0;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary);
}

.reference-section__action {
  padding: 4px;
  border: none;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  border-radius: 4px;
  transition: all 0.15s;
}

.reference-section__action:hover {
  background: rgba(194, 59, 63, 0.1);
  color: #a43a3d;
}

/* 上传区域 */
.reference-upload {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.reference-upload__dropzone {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 24px;
  border: 2px dashed var(--line-soft);
  border-radius: 12px;
  background: var(--surface-muted);
  cursor: pointer;
  transition: all 0.15s;
  color: var(--text-secondary);
}

.reference-upload__dropzone:hover {
  border-color: var(--accent-primary);
  background: rgba(138, 92, 246, 0.05);
}

.reference-upload__dropzone input {
  display: none;
}

.reference-upload__hint {
  font-size: 11px;
  color: var(--text-tertiary);
}

/* 文件列表 */
.reference-file-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.reference-file-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  background: var(--surface-muted);
  border-radius: 8px;
  font-size: 12px;
}

.reference-file-item__name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-primary);
}

.reference-file-item__size {
  color: var(--text-tertiary);
}

.reference-file-item__role {
  padding: 2px 6px;
  border: 1px solid var(--line-soft);
  border-radius: 4px;
  background: var(--surface-panel);
  color: var(--text-secondary);
  font-size: 11px;
  cursor: pointer;
  outline: none;
}

.reference-file-item__role:focus {
  border-color: var(--accent-primary);
}

.reference-file-item__delete {
  padding: 4px;
  border: none;
  background: transparent;
  color: var(--text-tertiary);
  cursor: pointer;
  border-radius: 4px;
  transition: all 0.15s;
}

.reference-file-item__delete:hover {
  background: rgba(194, 59, 63, 0.1);
  color: #a43a3d;
}

/* 操作按钮 */
.reference-actions {
  display: flex;
  gap: 8px;
}

.reference-actions .button {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  font-size: 13px;
}

.button--success {
  background: linear-gradient(180deg, #0d7a68, #0b6b5b);
  color: white;
  border-color: #0b6b5b;
}

.button--success:hover:not(:disabled) {
  background: linear-gradient(180deg, #0b6b5b, #095a4d);
}

/* 统计概览 */
.reference-stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}

.reference-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 12px;
  background: linear-gradient(180deg, rgba(138, 92, 246, 0.06), rgba(138, 92, 246, 0.02));
  border: 1px solid rgba(138, 92, 246, 0.12);
  border-radius: 10px;
}

.reference-stat__value {
  font-size: 20px;
  font-weight: 700;
  color: #7c3aed;
}

.reference-stat__label {
  font-size: 11px;
  color: var(--text-tertiary);
}

/* 分析报告 */
.reference-analysis {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  background: var(--surface-muted);
  border-radius: 10px;
}

.reference-analysis__row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.reference-analysis__row--full {
  flex-direction: column;
  align-items: flex-start;
}

.reference-analysis__label {
  font-size: 12px;
  color: var(--text-tertiary);
  min-width: 60px;
}

.reference-analysis__value {
  font-size: 13px;
  color: var(--text-primary);
  font-weight: 500;
}

.reference-analysis__text {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.6;
}

/* 术语 */
.reference-terms,
.reference-risks,
.reference-style,
.reference-tm {
  padding: 12px;
  background: var(--surface-muted);
  border-radius: 10px;
}

.reference-terms__header,
.reference-tm__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.reference-terms__title,
.reference-risks__title,
.reference-style__title,
.reference-tm__title {
  margin: 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
}

.reference-terms__toggle,
.reference-tm__toggle {
  padding: 2px 8px;
  border: 1px solid var(--line-soft);
  border-radius: 4px;
  background: transparent;
  color: var(--text-tertiary);
  font-size: 11px;
  cursor: pointer;
  transition: all 0.15s;
}

.reference-terms__toggle:hover,
.reference-tm__toggle:hover {
  background: var(--surface-panel);
  color: var(--text-secondary);
}

.reference-terms__list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.reference-term-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.reference-term-item__source {
  color: var(--text-primary);
  font-weight: 500;
}

.reference-term-item__arrow {
  color: var(--text-tertiary);
}

.reference-term-item__target {
  color: #7c3aed;
}

.reference-term-item__category {
  padding: 1px 4px;
  background: rgba(138, 92, 246, 0.1);
  color: #7c3aed;
  border-radius: 3px;
  font-size: 10px;
  margin-left: auto;
}

/* 翻译记忆列表 */
.reference-tm__list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.reference-tm-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 8px 10px;
  background: var(--surface-panel);
  border-radius: 6px;
  border-left: 3px solid #7c3aed;
}

.reference-tm-item__source {
  font-size: 12px;
  color: var(--text-primary);
  line-height: 1.5;
}

.reference-tm-item__target {
  font-size: 12px;
  color: #7c3aed;
  line-height: 1.5;
}

/* 风险提示 */
.reference-risk-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px 0;
  border-bottom: 1px solid var(--line-soft);
}

.reference-risk-item:last-child {
  border-bottom: none;
  padding-bottom: 0;
}

.reference-risk-item__category {
  padding: 2px 6px;
  background: rgba(234, 179, 8, 0.15);
  color: #a16207;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 500;
  white-space: nowrap;
}

.reference-risk-item__desc {
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.5;
}

/* 风格指南 */
.reference-style__row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 6px 0;
}

.reference-style__label {
  font-size: 11px;
  color: var(--text-tertiary);
  min-width: 40px;
}

.reference-style__value {
  font-size: 12px;
  color: var(--text-primary);
}

.reference-style__value--warn {
  color: #a16207;
}

/* 匹配统计 */
.reference-match-stats {
  display: flex;
  gap: 12px;
}

.reference-match-stat {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  border-radius: 8px;
  font-size: 12px;
}

.reference-match-stat--exact {
  background: rgba(13, 122, 104, 0.1);
  color: #0b6b5b;
}

.reference-match-stat--fuzzy {
  background: rgba(138, 92, 246, 0.1);
  color: #7c3aed;
}

.reference-match-stat--term {
  background: rgba(234, 179, 8, 0.1);
  color: #a16207;
}

.reference-match-stat__value {
  font-weight: 700;
  font-size: 14px;
}

.reference-match-stat__label {
  font-size: 11px;
  opacity: 0.8;
}

/* 动画 */
.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* 进度条 */
.reference-progress {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 12px;
  background: linear-gradient(180deg, rgba(138, 92, 246, 0.08), rgba(138, 92, 246, 0.03));
  border: 1px solid rgba(138, 92, 246, 0.15);
  border-radius: 10px;
}

.reference-progress__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.reference-progress__stage {
  font-size: 13px;
  font-weight: 600;
  color: #7c3aed;
}

.reference-progress__percent {
  font-size: 12px;
  font-weight: 600;
  color: #7c3aed;
}

.reference-progress__bar {
  height: 6px;
  background: rgba(138, 92, 246, 0.15);
  border-radius: 3px;
  overflow: hidden;
}

.reference-progress__fill {
  height: 100%;
  background: linear-gradient(90deg, #8b5cf6, #7c3aed);
  border-radius: 3px;
  transition: width 0.2s ease-out;
}

.reference-progress__info {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.reference-progress__message {
  font-size: 12px;
  color: var(--text-secondary);
}

.reference-progress__detail {
  font-size: 11px;
  color: var(--text-tertiary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 匹配详情列表 */
.reference-match-detail {
  margin-top: 12px;
  padding: 12px;
  background: var(--surface-muted);
  border-radius: 10px;
}

.reference-match-detail__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.reference-match-detail__title {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  font-size: 12px;
  font-weight: 600;
}

.reference-match-detail__title--exact {
  color: #0b6b5b;
}

.reference-match-detail__title--fuzzy {
  color: #7c3aed;
}

.reference-match-detail__title--term {
  color: #a16207;
}

.reference-match-detail__toggle {
  padding: 2px 8px;
  border: 1px solid var(--line-soft);
  border-radius: 4px;
  background: transparent;
  color: var(--text-tertiary);
  font-size: 11px;
  cursor: pointer;
  transition: all 0.15s;
}

.reference-match-detail__toggle:hover {
  background: var(--surface-panel);
  color: var(--text-secondary);
}

.reference-match-detail__list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.reference-match-item {
  padding: 10px;
  background: var(--surface-panel);
  border-radius: 8px;
  border-left: 3px solid transparent;
}

.reference-match-item--exact {
  border-left-color: #0d7a68;
}

.reference-match-item--fuzzy {
  border-left-color: #7c3aed;
}

.reference-match-item--term {
  border-left-color: #eab308;
}

.reference-match-item__source {
  font-size: 12px;
  color: var(--text-primary);
  line-height: 1.5;
  margin-bottom: 4px;
}

.reference-match-item__matched {
  font-size: 11px;
  color: var(--text-tertiary);
  line-height: 1.4;
  margin-bottom: 4px;
  font-style: italic;
}

.reference-match-item__target {
  font-size: 12px;
  color: #7c3aed;
  line-height: 1.5;
  margin-bottom: 6px;
}

.reference-match-item__meta {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 10px;
  color: var(--text-tertiary);
}

.reference-match-item__similarity {
  padding: 2px 6px;
  background: rgba(138, 92, 246, 0.1);
  color: #7c3aed;
  border-radius: 4px;
  font-weight: 600;
}

.reference-match-item--exact .reference-match-item__similarity {
  background: rgba(13, 122, 104, 0.1);
  color: #0b6b5b;
}

.reference-match-item__source-file {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reference-match-item__terms {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 6px;
}

.reference-match-item__term-badge {
  padding: 3px 8px;
  background: rgba(234, 179, 8, 0.15);
  color: #a16207;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
}

/* AI翻译相关样式 */
.reference-section__icon {
  color: #8b5cf6;
  vertical-align: middle;
  margin-right: 4px;
}

.reference-section__title {
  display: flex;
  align-items: center;
  gap: 4px;
}

.reference-section__desc {
  margin: 0;
  font-size: 12px;
  color: var(--text-tertiary);
  line-height: 1.5;
}

.button--ai {
  background: linear-gradient(135deg, #8b5cf6 0%, #6366f1 100%);
  border: none;
  color: white;
  box-shadow: 0 2px 8px rgba(139, 92, 246, 0.3);
}

.button--ai:hover:not(:disabled) {
  background: linear-gradient(135deg, #7c3aed 0%, #4f46e5 100%);
  box-shadow: 0 4px 12px rgba(139, 92, 246, 0.4);
  transform: translateY(-1px);
}

.button--ai:disabled {
  background: linear-gradient(135deg, #9ca3af 0%, #6b7280 100%);
  box-shadow: none;
  cursor: not-allowed;
  opacity: 0.7;
}

.reference-progress__fill--ai {
  background: linear-gradient(90deg, #8b5cf6 0%, #6366f1 100%);
}

.reference-alert--success {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 8px;
  font-size: 13px;
  background: rgba(16, 185, 129, 0.1);
  color: #059669;
  border: 1px solid rgba(16, 185, 129, 0.2);
}

.reference-alert--success svg {
  flex-shrink: 0;
}
</style>
