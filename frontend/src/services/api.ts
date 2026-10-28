import axios from 'axios'
import type {
  TripFormData,
  TripJobState,
  TripJobSubmission,
  TripPlanResponse,
  AccountSession,
  PoiCandidate,
  SavedTrip,
  ShareLink,
  SharedTrip,
  TripEditOperation,
  CircuitStatus,
  JobTrace,
  OperationsOverview,
  PrivacyInformation,
  PublicSecurityConfig,
  TripPlan,
  TripSummary
} from '@/types'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || ''

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 480000, // 8分钟超时
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json'
  }
})

const GUEST_ID_STORAGE_KEY = 'tripGuestId'

/**
 * 把 Axios、FastAPI 字段校验和普通 Error 统一为可展示文本。
 *
 * @param error 捕获到的未知错误。
 * @param fallback 无法识别错误结构时使用的默认文案。
 * @returns 不会退化为 `[object Object]` 的用户可读错误信息。
 */
export function getApiErrorMessage(error: unknown, fallback: string): string {
  const candidate = error as {
    message?: unknown
    response?: { data?: { detail?: unknown } }
  }
  const detail = candidate?.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (typeof item === 'string') return item
        if (item && typeof item === 'object' && 'msg' in item) {
          const message = (item as { msg?: unknown }).msg
          return typeof message === 'string' ? message : ''
        }
        return ''
      })
      .filter(Boolean)
    if (messages.length > 0) return messages.join('；')
  }
  if (detail && typeof detail === 'object' && 'msg' in detail) {
    const message = (detail as { msg?: unknown }).msg
    if (typeof message === 'string' && message.trim()) return message
  }
  if (typeof candidate?.message === 'string' && candidate.message.trim()) {
    return candidate.message
  }
  return fallback
}

/**
 * 返回当前浏览器稳定的游客身份，不把账号凭据放入 localStorage。
 *
 * @returns 可通过 X-Guest-ID 发送的随机标识。
 */
export function getGuestId(): string {
  const existing = localStorage.getItem(GUEST_ID_STORAGE_KEY)
  if (existing) return existing
  const generated = typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `guest-${Date.now()}-${Math.random().toString(16).slice(2)}`
  localStorage.setItem(GUEST_ID_STORAGE_KEY, generated)
  return generated
}

// 请求拦截器
apiClient.interceptors.request.use(
  (config) => {
    config.headers.set('X-Guest-ID', getGuestId())
    return config
  },
  (error) => {
    console.error('请求错误:', error)
    return Promise.reject(error)
  }
)

// 响应拦截器
apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    console.error('响应错误:', error.response?.status, error.message)
    return Promise.reject(error)
  }
)

/**
 * 生成旅行计划
 */
export async function generateTripPlan(formData: TripFormData): Promise<TripPlanResponse> {
  try {
    const response = await apiClient.post<TripPlanResponse>('/api/trip/plan', formData)
    return response.data
  } catch (error: any) {
    console.error('生成旅行计划失败:', error)
    throw new Error(error.response?.data?.detail || error.message || '生成旅行计划失败')
  }
}

/**
 * 提交可追踪的异步旅行计划任务。
 *
 * @param formData 已完成前端校验的旅行规划表单。
 * @param idempotencyKey 本次用户操作的唯一键，用于阻止重复付费生成。
 * @param captchaToken 启用 Turnstile 时签发的单次 Token。
 * @returns 新建任务或相同幂等请求对应的已有任务。
 */
export async function createTripJob(
  formData: TripFormData,
  idempotencyKey: string,
  captchaToken?: string
): Promise<TripJobSubmission> {
  try {
    const response = await apiClient.post<TripJobSubmission>(
      '/api/trip/jobs',
      formData,
      {
        headers: {
          'Idempotency-Key': idempotencyKey,
          ...(captchaToken ? { 'X-Captcha-Token': captchaToken } : {})
        }
      }
    )
    return response.data
  } catch (error: any) {
    console.error('提交旅行计划任务失败:', error)
    throw new Error(error.response?.data?.detail || error.message || '提交旅行计划任务失败')
  }
}

/**
 * 查询异步旅行计划任务的最新公开状态。
 *
 * @param jobId 提交接口返回的公开任务ID。
 * @returns 当前阶段、进度、错误或最终结果。
 */
export async function getTripJob(jobId: string): Promise<TripJobState> {
  try {
    const response = await apiClient.get<TripJobState>(`/api/trip/jobs/${jobId}`)
    return response.data
  } catch (error: any) {
    console.error('查询旅行计划任务失败:', error)
    throw new Error(error.response?.data?.detail || error.message || '查询旅行计划任务失败')
  }
}

/**
 * 取消排队任务，或请求运行中的任务在阶段边界停止。
 *
 * @param jobId 需要取消的公开任务ID。
 * @returns 取消请求处理后的最新任务状态。
 */
export async function cancelTripJob(jobId: string): Promise<TripJobState> {
  try {
    const response = await apiClient.delete<TripJobState>(`/api/trip/jobs/${jobId}`)
    return response.data
  } catch (error: any) {
    console.error('取消旅行计划任务失败:', error)
    throw new Error(error.response?.data?.detail || error.message || '取消旅行计划任务失败')
  }
}

/**
 * 等待任务进入终态，优先使用 SSE 并用低频轮询处理断线情况。
 *
 * @param jobId 需要观察的公开任务ID。
 * @param onProgress 每次收到新状态时调用的界面更新函数。
 * @param signal 页面卸载或用户切换任务时使用的可选取消信号。
 * @returns 成功、失败或取消后的最终任务状态。
 */
export function waitForTripJob(
  jobId: string,
  onProgress: (state: TripJobState) => void,
  signal?: AbortSignal
): Promise<TripJobState> {
  return new Promise((resolve, reject) => {
    let settled = false
    let consecutivePollFailures = 0
    const eventUrl = apiClient.getUri({
      url: `/api/trip/jobs/${jobId}/events`,
      params: { guest_id: getGuestId() }
    })
    const eventSource = new EventSource(eventUrl)

    /** 释放订阅、轮询、超时和取消监听器。 */
    const cleanup = (): void => {
      eventSource.close()
      window.clearInterval(pollTimer)
      window.clearTimeout(timeoutTimer)
      signal?.removeEventListener('abort', handleAbort)
    }

    /** 接收状态并在任务进入终态后完成等待。 */
    const consume = (state: TripJobState): void => {
      if (settled) return
      onProgress(state)
      if (['succeeded', 'failed', 'cancelled'].includes(state.status)) {
        settled = true
        cleanup()
        resolve(state)
      }
    }

    /** 在 SSE 中断时继续从状态查询接口恢复真实进度。 */
    const poll = async (): Promise<void> => {
      if (settled) return
      try {
        consume(await getTripJob(jobId))
        consecutivePollFailures = 0
      } catch (error) {
        consecutivePollFailures += 1
        if (consecutivePollFailures >= 3) {
          settled = true
          cleanup()
          reject(error)
        }
      }
    }

    /** 页面卸载或观察任务被替换时结束本地等待。 */
    const handleAbort = (): void => {
      if (settled) return
      settled = true
      cleanup()
      reject(new DOMException('旅行计划任务观察已停止', 'AbortError'))
    }

    eventSource.addEventListener('trip-job', (event) => {
      const messageEvent = event as MessageEvent<string>
      consume(JSON.parse(messageEvent.data) as TripJobState)
    })
    eventSource.onerror = () => {
      console.warn('旅行计划进度流暂时中断，正在通过轮询恢复')
    }

    const pollTimer = window.setInterval(() => {
      void poll()
    }, 2000)
    const timeoutTimer = window.setTimeout(() => {
      if (settled) return
      settled = true
      cleanup()
      reject(new Error('旅行计划任务等待超时，请稍后查询任务状态'))
    }, 420000)

    signal?.addEventListener('abort', handleAbort, { once: true })
    void poll()
  })
}

/**
 * 健康检查
 */
export async function healthCheck(): Promise<any> {
  try {
    const response = await apiClient.get('/health')
    return response.data
  } catch (error: any) {
    console.error('健康检查失败:', error)
    throw new Error(error.message || '健康检查失败')
  }
}

/**
 * 恢复当前 HttpOnly Cookie 对应的账号会话。
 *
 * @returns 当前认证状态和公开用户信息。
 */
export async function getAccountSession(): Promise<AccountSession> {
  const response = await apiClient.get<AccountSession>('/api/auth/me')
  return response.data
}

/**
 * 注册账号并认领当前浏览器的游客行程。
 *
 * @param payload 邮箱、密码和显示名称。
 * @param captchaToken 启用 Turnstile 时签发的单次 Token。
 * @returns 新账号会话和认领行程数量。
 */
export async function registerAccount(payload: {
  email: string
  password: string
  display_name: string
}, captchaToken?: string): Promise<AccountSession> {
  const response = await apiClient.post<AccountSession>(
    '/api/auth/register',
    payload,
    {
      headers: captchaToken ? { 'X-Captcha-Token': captchaToken } : {}
    }
  )
  return response.data
}

/**
 * 登录账号并认领当前浏览器尚未迁移的游客行程。
 *
 * @param payload 邮箱和密码。
 * @returns 登录会话和认领行程数量。
 */
export async function loginAccount(payload: {
  email: string
  password: string
}): Promise<AccountSession> {
  const response = await apiClient.post<AccountSession>('/api/auth/login', payload)
  return response.data
}

/**
 * 撤销当前服务端会话并删除 Cookie。
 *
 * @returns 未认证的会话状态。
 */
export async function logoutAccount(): Promise<AccountSession> {
  const response = await apiClient.post<AccountSession>('/api/auth/logout')
  return response.data
}

/**
 * 再次验证密码后永久删除当前账号和关联数据。
 *
 * @param password 当前账号密码。
 * @returns 服务端确认的删除数据数量。
 */
export async function deleteAccount(password: string): Promise<{
  deleted: boolean
  trips: number
  jobs: number
  feedback: number
}> {
  const response = await apiClient.delete('/api/auth/me', {
    data: { password }
  })
  return response.data
}

/**
 * 读取管理员运营总览。
 *
 * @param hours 向前统计的小时数。
 * @returns 任务质量、成本、缓存和反馈指标。
 */
export async function getOperationsOverview(
  hours: number
): Promise<OperationsOverview> {
  const response = await apiClient.get<OperationsOverview>(
    '/api/admin/operations/overview',
    { params: { hours } }
  )
  return response.data
}

/**
 * 使用任务 ID 或请求 ID 查询完整执行轨迹。
 *
 * @param locator 公开 job_id 或 X-Request-ID。
 * @returns 任务、预算、Agent 与外部调用轨迹。
 */
export async function getOperationsJobTrace(locator: string): Promise<JobTrace> {
  const response = await apiClient.get<JobTrace>(
    `/api/admin/operations/jobs/${encodeURIComponent(locator)}`
  )
  return response.data
}

/**
 * 读取地图和 LLM 当前共享熔断状态。
 *
 * @returns 固定提供方集合的熔断状态。
 */
export async function getCircuitStatuses(): Promise<CircuitStatus[]> {
  const response = await apiClient.get<CircuitStatus[]>(
    '/api/admin/operations/circuits'
  )
  return response.data
}

/**
 * 读取当前部署的数据处理与第三方来源说明。
 *
 * @returns 结构化隐私和提供方信息。
 */
export async function getPrivacyInformation(): Promise<PrivacyInformation> {
  const response = await apiClient.get<PrivacyInformation>(
    '/api/compliance/privacy'
  )
  return response.data
}

/**
 * 读取验证码开关和允许公开的站点 Key。
 *
 * @returns 前端渲染安全组件所需配置。
 */
export async function getPublicSecurityConfig(): Promise<PublicSecurityConfig> {
  const response = await apiClient.get<PublicSecurityConfig>(
    '/api/compliance/security-config'
  )
  return response.data
}

/**
 * 读取当前账号或游客拥有的行程摘要。
 *
 * @returns 按更新时间倒序排列的行程摘要。
 */
export async function listSavedTrips(): Promise<TripSummary[]> {
  const response = await apiClient.get<TripSummary[]>('/api/trips')
  return response.data
}

/**
 * 读取一份行程当前快照和版本历史。
 *
 * @param tripId 行程资产 ID。
 * @returns 当前计划、锁定信息和版本历史。
 */
export async function getSavedTrip(tripId: string): Promise<SavedTrip> {
  const response = await apiClient.get<SavedTrip>(`/api/trips/${tripId}`)
  return response.data
}

/**
 * 把未归档的客户端计划保存为服务器行程资产。
 *
 * @param plan 已通过前端类型约束的旅行计划。
 * @param title 可选自定义标题。
 * @returns 具有首个不可变版本的行程资产。
 */
export async function createSavedTrip(
  plan: TripPlan,
  title?: string
): Promise<SavedTrip> {
  const response = await apiClient.post<SavedTrip>('/api/trips', { plan, title })
  return response.data
}

/**
 * 提交结构化编辑，并让后端重算路线、时间表和预算。
 *
 * @param tripId 行程资产 ID。
 * @param baseVersion 客户端编辑所基于的版本号。
 * @param operations 按顺序执行的结构化编辑命令。
 * @param changeSummary 版本历史中的用户可读说明。
 * @returns 后端重算后的新当前版本。
 */
export async function editSavedTrip(
  tripId: string,
  baseVersion: number,
  operations: TripEditOperation[],
  changeSummary = '用户编辑行程'
): Promise<SavedTrip> {
  const response = await apiClient.patch<SavedTrip>(`/api/trips/${tripId}`, {
    base_version: baseVersion,
    operations,
    change_summary: changeSummary
  })
  return response.data
}

/**
 * 把历史快照复制为新的当前版本。
 *
 * @param tripId 行程资产 ID。
 * @param baseVersion 客户端当前版本号。
 * @param versionNumber 需要恢复的历史版本号。
 * @returns 新追加的恢复版本。
 */
export async function restoreSavedTrip(
  tripId: string,
  baseVersion: number,
  versionNumber: number
): Promise<SavedTrip> {
  const response = await apiClient.post<SavedTrip>(`/api/trips/${tripId}/restore`, {
    base_version: baseVersion,
    version_number: versionNumber
  })
  return response.data
}

/**
 * 软删除当前用户或游客拥有的行程。
 *
 * @param tripId 行程资产 ID。
 * @returns 无返回值。
 */
export async function deleteSavedTrip(tripId: string): Promise<void> {
  await apiClient.delete(`/api/trips/${tripId}`)
}

/**
 * 为当前行程版本创建一个有期限的只读分享链接。
 *
 * @param tripId 行程资产 ID。
 * @returns 只出现一次的原始分享令牌和路径。
 */
export async function createTripShare(tripId: string): Promise<ShareLink> {
  const response = await apiClient.post<ShareLink>(`/api/trips/${tripId}/shares`, {
    expires_in_days: 30
  })
  return response.data
}

/**
 * 无需登录读取分享令牌固定的行程版本。
 *
 * @param token URL 中的原始分享令牌。
 * @returns 不包含所有者信息的固定版本快照。
 */
export async function getSharedTrip(token: string): Promise<SharedTrip> {
  const response = await apiClient.get<SharedTrip>(`/api/shares/${token}`)
  return response.data
}

/**
 * 对仍然有效的分享行程提交评分和建议。
 *
 * @param token URL 中的原始分享令牌。
 * @param rating 一到五星评分。
 * @param comment 可选文本建议。
 * @returns 无返回值。
 */
export async function submitTripFeedback(
  token: string,
  rating: number,
  comment: string
): Promise<void> {
  await apiClient.post(`/api/shares/${token}/feedback`, { rating, comment })
}

/**
 * 查询真实 POI 候选，供替换景点时选择。
 *
 * @param keywords 景点搜索关键词。
 * @param city 当前行程城市。
 * @returns 高德返回并经后端解析的候选列表。
 */
export async function searchPoiCandidates(
  keywords: string,
  city: string
): Promise<PoiCandidate[]> {
  const response = await apiClient.get<{ data: PoiCandidate[] }>('/api/map/poi', {
    params: { keywords, city, citylimit: true }
  })
  return response.data.data || []
}

/**
 * 下载后端根据当前版本时间轴生成的 iCalendar 文件。
 *
 * @param tripId 行程资产 ID。
 * @returns 无返回值。
 */
export async function downloadTripCalendar(tripId: string): Promise<void> {
  const response = await apiClient.get<Blob>(`/api/trips/${tripId}/calendar.ics`, {
    responseType: 'blob'
  })
  const objectUrl = URL.createObjectURL(response.data)
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = `trip-${tripId}.ics`
  link.click()
  URL.revokeObjectURL(objectUrl)
}

export default apiClient

