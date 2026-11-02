export type ServiceState = "ONLINE" | "DEGRADED" | "OFFLINE";
export type PostStatus = "DRAFT" | "AI_GENERATED" | "REVIEW" | "APPROVED" | "SCHEDULED" | "PUBLISHING" | "PUBLISHED" | "FAILED" | "ARCHIVED";
export type ContentType = "VACANCY" | "PROVOCATIVE_RECRUITMENT" | "RECRUITER_STORY" | "CANDIDATE_STORY" | "FAQ" | "EDUCATIONAL" | "SALARY_DISCUSSION" | "HOUSING_DISCUSSION" | "DOCUMENTS_INFORMATION" | "LEGAL_INFORMATION" | "MYTH_BUSTING" | "QUESTION" | "ENGAGEMENT" | "TREND_RESPONSE" | "BEHIND_THE_SCENES" | "RECRUITMENT_ADVICE";
export type Language = "uk" | "ru" | "pl" | "en";
export type Risk = "LOW" | "MEDIUM" | "HIGH";
export type SocialPlatform = "INSTAGRAM" | "TIKTOK";
export type MediaKind = "IMAGE" | "VIDEO";
export type MediaGenerationStatus = "QUEUED" | "RUNNING" | "READY" | "FAILED";

export interface Health { mode: "mock" | "live"; services: Record<string, ServiceState>; checked_at: string; }
export interface DashboardStats { posts_today: number; scheduled_posts: number; drafts: number; ai_generated?: number; waiting_approval: number; new_replies: number; new_mentions: number; candidate_signals: number; failed_jobs: number; }
export interface Activity { id: string; action: string; detail: string; created_at: string; actor: string; }
export interface Recommendation { id: string; title: string; detail: string; confidence?: number; }
export interface ContentStock {
  refill_batches_today: number; refill_daily_limit: number;
  target_days: number; posts_per_day: number; ready_scheduled: number; ready_reserve: number;
  coverage_days: number; next_publication_at: string | null; missing_slots: string[];
  covered_slots: number; stale_scheduled_ids: number[]; rejected_count: number;
  rejected_posts: { id: number; reason: string; repairs: number }[];
  auto_content_plan: boolean; timezone: string;
}
export interface Dashboard { content_stock?: ContentStock; stats: DashboardStats; funnel?: { signals_in_queue: number; contacts_confirmed: number; candidates_qualified: number; candidates_hired: number; consent_recorded: number }; automation?: { content_plan: boolean; auto_publish: boolean; auto_reply: boolean; auto_external_comment: boolean; last_generated_at?: string }; activity: Activity[]; recommendations: Recommendation[]; }

export interface Vacancy {
  id: string; agency_id: string; title: string; description: string; location: string;
  // Keep compensation and housing conditions as source text. Vacancy imports can
  // contain ranges, netto/brutto qualifiers, student rates, and other conditions.
  // Housing and transport stay free text for the same reason: the source says
  // "650 zł/mies., shared room", not just "housing: yes".
  salary: string; student_salary?: string; currency: string; schedule: string; contract_type: string;
  housing: string; housing_cost?: string; transport: string; requirements: string;
  contact: string; language: Language; active: boolean; source?: string; source_message_id?: string; source_url?: string; created_at: string; updated_at: string;
}
export type VacancyInput = Omit<Vacancy, "id" | "created_at" | "updated_at">;

export interface Post {
  feedback_category?: string;
  feedback_reason?: string;
  expected_version?: number;
  id: string; version?: number; content: string; hook?: string; content_type: ContentType; language: Language;
  status: PostStatus; related_vacancy_id?: string; scheduled_at?: string; published_at?: string;
  reasoning_summary?: string; failure_reason?: string;
  model_qa_passed?: boolean; model_qa_notes?: string; model_qa_at?: string; qa_attempts?: number; qa_repairs?: number;
  metrics_status?: "PENDING" | "OK" | "UNAVAILABLE"; metrics_note?: string;
  created_at: string; updated_at: string;
}
export interface PostInput { content: string; content_type: ContentType; language: Language; related_vacancy_id?: string; }
export interface GeneratePostInput { vacancy_id?: string; content_type: ContentType; language: Language; tone: string; cta?: string; idea?: string; }
export interface ProvocativeVariant { style: "умеренный" | "саркастический" | "дискуссионный"; content: string; character_count: number; post_id?: number; }

export interface CommunityItem {
  id: string; kind: "REPLY" | "MENTION"; author: string; message: string; related_post?: string;
  created_at: string; classification: string; confidence: number; risk: Risk; suggested_reply: string;
  state: "NEEDS_REVIEW" | "READY_TO_SEND" | "ANSWERED" | "IGNORED" | "ESCALATED"; candidate_signal: boolean;
  reply_status?: "DRAFT" | "APPROVED" | "REJECTED" | "SENT" | "FAILED";
}
export interface CommentEngagementVariant { id: number; campaign_id: number; style: string; content: string; character_count: number; status: string; selected: boolean; content_hash: string; last_error?: string; }
export interface CommentEngagementCampaign { id: number; source_post_id?: number; source_remote_id?: string; mode: "MONITOR" | "DRAFT" | "AUTO"; status: string; analysis: { interaction_id?: number; username?: string; remote_id?: string; topic?: string; sentiment?: string; [key: string]: unknown }; error?: string; created_at: string; updated_at: string; variants: CommentEngagementVariant[]; }
export type CandidateStage = "NEW" | "CONTACTED" | "QUALIFIED" | "HIRED" | "REJECTED";
export type ContactChannel = "TELEGRAM" | "INSTAGRAM" | "THREADS" | "PHONE" | "OTHER";
export interface Candidate { id: string; name: string; username: string; vacancy?: string; status: string; source: string; created_at: string;
  channel?: string; contact_value?: string; confirmed_by?: string; confirmed_at?: string; consent_given?: boolean; }
export interface CandidateFunnel {
  signals_total: number; signals_new: number; signals_reviewed: number; signals_ignored: number; signals_converted: number;
  candidates_total: number; contacts_confirmed: number; consent_recorded: number;
  stage_new: number; stage_contacted: number; stage_qualified: number; stage_hired: number; stage_rejected: number;
}
export interface CandidateSignal { id: string; interaction_id: string; username: string; intent: string; confidence: number; vacancy?: string; status: "NEW" | "REVIEWED" | "CONTACTED" | "CONVERTED" | "IGNORED"; created_at: string; }
export interface AgentConfig { id: string; name: "SCOUT" | "STRATEGIST" | "WRITER" | "COMMUNITY" | "ANALYST"; enabled: boolean; status: "IDLE" | "QUEUED" | "RUNNING" | "FAILED" | "BLOCKED"; route: string; last_run?: string; last_duration_ms?: number; last_result?: string; last_error?: string; }
export interface ThreadsAccount {
  connected: boolean; username?: string; profile_id?: string; permissions: string[];
  token_status: "VALID" | "EXPIRING" | "EXPIRED" | "MISSING" | "BLOCKED" | "MOCK";
  token_expires_at?: string; last_sync?: string; last_successful_call?: string; last_error?: string;
  api_status?: "UNKNOWN" | "READY" | "BLOCKED" | "AUTH_ERROR" | "DEGRADED";
  publishing_ready?: boolean; last_error_code?: string; last_error_subcode?: string;
  last_error_trace_id?: string; blocked_at?: string; scheduled_posts_affected?: number; mock_mode: boolean;
}
export interface SocialAccount {
  platform: SocialPlatform;
  connected: boolean;
  provider_configured: boolean;
  external_id?: string;
  username?: string;
  permissions: string[];
  token_status: "VALID" | "EXPIRING" | "EXPIRED" | "MISSING" | "CONFIG_ERROR" | "UNKNOWN";
  token_expires_at?: string;
  last_success_at?: string;
  last_error?: string;
}
export interface MediaGeneration {
  id: number; kind: MediaKind; status: MediaGenerationStatus; prompt: string;
  width: number; height: number; seed: number; mime_type?: string; byte_size?: number;
  sha256?: string; duration_ms?: number; asset_url?: string; error?: string;
  created_at: string; updated_at: string;
}
export interface MediaGenerationInput {
  kind: MediaKind; prompt: string; negative_prompt?: string; width: number; height: number; seed: number;
}
export interface MediaStatus {
  mode: "mock" | "live"; status: ServiceState; configured: boolean;
  image_workflow_configured: boolean; video_workflow_configured: boolean;
  video_available: boolean; video_engine: "local_ffmpeg" | "comfyui" | "unavailable";
  supported_video_aspect_ratios: Array<"1:1" | "16:9" | "9:16">;
  image_checkpoint_configured: boolean; wan_text_encoder_configured: boolean;
  detail: string; hardware_note: string;
}
export interface Settings {
  default_language: Language; timezone: string; brand_voice: string; recruiter_identity: string;
  cta_templates: string; telegram_contact: string; phone_contact: string;
  scout_keywords: string; ignored_keywords: string;
  max_posts_per_hour: number; max_replies_per_hour: number;
  polling_interval_minutes: number; analytics_refresh_minutes: number; reply_scan_interval_minutes: number;
  auto_content_plan: boolean; auto_community_sync: boolean; auto_post: boolean; auto_reply: boolean; auto_external_comment: boolean;
  comment_engagement_mode?: "MONITOR" | "DRAFT" | "AUTO"; comment_engagement_interval_seconds?: number;
  mock_mode?: boolean; content_plan_language: Language; posts_per_day: number;
}
export interface CapabilityScope { scope: string; title: string; granted: boolean; required: boolean; unlocks: string; cost: string; }
export interface CapabilityReport {
  connected: boolean; username?: string; granted: string[]; scopes: CapabilityScope[];
  missing: string[]; missing_required: string[]; complete: boolean; reconnect_required?: boolean;
  summary: string; audience_impact: string[]; where: string; how_to_get?: string[];
}
export type AutonomyMode = "MANUAL" | "SEMI_AUTO" | "AUTO";
export interface AutonomyMatrixRow { action: string; label: string; manual: string; semi_auto: string; auto: string; }
export interface PolicyDecision {
  id: number; action: string; decision: "ALLOW" | "REVIEW" | "DENY"; reason: string;
  mode: string; policy_version: number; actor: string;
  subject_type?: string; subject_id?: number; job_id?: number; created_at: string;
}
export interface AutonomyStatus {
  mode: AutonomyMode; version: number; emergency_stop: boolean;
  limits: Record<string, number>; reply_topics: string[]; platform_allowlist: string[]; updated_by: string;
  derived_flags: { auto_post: boolean; auto_reply: boolean; auto_external_comment: boolean };
  auto_available: boolean; auto_blockers: string[];
  matrix: AutonomyMatrixRow[];
  usage: { published_24h: number; answered_24h: number };
  decisions: PolicyDecision[];
}
export interface AudienceDemand { theme: string; label: string; asked: number; from_interactions: number; from_signals: number; last_asked_at?: string; examples: string[]; on_our_posts: number; }
export interface AudienceCoverage { theme: string; label: string; published: number; median_views?: number; median_replies?: number; last_published_at?: string; }
export interface AudienceGap { theme: string; label: string; asked: number; published: number; deficit: number; last_asked_at?: string; }
export interface AudienceProfile {
  window_days: number; generated_at: string;
  sample: { interactions: number; questions: number; other_comments: number; signals: number; published_posts: number; posts_with_metrics: number };
  demands: AudienceDemand[]; coverage: AudienceCoverage[]; gaps: AudienceGap[];
  served: { theme: string; label: string; published: number; asked: number }[];
  best_performing_theme?: { theme: string; label: string; median_views: number; median_replies: number; published: number } | null;
  recommendation: string; limitations: string[];
}
export interface ExperimentVariant {
  key: string; format: string; label: string;
  assigned: number; verified: number; excluded: number;
  published: number; measured: number; signals: number; candidates: number;
  median_views?: number | null;
  unique_candidates?: number; completed_windows?: number[]; window_posts?: number;
  posts: { post_id?: number | null; status?: string | null; verified: boolean; mismatch_reason?: string | null; views?: number | null; signals: number; candidates: number; content?: string | null }[];
}
export interface ExperimentWindowVariant {
  key: string; label: string; published: number; posts: number;
  views_median?: number | null; views_total?: number | null;
  signals: number; candidates: number; unique_people: number;
  candidates_per_post?: number | null; signals_per_post?: number | null;
}
export interface ExperimentWindow { hours: number; label: string; complete: boolean; variants: ExperimentWindowVariant[]; }
export interface ExperimentStatistics {
  min_posts_per_variant: number; clear_lead_ratio: number;
  primary_window?: number | null; closed_windows: number[];
  posts_per_variant: Record<string, number>; shared_candidates: number; sample_note: string;
}
export interface ExperimentReport {
  experiment_id: number; name: string; status: string;
  subject: string; subject_label: string; fact: string; vacancy_id?: number | null;
  metric: string; hypothesis: string;
  started_at?: string | null; concluded_at?: string | null;
  variants: ExperimentVariant[];
  excluded: { post_id?: number | null; variant: string; reason: string }[];
  verdict: string; verdict_text: string;
  winner?: { key: string; label: string; candidates: number; signals: number; published: number; posts?: number; candidates_per_post?: number | null } | null;
  caveats: string[];
  windows?: ExperimentWindow[];
  primary_window?: number | null;
  statistics?: ExperimentStatistics;
}
export interface AttributionQuality {
  signals_total: number; signals_ignored: number; signals_without_post: number; signals_without_vacancy: number;
  candidates_total: number; candidates_without_contact: number; candidates_without_consent: number;
  duplicate_handles: number; duplicate_candidate_records: number; posts_with_signals: number;
  identity: string; limitations: string[];
}
export interface AttributionPost {
  post_id: number; vacancy_id?: number | null; content: string; published_at?: string | null;
  signals: number; unique_people: number; candidates: number;
  contacted: number; qualified: number; hired: number; rejected: number;
}
export interface AttributionVacancy {
  vacancy_id: number; title: string; published_posts: number; signals: number; unique_people: number;
  candidates: number; contacted: number; qualified: number; hired: number; rejected: number;
  invited: number | null;
}
export interface AttributionReport {
  quality: AttributionQuality; posts: AttributionPost[]; vacancies: AttributionVacancy[]; limitations: string[];
}
export interface AnalyticsPoint { date: string; impressions: number; replies: number; signals: number; }
export interface AnalyticsCoverage { published_posts: number; posts_with_metrics: number; posts_metrics_unavailable?: number; public_threads: "available" | "unavailable" | "no_recent_data"; public_topics: number; public_threads_detail?: string; }
export interface TrendSignal { id: string; title: string; source: string; score: number; explanation?: string; url?: string; discovered_at: string; }
export interface EditorialAnalysis { comparable_posts: number; insufficient_data: boolean; cohorts: { content_type: string; language: string; age_bucket: string; posts: number; engagement_rate: number; evidence: string }[]; limitations: string[]; }
export interface Analytics { range: string; sample_size: number; performance: AnalyticsPoint[]; by_content_type: { name: string; posts: number; engagement: number }[]; top_posts: { id: string; content: string; impressions: number; replies: number; signals: number }[]; recommendation: string; coverage: AnalyticsCoverage; trends: TrendSignal[]; editorial_analysis?: EditorialAnalysis; }
export interface ListResult<T> { items: T[]; total: number; }
