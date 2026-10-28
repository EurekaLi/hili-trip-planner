<template>
  <main class="privacy-page">
    <header>
      <p class="eyebrow">数据透明</p>
      <h1>隐私与数据来源</h1>
      <p>这里展示当前部署实际使用的数据范围、保留期限和第三方服务。</p>
    </header>

    <a-spin :spinning="loading">
      <a-alert v-if="errorMessage" type="error" show-icon :message="errorMessage" />
      <template v-if="privacy">
        <section>
          <h2>保存的数据</h2>
          <ul><li v-for="item in privacy.collected_data" :key="item">{{ item }}</li></ul>
          <p>默认保留期限为 {{ privacy.retention_days }} 天；依法或为解决争议需要时除外。</p>
        </section>

        <section>
          <h2>第三方提供方</h2>
          <div class="provider-list">
            <div v-for="provider in privacy.external_providers" :key="provider.name">
              <strong>{{ provider.name }}</strong>
              <span>{{ provider.purpose }}</span>
            </div>
          </div>
          <p>外部图片展示时保留作者、许可和来源链接；行程中的事实来源与估算状态以标签为准。</p>
        </section>

        <section>
          <h2>你的控制权</h2>
          <ul><li v-for="item in privacy.user_controls" :key="item">{{ item }}</li></ul>
          <p v-if="privacy.contact_email">数据问题联系：{{ privacy.contact_email }}</p>
        </section>
      </template>
    </a-spin>
  </main>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { getApiErrorMessage, getPrivacyInformation } from '@/services/api'
import type { PrivacyInformation } from '@/types'

const loading = ref(false)
const errorMessage = ref('')
const privacy = ref<PrivacyInformation | null>(null)

/** 从后端读取与当前部署配置一致的隐私说明。 */
const loadPrivacy = async (): Promise<void> => {
  loading.value = true
  try {
    privacy.value = await getPrivacyInformation()
  } catch (error: unknown) {
    errorMessage.value = getApiErrorMessage(error, '隐私说明加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(() => { void loadPrivacy() })
</script>

<style scoped>
.privacy-page {
  width: min(820px, calc(100% - 32px));
  margin: 0 auto;
  padding: 40px 0 64px;
}

header { padding-bottom: 24px; border-bottom: 1px solid #dbe3e0; }
h1, h2 { color: #182724; letter-spacing: 0; }
h1 { margin: 0; font-size: 30px; }
h2 { margin: 0 0 12px; font-size: 19px; }
header > p:last-child, section p, li { color: #52615d; line-height: 1.7; }
.eyebrow { margin: 0 0 6px; color: #27796e; font-size: 13px; font-weight: 700; }
section { padding: 26px 0; border-bottom: 1px solid #e1e7e5; }
ul { margin: 0; padding-left: 20px; }
.provider-list { display: grid; grid-template-columns: 1fr 1fr; gap: 14px 24px; }
.provider-list div { display: flex; flex-direction: column; gap: 3px; }
.provider-list span { color: #65726f; line-height: 1.5; }
@media (max-width: 640px) { .provider-list { grid-template-columns: 1fr; } }
</style>
