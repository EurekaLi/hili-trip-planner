<template>
  <div class="result-container">
    <!-- 页面头部 -->
    <div class="page-header">
      <a-button class="back-button" size="large" @click="goBack">
        ← 返回首页
      </a-button>
      <a-space size="middle">
        <a-button v-if="!editMode" @click="toggleEditMode" type="default">
          ✏️ 编辑行程
        </a-button>
        <a-button v-else @click="saveChanges" type="primary" :loading="saving">
          💾 保存修改
        </a-button>
        <a-button v-if="editMode" @click="cancelEdit" type="default">
          ❌ 取消编辑
        </a-button>
        <a-button v-if="!editMode && !savedTripId" :loading="saving" @click="saveCurrentTrip">
          <SaveOutlined /> 保存行程
        </a-button>
        <a-button v-if="!editMode && savedTripId" @click="versionsOpen = true">
          <HistoryOutlined /> 版本历史
        </a-button>
        <a-button v-if="!editMode && savedTripId" @click="shareCurrentTrip">
          <ShareAltOutlined /> 分享
        </a-button>
        <a-button v-if="!editMode && savedTripId" @click="exportCalendar">
          <CalendarOutlined /> 日历
        </a-button>

        <!-- 导出按钮 -->
        <a-dropdown v-if="!editMode">
          <template #overlay>
            <a-menu>
              <a-menu-item key="image" @click="exportAsImage">
                📷 导出为图片
              </a-menu-item>
              <a-menu-item key="pdf" @click="exportAsPDF">
                📄 导出为PDF
              </a-menu-item>
            </a-menu>
          </template>
          <a-button type="default">
            📥 导出行程 <DownOutlined />
          </a-button>
        </a-dropdown>
      </a-space>
    </div>

    <div v-if="tripPlan" class="content-wrapper">
      <!-- 侧边导航 -->
      <div class="side-nav">
        <a-affix :offset-top="80">
          <a-menu mode="inline" :selected-keys="[activeSection]" @click="scrollToSection">
            <a-menu-item key="overview">
              <span>📋 行程概览</span>
            </a-menu-item>
            <a-menu-item key="budget" v-if="tripPlan.budget">
              <span>💰 预算明细</span>
            </a-menu-item>
            <a-menu-item key="map">
              <span>📍 景点地图</span>
            </a-menu-item>
            <a-sub-menu key="days" title="📅 每日行程">
              <a-menu-item v-for="(day, index) in tripPlan.days" :key="`day-${index}`">
                第{{ day.day_index + 1 }}天
              </a-menu-item>
            </a-sub-menu>
            <a-menu-item key="weather" v-if="tripPlan.weather_info && tripPlan.weather_info.length > 0">
              <span>🌤️ 天气信息</span>
            </a-menu-item>
          </a-menu>
        </a-affix>
      </div>

      <!-- 主内容区 -->
      <div class="main-content">
        <!-- 顶部信息区:左侧概览+预算,右侧地图 -->
        <div class="top-info-section">
          <!-- 左侧:行程概览和预算明细 -->
          <div class="left-info">
            <!-- 行程概览 -->
            <a-card id="overview" :title="`${tripPlan.city}旅行计划`" :bordered="false" class="overview-card">
              <div class="overview-content">
                <div class="info-item">
                  <span class="info-label">📅 日期:</span>
                  <span class="info-value">{{ tripPlan.start_date }} 至 {{ tripPlan.end_date }}</span>
                </div>
                <div class="info-item">
                  <span class="info-label">💡 建议:</span>
                  <span class="info-value">{{ tripPlan.overall_suggestions }}</span>
                </div>
              </div>
            </a-card>

            <!-- 预算明细 -->
            <a-card id="budget" v-if="tripPlan.budget" title="💰 预算明细" :bordered="false" class="budget-card">
              <div class="budget-grid">
                <div class="budget-item">
                  <div class="budget-label">景点门票</div>
                  <div class="budget-value">¥{{ tripPlan.budget.total_attractions }}</div>
                </div>
                <div class="budget-item">
                  <div class="budget-label">酒店住宿</div>
                  <div class="budget-value">¥{{ tripPlan.budget.total_hotels }}</div>
                </div>
                <div class="budget-item">
                  <div class="budget-label">餐饮费用</div>
                  <div class="budget-value">¥{{ tripPlan.budget.total_meals }}</div>
                </div>
                <div class="budget-item">
                  <div class="budget-label">交通费用</div>
                  <div class="budget-value">¥{{ tripPlan.budget.total_transportation }}</div>
                </div>
              </div>
              <div class="budget-total">
                <span class="total-label">预估总费用</span>
                <span class="total-value">¥{{ tripPlan.budget.total }}</span>
              </div>
              <a-alert
                v-if="(tripPlan.budget.unknown_cost_items?.length || 0) > 0"
                class="budget-warning"
                type="warning"
                show-icon
                message="部分价格尚无可靠来源"
                :description="tripPlan.budget.unknown_cost_items?.join('、')"
              />
            </a-card>
          </div>

          <!-- 右侧:地图 -->
          <div class="right-map">
            <a-card id="map" title="📍 景点地图" :bordered="false" class="map-card">
              <div class="map-stage">
                <div id="amap-container" class="amap-container"></div>
                <div v-if="mapStatus === 'loading'" class="map-state">
                  <a-spin size="large" tip="地图加载中" />
                </div>
                <div v-else-if="mapStatus === 'unconfigured'" class="map-state">
                  <a-empty description="地图服务尚未配置" />
                </div>
                <div v-else-if="mapStatus === 'error'" class="map-state">
                  <a-alert
                    type="error"
                    show-icon
                    message="地图加载失败"
                    :description="mapError"
                  />
                </div>
              </div>
            </a-card>
          </div>
        </div>

        <!-- 每日行程:可折叠 -->
        <a-card title="📅 每日行程" :bordered="false" class="days-card">
          <a-collapse v-model:activeKey="activeDays">
            <a-collapse-panel
              v-for="(day, index) in tripPlan.days"
              :key="index"
              :id="`day-${index}`"
            >
              <template #header>
                <div class="day-header">
                  <span class="day-title">第{{ day.day_index + 1 }}天</span>
                  <span class="day-date">{{ day.date }}</span>
                </div>
              </template>

              <!-- 行程基本信息 -->
              <div class="day-info">
                <div class="info-row">
                  <span class="label">📝 行程描述:</span>
                  <span class="value">{{ day.description }}</span>
                </div>
                <div class="info-row">
                  <span class="label">🚗 交通方式:</span>
                  <span class="value">{{ day.transportation }}</span>
                </div>
                <div class="info-row">
                  <span class="label">🏨 住宿:</span>
                  <span class="value">{{ day.accommodation }}</span>
                </div>
                <div class="info-row">
                  <span class="label">时间表:</span>
                  <span class="value schedule-summary">
                    <a-tag :color="getScheduleColor(day.schedule_status)">
                      {{ getScheduleLabel(day.schedule_status) }}
                    </a-tag>
                    <span>游览 {{ day.total_visit_minutes || 0 }} 分钟</span>
                    <span>通勤 {{ day.total_transit_minutes || 0 }} 分钟</span>
                  </span>
                </div>
              </div>

              <template v-if="day.timeline && day.timeline.length > 0">
                <a-divider orientation="left">可执行时间表</a-divider>
                <div class="timeline-list">
                  <div
                    v-for="(entry, timelineIndex) in day.timeline"
                    :key="`${entry.start_time}-${entry.kind}-${timelineIndex}`"
                    class="timeline-entry"
                    :class="`timeline-entry--${entry.status}`"
                  >
                    <time class="timeline-time">
                      {{ entry.start_time }}–{{ entry.end_time }}
                    </time>
                    <a-tag :color="getTimelineColor(entry.status)">
                      {{ getTimelineKindLabel(entry.kind) }}
                    </a-tag>
                    <div class="timeline-content">
                      <strong>{{ entry.title }}</strong>
                      <span v-if="entry.note">{{ entry.note }}</span>
                    </div>
                  </div>
                </div>
              </template>

              <!-- 景点安排 -->
              <a-divider orientation="left">🎯 景点安排</a-divider>
              <a-list
                :data-source="day.attractions"
                :grid="{ gutter: 16, xs: 1, sm: 1, md: 2 }"
              >
                <template #renderItem="{ item, index }">
                  <a-list-item>
                    <a-card
                      :title="item.name"
                      size="small"
                      class="attraction-card"
                      :class="{ 'attraction-card--locked': isLocked(item) }"
                      :draggable="editMode && !isLocked(item)"
                      @dragstart="startAttractionDrag(item)"
                      @dragover.prevent
                      @drop="dropAttraction(day.day_index, index)"
                    >
                      <!-- 编辑模式下的操作按钮 -->
                      <template #extra v-if="editMode">
                        <a-space>
                          <a-button
                            size="small"
                            @click="moveAttraction(day.day_index, index, 'up')"
                            :disabled="index === 0 || isLocked(item)"
                          >
                            ↑
                          </a-button>
                          <a-button
                            size="small"
                            @click="moveAttraction(day.day_index, index, 'down')"
                            :disabled="index === day.attractions.length - 1 || isLocked(item)"
                          >
                            ↓
                          </a-button>
                          <a-button
                            size="small"
                            danger
                            @click="deleteAttraction(day.day_index, index)"
                            :disabled="isLocked(item)"
                          >
                            🗑️
                          </a-button>
                          <a-button size="small" @click="toggleAttractionLock(item)">
                            <UnlockOutlined v-if="isLocked(item)" />
                            <LockOutlined v-else />
                          </a-button>
                          <a-button
                            size="small"
                            :disabled="isLocked(item)"
                            @click="openReplaceDialog(item)"
                          >
                            <SwapOutlined />
                          </a-button>
                        </a-space>
                      </template>

                      <!-- 景点图片 -->
                      <div class="attraction-image-wrapper">
                        <img
                          :src="getAttractionImage(item, index)"
                          :alt="item.name"
                          class="attraction-image"
                          @error="handleImageError"
                        />
                        <div class="attraction-badge">
                          <span class="badge-number">{{ index + 1 }}</span>
                        </div>
                        <div v-if="item.ticket_price" class="price-tag">
                          ¥{{ item.ticket_price }}
                        </div>
                      </div>

                      <!-- 编辑模式下可编辑的字段 -->
                      <div v-if="editMode">
                        <p><strong>地址:</strong> {{ item.address }}</p>

                        <p><strong>安排到:</strong></p>
                        <a-select
                          :value="day.day_index"
                          size="small"
                          style="width: 100%; margin-bottom: 8px"
                          :disabled="isLocked(item)"
                          @change="moveAttractionToDay(item, Number($event))"
                        >
                          <a-select-option
                            v-for="targetDay in tripPlan.days"
                            :key="targetDay.day_index"
                            :value="targetDay.day_index"
                          >
                            第 {{ targetDay.day_index + 1 }} 天 · {{ targetDay.date }}
                          </a-select-option>
                        </a-select>

                        <p><strong>游览时长(分钟):</strong></p>
                        <a-input-number v-model:value="item.visit_duration" :min="10" :max="480" size="small" style="width: 100%; margin-bottom: 8px" />

                        <p><strong>描述:</strong></p>
                        <a-textarea v-model:value="item.description" :rows="2" size="small" style="margin-bottom: 8px" />
                      </div>

                      <!-- 查看模式 -->
                      <div v-else>
                        <div class="source-tags">
                          <a-tag :color="getFactColor(item.source)">
                            {{ getFactLabel(item.source) }}
                          </a-tag>
                          <a-tag :color="getFactColor(item.ticket_price_source)">
                            门票：{{ getFactLabel(item.ticket_price_source) }}
                          </a-tag>
                        </div>
                        <a
                          v-if="attractionAttributions[item.name]"
                          class="image-attribution"
                          :href="getAttributionLink(item.name)"
                          target="_blank"
                          rel="noopener noreferrer"
                          @click.stop
                        >
                          {{ getAttributionLabel(item.name) }}
                        </a>
                        <p><strong>地址:</strong> {{ item.address }}</p>
                        <p><strong>游览时长:</strong> {{ item.visit_duration }}分钟</p>
                        <p v-if="item.visit_constraints">
                          <strong>开放时间:</strong>
                          {{ formatOpeningHours(item.visit_constraints) }}
                          <a-tag
                            :color="getFactColor(item.visit_constraints.opening_hours_source)"
                          >
                            {{ getFactLabel(item.visit_constraints.opening_hours_source) }}
                          </a-tag>
                        </p>
                        <p v-if="item.visit_constraints?.reservation_required">
                          <a-tag color="gold">通常需要提前预约</a-tag>
                        </p>
                        <p><strong>描述:</strong> {{ item.description }}</p>
                        <p v-if="item.rating"><strong>评分:</strong> {{ item.rating }}⭐</p>
                        <a
                          :href="getNavigationUrl(item)"
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          <EnvironmentOutlined /> 在高德地图中导航
                        </a>
                      </div>
                    </a-card>
                  </a-list-item>
                </template>
              </a-list>

              <!-- 相邻景点之间的真实导航 -->
              <template v-if="day.routes && day.routes.length > 0">
                <a-divider orientation="left">🧭 出行导航</a-divider>
                <div class="route-list">
                  <div
                    v-for="(route, routeIndex) in day.routes"
                    :key="`${route.origin_poi_id}-${route.destination_poi_id}-${routeIndex}`"
                    class="route-item"
                  >
                    <div class="route-heading">
                      <span class="route-title">
                        {{ route.origin_name }} → {{ route.destination_name }}
                      </span>
                      <a-tag :color="getRouteTypeColor(route.route_type)">
                        {{ getRouteTypeLabel(route.route_type) }}
                      </a-tag>
                      <a-tag :color="getFactColor(route.source)">
                        {{ getFactLabel(route.source) }}
                      </a-tag>
                      <a-tag :color="getRouteStatusColor(route.status)">
                        {{ getRouteStatusLabel(route.status) }}
                      </a-tag>
                      <a-tag v-if="route.cross_region" color="volcano">跨区域</a-tag>
                    </div>
                    <div class="route-meta">
                      <span>{{ formatDistance(route.distance) }}</span>
                      <span>{{ formatDuration(route.duration) }}</span>
                    </div>
                    <p class="route-description">{{ route.description }}</p>
                    <p v-if="route.fallback_reason" class="route-warning">
                      {{ route.fallback_reason }}
                    </p>
                  </div>
                </div>
              </template>

              <!-- 酒店推荐 -->
              <a-divider v-if="day.hotel" orientation="left">🏨 住宿推荐</a-divider>
              <a-card v-if="day.hotel" size="small" class="hotel-card">
                <template #title>
                  <span class="hotel-title">
                    {{ day.hotel.name }}
                    <a-tag :color="getFactColor(day.hotel.source)">
                      {{ getFactLabel(day.hotel.source) }}
                    </a-tag>
                  </span>
                </template>
                <a-descriptions :column="2" size="small">
                  <a-descriptions-item label="地址">{{ day.hotel.address }}</a-descriptions-item>
                  <a-descriptions-item label="类型">{{ day.hotel.type }}</a-descriptions-item>
                  <a-descriptions-item label="价格范围">{{ day.hotel.price_range }}</a-descriptions-item>
                  <a-descriptions-item label="评分">{{ day.hotel.rating }}⭐</a-descriptions-item>
                  <a-descriptions-item label="距离" :span="2">{{ day.hotel.distance }}</a-descriptions-item>
                </a-descriptions>
              </a-card>

              <!-- 餐饮安排 -->
              <a-divider orientation="left">🍽️ 餐饮安排</a-divider>
              <a-descriptions :column="1" bordered size="small">
                <a-descriptions-item
                  v-for="meal in day.meals"
                  :key="meal.type"
                  :label="getMealLabel(meal.type)"
                >
                  {{ meal.name }}
                  <span v-if="meal.description"> - {{ meal.description }}</span>
                  <span class="meal-source">
                    <a-tag :color="getFactColor(meal.source)">
                      {{ getFactLabel(meal.source) }}
                    </a-tag>
                    <a-tag :color="getFactColor(meal.cost_source)">
                      餐费：{{ getFactLabel(meal.cost_source) }}
                    </a-tag>
                  </span>
                </a-descriptions-item>
              </a-descriptions>
            </a-collapse-panel>
          </a-collapse>
        </a-card>

        <a-card id="weather" v-if="tripPlan.weather_info && tripPlan.weather_info.length > 0" title="天气信息" style="margin-top: 20px" :bordered="false">
        <a-list
          :data-source="tripPlan.weather_info"
          :grid="{ gutter: 16, column: 3 }"
        >
          <template #renderItem="{ item }">
            <a-list-item>
              <a-card size="small" class="weather-card">
                <div class="weather-date">{{ item.date }}</div>
                <a-tag :color="getFactColor(item.source)">
                  {{ getFactLabel(item.source) }}
                </a-tag>
                <div class="weather-info-row">
                  <span class="weather-icon">☀️</span>
                  <div>
                    <div class="weather-label">白天</div>
                    <div class="weather-value">{{ item.day_weather }} {{ item.day_temp }}°C</div>
                  </div>
                </div>
                <div class="weather-info-row">
                  <span class="weather-icon">🌙</span>
                  <div>
                    <div class="weather-label">夜间</div>
                    <div class="weather-value">{{ item.night_weather }} {{ item.night_temp }}°C</div>
                  </div>
                </div>
                <div class="weather-wind">
                  💨 {{ item.wind_direction }} {{ item.wind_power }}
                </div>
              </a-card>
            </a-list-item>
          </template>
        </a-list>
        </a-card>
      </div>
    </div>

    <a-empty v-else description="没有找到旅行计划数据">
      <template #image>
        <div style="font-size: 80px;">🗺️</div>
      </template>
      <template #description>
        <span style="color: #999;">暂无旅行计划数据,请先创建行程</span>
      </template>
      <a-button type="primary" @click="goBack">返回首页创建行程</a-button>
    </a-empty>

    <!-- 回到顶部按钮 -->
    <a-back-top :visibility-height="300">
      <div class="back-top-button">
        ↑
      </div>
    </a-back-top>

    <a-drawer v-model:open="versionsOpen" title="版本历史" width="420">
      <a-timeline v-if="savedTrip">
        <a-timeline-item v-for="version in savedTrip.versions" :key="version.version_number">
          <div class="version-entry">
            <strong>版本 {{ version.version_number }}</strong>
            <span>{{ version.change_summary }}</span>
            <time>{{ new Date(version.created_at).toLocaleString() }}</time>
            <a-button
              v-if="version.version_number !== savedTrip.current_version"
              type="link"
              :loading="saving"
              @click="restoreVersion(version.version_number)"
            >
              恢复此版本
            </a-button>
          </div>
        </a-timeline-item>
      </a-timeline>
    </a-drawer>

    <a-modal
      v-model:open="replaceOpen"
      title="替换景点"
      :footer="null"
      destroy-on-close
    >
      <a-input-search
        v-model:value="replacementKeyword"
        placeholder="输入景点名称"
        enter-button="查询真实 POI"
        :loading="replacementLoading"
        @search="searchReplacementCandidates"
      />
      <a-list class="replacement-list" :data-source="replacementCandidates">
        <template #renderItem="{ item }">
          <a-list-item>
            <a-list-item-meta :title="item.name" :description="item.address" />
            <template #actions>
              <a-button type="primary" @click="selectReplacement(item)">选择</a-button>
            </template>
          </a-list-item>
        </template>
      </a-list>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import {
  CalendarOutlined,
  DownOutlined,
  EnvironmentOutlined,
  HistoryOutlined,
  LockOutlined,
  SaveOutlined,
  ShareAltOutlined,
  SwapOutlined,
  UnlockOutlined
} from '@ant-design/icons-vue'
import apiClient, {
  createSavedTrip,
  createTripShare,
  downloadTripCalendar,
  editSavedTrip,
  getSavedTrip,
  restoreSavedTrip,
  searchPoiCandidates
} from '@/services/api'
import type {
  Attraction,
  FactSource,
  ImageAttribution,
  PoiCandidate,
  SavedTrip,
  TripEditOperation,
  TripPlan,
  VisitConstraints
} from '@/types'

const router = useRouter()
const route = useRoute()
const tripPlan = ref<TripPlan | null>(null)
const savedTrip = ref<SavedTrip | null>(null)
const savedTripId = ref<string | null>(null)
const lockedPoiIds = ref<string[]>([])
const pendingOperations = ref<TripEditOperation[]>([])
const editMode = ref(false)
const saving = ref(false)
const versionsOpen = ref(false)
const originalPlan = ref<TripPlan | null>(null)
const attractionPhotos = ref<Record<string, string>>({})
const attractionAttributions = ref<Record<string, ImageAttribution>>({})
const activeSection = ref('overview')
const activeDays = ref<number[]>([])
const mapStatus = ref<'idle' | 'loading' | 'ready' | 'unconfigured' | 'error'>('idle')
const mapError = ref('')
const replaceOpen = ref(false)
const replacementLoading = ref(false)
const replacementKeyword = ref('')
const replacementCandidates = ref<PoiCandidate[]>([])
const replacementTarget = ref<Attraction | null>(null)
const draggedPoiId = ref<string | null>(null)
let map: any = null

onMounted(async () => {
  const routeTripId = typeof route.params.tripId === 'string'
    ? route.params.tripId
    : null
  const sessionTripId = sessionStorage.getItem('savedTripId')
  savedTripId.value = routeTripId || sessionTripId

  if (savedTripId.value) {
    try {
      applySavedTrip(await getSavedTrip(savedTripId.value))
    } catch (error: any) {
      message.error(error.response?.data?.detail || error.message || '服务器行程加载失败')
    }
  }

  if (!tripPlan.value) {
    const data = sessionStorage.getItem('tripPlan')
    if (data) tripPlan.value = JSON.parse(data)
  }
  if (tripPlan.value) {
    activeDays.value = tripPlan.value.days.map((_, index) => index)
    await loadAttractionPhotos()
    await nextTick()
    await initMap()
  }
})

onBeforeUnmount(() => {
  map?.destroy()
  map = null
})

/**
 * 返回旅行规划首页。
 *
 * @returns 无返回值。
 */
const goBack = (): void => {
  router.push('/')
}

/**
 * 用服务器返回的规范化版本刷新页面和浏览器恢复快照。
 *
 * @param value 后端保存并重算后的完整行程资产。
 * @returns 无返回值。
 */
const applySavedTrip = (value: SavedTrip): void => {
  savedTrip.value = value
  savedTripId.value = value.id
  lockedPoiIds.value = [...value.locked_poi_ids]
  tripPlan.value = JSON.parse(JSON.stringify(value.plan))
  sessionStorage.setItem('tripPlan', JSON.stringify(value.plan))
  sessionStorage.setItem('savedTripId', value.id)
}

/**
 * 获取支持旧计划数据的景点稳定标识。
 *
 * @param attraction 目标景点。
 * @returns 优先使用 POI ID 的稳定标识。
 */
const getAttractionKey = (attraction: Attraction): string => {
  return attraction.poi_id || attraction.name
}

/**
 * 判断景点是否被用户锁定。
 *
 * @param attraction 目标景点。
 * @returns 已锁定时返回 true。
 */
const isLocked = (attraction: Attraction): boolean => {
  return lockedPoiIds.value.includes(getAttractionKey(attraction))
}

/**
 * 展开并滚动到侧边导航选择的内容区域。
 *
 * @param key Ant Design 菜单项键值。
 * @returns 无返回值。
 */
const scrollToSection = ({ key }: { key: string }) => {
  activeSection.value = key

  if (key.startsWith('day-')) {
    const dayIndex = Number(key.slice(4))
    if (Number.isInteger(dayIndex) && !activeDays.value.includes(dayIndex)) {
      activeDays.value = [...activeDays.value, dayIndex]
    }
  }

  nextTick(() => {
    document.getElementById(key)?.scrollIntoView({
      behavior: 'smooth',
      block: 'start'
    })
  })
}

/**
 * 将后端路线类型转换为界面文案。
 *
 * @param routeType - 后端返回的路线类型。
 * @returns 对应的中文交通方式。
 */
const getRouteTypeLabel = (routeType: string): string => {
  const labels: Record<string, string> = {
    walking: '步行',
    driving: '驾车',
    transit: '公共交通'
  }

  return labels[routeType] || routeType
}

/**
 * 获取不同路线类型对应的标签颜色。
 *
 * @param routeType - 后端返回的路线类型。
 * @returns Ant Design Vue 标签颜色。
 */
const getRouteTypeColor = (routeType: string): string => {
  const colors: Record<string, string> = {
    walking: 'green',
    driving: 'blue',
    transit: 'orange'
  }

  return colors[routeType] || 'default'
}

/**
 * 将后端日程状态转换为用户可理解的文案。
 *
 * @param status - 后端返回的时间表状态。
 * @returns 可执行、已降级或存在冲突。
 */
const getScheduleLabel = (status?: string): string => {
  const labels: Record<string, string> = {
    feasible: '可执行',
    degraded: '部分信息为估算',
    infeasible: '存在时间冲突'
  }

  return labels[status || 'degraded'] || '待校验'
}

/**
 * 获取日程状态对应的 Ant Design Vue 标签颜色。
 *
 * @param status - 后端返回的时间表状态。
 * @returns 标签颜色名称。
 */
const getScheduleColor = (status?: string): string => {
  const colors: Record<string, string> = {
    feasible: 'green',
    degraded: 'gold',
    infeasible: 'red'
  }

  return colors[status || 'degraded'] || 'default'
}

/**
 * 将时间轴活动类型转换为中文标签。
 *
 * @param kind - attraction、meal、transit 或 wait。
 * @returns 活动类型中文名称。
 */
const getTimelineKindLabel = (kind: string): string => {
  const labels: Record<string, string> = {
    attraction: '游览',
    meal: '用餐',
    transit: '通勤',
    wait: '等候'
  }

  return labels[kind] || kind
}

/**
 * 获取时间轴条目状态对应的标签颜色。
 *
 * @param status - scheduled、degraded 或 conflict。
 * @returns 标签颜色名称。
 */
const getTimelineColor = (status: string): string => {
  const colors: Record<string, string> = {
    scheduled: 'green',
    degraded: 'gold',
    conflict: 'red'
  }

  return colors[status] || 'default'
}

/**
 * 将路线可用状态转换为界面文案。
 *
 * @param status - 路线真实、估算或不可达状态。
 * @returns 路线状态中文名称。
 */
const getRouteStatusLabel = (status?: string): string => {
  const labels: Record<string, string> = {
    available: '真实路线',
    estimated: '估算路线',
    unavailable: '不可达'
  }

  return labels[status || 'estimated'] || '状态未知'
}

/**
 * 获取路线可用状态对应的标签颜色。
 *
 * @param status - 路线真实、估算或不可达状态。
 * @returns 标签颜色名称。
 */
const getRouteStatusColor = (status?: string): string => {
  const colors: Record<string, string> = {
    available: 'green',
    estimated: 'gold',
    unavailable: 'red'
  }

  return colors[status || 'estimated'] || 'default'
}

/**
 * 格式化景点常规开放时间和固定闭馆星期。
 *
 * @param constraints - 后端结构化的游览约束。
 * @returns 适合结果页显示的开放时间文本。
 */
const formatOpeningHours = (constraints: VisitConstraints): string => {
  const hours = constraints.opening_time && constraints.closing_time
    ? `${constraints.opening_time}-${constraints.closing_day_offset ? '次日' : ''}${constraints.closing_time}`
    : '时间未知'
  const weekdayLabels = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
  const closed = constraints.closed_weekdays?.length
    ? `；${constraints.closed_weekdays.map(day => weekdayLabels[day]).join('、')}可能闭馆`
    : ''

  return `${hours}${closed}`
}

/**
 * 将后端事实可信类型转换为用户可理解的标签。
 *
 * @param source - 可选的数据来源对象。
 * @returns 已验证、估算或未知标签。
 */
const getFactLabel = (source?: FactSource | null): string => {
  const labels = {
    verified: '高德已验证',
    estimated: '估算',
    unknown: '来源未知'
  }

  return source ? labels[source.quality] : labels.unknown
}

/**
 * 获取事实可信标签对应的 Ant Design 颜色。
 *
 * @param source - 可选的数据来源对象。
 * @returns 标签颜色名称。
 */
const getFactColor = (source?: FactSource | null): string => {
  const colors = {
    verified: 'green',
    estimated: 'gold',
    unknown: 'default'
  }

  return source ? colors[source.quality] : colors.unknown
}

/**
 * 将米转换为适合阅读的距离文本。
 *
 * @param distance - 路线距离，单位为米。
 * @returns 米或公里格式的距离文本。
 */
const formatDistance = (distance: number): string => {
  if (distance < 1000) {
    return `${Math.round(distance)} 米`
  }

  return `${(distance / 1000).toFixed(1)} 公里`
}

/**
 * 将秒转换为小时和分钟组成的时长文本。
 *
 * @param duration - 路线时长，单位为秒。
 * @returns 适合阅读的路线时长。
 */
const formatDuration = (duration: number): string => {
  const totalMinutes = Math.max(1, Math.round(duration / 60))
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60

  return hours > 0
    ? `${hours} 小时${minutes > 0 ? ` ${minutes} 分钟` : ''}`
    : `${minutes} 分钟`
}

/**
 * 进入编辑模式并保存可取消恢复的原始快照。
 *
 * @returns 无返回值。
 */
const toggleEditMode = (): void => {
  editMode.value = true
  originalPlan.value = JSON.parse(JSON.stringify(tripPlan.value))
  pendingOperations.value = []
  message.info('进入编辑模式')
}

/**
 * 收集文本和游览时长变化，避免把整份客户端 JSON 当成可信编辑结果。
 *
 * @returns 与原始快照相比发生变化的 update 操作。
 */
const collectFieldUpdates = (): TripEditOperation[] => {
  if (!originalPlan.value || !tripPlan.value) return []
  const originalById = new Map<string, Attraction>()
  originalPlan.value.days.forEach((day) => {
    day.attractions.forEach((attraction) => {
      originalById.set(getAttractionKey(attraction), attraction)
    })
  })
  const deletedIds = new Set(
    pendingOperations.value
      .filter((operation) => operation.type === 'delete')
      .map((operation) => operation.poi_id)
  )
  const updates: TripEditOperation[] = []
  tripPlan.value.days.forEach((day) => {
    day.attractions.forEach((attraction) => {
      const key = getAttractionKey(attraction)
      const original = originalById.get(key)
      if (!original || deletedIds.has(key)) return
      if (
        original.visit_duration !== attraction.visit_duration ||
        original.description !== attraction.description
      ) {
        updates.push({
          type: 'update',
          poi_id: key,
          visit_duration: attraction.visit_duration,
          description: attraction.description
        })
      }
    })
  })
  return updates
}

/**
 * 保存当前结构化编辑，并使用后端重算后的新版本替换页面。
 *
 * @returns 无返回值。
 */
const saveChanges = async (): Promise<void> => {
  if (!tripPlan.value || !originalPlan.value || saving.value) return
  saving.value = true
  try {
    const fieldUpdates = collectFieldUpdates()
    if (!savedTripId.value) {
      applySavedTrip(await createSavedTrip(originalPlan.value))
    }
    const operations = [...pendingOperations.value, ...fieldUpdates]
    if (operations.length === 0) {
      editMode.value = false
      message.info('没有需要保存的修改')
      return
    }
    const updated = await editSavedTrip(
      savedTripId.value as string,
      savedTrip.value?.current_version || 1,
      operations
    )
    applySavedTrip(updated)
    pendingOperations.value = []
    editMode.value = false
    message.success('修改已保存，路线、时间表和预算已重算')
    map?.destroy()
    map = null
    await nextTick()
    await initMap()
  } catch (error: any) {
    message.error(error.response?.data?.detail || error.message || '修改保存失败')
  } finally {
    saving.value = false
  }
}

/**
 * 取消本轮未提交的编辑并恢复服务器快照。
 *
 * @returns 无返回值。
 */
const cancelEdit = () => {
  if (originalPlan.value) {
    tripPlan.value = JSON.parse(JSON.stringify(originalPlan.value))
  }
  lockedPoiIds.value = [...(savedTrip.value?.locked_poi_ids || [])]
  pendingOperations.value = []
  editMode.value = false
  message.info('已取消编辑')
}

/**
 * 删除未锁定景点并记录结构化操作。
 *
 * @param dayIndex 所属日期下标。
 * @param attrIndex 日内景点下标。
 * @returns 无返回值。
 */
const deleteAttraction = (dayIndex: number, attrIndex: number) => {
  if (!tripPlan.value) return

  const day = tripPlan.value.days[dayIndex]
  const [removed] = day.attractions.splice(attrIndex, 1)
  if (!removed) return
  pendingOperations.value.push({
    type: 'delete',
    poi_id: getAttractionKey(removed)
  })
  message.success('景点已删除')
}

/**
 * 调整同一天景点顺序并记录移动命令。
 *
 * @param dayIndex 所属日期下标。
 * @param attrIndex 日内景点下标。
 * @param direction 向上或向下移动。
 * @returns 无返回值。
 */
const moveAttraction = (dayIndex: number, attrIndex: number, direction: 'up' | 'down') => {
  if (!tripPlan.value) return

  const day = tripPlan.value.days[dayIndex]
  const attractions = day.attractions

  if (direction === 'up' && attrIndex > 0) {
    [attractions[attrIndex], attractions[attrIndex - 1]] = [attractions[attrIndex - 1], attractions[attrIndex]]
    pendingOperations.value.push({
      type: 'move',
      poi_id: getAttractionKey(attractions[attrIndex - 1]),
      target_day_index: dayIndex,
      target_position: attrIndex - 1
    })
  } else if (direction === 'down' && attrIndex < attractions.length - 1) {
    [attractions[attrIndex], attractions[attrIndex + 1]] = [attractions[attrIndex + 1], attractions[attrIndex]]
    pendingOperations.value.push({
      type: 'move',
      poi_id: getAttractionKey(attractions[attrIndex + 1]),
      target_day_index: dayIndex,
      target_position: attrIndex + 1
    })
  }
}

/**
 * 把景点移动到另一日期末尾，并记录跨天移动命令。
 *
 * @param attraction 待移动景点。
 * @param targetDayIndex 目标日期下标。
 * @returns 无返回值。
 */
const moveAttractionToDay = (
  attraction: Attraction,
  targetDayIndex: number
): void => {
  if (!tripPlan.value || isLocked(attraction)) return
  const key = getAttractionKey(attraction)
  const sourceDay = tripPlan.value.days.find((day) =>
    day.attractions.some((item) => getAttractionKey(item) === key)
  )
  const targetDay = tripPlan.value.days[targetDayIndex]
  if (!sourceDay || !targetDay || sourceDay.day_index === targetDayIndex) return
  const sourceIndex = sourceDay.attractions.findIndex(
    (item) => getAttractionKey(item) === key
  )
  sourceDay.attractions.splice(sourceIndex, 1)
  const targetPosition = targetDay.attractions.length
  targetDay.attractions.push(attraction)
  pendingOperations.value.push({
    type: 'move',
    poi_id: key,
    target_day_index: targetDayIndex,
    target_position: targetPosition
  })
}

/**
 * 记录原生拖放开始时的景点标识。
 *
 * @param attraction 被拖动景点。
 * @returns 无返回值。
 */
const startAttractionDrag = (attraction: Attraction): void => {
  draggedPoiId.value = getAttractionKey(attraction)
}

/**
 * 在目标日期和位置完成原生拖放操作。
 *
 * @param targetDayIndex 目标日期下标。
 * @param targetPosition 目标日内位置。
 * @returns 无返回值。
 */
const dropAttraction = (targetDayIndex: number, targetPosition: number): void => {
  if (!tripPlan.value || !draggedPoiId.value) return
  const key = draggedPoiId.value
  const sourceDay = tripPlan.value.days.find((day) =>
    day.attractions.some((item) => getAttractionKey(item) === key)
  )
  const attraction = sourceDay?.attractions.find(
    (item) => getAttractionKey(item) === key
  )
  if (!sourceDay || !attraction || isLocked(attraction)) return
  const sourceIndex = sourceDay.attractions.indexOf(attraction)
  sourceDay.attractions.splice(sourceIndex, 1)
  const target = tripPlan.value.days[targetDayIndex]
  const position = Math.min(targetPosition, target.attractions.length)
  target.attractions.splice(position, 0, attraction)
  pendingOperations.value.push({
    type: 'move',
    poi_id: key,
    target_day_index: targetDayIndex,
    target_position: position
  })
  draggedPoiId.value = null
}

/**
 * 切换景点锁定状态并记录锁定命令。
 *
 * @param attraction 目标景点。
 * @returns 无返回值。
 */
const toggleAttractionLock = (attraction: Attraction): void => {
  const key = getAttractionKey(attraction)
  const locked = !lockedPoiIds.value.includes(key)
  lockedPoiIds.value = locked
    ? [...lockedPoiIds.value, key]
    : lockedPoiIds.value.filter((item) => item !== key)
  pendingOperations.value.push({ type: 'lock', poi_id: key, locked })
}

/**
 * 打开真实 POI 替换对话框。
 *
 * @param attraction 当前待替换景点。
 * @returns 无返回值。
 */
const openReplaceDialog = (attraction: Attraction): void => {
  replacementTarget.value = attraction
  replacementKeyword.value = attraction.name
  replacementCandidates.value = []
  replaceOpen.value = true
}

/**
 * 根据关键词查询同城真实 POI 替换候选。
 *
 * @returns 无返回值。
 */
const searchReplacementCandidates = async (): Promise<void> => {
  if (!tripPlan.value || !replacementKeyword.value.trim()) return
  replacementLoading.value = true
  try {
    replacementCandidates.value = await searchPoiCandidates(
      replacementKeyword.value.trim(),
      tripPlan.value.city
    )
  } catch (error: any) {
    message.error(error.response?.data?.detail || error.message || 'POI 查询失败')
  } finally {
    replacementLoading.value = false
  }
}

/**
 * 选择替换候选并在本地预览，保存时由服务端再次验证 POI。
 *
 * @param candidate 用户选择的真实 POI 候选。
 * @returns 无返回值。
 */
const selectReplacement = (candidate: PoiCandidate): void => {
  if (!tripPlan.value || !replacementTarget.value) return
  const oldKey = getAttractionKey(replacementTarget.value)
  for (const day of tripPlan.value.days) {
    const index = day.attractions.findIndex(
      (item) => getAttractionKey(item) === oldKey
    )
    if (index < 0) continue
    day.attractions[index] = {
      name: candidate.name,
      address: candidate.address,
      location: candidate.location,
      visit_duration: candidate.suggested_visit_duration || 180,
      description: `用户选择替换为${candidate.name}`,
      category: candidate.type,
      rating: candidate.rating,
      photos: candidate.photos,
      image_url: candidate.photos[0] || null,
      poi_id: candidate.id,
      ticket_price: 0
    }
    break
  }
  pendingOperations.value.push({
    type: 'replace',
    poi_id: oldKey,
    replacement_poi_id: candidate.id,
    replacement_keyword: replacementKeyword.value.trim()
  })
  replaceOpen.value = false
  message.success('已选择替换景点，保存后将重算行程')
}

/**
 * 把当前未归档计划保存为服务器资产。
 *
 * @returns 无返回值。
 */
const saveCurrentTrip = async (): Promise<void> => {
  if (!tripPlan.value || saving.value) return
  saving.value = true
  try {
    applySavedTrip(await createSavedTrip(tripPlan.value))
    message.success('行程已保存，可在“我的行程”中恢复')
  } catch (error: any) {
    message.error(error.response?.data?.detail || error.message || '行程保存失败')
  } finally {
    saving.value = false
  }
}

/**
 * 创建只读分享链接并写入系统剪贴板。
 *
 * @returns 无返回值。
 */
const shareCurrentTrip = async (): Promise<void> => {
  if (!savedTripId.value) return
  try {
    const share = await createTripShare(savedTripId.value)
    const shareUrl = new URL(share.share_path, window.location.origin).toString()
    await navigator.clipboard.writeText(shareUrl)
    message.success('只读分享链接已复制，有效期 30 天')
  } catch (error: any) {
    message.error(error.response?.data?.detail || error.message || '分享创建失败')
  }
}

/**
 * 恢复历史快照为新的当前版本。
 *
 * @param versionNumber 需要恢复的历史版本号。
 * @returns 无返回值。
 */
const restoreVersion = async (versionNumber: number): Promise<void> => {
  if (!savedTripId.value || !savedTrip.value || saving.value) return
  saving.value = true
  try {
    const restored = await restoreSavedTrip(
      savedTripId.value,
      savedTrip.value.current_version,
      versionNumber
    )
    applySavedTrip(restored)
    versionsOpen.value = false
    message.success(`已恢复版本 ${versionNumber}`)
  } catch (error: any) {
    message.error(error.response?.data?.detail || error.message || '版本恢复失败')
  } finally {
    saving.value = false
  }
}

/**
 * 下载当前服务器版本的 iCalendar 文件。
 *
 * @returns 无返回值。
 */
const exportCalendar = async (): Promise<void> => {
  if (!savedTripId.value) return
  try {
    await downloadTripCalendar(savedTripId.value)
    message.success('日历文件已生成')
  } catch (error: any) {
    message.error(error.response?.data?.detail || error.message || '日历导出失败')
  }
}

/**
 * 生成高德地图网页导航链接。
 *
 * @param attraction 目的地景点。
 * @returns 带坐标和名称的高德 URI。
 */
const getNavigationUrl = (attraction: Attraction): string => {
  const destination = [
    attraction.location.longitude,
    attraction.location.latitude,
    attraction.name
  ].map(encodeURIComponent).join(',')
  return `https://uri.amap.com/navigation?to=${destination}&mode=transit&coordinate=gaode&callnative=0`
}

const getMealLabel = (type: string): string => {
  const labels: Record<string, string> = {
    breakfast: '早餐',
    lunch: '午餐',
    dinner: '晚餐',
    snack: '小吃'
  }
  return labels[type] || type
}

/**
 * 以固定并发数处理异步任务，避免同时触发过多付费或限流接口。
 */
const runWithConcurrency = async <T,>(
  items: T[],
  limit: number,
  worker: (item: T) => Promise<void>
): Promise<void> => {
  let nextIndex = 0

  /** 持续领取并执行下一个尚未处理的任务。 */
  const consume = async (): Promise<void> => {
    while (nextIndex < items.length) {
      const currentIndex = nextIndex
      nextIndex += 1
      await worker(items[currentIndex])
    }
  }

  const workerCount = Math.min(Math.max(limit, 1), items.length)
  await Promise.all(Array.from({ length: workerCount }, () => consume()))
}

/**
 * 优先复用行程中的高德图片，仅为缺图景点请求后端降级链。
 */
const loadAttractionPhotos = async () => {
  if (!tripPlan.value) return

  const missingAttractions: Attraction[] = []
  const seenNames = new Set<string>()
  tripPlan.value.days.forEach(day => {
    day.attractions.forEach(attraction => {
      const amapPhoto = attraction.image_url || attraction.photos?.find(Boolean)
      if (amapPhoto) {
        attractionPhotos.value[attraction.name] = amapPhoto
        attractionAttributions.value[attraction.name] = {
          provider: 'amap',
          attribution: '高德地图'
        }
      } else if (!seenNames.has(attraction.name)) {
        seenNames.add(attraction.name)
        missingAttractions.push(attraction)
      }
    })
  })

  await runWithConcurrency(missingAttractions, 3, async attraction => {
    try {
      const response = await apiClient.get('/api/poi/photo', {
        params: {
          name: attraction.name,
          city: tripPlan.value?.city || ''
        }
      })
      const imageResult = response.data?.data
      if (imageResult?.photo_url) {
        attractionPhotos.value[attraction.name] = imageResult.photo_url
        attractionAttributions.value[attraction.name] = {
          provider: imageResult.provider,
          attribution: imageResult.attribution,
          attribution_url: imageResult.attribution_url,
          source_url: imageResult.source_url,
          license: imageResult.license
        }
      } else {
        console.warn(
          `景点图片降级为本地占位图: ${attraction.name}`,
          imageResult?.attempts || []
        )
      }
    } catch (error) {
      console.warn(`获取${attraction.name}图片失败，使用本地占位图:`, error)
    }
  })
}

/**
 * 返回景点真实图片；没有可用来源时生成稳定的本地占位图。
 */
const getAttractionImage = (attraction: Attraction, index: number): string => {
  const directPhoto = attraction.image_url || attraction.photos?.find(Boolean)
  if (directPhoto) {
    return directPhoto
  }

  if (attractionPhotos.value[attraction.name]) {
    return attractionPhotos.value[attraction.name]
  }

  // 返回一个纯色占位图(避免跨域问题)
  const colors = [
    { start: '#667eea', end: '#764ba2' },
    { start: '#f093fb', end: '#f5576c' },
    { start: '#4facfe', end: '#00f2fe' },
    { start: '#43e97b', end: '#38f9d7' },
    { start: '#fa709a', end: '#fee140' }
  ]
  const colorIndex = index % colors.length
  const { start, end } = colors[colorIndex]

  // 使用base64编码避免中文问题
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300">
    <defs>
      <linearGradient id="grad${index}" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" style="stop-color:${start};stop-opacity:1" />
        <stop offset="100%" style="stop-color:${end};stop-opacity:1" />
      </linearGradient>
    </defs>
    <rect width="400" height="300" fill="url(#grad${index})"/>
    <text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" font-family="sans-serif" font-size="24" font-weight="bold" fill="white">${attraction.name}</text>
  </svg>`

  return `data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(svg)))}`
}

// 图片加载失败时的处理
const handleImageError = (event: Event) => {
  const img = event.target as HTMLImageElement
  // 使用灰色占位图
  img.src = 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="400" height="300"%3E%3Crect width="400" height="300" fill="%23f0f0f0"/%3E%3Ctext x="50%25" y="50%25" dominant-baseline="middle" text-anchor="middle" font-family="sans-serif" font-size="18" fill="%23999"%3E图片加载失败%3C/text%3E%3C/svg%3E'
}



/**
 * 按需加载截图依赖并导出完整行程图片。
 *
 * @returns 无返回值。
 */
const exportAsImage = async (): Promise<void> => {
  try {
    message.loading({ content: '正在生成图片...', key: 'export', duration: 0 })
    const { default: html2canvas } = await import('html2canvas')

    const element = document.querySelector('.main-content') as HTMLElement
    if (!element) {
      throw new Error('未找到内容元素')
    }

    // 创建一个独立的容器
    const exportContainer = document.createElement('div')
    exportContainer.style.width = element.offsetWidth + 'px'
    exportContainer.style.backgroundColor = '#f5f7fa'
    exportContainer.style.padding = '20px'

    // 复制所有内容
    exportContainer.innerHTML = element.innerHTML

    // 处理地图截图
    const mapContainer = document.getElementById('amap-container')
    if (mapContainer && map) {
      const mapCanvas = mapContainer.querySelector('canvas')
      if (mapCanvas) {
        const mapSnapshot = mapCanvas.toDataURL('image/png')
        const exportMapContainer = exportContainer.querySelector('#amap-container')
        if (exportMapContainer) {
          exportMapContainer.innerHTML = `<img src="${mapSnapshot}" style="width:100%;height:100%;object-fit:cover;" />`
        }
      }
    }

    // 移除所有ant-card类,替换为纯div
    const cards = exportContainer.querySelectorAll('.ant-card')
    cards.forEach((card) => {
      const cardEl = card as HTMLElement
      try {
        cardEl.className = '' // 移除所有类
        cardEl.style.setProperty('background-color', '#ffffff')
        cardEl.style.setProperty('border-radius', '12px')
        cardEl.style.setProperty('box-shadow', '0 4px 12px rgba(0, 0, 0, 0.1)')
        cardEl.style.setProperty('margin-bottom', '20px')
        cardEl.style.setProperty('overflow', 'hidden')
      } catch (err) {
        console.error('设置卡片样式失败:', err)
      }
    })

    // 处理卡片头部
    const cardHeads = exportContainer.querySelectorAll('.ant-card-head')
    cardHeads.forEach((head) => {
      const headEl = head as HTMLElement
      try {
        headEl.style.setProperty('background-color', '#667eea')
        headEl.style.setProperty('color', '#ffffff')
        headEl.style.setProperty('padding', '16px 24px')
        headEl.style.setProperty('font-size', '18px')
        headEl.style.setProperty('font-weight', '600')
      } catch (err) {
        console.error('设置卡片头部样式失败:', err)
      }
    })

    // 处理卡片内容
    const cardBodies = exportContainer.querySelectorAll('.ant-card-body')
    cardBodies.forEach((body) => {
      const bodyEl = body as HTMLElement
      bodyEl.style.setProperty('background-color', '#ffffff')
      bodyEl.style.setProperty('padding', '24px')
    })

    // 处理酒店卡片头部
    const hotelCards = exportContainer.querySelectorAll('.hotel-card')
    hotelCards.forEach((card) => {
      const head = card.querySelector('.ant-card-head') as HTMLElement
      if (head) {
        head.style.setProperty('background-color', '#1976d2')
      }
      (card as HTMLElement).style.setProperty('background-color', '#e3f2fd')
    })

    // 处理天气卡片
    const weatherCards = exportContainer.querySelectorAll('.weather-card')
    weatherCards.forEach((card) => {
      (card as HTMLElement).style.setProperty('background-color', '#e0f7fa')
    })

    // 处理预算总计
    const budgetTotal = exportContainer.querySelector('.budget-total')
    if (budgetTotal) {
      const el = budgetTotal as HTMLElement
      el.style.setProperty('background-color', '#667eea')
      el.style.setProperty('color', '#ffffff')
      el.style.setProperty('padding', '20px')
      el.style.setProperty('border-radius', '12px')
      el.style.setProperty('margin-bottom', '20px')
    }

    // 处理预算项
    const budgetItems = exportContainer.querySelectorAll('.budget-item')
    budgetItems.forEach((item) => {
      const el = item as HTMLElement
      el.style.setProperty('background-color', '#f5f7fa')
      el.style.setProperty('padding', '16px')
      el.style.setProperty('border-radius', '8px')
      el.style.setProperty('margin-bottom', '12px')
    })

    // 添加到body(隐藏)
    exportContainer.style.position = 'absolute'
    exportContainer.style.left = '-9999px'
    document.body.appendChild(exportContainer)

    const canvas = await html2canvas(exportContainer, {
      backgroundColor: '#f5f7fa',
      scale: 2,
      logging: false,
      useCORS: true,
      allowTaint: true
    })

    // 移除容器
    document.body.removeChild(exportContainer)

    // 转换为图片并下载
    const link = document.createElement('a')
    link.download = `旅行计划_${tripPlan.value?.city}_${new Date().getTime()}.png`
    link.href = canvas.toDataURL('image/png')
    link.click()

    message.success({ content: '图片导出成功!', key: 'export' })
  } catch (error: any) {
    console.error('导出图片失败:', error)
    message.error({ content: `导出图片失败: ${error.message}`, key: 'export' })
  }
}

/**
 * 按需加载截图和 PDF 依赖并分页导出行程。
 *
 * @returns 无返回值。
 */
const exportAsPDF = async (): Promise<void> => {
  try {
    message.loading({ content: '正在生成PDF...', key: 'export', duration: 0 })
    const [{ default: html2canvas }, { default: jsPDF }] = await Promise.all([
      import('html2canvas'),
      import('jspdf')
    ])

    const element = document.querySelector('.main-content') as HTMLElement
    if (!element) {
      throw new Error('未找到内容元素')
    }

    // 创建一个独立的容器
    const exportContainer = document.createElement('div')
    exportContainer.style.width = element.offsetWidth + 'px'
    exportContainer.style.backgroundColor = '#f5f7fa'
    exportContainer.style.padding = '20px'

    // 复制所有内容
    exportContainer.innerHTML = element.innerHTML

    // 处理地图截图
    const mapContainer = document.getElementById('amap-container')
    if (mapContainer && map) {
      const mapCanvas = mapContainer.querySelector('canvas')
      if (mapCanvas) {
        const mapSnapshot = mapCanvas.toDataURL('image/png')
        const exportMapContainer = exportContainer.querySelector('#amap-container')
        if (exportMapContainer) {
          exportMapContainer.innerHTML = `<img src="${mapSnapshot}" style="width:100%;height:100%;object-fit:cover;" />`
        }
      }
    }

    // 移除所有ant-card类,替换为纯div
    const cards = exportContainer.querySelectorAll('.ant-card')
    cards.forEach((card) => {
      const cardEl = card as HTMLElement
      try {
        cardEl.className = ''
        cardEl.style.setProperty('background-color', '#ffffff')
        cardEl.style.setProperty('border-radius', '12px')
        cardEl.style.setProperty('box-shadow', '0 4px 12px rgba(0, 0, 0, 0.1)')
        cardEl.style.setProperty('margin-bottom', '20px')
        cardEl.style.setProperty('overflow', 'hidden')
      } catch (err) {
        console.error('设置卡片样式失败:', err)
      }
    })

    // 处理卡片头部
    const cardHeads = exportContainer.querySelectorAll('.ant-card-head')
    cardHeads.forEach((head) => {
      const headEl = head as HTMLElement
      try {
        headEl.style.setProperty('background-color', '#667eea')
        headEl.style.setProperty('color', '#ffffff')
        headEl.style.setProperty('padding', '16px 24px')
        headEl.style.setProperty('font-size', '18px')
        headEl.style.setProperty('font-weight', '600')
      } catch (err) {
        console.error('设置卡片头部样式失败:', err)
      }
    })

    // 处理卡片内容
    const cardBodies = exportContainer.querySelectorAll('.ant-card-body')
    cardBodies.forEach((body) => {
      const bodyEl = body as HTMLElement
      bodyEl.style.setProperty('background-color', '#ffffff')
      bodyEl.style.setProperty('padding', '24px')
    })

    // 处理酒店卡片头部
    const hotelCards = exportContainer.querySelectorAll('.hotel-card')
    hotelCards.forEach((card) => {
      const head = card.querySelector('.ant-card-head') as HTMLElement
      if (head) {
        head.style.setProperty('background-color', '#1976d2')
      }
      (card as HTMLElement).style.setProperty('background-color', '#e3f2fd')
    })

    // 处理天气卡片
    const weatherCards = exportContainer.querySelectorAll('.weather-card')
    weatherCards.forEach((card) => {
      (card as HTMLElement).style.setProperty('background-color', '#e0f7fa')
    })

    // 处理预算总计
    const budgetTotal = exportContainer.querySelector('.budget-total')
    if (budgetTotal) {
      const el = budgetTotal as HTMLElement
      el.style.setProperty('background-color', '#667eea')
      el.style.setProperty('color', '#ffffff')
      el.style.setProperty('padding', '20px')
      el.style.setProperty('border-radius', '12px')
      el.style.setProperty('margin-bottom', '20px')
    }

    // 处理预算项
    const budgetItems = exportContainer.querySelectorAll('.budget-item')
    budgetItems.forEach((item) => {
      const el = item as HTMLElement
      el.style.setProperty('background-color', '#f5f7fa')
      el.style.setProperty('padding', '16px')
      el.style.setProperty('border-radius', '8px')
      el.style.setProperty('margin-bottom', '12px')
    })

    // 添加到body(隐藏)
    exportContainer.style.position = 'absolute'
    exportContainer.style.left = '-9999px'
    document.body.appendChild(exportContainer)

    const canvas = await html2canvas(exportContainer, {
      backgroundColor: '#f5f7fa',
      scale: 2,
      logging: false,
      useCORS: true,
      allowTaint: true
    })

    // 移除容器
    document.body.removeChild(exportContainer)

    const imgData = canvas.toDataURL('image/png')
    const pdf = new jsPDF({
      orientation: 'portrait',
      unit: 'mm',
      format: 'a4'
    })

    const imgWidth = 210 // A4宽度(mm)
    const imgHeight = (canvas.height * imgWidth) / canvas.width

    // 如果内容高度超过一页,分页处理
    let heightLeft = imgHeight
    let position = 0

    pdf.addImage(imgData, 'PNG', 0, position, imgWidth, imgHeight)
    heightLeft -= 297 // A4高度

    while (heightLeft > 0) {
      position = heightLeft - imgHeight
      pdf.addPage()
      pdf.addImage(imgData, 'PNG', 0, position, imgWidth, imgHeight)
      heightLeft -= 297
    }

    pdf.save(`旅行计划_${tripPlan.value?.city}_${new Date().getTime()}.pdf`)

    message.success({ content: 'PDF导出成功!', key: 'export' })
  } catch (error: any) {
    console.error('导出PDF失败:', error)
    message.error({ content: `导出PDF失败: ${error.message}`, key: 'export' })
  }
}

// 截取地图图片
// const captureMapImage = async () => {
//   if (!map) return
//
//   try {
//     // 获取地图容器
//     const mapContainer = document.getElementById('amap-container')
//     if (!mapContainer) return
//
//     // 使用高德地图的截图功能
//     const mapCanvas = mapContainer.querySelector('canvas')
//     if (mapCanvas) {
//       // 创建一个img元素替换地图容器
//       const img = document.createElement('img')
//       img.src = mapCanvas.toDataURL('image/png')
//       img.style.width = '100%'
//       img.style.height = '500px'
//       img.style.objectFit = 'cover'
//       img.id = 'map-snapshot'
//
//       // 隐藏原地图,显示截图
//       mapContainer.style.display = 'none'
//       mapContainer.parentElement?.appendChild(img)
//     }
//   } catch (error) {
//     console.error('截取地图失败:', error)
//   }
// }

// 恢复地图
// const restoreMap = () => {
//   const mapContainer = document.getElementById('amap-container')
//   const snapshot = document.getElementById('map-snapshot')
//
//   if (mapContainer) {
//     mapContainer.style.display = 'block'
//   }
//
//   if (snapshot) {
//     snapshot.remove()
//   }
// }

// 初始化地图
/**
 * 按需加载高德 JS API，并根据当前计划创建地图和路线图层。
 *
 * @returns 无返回值。
 */
const initMap = async (): Promise<void> => {
  const webJsKey = import.meta.env.VITE_AMAP_WEB_JS_KEY?.trim()
  const securityJsCode = import.meta.env.VITE_AMAP_SECURITY_JS_CODE?.trim()
  const serviceHost = import.meta.env.VITE_AMAP_SERVICE_HOST?.trim()

  if (!webJsKey || (!securityJsCode && !serviceHost)) {
    mapStatus.value = 'unconfigured'
    return
  }

  mapStatus.value = 'loading'
  mapError.value = ''

  try {
    const { default: AMapLoader } = await import('@amap/amap-jsapi-loader')
    // 高德安全配置必须在 JS API Loader 首次加载前完成。
    window._AMapSecurityConfig = serviceHost
      ? { serviceHost }
      : { securityJsCode }

    const AMap = await AMapLoader.load({
      key: webJsKey,
      version: '2.0',
      plugins: ['AMap.Marker', 'AMap.Polyline', 'AMap.InfoWindow']
    })

    map?.destroy()

    // 创建地图实例
    map = new AMap.Map('amap-container', {
      zoom: 12,
      center: [116.397128, 39.916527], // 默认中心点(北京)
      viewMode: '3D'
    })

    // 添加景点标记
    addAttractionMarkers(AMap)
    mapStatus.value = 'ready'
  } catch (error: unknown) {
    console.error('地图加载失败:', error)
    mapStatus.value = 'error'
    mapError.value = error instanceof Error
      ? error.message
      : '请检查高德 Web JS Key、安全密钥和域名白名单。'
  }
}

/**
 * 返回图片作者页或原始来源链接。
 *
 * @param attractionName 景点名称。
 * @returns 可访问的署名链接；高德图片没有独立链接时返回空值。
 */
const getAttributionLink = (attractionName: string): string => {
  const attribution = attractionAttributions.value[attractionName]
  return attribution?.attribution_url || attribution?.source_url || '#'
}

/**
 * 构造简短但包含许可信息的图片署名文本。
 *
 * @param attractionName 景点名称。
 * @returns 图片提供方、作者和可选许可组成的文本。
 */
const getAttributionLabel = (attractionName: string): string => {
  const item = attractionAttributions.value[attractionName]
  if (!item) return ''
  const author = item.attribution ? ` · ${item.attribution}` : ''
  const license = item.license ? ` · ${item.license}` : ''
  return `${item.provider}${author}${license}`
}

// 添加景点标记
const addAttractionMarkers = (AMap: any) => {
  if (!tripPlan.value) return

  const markers: any[] = []
  const allAttractions: any[] = []

  // 收集所有景点
  tripPlan.value.days.forEach((day, dayIndex) => {
    day.attractions.forEach((attraction, attrIndex) => {
      if (attraction.location && attraction.location.longitude && attraction.location.latitude) {
        allAttractions.push({
          ...attraction,
          dayIndex,
          attrIndex
        })
      }
    })
  })

  // 创建标记
  allAttractions.forEach((attraction, index) => {
    const marker = new AMap.Marker({
      position: [attraction.location.longitude, attraction.location.latitude],
      title: attraction.name,
      label: {
        content: `<div style="background: #4CAF50; color: white; padding: 4px 8px; border-radius: 4px; font-size: 12px;">${index + 1}</div>`,
        offset: new AMap.Pixel(0, -30)
      }
    })

    // 创建信息窗口
    const infoWindow = new AMap.InfoWindow({
      content: `
        <div style="padding: 10px;">
          <h4 style="margin: 0 0 8px 0;">${attraction.name}</h4>
          <p style="margin: 4px 0;"><strong>地址:</strong> ${attraction.address}</p>
          <p style="margin: 4px 0;"><strong>游览时长:</strong> ${attraction.visit_duration}分钟</p>
          <p style="margin: 4px 0;"><strong>描述:</strong> ${attraction.description}</p>
          <p style="margin: 4px 0; color: #1890ff;"><strong>第${attraction.dayIndex + 1}天 景点${attraction.attrIndex + 1}</strong></p>
        </div>
      `,
      offset: new AMap.Pixel(0, -30)
    })

    // 点击标记显示信息窗口
    marker.on('click', () => {
      infoWindow.open(map, marker.getPosition())
    })

    markers.push(marker)
  })

  // 添加标记到地图
  map.add(markers)

  // 自动调整视野以包含所有标记
  if (allAttractions.length > 0) {
    map.setFitView(markers)
  }

  // 绘制路线
  drawRoutes(AMap)
}

/**
 * 使用后端返回的高德 geometry 绘制逐日真实路线。
 *
 * @param AMap - 高德 JavaScript API 命名空间。
 */
const drawRoutes = (AMap: any) => {
  if (!tripPlan.value) return

  const dayColors = ['#1677ff', '#16a34a', '#ea580c', '#9333ea', '#0891b2']
  tripPlan.value.days.forEach((day, dayIndex) => {
    day.routes?.forEach(route => {
      const path = (route.geometry || [])
        .filter(point => Number.isFinite(point.longitude) && Number.isFinite(point.latitude))
        .map(point => [point.longitude, point.latitude])
      if (path.length < 2) return

      const polyline = new AMap.Polyline({
        path,
        strokeColor: dayColors[dayIndex % dayColors.length],
        strokeWeight: route.status === 'available' ? 5 : 3,
        strokeOpacity: route.status === 'unavailable' ? 0.45 : 0.85,
        strokeStyle: route.status === 'available' ? 'solid' : 'dashed',
        showDir: true
      })

      map.add(polyline)
    })
  })
}
</script>

<style scoped>
.result-container {
  min-height: 100vh;
  background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
  padding: 40px 20px;
}

.page-header {
  max-width: 1200px;
  margin: 0 auto 30px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  animation: fadeInDown 0.6s ease-out;
}

.back-button {
  border-radius: 8px;
  font-weight: 500;
}

/* 内容布局 */
.content-wrapper {
  max-width: 1400px;
  margin: 0 auto;
  display: flex;
  gap: 24px;
}

.side-nav {
  width: 240px;
  flex-shrink: 0;
}

.side-nav :deep(.ant-menu) {
  border-radius: 12px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  background: white;
}

.side-nav :deep(.ant-menu-item) {
  margin: 4px 8px;
  border-radius: 8px;
  transition: all 0.3s ease;
}

.side-nav :deep(.ant-menu-item-selected) {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
}

.side-nav :deep(.ant-menu-item:hover) {
  background: rgba(102, 126, 234, 0.1);
}

.main-content {
  flex: 1;
  min-width: 0;
}

/* 景点图片样式 */
.attraction-image-wrapper {
  position: relative;
  margin-bottom: 12px;
  border-radius: 8px;
  overflow: hidden;
}

.attraction-image {
  width: 100%;
  height: 200px;
  object-fit: cover;
  transition: transform 0.3s ease;
}

.attraction-image-wrapper:hover .attraction-image {
  transform: scale(1.05);
}

.attraction-badge {
  position: absolute;
  top: 12px;
  left: 12px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: bold;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
}

.badge-number {
  font-size: 18px;
}

.price-tag {
  position: absolute;
  top: 12px;
  right: 12px;
  background: rgba(255, 77, 79, 0.9);
  color: white;
  padding: 4px 12px;
  border-radius: 12px;
  font-weight: bold;
  font-size: 14px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
}

.image-attribution {
  position: absolute;
  right: 8px;
  bottom: 8px;
  max-width: calc(100% - 16px);
  padding: 3px 7px;
  overflow: hidden;
  color: #fff;
  font-size: 11px;
  line-height: 1.3;
  text-overflow: ellipsis;
  white-space: nowrap;
  background: rgba(20, 31, 29, 0.78);
  border-radius: 3px;
}

.image-attribution:hover {
  color: #f3d57d;
}

/* 天气卡片样式 */
.weather-card {
  background: linear-gradient(135deg, #e0f7fa 0%, #b2ebf2 100%);
  border: none !important;
  transition: all 0.3s ease;
}

.weather-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 8px 16px rgba(0, 0, 0, 0.15);
}

.weather-date {
  font-size: 16px;
  font-weight: bold;
  color: #00796b;
  margin-bottom: 12px;
  text-align: center;
}

.weather-info-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.weather-icon {
  font-size: 24px;
}

.weather-label {
  font-size: 12px;
  color: #666;
}

.weather-value {
  font-size: 16px;
  font-weight: 600;
  color: #00796b;
}

.weather-wind {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid rgba(0, 121, 107, 0.2);
  text-align: center;
  color: #00796b;
  font-size: 14px;
}

/* 回到顶部按钮 */
.back-top-button {
  width: 50px;
  height: 50px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  font-weight: bold;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
  cursor: pointer;
  transition: all 0.3s ease;
}

.back-top-button:hover {
  transform: scale(1.1);
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.4);
}

/* 酒店卡片样式 */
.hotel-card {
  background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%);
  border: none !important;
}

.hotel-card :deep(.ant-card-head) {
  background: linear-gradient(135deg, #1976d2 0%, #1565c0 100%);
}

.hotel-title {
  color: white !important;
  font-weight: 600;
}

/* 顶部信息区布局 */
.top-info-section {
  display: flex;
  gap: 20px;
  margin-bottom: 20px;
}

.left-info {
  flex: 0 0 400px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.right-map {
  flex: 1;
}

/* 行程概览卡片 */
.overview-card {
  height: fit-content;
}

.overview-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.info-label {
  font-size: 14px;
  font-weight: 600;
  color: #666;
}

.info-value {
  font-size: 15px;
  color: #333;
  line-height: 1.6;
}

/* 预算卡片 */
.budget-card {
  height: fit-content;
}

.budget-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
  margin-bottom: 16px;
}

.budget-item {
  text-align: center;
  padding: 12px;
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  border-radius: 8px;
  border: 1px solid #e8e8e8;
}

.budget-label {
  font-size: 13px;
  color: #666;
  margin-bottom: 8px;
}

.budget-value {
  font-size: 20px;
  font-weight: 700;
  color: #1890ff;
}

.budget-total {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 8px;
  color: white;
}

.budget-warning {
  margin-top: 12px;
}

.source-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 10px;
}

.meal-source {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-left: 8px;
}

.total-label {
  font-size: 16px;
  font-weight: 600;
}

.total-value {
  font-size: 28px;
  font-weight: 700;
}

/* 地图卡片 */
.map-card {
  height: 100%;
  min-height: 500px;
}

.map-card :deep(.ant-card-body) {
  height: calc(100% - 57px);
  padding: 0;
}

.map-stage {
  position: relative;
  width: 100%;
  min-height: 443px;
  height: 100%;
  overflow: hidden;
}

.amap-container {
  width: 100%;
  min-height: 443px;
  height: 100%;
}

.map-state {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: #f7f8fa;
  z-index: 2;
}

/* 每日行程卡片 */
.days-card {
  margin-top: 20px;
}

.day-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.day-title {
  font-size: 18px;
  font-weight: 600;
  color: #333;
}

.day-date {
  font-size: 14px;
  color: #999;
}

.day-info {
  margin-bottom: 20px;
  padding: 16px;
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  border-radius: 8px;
  border: 1px solid #e8e8e8;
}

.route-list {
  display: grid;
  gap: 12px;
  margin-bottom: 20px;
}

.timeline-list {
  display: grid;
  gap: 0;
  margin-bottom: 20px;
  border-left: 2px solid #d9e2ec;
}

.timeline-entry {
  display: grid;
  grid-template-columns: 110px 72px minmax(0, 1fr);
  align-items: start;
  gap: 12px;
  position: relative;
  padding: 10px 12px 10px 18px;
}

.timeline-entry::before {
  position: absolute;
  top: 16px;
  left: -6px;
  width: 10px;
  height: 10px;
  border: 2px solid #fff;
  border-radius: 50%;
  background: #16a34a;
  content: '';
}

.timeline-entry--degraded::before {
  background: #d97706;
}

.timeline-entry--conflict::before {
  background: #dc2626;
}

.timeline-time {
  color: #344054;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  white-space: nowrap;
}

.timeline-content {
  display: grid;
  min-width: 0;
  gap: 3px;
  color: #1f2937;
}

.timeline-content span {
  color: #667085;
  font-size: 13px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.schedule-summary {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 14px;
}

.route-item {
  padding: 14px 16px;
  border: 1px solid #d9dfe8;
  border-left: 4px solid #1677ff;
  border-radius: 6px;
  background: #f8fbff;
}

.route-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.route-title {
  min-width: 0;
  color: #1f2937;
  font-weight: 600;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.route-meta {
  display: flex;
  gap: 16px;
  margin-top: 8px;
  color: #52606d;
  font-size: 13px;
}

.route-description {
  margin: 10px 0 0;
  color: #344054;
  line-height: 1.7;
  overflow-wrap: anywhere;
}

.route-warning {
  margin: 6px 0 0;
  color: #b42318;
  font-size: 13px;
  line-height: 1.5;
}

.info-row {
  display: flex;
  gap: 12px;
  margin-bottom: 8px;
}

.info-row:last-child {
  margin-bottom: 0;
}

.info-row .label {
  font-weight: 600;
  color: #666;
  min-width: 100px;
}

.info-row .value {
  color: #333;
  flex: 1;
}

/* 卡片样式优化 */
:deep(.ant-card) {
  border-radius: 12px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  margin-bottom: 20px;
  transition: all 0.3s ease;
  animation: fadeInUp 0.6s ease-out;
}

:deep(.ant-card:hover) {
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
}

:deep(.ant-card-head) {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white !important;
  border-radius: 12px 12px 0 0;
  font-weight: 600;
}

:deep(.ant-card-head-title) {
  color: white !important;
  font-size: 18px;
}

:deep(.ant-card-head-title span) {
  color: white !important;
}

/* Collapse样式 */
:deep(.ant-collapse) {
  border: none;
  background: transparent;
}

:deep(.ant-collapse-item) {
  margin-bottom: 16px;
  border: 1px solid #e8e8e8;
  border-radius: 12px;
  overflow: hidden;
}

:deep(.ant-collapse-header) {
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  padding: 16px 20px !important;
  font-weight: 600;
}

:deep(.ant-collapse-content) {
  border-top: 1px solid #e8e8e8;
}

:deep(.ant-collapse-content-box) {
  padding: 20px;
}

/* 统计卡片样式 */
:deep(.ant-statistic-title) {
  font-size: 14px;
  color: #666;
  margin-bottom: 8px;
}

:deep(.ant-statistic-content) {
  font-size: 24px;
  font-weight: 600;
  color: #1890ff;
}

/* 景点卡片样式 */
:deep(.ant-list-item) {
  transition: all 0.3s ease;
}

:deep(.ant-list-item:hover) {
  transform: scale(1.02);
}

/* 动画 */
@keyframes fadeInDown {
  from {
    opacity: 0;
    transform: translateY(-20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 响应式设计 */
@media (max-width: 768px) {
  .result-container {
    padding: 20px 10px;
    overflow-x: hidden;
  }

  .page-header {
    flex-direction: column;
    align-items: stretch;
    gap: 16px;
  }

  .page-header :deep(.ant-space) {
    display: flex;
    flex-wrap: wrap;
  }

  .content-wrapper {
    width: 100%;
    flex-direction: column;
    gap: 16px;
  }

  .side-nav {
    width: 100%;
  }

  .side-nav :deep(.ant-affix) {
    position: static !important;
    top: auto !important;
    width: 100% !important;
  }

  .main-content,
  .left-info,
  .right-map {
    width: 100%;
    min-width: 0;
  }

  .top-info-section {
    flex-direction: column;
  }

  .left-info {
    flex-basis: auto;
  }

  .route-heading {
    align-items: flex-start;
    flex-direction: column;
  }

  .timeline-entry {
    grid-template-columns: 1fr;
    gap: 6px;
    padding-left: 18px;
  }

  .map-card {
    min-height: 360px;
  }

  .map-stage,
  .amap-container {
    min-height: 303px;
  }

  .days-card :deep(.ant-card-body),
  :deep(.ant-collapse-content-box) {
    padding: 12px;
  }

  .info-row {
    flex-direction: column;
    gap: 4px;
  }

  .info-row .label {
    min-width: 0;
  }
}
</style>

