<template>
  <div ref="container" class="turnstile-container" aria-label="人机验证"></div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'

const props = defineProps<{
  siteKey: string
  action: string
}>()
const emit = defineEmits<{
  verified: [token: string]
  expired: []
  error: []
}>()
const container = ref<HTMLElement | null>(null)
let widgetId: string | null = null

interface TurnstileApi {
  render: (element: HTMLElement, options: Record<string, unknown>) => string
  remove: (widgetId: string) => void
}

declare global {
  interface Window {
    turnstile?: TurnstileApi
    tripTurnstileLoader?: Promise<TurnstileApi>
  }
}

/** 按需加载一次官方 Turnstile 脚本并等待全局 API 就绪。 */
const loadTurnstile = (): Promise<TurnstileApi> => {
  if (window.turnstile) return Promise.resolve(window.turnstile)
  if (window.tripTurnstileLoader) return window.tripTurnstileLoader
  window.tripTurnstileLoader = new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'
    script.async = true
    script.defer = true
    script.onload = () => {
      if (window.turnstile) resolve(window.turnstile)
      else reject(new Error('Turnstile API 未初始化'))
    }
    script.onerror = () => reject(new Error('Turnstile 脚本加载失败'))
    document.head.appendChild(script)
  })
  return window.tripTurnstileLoader
}

/** 渲染与当前敏感操作绑定的托管验证码小组件。 */
const renderWidget = async (): Promise<void> => {
  if (!container.value) return
  try {
    const turnstile = await loadTurnstile()
    widgetId = turnstile.render(container.value, {
      sitekey: props.siteKey,
      action: props.action,
      callback: (token: string) => emit('verified', token),
      'expired-callback': () => emit('expired'),
      'error-callback': () => emit('error')
    })
  } catch {
    emit('error')
  }
}

onMounted(() => { void renderWidget() })
onBeforeUnmount(() => {
  if (widgetId && window.turnstile) window.turnstile.remove(widgetId)
})
</script>

<style scoped>
.turnstile-container {
  min-height: 65px;
  margin: 8px 0 18px;
}
</style>
