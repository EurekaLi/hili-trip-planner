export interface Location {
  longitude: number
  latitude: number
}

export type FactQuality = 'verified' | 'estimated' | 'unknown'

export interface FactSource {
  provider: string
  quality: FactQuality
  retrieved_at?: string | null
  note?: string | null
}

export interface VisitConstraints {
  opening_time?: string | null
  closing_time?: string | null
  closing_day_offset: 0 | 1
  raw_opening_hours?: string | null
  closed_weekdays: number[]
  reservation_required?: boolean | null
  opening_hours_source?: FactSource | null
  reservation_source?: FactSource | null
}

export interface AgentRunMetrics {
  controller: string
  agent_name: string
  succeeded: boolean
  elapsed_seconds: number
  input_chars: number
  output_chars: number
  model?: string | null
  input_tokens?: number | null
  output_tokens?: number | null
  total_tokens?: number | null
  cost_cny?: number | null
  usage_source: 'provider' | 'estimated' | 'unavailable'
}

export interface AgentDecisionTrace {
  controller: string
  status: 'success' | 'degraded'
  decision: Record<string, unknown>
  metrics?: AgentRunMetrics | null
  error_message?: string | null
}

export interface ExternalCallMetric {
  provider: string
  operation: string
  succeeded: boolean
  elapsed_seconds: number
  requested_at: string
  units: number
  cost_cny?: number | null
  error_type?: string | null
}

export interface Attraction {
  name: string
  address: string
  location: Location
  visit_duration: number
  description: string
  category?: string
  rating?: number | null
  photos?: string[]
  poi_id?: string
  image_url?: string | null
  ticket_price?: number
  source?: FactSource | null
  ticket_price_source?: FactSource | null
  visit_constraints?: VisitConstraints | null
}

export interface Meal {
  type: 'breakfast' | 'lunch' | 'dinner' | 'snack'
  name: string
  address?: string | null
  location?: Location | null
  description?: string
  estimated_cost?: number
  poi_id?: string | null
  rating?: number | null
  source?: FactSource | null
  cost_source?: FactSource | null
}

export interface Hotel {
  name: string
  address: string
  location?: Location
  price_range: string
  rating: string
  distance: string
  type: string
  estimated_cost?: number
  poi_id?: string
  source?: FactSource | null
  cost_source?: FactSource | null
}

export interface RouteLeg {
  origin_poi_id: string
  destination_poi_id: string
  origin_name: string
  destination_name: string
  distance: number
  duration: number
  route_type: 'walking' | 'driving' | 'transit'
  description: string
  geometry: Location[]
  status: 'available' | 'estimated' | 'unavailable'
  fallback_reason?: string | null
  cross_region: boolean
  source?: FactSource | null
}

export interface TimelineEntry {
  kind: 'attraction' | 'meal' | 'transit' | 'wait'
  start_time: string
  end_time: string
  title: string
  status: 'scheduled' | 'degraded' | 'conflict'
  poi_id?: string | null
  route_index?: number | null
  note?: string | null
}

export interface Budget {
  total_attractions: number
  total_hotels: number
  total_meals: number
  total_transportation: number
  total: number
  source?: FactSource | null
  unknown_cost_items?: string[]
}

export interface DayPlan {
  date: string
  day_index: number
  description: string
  transportation: string
  accommodation: string
  hotel?: Hotel
  attractions: Attraction[]
  meals: Meal[]
  routes: RouteLeg[]
  timeline: TimelineEntry[]
  schedule_status: 'feasible' | 'degraded' | 'infeasible'
  total_visit_minutes: number
  total_transit_minutes: number
}

export interface WeatherInfo {
  date: string
  day_weather: string
  night_weather: string
  day_temp: number | string
  night_temp: number | string
  wind_direction: string
  wind_power: string
  source?: FactSource | null
}

export interface TripPlan {
  city: string
  start_date: string
  end_date: string
  days: DayPlan[]
  weather_info: WeatherInfo[]
  overall_suggestions: string
  budget?: Budget
  warnings?: string[]
  agent_trace?: AgentDecisionTrace[]
  external_calls?: ExternalCallMetric[]
}

export interface TripFormData {
  city: string
  start_date: string
  end_date: string
  travel_days: number
  transportation: string
  accommodation: string
  preferences: string[]
  free_text_input: string
}

export interface TripPlanResponse {
  success: boolean
  message: string
  data?: TripPlan
}

export type TripJobStatus =
  | 'queued'
  | 'running'
  | 'cancel_requested'
  | 'cancelled'
  | 'succeeded'
  | 'failed'

export type TripJobStage =
  | 'queued'
  | 'loading_grounding_data'
  | 'running_control_agents'
  | 'running_planner'
  | 'validating_plan'
  | 'grounding_plan'
  | 'filling_schedule'
  | 'enriching_weather'
  | 'enriching_routes'
  | 'optimizing_itinerary'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface TripJobSubmission {
  job_id: string
  status: TripJobStatus
  stage: TripJobStage
  progress: number
  message: string
  created: boolean
  attempt_count: number
  max_attempts: number
  saved_trip_id?: string | null
}

export interface TripJobState {
  job_id: string
  status: TripJobStatus
  stage: TripJobStage
  progress: number
  message: string
  result?: TripPlan
  saved_trip_id?: string | null
  error_code?: string
  error_message?: string
  attempt_count: number
  max_attempts: number
  attempt_started_at?: string
  finished_at?: string
  created_at: string
  updated_at: string
  revision: number
}

export interface UserAccount {
  id: string
  email: string
  display_name: string
  is_admin: boolean
  created_at: string
}

export interface AccountSession {
  authenticated: boolean
  user?: UserAccount | null
  claimed_trip_count: number
}

export interface TripSummary {
  id: string
  title: string
  city: string
  start_date: string
  end_date: string
  current_version: number
  created_at: string
  updated_at: string
}

export interface TripVersion {
  version_number: number
  change_type: string
  change_summary: string
  created_at: string
  plan?: TripPlan | null
}

export interface SavedTrip {
  id: string
  title: string
  city: string
  current_version: number
  locked_poi_ids: string[]
  plan: TripPlan
  versions: TripVersion[]
  created_at: string
  updated_at: string
}

export type TripEditOperation =
  | {
      type: 'move'
      poi_id: string
      target_day_index: number
      target_position: number
    }
  | { type: 'delete'; poi_id: string }
  | {
      type: 'update'
      poi_id: string
      visit_duration?: number
      description?: string
    }
  | { type: 'lock'; poi_id: string; locked: boolean }
  | {
      type: 'replace'
      poi_id: string
      replacement_poi_id: string
      replacement_keyword: string
    }

export interface ShareLink {
  share_id: string
  share_token: string
  share_path: string
  trip_id: string
  version_number: number
  expires_at?: string | null
}

export interface SharedTrip {
  trip_id: string
  title: string
  version_number: number
  plan: TripPlan
  created_at: string
}

export interface PoiCandidate {
  id: string
  name: string
  type: string
  address: string
  location: Location
  rating?: number | null
  photos: string[]
  suggested_visit_duration?: number
}

export interface StatusCount {
  name: string
  count: number
}

export interface ProviderOperationMetrics {
  provider: string
  operation: string
  calls: number
  success_rate: number
  average_latency_seconds: number
  p95_latency_seconds: number
  known_cost_cny: number
}

export interface RecentFailure {
  job_id: string
  request_id?: string | null
  city: string
  error_code: string
  error_message: string
  created_at: string
  finished_at?: string | null
}

export interface OperationsOverview {
  generated_at: string
  window_hours: number
  jobs_total: number
  success_rate: number
  p50_job_seconds: number
  p95_job_seconds: number
  known_cost_cny: number
  status_counts: StatusCount[]
  failure_counts: StatusCount[]
  provider_metrics: ProviderOperationMetrics[]
  recent_failures: RecentFailure[]
  feedback_count: number
  average_rating?: number | null
  image_cache_hits: number
  image_cache_misses: number
  image_cache_hit_rate?: number | null
}

export interface ExternalCallTrace {
  provider: string
  operation: string
  succeeded: boolean
  elapsed_seconds: number
  units: number
  cost_cny?: number | null
  error_type?: string | null
  requested_at: string
}

export interface JobTrace {
  job_id: string
  request_id?: string | null
  trip_id?: string | null
  status: string
  city: string
  request: Record<string, unknown>
  budget: Record<string, unknown>
  error_code?: string | null
  error_message?: string | null
  created_at: string
  updated_at: string
  finished_at?: string | null
  external_calls: ExternalCallTrace[]
  agent_trace: Array<Record<string, unknown>>
}

export interface CircuitStatus {
  provider: string
  failures: number
  is_open: boolean
  retry_after_seconds: number
}

export interface PrivacyInformation {
  contact_email?: string | null
  retention_days: number
  collected_data: string[]
  external_providers: Array<{ name: string; purpose: string }>
  user_controls: string[]
}

export interface PublicSecurityConfig {
  turnstile_enabled: boolean
  turnstile_site_key?: string | null
}

export interface ImageAttribution {
  provider: string
  attribution?: string | null
  attribution_url?: string | null
  source_url?: string | null
  license?: string | null
}

