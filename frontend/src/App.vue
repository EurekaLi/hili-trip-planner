<template>
  <a-layout class="app-shell">
    <a-layout-header class="app-header">
      <router-link class="brand" to="/">
        <CompassOutlined />
        <span>Hili Trip</span>
      </router-link>
      <nav class="header-actions" aria-label="主导航">
        <router-link class="nav-link" to="/trips">
          <BookOutlined />
          <span>我的行程</span>
        </router-link>
        <a-dropdown v-if="sessionState.user">
          <a-button type="text" class="account-button">
            <UserOutlined />
            {{ sessionState.user.display_name }}
          </a-button>
          <template #overlay>
            <a-menu @click="handleAccountMenu">
              <a-menu-item v-if="sessionState.user?.is_admin" key="operations">
                <DashboardOutlined />
                运营看板
              </a-menu-item>
              <a-menu-item key="logout">
                <LogoutOutlined />
                退出登录
              </a-menu-item>
              <a-menu-item key="delete-account" danger>
                <DeleteOutlined />
                删除账号
              </a-menu-item>
            </a-menu>
          </template>
        </a-dropdown>
        <a-button v-else type="primary" @click="accountModalOpen = true">
          <UserOutlined />
          登录
        </a-button>
      </nav>
    </a-layout-header>
    <a-layout-content class="app-content">
      <router-view />
    </a-layout-content>
    <a-layout-footer class="app-footer">
      <span>HiliAgents 智能旅行助手 · 数据来源与估算状态以行程标记为准</span>
      <router-link to="/privacy">隐私与数据来源</router-link>
    </a-layout-footer>
  </a-layout>

  <a-modal
    v-model:open="accountModalOpen"
    :title="accountMode === 'login' ? '登录账号' : '创建账号'"
    :confirm-loading="sessionState.loading"
    ok-text="确认"
    cancel-text="取消"
    @ok="submitAccount"
  >
    <a-segmented
      v-model:value="accountMode"
      block
      :options="[
        { label: '登录', value: 'login' },
        { label: '注册', value: 'register' }
      ]"
      class="account-mode"
    />
    <a-form layout="vertical">
      <a-form-item v-if="accountMode === 'register'" label="显示名称" required>
        <a-input v-model:value="accountForm.displayName" autocomplete="name" />
      </a-form-item>
      <a-form-item label="邮箱" required>
        <a-input v-model:value="accountForm.email" autocomplete="email" />
      </a-form-item>
      <a-form-item label="密码" required extra="至少 8 个字符">
        <a-input-password
          v-model:value="accountForm.password"
          :autocomplete="accountMode === 'login' ? 'current-password' : 'new-password'"
        />
      </a-form-item>
      <TurnstileWidget
        v-if="accountMode === 'register' && securityConfig.turnstile_enabled && securityConfig.turnstile_site_key"
        :key="registrationCaptchaKey"
        :site-key="securityConfig.turnstile_site_key"
        action="register"
        @verified="registrationCaptchaToken = $event"
        @expired="registrationCaptchaToken = ''"
        @error="registrationCaptchaToken = ''"
      />
    </a-form>
  </a-modal>

  <a-modal
    v-model:open="deleteModalOpen"
    title="永久删除账号"
    ok-text="确认永久删除"
    cancel-text="取消"
    ok-type="danger"
    :confirm-loading="sessionState.loading"
    @ok="submitAccountDeletion"
  >
    <a-alert
      type="warning"
      show-icon
      message="此操作不可撤销"
      description="账号、行程版本、任务记录、外部调用审计和反馈将被永久删除。"
    />
    <a-form layout="vertical" class="delete-form">
      <a-form-item label="输入当前密码确认" required>
        <a-input-password
          v-model:value="deletePassword"
          autocomplete="current-password"
        />
      </a-form-item>
    </a-form>
  </a-modal>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import TurnstileWidget from '@/components/TurnstileWidget.vue'
import { getApiErrorMessage, getPublicSecurityConfig } from '@/services/api'
import {
  BookOutlined,
  CompassOutlined,
  DashboardOutlined,
  DeleteOutlined,
  LogoutOutlined,
  UserOutlined
} from '@ant-design/icons-vue'
import {
  deleteCurrentAccount,
  initializeSession,
  sessionState,
  signIn,
  signOut,
  signUp
} from '@/services/session'
import type { PublicSecurityConfig } from '@/types'

const router = useRouter()
const accountModalOpen = ref(false)
const deleteModalOpen = ref(false)
const deletePassword = ref('')
const registrationCaptchaToken = ref('')
const registrationCaptchaKey = ref(0)
const securityConfig = reactive<PublicSecurityConfig>({
  turnstile_enabled: false,
  turnstile_site_key: null
})
const accountMode = ref<'login' | 'register'>('login')
const accountForm = reactive({
  displayName: '',
  email: '',
  password: ''
})

/**
 * 校验账号表单并执行登录或注册。
 *
 * @returns 无返回值。
 */
const submitAccount = async (): Promise<void> => {
  if (!accountForm.email.trim() || accountForm.password.length < 8) {
    message.warning('请填写有效邮箱和至少 8 位密码')
    return
  }
  if (accountMode.value === 'register' && !accountForm.displayName.trim()) {
    message.warning('请填写显示名称')
    return
  }
  if (
    accountMode.value === 'register'
    && securityConfig.turnstile_enabled
    && !registrationCaptchaToken.value
  ) {
    message.warning('请完成人机验证')
    return
  }

  try {
    const claimed = accountMode.value === 'login'
      ? await signIn(accountForm.email, accountForm.password)
      : await signUp(
          accountForm.email,
          accountForm.password,
          accountForm.displayName,
          registrationCaptchaToken.value || undefined
        )
    accountModalOpen.value = false
    accountForm.password = ''
    message.success(claimed > 0 ? `已登录并恢复 ${claimed} 个游客行程` : '账号操作成功')
  } catch (error: unknown) {
    message.error(getApiErrorMessage(error, '账号操作失败'))
    if (accountMode.value === 'register' && securityConfig.turnstile_enabled) {
      registrationCaptchaToken.value = ''
      registrationCaptchaKey.value += 1
    }
  }
}

/**
 * 处理账号下拉菜单命令。
 *
 * @param event Ant Design 菜单点击事件。
 * @returns 无返回值。
 */
const handleAccountMenu = async (event: { key: string }): Promise<void> => {
  if (event.key === 'operations') {
    await router.push('/operations')
    return
  }
  if (event.key === 'delete-account') {
    deleteModalOpen.value = true
    return
  }
  if (event.key === 'logout') {
    await signOut()
    message.success('已退出登录，当前浏览器仍可使用游客行程')
    await router.push('/')
  }
}

/**
 * 再次验证密码并永久删除当前账号及关联数据。
 *
 * @returns 无返回值。
 */
const submitAccountDeletion = async (): Promise<void> => {
  if (deletePassword.value.length < 8) {
    message.warning('请输入当前账号密码')
    return
  }
  try {
    const deleted = await deleteCurrentAccount(deletePassword.value)
    deleteModalOpen.value = false
    deletePassword.value = ''
    message.success(`账号已删除，同时清理 ${deleted.trips} 个行程`)
    await router.push('/')
  } catch (error: unknown) {
    message.error(getApiErrorMessage(error, '账号删除失败'))
  }
}

onMounted(async () => {
  await initializeSession()
  try {
    Object.assign(securityConfig, await getPublicSecurityConfig())
  } catch {
    securityConfig.turnstile_enabled = false
  }
})
</script>

<style>
* {
  box-sizing: border-box;
}

body {
  margin: 0;
  color: #1f2933;
  background: #f4f6f7;
}

#app {
  font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

.app-shell {
  min-height: 100vh;
}

.app-header {
  position: sticky;
  top: 0;
  z-index: 100;
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 64px;
  padding: 0 32px;
  background: #162321;
  border-bottom: 3px solid #d7a93d;
}

.brand,
.nav-link {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: #fff;
  text-decoration: none;
}

.brand {
  font-size: 20px;
  font-weight: 700;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 18px;
}

.nav-link {
  color: #dbe7e4;
}

.nav-link:hover,
.router-link-active.nav-link {
  color: #f0c75e;
}

.account-button {
  color: #fff;
}

.app-content {
  min-height: calc(100vh - 126px);
}

.app-footer {
  display: flex;
  justify-content: center;
  gap: 16px;
  flex-wrap: wrap;
  padding: 20px;
  color: #5d6966;
  text-align: center;
  background: #edf1f0;
}

.app-footer a {
  color: #16665c;
}

.delete-form {
  margin-top: 20px;
}

.account-mode {
  margin-bottom: 24px;
}

@media (max-width: 640px) {
  .app-header {
    padding: 0 14px;
  }

  .nav-link span,
  .account-button span:not(.anticon) {
    display: none;
  }

  .header-actions {
    gap: 8px;
  }
}
</style>
