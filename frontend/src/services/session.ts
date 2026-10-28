import { reactive, readonly } from 'vue'
import {
  deleteAccount,
  getAccountSession,
  loginAccount,
  logoutAccount,
  registerAccount
} from '@/services/api'
import type { UserAccount } from '@/types'

interface SessionState {
  initialized: boolean
  loading: boolean
  user: UserAccount | null
}

const mutableSession = reactive<SessionState>({
  initialized: false,
  loading: false,
  user: null
})

export const sessionState = readonly(mutableSession)

/**
 * 从服务端 HttpOnly Cookie 恢复账号状态。
 *
 * @returns 会话初始化完成后的用户，游客返回 null。
 */
export async function initializeSession(): Promise<UserAccount | null> {
  if (mutableSession.initialized) return mutableSession.user
  mutableSession.loading = true
  try {
    const session = await getAccountSession()
    mutableSession.user = session.user || null
    return mutableSession.user
  } finally {
    mutableSession.initialized = true
    mutableSession.loading = false
  }
}

/**
 * 使用邮箱密码登录并刷新全局账号状态。
 *
 * @param email 用户邮箱。
 * @param password 用户密码。
 * @returns 本次登录认领的游客行程数量。
 */
export async function signIn(email: string, password: string): Promise<number> {
  mutableSession.loading = true
  try {
    const session = await loginAccount({ email, password })
    mutableSession.user = session.user || null
    mutableSession.initialized = true
    return session.claimed_trip_count
  } finally {
    mutableSession.loading = false
  }
}

/**
 * 创建账号、认领游客行程并刷新全局账号状态。
 *
 * @param email 用户邮箱。
 * @param password 用户密码。
 * @param displayName 用户显示名称。
 * @param captchaToken 启用 Turnstile 时签发的单次 Token。
 * @returns 本次注册认领的游客行程数量。
 */
export async function signUp(
  email: string,
  password: string,
  displayName: string,
  captchaToken?: string
): Promise<number> {
  mutableSession.loading = true
  try {
    const session = await registerAccount({
      email,
      password,
      display_name: displayName
    }, captchaToken)
    mutableSession.user = session.user || null
    mutableSession.initialized = true
    return session.claimed_trip_count
  } finally {
    mutableSession.loading = false
  }
}

/**
 * 撤销服务端会话并恢复游客界面状态。
 *
 * @returns 无返回值。
 */
export async function signOut(): Promise<void> {
  mutableSession.loading = true
  try {
    await logoutAccount()
    mutableSession.user = null
    mutableSession.initialized = true
  } finally {
    mutableSession.loading = false
  }
}

/**
 * 永久删除当前账号并清空全局登录状态。
 *
 * @param password 用于敏感操作再次认证的当前密码。
 * @returns 服务端删除的关联数据数量。
 */
export async function deleteCurrentAccount(password: string): Promise<{
  trips: number
  jobs: number
  feedback: number
}> {
  mutableSession.loading = true
  try {
    const result = await deleteAccount(password)
    mutableSession.user = null
    mutableSession.initialized = true
    return result
  } finally {
    mutableSession.loading = false
  }
}
