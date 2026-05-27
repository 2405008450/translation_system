import { expect, request as playwrightRequest, test, type APIRequestContext, type APIResponse } from '@playwright/test'

type UserAccount = {
  username: string
  token: string
  context: APIRequestContext
}

type Segment = {
  sentence_id: string
  source_text: string
  target_text: string
  source: string
}

type UploadedFile = {
  id: string
  filename: string
}

type Workspace = {
  user: UserAccount
  marker: string
  projectId: string
  llmFile: UploadedFile
  editFile: UploadedFile
  editUpdates: Array<{ sentence_id: string, target_text: string, source: string, track_revision: boolean }>
}

const API_BASE_URL = process.env.E2E_API_BASE_URL || `http://127.0.0.1:${process.env.E2E_API_PORT || 19115}`
const FAKE_LLM_BASE_URL = process.env.E2E_FAKE_LLM_BASE_URL || `http://127.0.0.1:${process.env.E2E_FAKE_LLM_PORT || 19114}`
const ADMIN_USERNAME = process.env.E2E_ADMIN_USERNAME || 'e2e_admin'
const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD || 'E2ePass123!'
const CONCURRENCY_PASSWORD = process.env.E2E_CONCURRENCY_PASSWORD || 'E2EConc123!'
const DEFAULT_USER_COUNT = 6
const MIN_USER_COUNT = 5
const MAX_USER_COUNT = 10

function parseGeneratedUserCount() {
  const value = Number(process.env.E2E_CONCURRENCY_USER_COUNT || DEFAULT_USER_COUNT)
  if (!Number.isFinite(value)) {
    return DEFAULT_USER_COUNT
  }
  return Math.min(MAX_USER_COUNT, Math.max(MIN_USER_COUNT, Math.floor(value)))
}

function parseConfiguredUsers() {
  return (process.env.E2E_CONCURRENCY_USERS || '')
    .split(/[,\s;]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

async function expectOk(response: APIResponse, action: string) {
  if (response.ok()) {
    return
  }
  throw new Error(`${action} failed: ${response.status()} ${await response.text()}`)
}

async function createApiContext(token?: string) {
  return playwrightRequest.newContext({
    baseURL: API_BASE_URL,
    extraHTTPHeaders: token ? { Authorization: `Bearer ${token}` } : undefined,
  })
}

async function createFakeLlmContext() {
  return playwrightRequest.newContext({ baseURL: FAKE_LLM_BASE_URL })
}

async function login(context: APIRequestContext, username: string, password: string) {
  const response = await context.post('/api/auth/login', {
    data: { username, password },
  })
  await expectOk(response, `login ${username}`)
  const payload = await response.json()
  return String(payload.access_token)
}

async function initOrLoginAdmin() {
  const context = await createApiContext()
  const initResponse = await context.get('/api/auth/init')
  await expectOk(initResponse, 'get init status')
  const initStatus = await initResponse.json()

  if (initStatus.requires_init) {
    const response = await context.post('/api/auth/init', {
      data: {
        username: ADMIN_USERNAME,
        nickname: 'E2E 管理员',
        password: ADMIN_PASSWORD,
      },
    })
    await expectOk(response, 'initialize admin')
    const payload = await response.json()
    await context.dispose()
    return String(payload.access_token)
  }

  const token = await login(context, ADMIN_USERNAME, ADMIN_PASSWORD)
  await context.dispose()
  return token
}

async function listUsers(adminContext: APIRequestContext) {
  const response = await adminContext.get('/api/auth/users')
  await expectOk(response, 'list users')
  return await response.json() as Array<{ id: string, username: string }>
}

async function setUserPassword(adminContext: APIRequestContext, userId: string) {
  const response = await adminContext.patch(`/api/auth/users/${userId}`, {
    data: {
      password: CONCURRENCY_PASSWORD,
      is_active: true,
    },
  })
  await expectOk(response, `set user password ${userId}`)
}

async function registerUser(adminContext: APIRequestContext, username: string) {
  const response = await adminContext.post('/api/auth/register', {
    data: {
      username,
      nickname: username,
      password: CONCURRENCY_PASSWORD,
      role: 'user',
    },
  })
  if (response.status() === 409) {
    return
  }
  await expectOk(response, `register ${username}`)
}

async function ensureTestUsers(adminContext: APIRequestContext) {
  const configuredUsers = parseConfiguredUsers()
  const usernames = configuredUsers.length > 0
    ? configuredUsers
    : Array.from({ length: parseGeneratedUserCount() }, (_, index) => `e2e_conc_${String(index + 1).padStart(2, '0')}`)

  if (configuredUsers.length > 0) {
    const existingUsers = await listUsers(adminContext)
    const userByName = new Map(existingUsers.map((user) => [user.username, user]))
    for (const username of usernames) {
      const user = userByName.get(username)
      if (!user) {
        throw new Error(`E2E_CONCURRENCY_USERS 包含不存在的账号: ${username}`)
      }
      await setUserPassword(adminContext, user.id)
    }
  } else {
    for (const username of usernames) {
      await registerUser(adminContext, username)
    }
    const existingUsers = await listUsers(adminContext)
    const userByName = new Map(existingUsers.map((user) => [user.username, user]))
    for (const username of usernames) {
      const user = userByName.get(username)
      if (!user) {
        throw new Error(`无法创建并发测试账号: ${username}`)
      }
      await setUserPassword(adminContext, user.id)
    }
  }

  const accounts: UserAccount[] = []
  for (const username of usernames) {
    const loginContext = await createApiContext()
    const token = await login(loginContext, username, CONCURRENCY_PASSWORD)
    await loginContext.dispose()
    accounts.push({
      username,
      token,
      context: await createApiContext(token),
    })
  }
  return accounts
}

async function pollImportTask(context: APIRequestContext, taskId: string) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const response = await context.get(`/api/import-tasks/${taskId}`)
    await expectOk(response, `poll import task ${taskId}`)
    const payload = await response.json()
    if (payload.status === 'completed') {
      return payload.result
    }
    if (payload.status === 'failed') {
      throw new Error(`导入任务失败: ${payload.error || payload.message || taskId}`)
    }
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  throw new Error(`导入任务超时: ${taskId}`)
}

async function createProject(context: APIRequestContext, name: string) {
  const response = await context.post('/api/projects', {
    data: {
      name,
      source_language: 'zh-CN',
      target_language: 'en-US',
      access_level: 'team',
    },
  })
  await expectOk(response, `create project ${name}`)
  const payload = await response.json()
  return String(payload.id)
}

async function uploadTextFile(context: APIRequestContext, projectId: string, filename: string, content: string) {
  const response = await context.post(`/api/projects/${projectId}/source-document`, {
    multipart: {
      file: {
        name: filename,
        mimeType: 'text/plain',
        buffer: Buffer.from(content, 'utf8'),
      },
      threshold: '0.6',
      source_language: 'zh-CN',
      target_language: 'en-US',
    },
  })
  await expectOk(response, `upload ${filename}`)
  const queued = await response.json()
  const result = await pollImportTask(context, queued.task_id)
  const uploaded = Array.isArray(result.files) ? result.files[0] : result
  if (!uploaded?.id) {
    throw new Error(`上传结果缺少文件 id: ${filename}`)
  }
  return { id: String(uploaded.id), filename: String(uploaded.filename || filename) }
}

async function getSegments(context: APIRequestContext, fileId: string) {
  const response = await context.get(`/api/file-records/${fileId}/segments`, {
    params: {
      limit: 20,
    },
  })
  await expectOk(response, `get segments ${fileId}`)
  const payload = await response.json()
  return payload.segments as Segment[]
}

function buildSourceText(marker: string, purpose: string) {
  return [
    `这是 ${marker} 的第一段${purpose}内容。`,
    `这是 ${marker} 的第二段${purpose}内容，用于验证并发隔离。`,
    `这是 ${marker} 的第三段${purpose}内容，确保有多个片段。`,
  ].join('\n\n')
}

async function createWorkspace(user: UserAccount, index: number) {
  const marker = `USER_${String(index + 1).padStart(2, '0')}_${Date.now()}`
  const projectId = await createProject(user.context, `并发回归 ${user.username} ${marker}`)
  const llmFile = await uploadTextFile(
    user.context,
    projectId,
    `llm-${marker}.txt`,
    buildSourceText(marker, 'LLM'),
  )
  const editFile = await uploadTextFile(
    user.context,
    projectId,
    `edit-${marker}.txt`,
    buildSourceText(marker, '手动编辑'),
  )
  const editSegments = await getSegments(user.context, editFile.id)
  const editUpdates = editSegments.map((segment, segmentIndex) => ({
    sentence_id: segment.sentence_id,
    target_text: `MANUAL_TRANSLATION ${marker} ${segmentIndex + 1}`,
    source: 'manual',
    track_revision: false,
  }))
  return {
    user,
    marker,
    projectId,
    llmFile,
    editFile,
    editUpdates,
  }
}

function parseSseEvents(text: string) {
  return text
    .split('\n\n')
    .map((chunk) => chunk.trim())
    .filter(Boolean)
    .map((chunk) => {
      const event = chunk.match(/^event:\s*(.+)$/m)?.[1]?.trim() || ''
      const dataText = chunk.match(/^data:\s*(.+)$/m)?.[1]?.trim() || '{}'
      return { event, data: JSON.parse(dataText) }
    })
}

async function runLlm(
  context: APIRequestContext,
  fileId: string,
  options: { operationToken?: string, scope?: string } = {},
) {
  const response = await context.post(`/api/file-records/${fileId}/llm-translate`, {
    headers: {
      Accept: 'text/event-stream',
      ...(options.operationToken ? { 'X-File-Operation-Token': options.operationToken } : {}),
    },
    data: {
      scope: options.scope || 'empty_target_only',
      provider: 'deepseek',
      translation_unit: 'paragraph',
    },
  })
  await expectOk(response, `run llm ${fileId}`)
  const events = parseSseEvents(await response.text())
  const complete = events.find((item) => item.event === 'complete')
  expect(events.some((item) => item.event === 'start')).toBeTruthy()
  expect(complete, `LLM complete event missing for ${fileId}`).toBeTruthy()
  expect(Number(complete?.data.error_count || 0)).toBe(0)
  return events
}

async function saveSegments(
  context: APIRequestContext,
  fileId: string,
  updates: Workspace['editUpdates'],
  operationToken?: string,
) {
  const response = await context.put(`/api/file-records/${fileId}/segments`, {
    headers: operationToken ? { 'X-File-Operation-Token': operationToken } : undefined,
    data: { updates },
  })
  await expectOk(response, `save segments ${fileId}`)
  const payload = await response.json()
  expect(Number(payload.updated_count)).toBe(updates.length)
}

async function resetFakeLlmStats() {
  const context = await createFakeLlmContext()
  const response = await context.post('/__reset')
  await expectOk(response, 'reset fake llm stats')
  await context.dispose()
}

async function getFakeLlmStats() {
  const context = await createFakeLlmContext()
  const response = await context.get('/__stats')
  await expectOk(response, 'get fake llm stats')
  const stats = await response.json()
  await context.dispose()
  return stats as { maxConcurrent: number, totalRequests: number }
}

test.describe.serial('多用户并发回归', () => {
  let adminContext: APIRequestContext
  let users: UserAccount[]

  test.beforeAll(async () => {
    const adminToken = await initOrLoginAdmin()
    adminContext = await createApiContext(adminToken)
    users = await ensureTestUsers(adminContext)
  })

  test.afterAll(async () => {
    await Promise.all([
      adminContext?.dispose(),
      ...(users || []).map((user) => user.context.dispose()),
    ])
  })

  test('多个用户可同时调用 LLM 并编辑不同文件，且数据不串号', async () => {
    await resetFakeLlmStats()

    const workspaces: Workspace[] = []
    for (const [index, user] of users.entries()) {
      workspaces.push(await createWorkspace(user, index))
    }

    const concurrentJobs: Array<Promise<unknown>> = []
    for (const workspace of workspaces) {
      concurrentJobs.push(runLlm(workspace.user.context, workspace.llmFile.id))
      concurrentJobs.push(saveSegments(workspace.user.context, workspace.editFile.id, workspace.editUpdates))
    }
    await Promise.all(concurrentJobs)

    const allMarkers = workspaces.map((workspace) => workspace.marker)
    for (const workspace of workspaces) {
      const llmSegments = await getSegments(workspace.user.context, workspace.llmFile.id)
      expect(llmSegments.length).toBeGreaterThan(0)
      for (const segment of llmSegments) {
        expect(segment.source).toBe('llm')
        expect(segment.target_text).toContain(workspace.marker)
        for (const otherMarker of allMarkers.filter((marker) => marker !== workspace.marker)) {
          expect(segment.target_text).not.toContain(otherMarker)
        }
      }

      const editSegments = await getSegments(workspace.user.context, workspace.editFile.id)
      for (const update of workspace.editUpdates) {
        const segment = editSegments.find((item) => item.sentence_id === update.sentence_id)
        expect(segment?.target_text).toBe(update.target_text)
        expect(segment?.source).toBe('manual')
        for (const otherMarker of allMarkers.filter((marker) => marker !== workspace.marker)) {
          expect(segment?.target_text || '').not.toContain(otherMarker)
        }
      }
    }

    const stats = await getFakeLlmStats()
    expect(stats.totalRequests).toBeGreaterThan(0)
    expect(stats.maxConcurrent).toBeGreaterThanOrEqual(2)
  })

  test('文件级预翻译锁会阻止无 token 写入，但不影响其他文件', async () => {
    await resetFakeLlmStats()

    const workspace = await createWorkspace(users[0], 99)
    const lockResponse = await users[0].context.post(`/api/file-records/${workspace.llmFile.id}/operation-lock`, {
      data: { operation: 'pre_translate' },
    })
    await expectOk(lockResponse, 'acquire operation lock')
    const lockPayload = await lockResponse.json()
    const operationToken = String(lockPayload.token)

    const lockedSegments = await getSegments(users[0].context, workspace.llmFile.id)
    const lockedUpdate = [{
      sentence_id: lockedSegments[0].sentence_id,
      target_text: '',
      source: 'manual',
      track_revision: false,
    }]

    const noTokenResponse = await users[0].context.put(`/api/file-records/${workspace.llmFile.id}/segments`, {
      data: { updates: lockedUpdate },
    })
    expect(noTokenResponse.status()).toBe(409)

    const wrongTokenResponse = await users[0].context.put(`/api/file-records/${workspace.llmFile.id}/segments`, {
      headers: { 'X-File-Operation-Token': 'wrong-token' },
      data: { updates: lockedUpdate },
    })
    expect(wrongTokenResponse.status()).toBe(409)

    await saveSegments(users[0].context, workspace.editFile.id, workspace.editUpdates)
    await saveSegments(users[0].context, workspace.llmFile.id, lockedUpdate, operationToken)
    await runLlm(users[0].context, workspace.llmFile.id, {
      operationToken,
      scope: 'empty_target_only',
    })

    const releaseResponse = await users[0].context.delete(`/api/file-records/${workspace.llmFile.id}/operation-lock`, {
      headers: { 'X-File-Operation-Token': operationToken },
    })
    await expectOk(releaseResponse, 'release operation lock')

    const llmSegments = await getSegments(users[0].context, workspace.llmFile.id)
    expect(llmSegments.some((segment) => segment.source === 'llm' && segment.target_text.includes(workspace.marker))).toBeTruthy()

    const stats = await getFakeLlmStats()
    expect(stats.totalRequests).toBeGreaterThan(0)
  })
})
