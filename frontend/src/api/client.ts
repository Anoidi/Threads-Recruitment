import type { AgentConfig, Analytics, AttributionReport, AudienceProfile, AutonomyStatus, Candidate, CandidateFunnel, CandidateSignal, CandidateStage, CapabilityReport, CommentEngagementCampaign, CommunityItem, ContactChannel, Dashboard, ExperimentReport, GeneratePostInput, Health, Language, ListResult, MediaGeneration, MediaGenerationInput, MediaStatus, Post, PostInput, ProvocativeVariant, Settings, SocialAccount, ThreadsAccount, Vacancy, VacancyInput } from "../types";
import { clearOperatorToken, getOperatorToken } from "./auth";
import { mockApi } from "./mock";

const API_BASE = import.meta.env.VITE_API_URL ?? "/api";
export interface EditorialRule { id: string; language: string; content_type: string; rule: string; status: string; occurrences: number; examples: { before: string; after: string; reason: string; post_id: number }[]; }
export const editorialMemory = {
  list: (): Promise<EditorialRule[]> => isMockMode ? Promise.resolve([]) : request('/threads/editorial-memory'),
  decide: (id: string, status: string) => request(`/threads/editorial-memory/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify({ status }) }),
};
export const audienceProfile = {
  load: (days = 30): Promise<AudienceProfile> => isMockMode
    ? Promise.resolve({ window_days: days, generated_at: new Date().toISOString(), sample: { interactions: 0, questions: 0, other_comments: 0, signals: 0, published_posts: 0, posts_with_metrics: 0 }, demands: [], coverage: [], gaps: [], served: [], best_performing_theme: null, recommendation: "В тестовом режиме обращения не собираются.", limitations: [] })
    : request(`/threads/audience?days=${days}`),
};
export const formatExperiments = {
  load: (): Promise<{ experiments: ExperimentReport[]; current: ExperimentReport | null }> => isMockMode
    ? Promise.resolve({ experiments: [], current: null })
    : request('/threads/experiments'),
  start: (payload: { subject?: string; formats?: string[]; name?: string; hypothesis?: string }): Promise<ExperimentReport> =>
    isMockMode ? Promise.resolve(formatExperiments.load().then(result => result.current!)) : request('/threads/experiments', body(payload)),
  conclude: (id: string): Promise<ExperimentReport> =>
    isMockMode ? Promise.resolve(formatExperiments.load().then(result => result.current!)) : request(`/threads/experiments/${encodeURIComponent(id)}/conclude`, body({})),
};
export const attribution = {
  load: (params?: { post_id?: number; vacancy_id?: number }): Promise<AttributionReport> => {
    if (isMockMode) {
      return Promise.resolve({
        quality: { signals_total: 0, signals_ignored: 0, signals_without_post: 0, signals_without_vacancy: 0, candidates_total: 0, candidates_without_contact: 0, candidates_without_consent: 0, duplicate_handles: 0, duplicate_candidate_records: 0, posts_with_signals: 0, identity: "threads_username → confirmed contact → record id", limitations: ["В тестовом режиме атрибуция не собирается."] },
        posts: [], vacancies: [], limitations: ["В тестовом режиме атрибуция не собирается."],
      });
    }
    const search = new URLSearchParams();
    if (params?.post_id != null) search.set("post_id", String(params.post_id));
    if (params?.vacancy_id != null) search.set("vacancy_id", String(params.vacancy_id));
    const suffix = search.toString();
    return request(`/threads/attribution${suffix ? `?${suffix}` : ""}`);
  },
};
export const autonomy = {
  load: (): Promise<AutonomyStatus> => isMockMode
    ? Promise.resolve({ mode: "MANUAL", version: 1, emergency_stop: false, limits: {}, reply_topics: [], platform_allowlist: [], updated_by: "local-operator", derived_flags: { auto_post: false, auto_reply: false, auto_external_comment: false }, auto_available: false, auto_blockers: ["В тестовом режиме автономность недоступна."], matrix: [], usage: { published_24h: 0, answered_24h: 0 }, decisions: [] })
    : request('/threads/autonomy'),
  update: (payload: { mode?: string; emergency_stop?: boolean; limits?: Record<string, number>; reply_topics?: string[]; platform_allowlist?: string[]; reason?: string }): Promise<AutonomyStatus> =>
    isMockMode ? autonomy.load() : request('/threads/autonomy', patch(payload)),
  emergencyStop: (enabled: boolean, reason?: string): Promise<AutonomyStatus> =>
    isMockMode ? autonomy.load() : request('/threads/autonomy/emergency-stop', body({ enabled, reason })),
};
export const capabilities = {
  load: (): Promise<CapabilityReport> => isMockMode
    ? Promise.resolve({ connected: false, granted: [], scopes: [], missing: [], missing_required: [], complete: true, summary: "В тестовом режиме проверка разрешений не выполняется.", audience_impact: [], where: "Meta Developer Portal → Threads API → Permissions and Features", how_to_get: [] })
    : request('/threads/capabilities'),
};
export interface AiUsageTotals { route: string; calls: number; prompt_tokens: number; completion_tokens: number; total_tokens: number; avg_duration_ms: number; errors: number; }
export interface AiUsageCall { id: number; route: string; resolved_model: string; prompt_tokens: number; completion_tokens: number; total_tokens: number; duration_ms: number; finish_reason: string; status: string; error: string; created_at: string; }
export interface AiUsageReport { totals_by_route: AiUsageTotals[]; recent: AiUsageCall[]; }
export const aiUsage = {
  load: (): Promise<AiUsageReport> => isMockMode
    ? Promise.resolve({ totals_by_route: [], recent: [] })
    : request('/threads/agents/usage'),
};
export const isMockMode = import.meta.env.MODE === "test" || import.meta.env.VITE_MOCK_MODE === "true";

class ApiError extends Error {
  constructor(message: string, public status: number, public details?: unknown) { super(message); }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // The operator token is kept in sessionStorage only (never in VITE_* or the
  // bundle) and attached to every API call the session makes.
  const token = getOperatorToken();
  const baseHeaders: Record<string, string> = { "Content-Type": "application/json" };
  if (token && !("X-Operator-Token" in (init?.headers ?? {}))) baseHeaders["X-Operator-Token"] = token;
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers: { ...baseHeaders, ...(init?.headers as Record<string, string> | undefined) } });
  if (!response.ok) {
    if (response.status === 401) {
      // The backend rejected this session; force the login gate to re-open.
      clearOperatorToken();
    }
    let details: unknown;
    try { details = await response.json(); } catch { details = undefined; }
    const detail = (details as { detail?: unknown } | undefined)?.detail;
    throw new ApiError(typeof detail === "string" ? detail : `Запрос не выполнен (${response.status})`, response.status, details);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

const unwrap = <T>(value: T[] | ListResult<T>): T[] => Array.isArray(value) ? value : value.items;
const body = (value: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(value) });
const patch = (value: unknown): RequestInit => ({ method: "PATCH", body: JSON.stringify(value) });
const textList = (value: string) => value.split(/\r?\n/).map(item => item.trim()).filter(Boolean);
const asString = (value: unknown) => value == null ? undefined : String(value);

type BackendHealth = { mode: "mock" | "live"; components: Record<string, { status: "ONLINE" | "DEGRADED" | "OFFLINE" }> };
type BackendVacancy = Omit<Vacancy, "id" | "agency_id" | "salary" | "student_salary" | "housing" | "housing_cost" | "transport"> & { id: number; agency_id?: number; salary?: string; student_salary?: string; housing?: string; housing_cost?: string; transport?: string };
type BackendPost = Omit<Post, "id" | "related_vacancy_id"> & { id: number; vacancy_id?: number; version: number };
type BackendInteraction = { id: number; post_id?: number; kind: "REPLY" | "MENTION"; username: string; message: string; classification?: string; confidence?: number; risk: CommunityItem["risk"]; needs_review: boolean; ignored: boolean; answered: boolean; occurred_at: string; reply_status?: CommunityItem["reply_status"]; suggested_reply?: string; candidate_signal?: boolean };
type BackendSignal = { id: number; interaction_id: number; vacancy_id?: number; username: string; intent: string; confidence: number; status: CandidateSignal["status"]; created_at: string };
type BackendAgent = { id: number; role: AgentConfig["name"]; enabled: boolean; model_alias: string; updated_at: string };
type BackendRun = { role: AgentConfig["name"]; status: string; started_at?: string; finished_at?: string; duration_ms?: number; result_summary?: string; error?: string };

const mapVacancy = (v: BackendVacancy): Vacancy => ({ ...v, id: String(v.id), agency_id: String(v.agency_id ?? ""), salary: v.salary ?? "", student_salary: v.student_salary || undefined, housing: v.housing ?? "", housing_cost: v.housing_cost || undefined, transport: v.transport ?? "" });
const vacancyPayload = (v: Partial<VacancyInput | Vacancy>) => ({ ...v, agency_id: v.agency_id ? Number(v.agency_id) : null, salary: v.salary == null ? null : String(v.salary), student_salary: v.student_salary == null ? null : String(v.student_salary), housing: v.housing == null ? null : String(v.housing), housing_cost: v.housing_cost == null ? null : String(v.housing_cost), transport: v.transport == null ? null : String(v.transport) });
const mapPost = (p: BackendPost): Post => ({ ...p, id: String(p.id), related_vacancy_id: asString(p.vacancy_id) });

// The backend returns the editorial evidence; dropping it here hid the
// "comparable sample" and "insufficient data" verdicts in live mode.
export const mapAnalytics = (a: any): Analytics => ({
  range: a.range ?? "все сохранённые данные",
  sample_size: Number(a.sample_size ?? 0),
  performance: a.performance ?? [],
  by_content_type: (a.segments ?? []).map((s: any) => ({
    name: `${s.content_type} · ${(s.language as Language).toUpperCase()}`,
    posts: Number(s.posts ?? 0),
    engagement: Number(s.engagement ?? 0),
  })),
  top_posts: a.top_posts ?? [],
  recommendation: a.recommendation ?? "Продолжайте собирать метрики и сравнивать форматы.",
  coverage: a.coverage ?? { published_posts: 0, posts_with_metrics: 0, posts_metrics_unavailable: 0, public_threads: "no_recent_data", public_topics: 0 },
  trends: a.trends ?? [],
  editorial_analysis: a.editorial_analysis,
  growth_learning: a.growth_learning,
});const postPayload = (p: Partial<PostInput | Post>) => ({ ...p, vacancy_id: p.related_vacancy_id ? Number(p.related_vacancy_id) : null, related_vacancy_id: undefined, version: undefined });
const mapCommunity = (i: BackendInteraction): CommunityItem => ({
  id: String(i.id), kind: i.kind, author: i.username, message: i.message, related_post: asString(i.post_id),
  created_at: i.occurred_at, classification: i.classification ?? "UNCLASSIFIED", confidence: i.confidence ?? 0, risk: i.risk,
  suggested_reply: i.suggested_reply ?? "Дякуємо за повідомлення. Рекрутер перевірить деталі.",
  reply_status: i.reply_status, state: i.ignored ? "IGNORED" : i.answered || i.reply_status === "SENT" ? "ANSWERED" : i.reply_status === "APPROVED" ? "READY_TO_SEND" : i.risk === "HIGH" ? "ESCALATED" : "NEEDS_REVIEW",
  candidate_signal: Boolean(i.candidate_signal),
});

async function loadAgents(): Promise<AgentConfig[]> {
  const configs = await request<BackendAgent[]>("/threads/agents");
  return Promise.all(configs.map(async cfg => {
    const history = await request<BackendRun[]>(`/threads/agents/${cfg.role}/history`);
    const last = history[0];
    const status: AgentConfig["status"] = last?.status === "QUEUED" ? "QUEUED" : last?.status === "RUNNING" ? "RUNNING" : last?.status === "FAILED" ? "FAILED" : last?.status === "BLOCKED" ? "BLOCKED" : "IDLE";
    return { id: cfg.role, name: cfg.role, enabled: cfg.enabled, status, route: cfg.model_alias, last_run: last?.finished_at ?? last?.started_at, last_duration_ms: last?.duration_ms, last_result: last?.result_summary, last_error: last?.error };
  }));
}

export const api = {
  health: async (): Promise<Health> => {
    if (isMockMode) return mockApi.health();
    const value = await request<BackendHealth>("/health");
    return { mode: value.mode, services: Object.fromEntries(Object.entries(value.components).map(([key, component]) => [key, component.status])), checked_at: new Date().toISOString() };
  },
  dashboard: async (): Promise<Dashboard> => {
    if (isMockMode) return mockApi.dashboard();
    const value = await request<any>("/dashboard");
    return { stats: value.stats, automation: value.automation, funnel: value.funnel, content_stock: value.content_stock, activity: (value.recent_activity ?? []).map((item: any) => ({ id: String(item.id), action: item.action, detail: [item.entity_type, item.entity_id].filter(Boolean).join(" #") || "System event", actor: "system", created_at: item.created_at })), recommendations: (value.recommendations ?? []).map((item: string, index: number) => ({ id: String(index), title: "Recommendation", detail: item })) };
  },
  vacancies: async (): Promise<Vacancy[]> => isMockMode ? mockApi.vacancies() : unwrap(await request<BackendVacancy[] | ListResult<BackendVacancy>>("/vacancies")).map(mapVacancy),
  createVacancy: async (value: VacancyInput): Promise<Vacancy> => isMockMode ? mockApi.createVacancy(value) : mapVacancy(await request<BackendVacancy>("/vacancies", body(vacancyPayload(value)))),
  updateVacancy: async (id: string, value: Partial<Vacancy>): Promise<Vacancy> => isMockMode ? mockApi.updateVacancy(id, value) : mapVacancy(await request<BackendVacancy>(`/vacancies/${id}`, patch(vacancyPayload(value)))),
  posts: async (): Promise<Post[]> => isMockMode ? mockApi.posts() : unwrap(await request<BackendPost[] | ListResult<BackendPost>>("/threads/posts")).map(mapPost),
  createPost: async (value: PostInput): Promise<Post> => isMockMode ? mockApi.createPost(value) : mapPost(await request<BackendPost>("/threads/posts", body(postPayload(value)))),
  generatePost: async (value: GeneratePostInput): Promise<Post> => isMockMode ? mockApi.generatePost(value) : mapPost(await request<BackendPost>("/threads/posts/generate", body({ ...value, vacancy_id: value.vacancy_id ? Number(value.vacancy_id) : null }))),
  generateProvocative: async (vacancyId: string, cta?: string): Promise<ProvocativeVariant[]> => isMockMode
    ? mockApi.generateProvocative(vacancyId)
    : request<ProvocativeVariant[]>("/threads/posts/generate-provocative", body({ vacancy_id: Number(vacancyId), cta: cta || null })),
  updatePost: async (id: string, value: Partial<Post>): Promise<Post> => isMockMode ? mockApi.updatePost(id, value) : mapPost(await request<BackendPost>(`/threads/posts/${id}`, patch(postPayload(value)))),
  transitionPost: async (id: string, action: string, scheduled_at?: string, version?: number, note?: string): Promise<Post> => {
    if (isMockMode) return mockApi.transitionPost(id, action, scheduled_at);
    const route = action === "submit" ? "review" : action;
    const payload = route === "schedule" ? { scheduled_at } : route === "approve" || route === "reject" ? { expected_version: version, note: note || null } : {};
    return mapPost(await request<BackendPost>(`/threads/posts/${id}/${route}`, body(payload)));
  },
  community: async (): Promise<CommunityItem[]> => isMockMode ? mockApi.community() : (await request<BackendInteraction[]>("/threads/community")).map(mapCommunity),
  commentEngagement: async (): Promise<CommentEngagementCampaign[]> => isMockMode ? [] : request<CommentEngagementCampaign[]>("/threads/comment-engagement"),
  generateCommentEngagement: async (interaction_id: number, mode: "MONITOR" | "DRAFT" | "AUTO" = "DRAFT"): Promise<CommentEngagementCampaign> => request<CommentEngagementCampaign>("/threads/comment-engagement/generate", body({ interaction_id, mode })),
  approveCommentEngagement: async (campaign_id: number, variant_id: number, content?: string): Promise<CommentEngagementCampaign> => request<CommentEngagementCampaign>(`/threads/comment-engagement/${campaign_id}/approve`, body({ variant_id, content: content || null })),
  setMentionPreference: async (username: string, opted_out = true): Promise<{ username: string; opted_out: boolean }> => request("/threads/comment-engagement/mention-preferences", { method: "PUT", body: JSON.stringify({ username, opted_out }) }),
  communityAction: async (id: string, action: string, reply?: string): Promise<CommunityItem> => isMockMode ? mockApi.communityAction(id, action, reply) : mapCommunity(await request<BackendInteraction>(`/threads/community/${id}/action`, body({ action: action === "reply" ? "approve_reply" : action === "send" ? "approve_reply" : action === "reject_reply" ? "reject_reply" : action, content: reply }))),
  sendCommunityReply: async (id: string): Promise<void> => { if (isMockMode) return mockApi.sendCommunityReply(id); await request(`/threads/community/${id}/reply`, body({})); },
  rejectCommunityReply: async (id: string): Promise<void> => { if (isMockMode) { mockApi.communityAction(id, "reject_reply"); return; } await request(`/threads/community/${id}/action`, body({ action: "reject_reply" })); },
  markCandidate: async (id: string): Promise<CommunityItem> => isMockMode ? mockApi.markCandidate(id) : mapCommunity(await request<BackendInteraction>(`/threads/community/${id}/action`, body({ action: "mark_candidate" }))),
  signals: async (): Promise<CandidateSignal[]> => isMockMode ? mockApi.signals() : (await request<BackendSignal[]>("/threads/candidate-signals")).map(s => ({ id: String(s.id), interaction_id: String(s.interaction_id), username: s.username, intent: s.intent, confidence: s.confidence, vacancy: asString(s.vacancy_id), status: s.status, created_at: s.created_at })),
  updateSignal: async (id: string, status: CandidateSignal["status"]): Promise<CandidateSignal> => {
    if (isMockMode) return mockApi.updateSignal(id, status);
    const s = await request<BackendSignal>(`/threads/candidate-signals/${id}`, patch({ status }));
    return { id: String(s.id), interaction_id: String(s.interaction_id), username: s.username, intent: s.intent, confidence: s.confidence, vacancy: asString(s.vacancy_id), status: s.status, created_at: s.created_at };
  },
  convertSignal: async (id: string, payload: { channel: ContactChannel; contact_value: string; consent_given?: boolean; notes?: string }): Promise<Candidate> => {
    if (isMockMode) return mockApi.convertSignal(id, payload);
    const c = await request<any>(`/threads/candidate-signals/${id}/convert`, body(payload));
    return { id: String(c.id), name: c.name, username: c.threads_username ?? c.name, vacancy: asString(c.vacancy_id), status: c.status, source: c.source_signal_id ? `signal:${c.source_signal_id}` : "manual", created_at: c.created_at, channel: c.channel, contact_value: c.contact_value, confirmed_by: c.confirmed_by, confirmed_at: c.confirmed_at, consent_given: c.consent_given };
  },
  candidates: async (): Promise<Candidate[]> => isMockMode ? mockApi.candidates() : (await request<any[]>("/candidates")).map(c => ({ id: String(c.id), name: c.name, username: c.threads_username ?? c.name, vacancy: asString(c.vacancy_id), status: c.status, source: c.source_signal_id ? `signal:${c.source_signal_id}` : "manual", created_at: c.created_at, channel: c.channel, contact_value: c.contact_value, confirmed_by: c.confirmed_by, confirmed_at: c.confirmed_at, consent_given: c.consent_given })),
  candidateFunnel: async (): Promise<CandidateFunnel> => isMockMode ? mockApi.candidateFunnel() : request<CandidateFunnel>("/candidates/funnel"),
  changeCandidateStage: async (id: string, stage: CandidateStage, reason?: string): Promise<Candidate> => {
    if (isMockMode) return mockApi.changeCandidateStage(id, stage, reason);
    const c = await request<any>(`/candidates/${id}/stage`, patch({ stage, reason }));
    return { id: String(c.id), name: c.name, username: c.threads_username ?? c.name, vacancy: asString(c.vacancy_id), status: c.status, source: c.source_signal_id ? `signal:${c.source_signal_id}` : "manual", created_at: c.created_at, channel: c.channel, contact_value: c.contact_value, confirmed_by: c.confirmed_by, confirmed_at: c.confirmed_at, consent_given: c.consent_given };
  },
  agents: async (): Promise<AgentConfig[]> => isMockMode ? mockApi.agents() : loadAgents(),
  toggleAgent: async (id: string, enabled: boolean): Promise<AgentConfig> => { if (isMockMode) return mockApi.toggleAgent(id, enabled); await request(`/threads/agents/${id}`, patch({ enabled })); return (await loadAgents()).find(a => a.id === id)!; },
  runAgent: async (id: string): Promise<AgentConfig> => { if (isMockMode) return mockApi.runAgent(id); await request(`/threads/agents/${id}/run`, body({})); return (await loadAgents()).find(a => a.id === id)!; },
  contentPlan: async (): Promise<{ job_id: number; status: string }> => {
    if (isMockMode) return mockApi.contentPlan();
    return request("/threads/content-plan", body({ count: 3 }));
  },
  account: async (): Promise<ThreadsAccount> => {
    if (isMockMode) return mockApi.account();
    const a = await request<any>("/threads/account");
    const rawStatus = String(a.token_status ?? "MISSING").toUpperCase();
    let token_status: ThreadsAccount["token_status"] = "MISSING";
    if (a.mock_mode) token_status = "MOCK";
    else if (a.api_status === "BLOCKED" || rawStatus === "BLOCKED") token_status = "BLOCKED";
    else if (rawStatus === "VALID" || rawStatus === "EXPIRED" || rawStatus === "EXPIRING") token_status = rawStatus as ThreadsAccount["token_status"];
    else if (a.connected && rawStatus === "CONFIGURED") token_status = "VALID";
    return {
      connected: a.connected, username: a.username, profile_id: a.profile_id, permissions: a.permissions ?? [], token_status,
      token_expires_at: a.token_expires_at, last_sync: a.last_sync_at, last_successful_call: a.last_success_at,
      last_error: a.last_error, api_status: a.api_status, publishing_ready: a.publishing_ready,
      last_error_code: a.last_error_code == null ? undefined : String(a.last_error_code),
      last_error_subcode: a.last_error_subcode == null ? undefined : String(a.last_error_subcode),
      last_error_trace_id: a.last_error_trace_id, blocked_at: a.blocked_at,
      scheduled_posts_affected: a.scheduled_posts_affected, mock_mode: Boolean(a.mock_mode),
    };
  },
  accountAction: async (input: string | { action: string; payload?: unknown }): Promise<ThreadsAccount> => {
    const action = typeof input === "string" ? input : input.action;
    const payload = typeof input === "string" ? undefined : input.payload;
    if (isMockMode) return mockApi.accountAction(action);
    const result = await request<any>(`/threads/account/${action}`, body(payload));
    if (action === "connect" && result?.status === "redirect_required" && result?.authorize_url) {
      window.location.href = result.authorize_url;
      return api.account();
    }
    return api.account();
  },
  socialAccounts: async (): Promise<SocialAccount[]> => {
    if (isMockMode) return mockApi.socialAccounts();
    return request<SocialAccount[]>("/social/accounts");
  },
  socialAction: async (platform: SocialAccount["platform"], action: "connect" | "test" | "disconnect"): Promise<SocialAccount> => {
    if (isMockMode) return mockApi.socialAction(platform, action);
    if (action === "connect") {
      const result = await request<{ authorize_url?: string }>(`/social/${platform}/oauth/start`, body({}));
      if (result.authorize_url) window.location.href = result.authorize_url;
      return (await api.socialAccounts()).find(item => item.platform === platform)!;
    }
    return request<SocialAccount>(`/social/${platform}/${action}`, body({}));
  },
  mediaStatus: async (): Promise<MediaStatus> => isMockMode ? mockApi.mediaStatus() : request<MediaStatus>("/media/status"),
  mediaGenerations: async (): Promise<MediaGeneration[]> => isMockMode ? mockApi.mediaGenerations() : request<MediaGeneration[]>("/media/generations"),
  createMediaGeneration: async (value: MediaGenerationInput): Promise<MediaGeneration> => isMockMode ? mockApi.createMediaGeneration(value) : request<MediaGeneration>("/media/generations", body(value)),
  retryMediaGeneration: async (id: number): Promise<MediaGeneration> => isMockMode ? mockApi.retryMediaGeneration(id) : request<MediaGeneration>(`/media/generations/${id}/retry`, body({})),
  settings: async (): Promise<Settings> => {
    if (isMockMode) return mockApi.settings();
    const s = await request<any>("/threads/settings");
    return { ...s, cta_templates: (s.cta_templates ?? []).join("\n"), scout_keywords: (s.scout_keywords ?? []).join("\n"), ignored_keywords: (s.ignored_keywords ?? []).join("\n"), polling_interval_minutes: Math.max(1, Math.round(s.polling_interval_seconds / 60)), reply_scan_interval_minutes: Number(s.reply_scan_interval_minutes ?? 30), content_plan_language: (s.content_plan_language ?? "uk") as Language, posts_per_day: Number(s.posts_per_day ?? 3) };
  },
  saveSettings: async (value: Settings): Promise<Settings> => {
    if (isMockMode) return mockApi.saveSettings(value);
    await request("/threads/settings", patch({ ...value, cta_templates: textList(value.cta_templates), scout_keywords: textList(value.scout_keywords), ignored_keywords: textList(value.ignored_keywords), polling_interval_seconds: value.polling_interval_minutes * 60, polling_interval_minutes: undefined, content_plan_language: value.content_plan_language, posts_per_day: value.posts_per_day, confirm_dangerous: value.auto_post || value.auto_reply || value.auto_external_comment }));
    return api.settings();
  },
  analytics: async (): Promise<Analytics> => {
    if (isMockMode) return mockApi.analytics();
    return mapAnalytics(await request<any>("/threads/analytics"));
  },
};
