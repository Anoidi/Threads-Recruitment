import type { Activity, AgentConfig, Analytics, Candidate, CandidateSignal, CommunityItem, Dashboard, Health, MediaGeneration, MediaGenerationInput, MediaStatus, Post, Settings, SocialAccount, ThreadsAccount, Vacancy } from "../types";

const now = () => new Date().toISOString();
const ago = (hours: number) => new Date(Date.now() - hours * 3_600_000).toISOString();
const future = (hours: number) => new Date(Date.now() + hours * 3_600_000).toISOString();

const initialVacancies: Vacancy[] = [
  { id: "vac-1", agency_id: "agency-1", title: "Працівник складу", description: "Комплектація замовлень у сучасному логістичному центрі.", location: "Краків", salary: "29,50–31,00 PLN netto/год", student_salary: "35,00 PLN netto/год для студентів", currency: "PLN", schedule: "2 зміни, 8–10 годин", contract_type: "Umowa zlecenie", housing: "600 PLN/місяць, кімната на двох", housing_cost: "600 PLN/місяць", transport: "Корпоративний транспорт від зупинок", requirements: "18+, готовність до позмінної роботи", contact: "recruitment@example.test", language: "uk", active: true, created_at: ago(120), updated_at: ago(4) },
  { id: "vac-2", agency_id: "agency-1", title: "Оператор виробництва", description: "Контроль якості та пакування готової продукції.", location: "Катовіце", salary: "28 PLN brutto/год", currency: "PLN", schedule: "Пн–Пт, 3 зміни", contract_type: "Umowa o pracę", housing: "", transport: "Корпоративний транспорт від зупинок", requirements: "Базова польська буде перевагою", contact: "recruitment@example.test", language: "uk", active: true, created_at: ago(240), updated_at: ago(28) },
  { id: "vac-3", agency_id: "agency-1", title: "Працівник пакування", description: "Пакування косметичної продукції.", location: "Вроцлав", salary: "27,50 PLN netto/год", currency: "PLN", schedule: "Денні зміни", contract_type: "Umowa zlecenie", housing: "550 PLN/місяць", housing_cost: "550 PLN/місяць", transport: "", requirements: "Уважність", contact: "recruitment@example.test", language: "uk", active: false, created_at: ago(360), updated_at: ago(70) },
];

const initialPosts: Post[] = [
  { id: "post-1", hook: "Робота у Кракові з житлом", content: "Шукаємо працівників складу у Кракові. Ставка 29,50 PLN/год, для студентів — 35 PLN/год. Є житло та транспорт. Напишіть нам, щоб дізнатися деталі.", content_type: "VACANCY", language: "uk", status: "REVIEW", related_vacancy_id: "vac-1", reasoning_summary: "Фактичні умови вакансії винесено у короткий, зрозумілий формат.", created_at: ago(3), updated_at: ago(2) },
  { id: "post-2", content: "Які документи потрібні для легальної роботи в Польщі? Зібрали короткий чекліст — збережіть, щоб не загубити.", content_type: "DOCUMENTS_INFORMATION", language: "uk", status: "SCHEDULED", scheduled_at: future(21), created_at: ago(18), updated_at: ago(5) },
  { id: "post-3", content: "Три запитання, які варто поставити рекрутеру до виходу на роботу.", content_type: "RECRUITMENT_ADVICE", language: "uk", status: "PUBLISHED", published_at: ago(24), created_at: ago(48), updated_at: ago(24) },
  { id: "post-4", content: "Робота без польської: що реально знайти на старті?", content_type: "FAQ", language: "uk", status: "DRAFT", created_at: ago(7), updated_at: ago(7) },
];

const initialCommunity: CommunityItem[] = [
  { id: "int-1", kind: "REPLY", author: "@marta_krk", message: "Можу вийти вже завтра. Є ще місця на склад у Кракові?", related_post: "post-1", created_at: ago(1), classification: "INTERESTED_CANDIDATE", confidence: .96, risk: "LOW", suggested_reply: "Так, набір ще триває. Напишіть нам у приватні повідомлення — уточнимо досвід і зручну дату старту.", state: "NEEDS_REVIEW", candidate_signal: true },
  { id: "int-2", kind: "MENTION", author: "@oleksii_work", message: "Підкажіть, житло 600 PLN — це за місяць?", related_post: "post-1", created_at: ago(2), classification: "HOUSING_QUESTION", confidence: .91, risk: "LOW", suggested_reply: "Так, вартість житла для цієї вакансії — 600 PLN на місяць.", state: "NEEDS_REVIEW", candidate_signal: false },
  { id: "int-3", kind: "REPLY", author: "@anon", message: "Це схоже на обман. Хто несе юридичну відповідальність?", related_post: "post-1", created_at: ago(5), classification: "LEGAL_OR_HIGH_RISK", confidence: .87, risk: "HIGH", suggested_reply: "Передано відповідальному рекрутеру для ручної перевірки.", state: "NEEDS_REVIEW", candidate_signal: false },
  { id: "int-4", kind: "REPLY", author: "@ivan_pl", message: "Дякую, написав вам.", related_post: "post-3", created_at: ago(22), classification: "GENERAL_DISCUSSION", confidence: .82, risk: "LOW", suggested_reply: "Дякуємо! Наш рекрутер відповість найближчим часом.", state: "ANSWERED", candidate_signal: false },
];

const initialSignals: CandidateSignal[] = [
  { id: "sig-1", interaction_id: "int-1", username: "@marta_krk", intent: "Ready to start immediately", confidence: .96, vacancy: "Працівник складу", status: "NEW", created_at: ago(1) },
  { id: "sig-2", interaction_id: "int-5", username: "@alina.jobs", intent: "Asked about vacancy availability", confidence: .88, vacancy: "Оператор виробництва", status: "REVIEWED", created_at: ago(28) },
];

const initialAgents: AgentConfig[] = [
  { id: "agent-scout", name: "SCOUT", enabled: true, status: "IDLE", route: "free-fast", last_run: ago(2), last_duration_ms: 8420, last_result: "Found 4 discussion opportunities" },
  { id: "agent-strategist", name: "STRATEGIST", enabled: true, status: "IDLE", route: "free-reasoning", last_run: ago(20), last_duration_ms: 12880, last_result: "Prepared 5 content ideas" },
  { id: "agent-writer", name: "WRITER", enabled: true, status: "IDLE", route: "free-default", last_run: ago(3), last_duration_ms: 4280, last_result: "Generated vacancy draft" },
  { id: "agent-community", name: "COMMUNITY", enabled: true, status: "IDLE", route: "free-fast", last_run: ago(.5), last_duration_ms: 2190, last_result: "Classified 6 interactions" },
  { id: "agent-analyst", name: "ANALYST", enabled: false, status: "IDLE", route: "free-reasoning", last_run: ago(72), last_duration_ms: 16200, last_result: "Insufficient sample for recommendation" },
];

const initialSettings: Settings = { default_language: "uk", timezone: "Europe/Warsaw", brand_voice: "Friendly, direct and trustworthy. Never overpromise.", recruiter_identity: "Threads Recruitment Team", cta_templates: "Напишіть нам у приватні повідомлення\nЗалиште + у коментарях", telegram_contact: "@recruitment_demo", phone_contact: "+48 000 000 000", scout_keywords: "робота Польща\nробота Краків\npraca Kraków\nszukam pracy", ignored_keywords: "crypto\nforex\nMLM", max_posts_per_hour: 2, max_replies_per_hour: 8, polling_interval_minutes: 10, analytics_refresh_minutes: 60, reply_scan_interval_minutes: 30, auto_content_plan: true, auto_community_sync: true, auto_post: false, auto_reply: false, auto_external_comment: false, content_plan_language: "uk", posts_per_day: 3 };

let vacancies = structuredClone(initialVacancies);
let posts = structuredClone(initialPosts);
let community = structuredClone(initialCommunity);
let signals = structuredClone(initialSignals);
let candidates: Candidate[] = [];
let agents = structuredClone(initialAgents);
let settings = structuredClone(initialSettings);
let account: ThreadsAccount = { connected: true, username: "@hire_demo_pl", profile_id: "17841••••920", permissions: ["threads_basic", "threads_content_publish", "threads_manage_replies", "threads_manage_insights"], token_status: "MOCK", token_expires_at: future(720), last_sync: ago(.25), last_successful_call: ago(.25), mock_mode: true };
let socialAccounts: SocialAccount[] = [
  { platform: "INSTAGRAM", connected: false, provider_configured: false, permissions: [], token_status: "CONFIG_ERROR" },
  { platform: "TIKTOK", connected: false, provider_configured: false, permissions: [], token_status: "CONFIG_ERROR" },
];
let mediaGenerations: MediaGeneration[] = [];
let agentTimers: ReturnType<typeof setTimeout>[] = [];
let activity: Activity[] = [
  { id: "act-1", action: "Community classified", detail: "6 replies and mentions processed", actor: "Community agent", created_at: ago(.5) },
  { id: "act-2", action: "Post submitted", detail: "Vacancy post sent for review", actor: "Anna", created_at: ago(2) },
  { id: "act-3", action: "Metrics refreshed", detail: "3 published posts updated", actor: "Worker", created_at: ago(4) },
  { id: "act-4", action: "Vacancy updated", detail: "Працівник складу", actor: "Anna", created_at: ago(5) },
];

export function resetMockData() { agentTimers.forEach(clearTimeout); agentTimers = []; vacancies = structuredClone(initialVacancies); posts = structuredClone(initialPosts); community = structuredClone(initialCommunity); signals = structuredClone(initialSignals); candidates = []; agents = structuredClone(initialAgents); settings = structuredClone(initialSettings); mediaGenerations = []; }
const delay = async <T>(value: T): Promise<T> => new Promise(resolve => setTimeout(() => resolve(structuredClone(value)), 80));
const addActivity = (action: string, detail: string, actor = "You") => activity.unshift({ id: crypto.randomUUID(), action, detail, actor, created_at: now() });

export const mockApi = {
  health: (): Promise<Health> => delay({ mode: "mock", checked_at: now(), services: { backend: "ONLINE", mysql: "ONLINE", redis: "ONLINE", hermes: "DEGRADED", litellm: "DEGRADED", threads: "ONLINE", worker: "ONLINE" } }),
  dashboard: (): Promise<Dashboard> => delay({ stats: { posts_today: 1, scheduled_posts: posts.filter(p => p.status === "SCHEDULED").length, drafts: posts.filter(p => ["DRAFT", "AI_GENERATED"].includes(p.status)).length, waiting_approval: posts.filter(p => p.status === "REVIEW").length, new_replies: community.filter(i => i.kind === "REPLY" && i.state === "NEEDS_REVIEW").length, new_mentions: community.filter(i => i.kind === "MENTION" && i.state === "NEEDS_REVIEW").length, candidate_signals: signals.filter(s => s.status === "NEW").length, failed_jobs: 0 }, activity: activity.slice(0, 8), recommendations: [{ id: "rec-1", title: "Answer housing questions", detail: "Two recent interactions ask about housing cost. Consider a focused FAQ post.", confidence: .82 }, { id: "rec-2", title: "Keep claims factual", detail: "Sample size is too small for a reliable best-time recommendation." }] }),
  vacancies: () => delay(vacancies),
  createVacancy: (input: Omit<Vacancy, "id" | "created_at" | "updated_at">) => { const item = { ...input, id: crypto.randomUUID(), created_at: now(), updated_at: now() }; vacancies.unshift(item); addActivity("Vacancy created", item.title); return delay(item); },
  updateVacancy: (id: string, input: Partial<Vacancy>) => { const index = vacancies.findIndex(v => v.id === id); vacancies[index] = { ...vacancies[index], ...input, updated_at: now() }; return delay(vacancies[index]); },
  posts: () => delay(posts),
  createPost: (input: Pick<Post, "content" | "content_type" | "language" | "related_vacancy_id">) => { const item: Post = { ...input, id: crypto.randomUUID(), status: "DRAFT", created_at: now(), updated_at: now() }; posts.unshift(item); addActivity("Post created", item.content.slice(0, 48)); return delay(item); },
  generatePost: (input: { vacancy_id?: string; content_type: Post["content_type"]; language: Post["language"]; tone: string; cta?: string; idea?: string }) => { const vacancy = vacancies.find(v => v.id === input.vacancy_id); const content = vacancy ? `Шукаємо: ${vacancy.title} — ${vacancy.location}. Ставка ${vacancy.salary} ${vacancy.currency}/год. Графік: ${vacancy.schedule}.${vacancy.housing ? ` Житло: ${vacancy.housing_cost ?? "уточнюйте"} ${vacancy.currency}.` : ""}${vacancy.transport ? " Організований транспорт." : ""} ${input.cta || "Напишіть нам, щоб дізнатися деталі."}` : `${input.idea || "Нова корисна тема про роботу в Польщі"}. ${input.cta || "Поділіться своїм досвідом у коментарях."}`; const item: Post = { id: crypto.randomUUID(), hook: vacancy ? `${vacancy.title} у місті ${vacancy.location}` : "Варто знати перед пошуком роботи", content, content_type: input.content_type, language: input.language, status: "AI_GENERATED", related_vacancy_id: input.vacancy_id, reasoning_summary: "Draft uses only supplied vacancy fields and the selected call to action.", created_at: now(), updated_at: now() }; posts.unshift(item); addActivity("AI draft generated", item.hook ?? item.content.slice(0, 40), "Writer agent"); return delay(item); },
  generateProvocative: (vacancyId: string) => { const vacancy = vacancies.find(v => v.id === vacancyId); const base = vacancy ? `${vacancy.title} у ${vacancy.location}. Ставка ${vacancy.salary} ${vacancy.currency}/год. Графік: ${vacancy.schedule}.` : "Вакансія не вибрана."; return delay((["умеренный", "саркастический", "дискуссионный"] as const).map((style, index) => ({ style, content: `${index === 0 ? "Чесно про умови" : index === 1 ? "«Та там просто», кажуть вони" : "Готові сперечатися"}. ${base} Що думаєте?`, character_count: base.length + 40 }))); },
  updatePost: (id: string, input: Partial<Post>) => { const index = posts.findIndex(p => p.id === id); posts[index] = { ...posts[index], ...input, updated_at: now() }; addActivity("Post edited", posts[index].content.slice(0, 42)); return delay(posts[index]); },
  transitionPost: (id: string, action: string, scheduledAt?: string) => { const map: Record<string, Post["status"]> = { submit: "REVIEW", approve: "APPROVED", reject: "DRAFT", schedule: "SCHEDULED", "cancel-schedule": "APPROVED", publish: "PUBLISHED", archive: "ARCHIVED" }; const index = posts.findIndex(p => p.id === id); posts[index] = { ...posts[index], status: map[action], scheduled_at: action === "schedule" ? scheduledAt : action === "cancel-schedule" ? undefined : posts[index].scheduled_at, published_at: action === "publish" ? now() : posts[index].published_at, updated_at: now() }; addActivity(`Post ${action}d`, posts[index].content.slice(0, 42)); return delay(posts[index]); },
  community: () => delay(community),
  communityAction: (id: string, action: string, reply?: string) => { const index = community.findIndex(i => i.id === id); const state: CommunityItem["state"] = action === "ignore" ? "IGNORED" : action === "escalate" ? "ESCALATED" : action === "approve_reply" ? "READY_TO_SEND" : "NEEDS_REVIEW"; community[index] = { ...community[index], state, reply_status: action === "approve_reply" ? "APPROVED" : action === "reject_reply" ? "REJECTED" : community[index].reply_status, suggested_reply: reply ?? community[index].suggested_reply }; addActivity(`Community ${action}`, `${community[index].author}: ${community[index].message.slice(0, 35)}`); return delay(community[index]); },
  sendCommunityReply: async (id: string) => { const index = community.findIndex(i => i.id === id); community[index] = { ...community[index], state: "ANSWERED", reply_status: "SENT" }; addActivity("Reply sent", community[index].author); await delay(undefined); },
  markCandidate: (id: string) => { const item = community.find(i => i.id === id)!; item.candidate_signal = true; if (!signals.some(s => s.interaction_id === id)) signals.unshift({ id: crypto.randomUUID(), interaction_id: id, username: item.author, intent: item.classification, confidence: item.confidence, status: "NEW", created_at: now() }); return delay(item); },
  signals: () => delay(signals),
  updateSignal: (id: string, status: CandidateSignal["status"]) => { const index = signals.findIndex(s => s.id === id); signals[index] = { ...signals[index], status }; return delay(signals[index]); },
  convertSignal: (id: string, payload?: { channel?: string; contact_value?: string }): Promise<Candidate> => { const signal = signals.find(s => s.id === id)!; signal.status = "CONVERTED"; return delay({ id: crypto.randomUUID(), name: signal.username.replace("@", ""), username: signal.username, vacancy: signal.vacancy, status: "NEW", source: "Threads", created_at: now(), channel: payload?.channel ?? "THREADS", contact_value: payload?.contact_value ?? signal.username, confirmed_by: "local-operator", confirmed_at: now(), consent_given: false }); },
  candidates: () => delay(candidates),
  candidateFunnel: () => delay({
    signals_total: signals.length, signals_new: signals.filter(s => s.status === "NEW").length,
    signals_reviewed: signals.filter(s => s.status === "REVIEWED").length,
    signals_ignored: signals.filter(s => s.status === "IGNORED").length,
    signals_converted: signals.filter(s => s.status === "CONVERTED").length,
    candidates_total: candidates.length,
    contacts_confirmed: candidates.filter(c => c.confirmed_at).length,
    consent_recorded: candidates.filter(c => c.consent_given).length,
    stage_new: candidates.filter(c => c.status === "NEW").length,
    stage_contacted: candidates.filter(c => c.status === "CONTACTED").length,
    stage_qualified: candidates.filter(c => c.status === "QUALIFIED").length,
    stage_hired: candidates.filter(c => c.status === "HIRED").length,
    stage_rejected: candidates.filter(c => c.status === "REJECTED").length,
  }),
  changeCandidateStage: (id: string, stage: string, reason?: string): Promise<Candidate> => { const item = candidates.find(c => c.id === id)!; item.status = stage; return delay(item); },
  agents: () => delay(agents),
  toggleAgent: (id: string, enabled: boolean) => { const index = agents.findIndex(a => a.id === id); agents[index] = { ...agents[index], enabled }; addActivity(enabled ? "Agent enabled" : "Agent disabled", agents[index].name); return delay(agents[index]); },
  runAgent: (id: string) => { const index = agents.findIndex(a => a.id === id); agents[index] = { ...agents[index], status: "QUEUED", last_run: now(), last_duration_ms: undefined, last_result: undefined, last_error: undefined }; const queued = structuredClone(agents[index]); agentTimers.push(setTimeout(() => { const current = agents.findIndex(a => a.id === id); if (current < 0) return; agents[current] = { ...agents[current], status: "IDLE", last_run: now(), last_duration_ms: 1640, last_result: `${agents[current].name} mock run completed safely`, last_error: undefined }; addActivity("Agent executed", agents[current].name, "System"); }, 150)); return delay(queued); },
  account: () => delay(account),
  socialAccounts: () => delay(socialAccounts),
  contentPlan: () => delay({ job_id: 1, status: "PENDING" }),
  accountAction: (action: string) => { if (action === "disconnect") account = { connected: false, permissions: [], token_status: "MISSING", last_sync: account.last_sync, mock_mode: true }; else account = { ...account, connected: true, username: "@hire_demo_pl", profile_id: "17841••••920", permissions: ["threads_basic", "threads_content_publish", "threads_manage_replies", "threads_manage_insights"], token_status: "MOCK", last_successful_call: now(), last_error: undefined, mock_mode: true }; return delay(account); },
  socialAction: (platform: SocialAccount["platform"], action: string) => { socialAccounts = socialAccounts.map(item => item.platform === platform ? { ...item, connected: action !== "disconnect", provider_configured: true, token_status: action === "disconnect" ? "MISSING" : "VALID", username: action === "disconnect" ? undefined : `demo_${platform.toLowerCase()}`, last_success_at: now(), last_error: undefined } : item); return delay(socialAccounts.find(item => item.platform === platform)!); },
  mediaStatus: (): Promise<MediaStatus> => delay({ mode: "mock", status: "ONLINE", configured: true, image_workflow_configured: false, video_workflow_configured: false, video_available: false, video_engine: "unavailable", supported_video_aspect_ratios: ["1:1", "16:9", "9:16"], image_checkpoint_configured: false, wan_text_encoder_configured: false, detail: "Безопасный тестовый режим", hardware_note: "Видео создаёт локальный backend через FFmpeg; браузерная имитация его не подменяет." }),
  mediaGenerations: () => delay(mediaGenerations),
  createMediaGeneration: (input: MediaGenerationInput) => { const item: MediaGeneration = { id: Date.now(), ...input, status: input.kind === "VIDEO" ? "FAILED" : "READY", mime_type: input.kind === "IMAGE" ? "image/png" : undefined, byte_size: input.kind === "IMAGE" ? 128 : undefined, sha256: input.kind === "IMAGE" ? "mock" : undefined, error: input.kind === "VIDEO" ? "Видео не имитируется в браузере — используйте локальный backend с FFmpeg." : undefined, created_at: now(), updated_at: now() }; mediaGenerations.unshift(item); addActivity("Media generated", input.prompt.slice(0, 42), "Media worker"); return delay(item); },
  retryMediaGeneration: (id: number) => {
    const index = mediaGenerations.findIndex(item => item.id === id);
    const item = mediaGenerations[index];
    mediaGenerations[index] = item.kind === "VIDEO"
      ? { ...item, status: "FAILED", error: "Видео недоступно в браузерном демо-режиме.", updated_at: now() }
      : { ...item, status: "READY", error: undefined, updated_at: now() };
    return delay(mediaGenerations[index]);
  },
  settings: () => delay(settings),
  saveSettings: (input: Settings) => { settings = { ...input }; addActivity("Settings changed", "Configuration saved"); return delay(settings); },
  analytics: (): Promise<Analytics> => delay({ range: "Последние 30 дней", sample_size: 18, performance: [{ date: "Sep 09", impressions: 620, replies: 12, signals: 2 }, { date: "Sep 14", impressions: 810, replies: 19, signals: 4 }, { date: "Sep 19", impressions: 730, replies: 17, signals: 3 }, { date: "Sep 24", impressions: 1180, replies: 26, signals: 7 }, { date: "Sep 29", impressions: 940, replies: 21, signals: 5 }, { date: "Oct 04", impressions: 1370, replies: 34, signals: 9 }], by_content_type: [{ name: "VACANCY · UK", posts: 7, engagement: 6.2 }, { name: "FAQ · UK", posts: 4, engagement: 5.4 }, { name: "RECRUITMENT_ADVICE · UK", posts: 4, engagement: 4.8 }, { name: "RECRUITER_STORY · UK", posts: 3, engagement: 3.9 }], top_posts: [{ id: "post-3", content: "Три запитання, які варто поставити рекрутеру…", impressions: 1370, replies: 34, signals: 9 }], recommendation: "Лучше всего сейчас работают полезные разборы условий вакансии.", coverage: { published_posts: 18, posts_with_metrics: 18, public_threads: "available", public_topics: 12 }, trends: [{ id: "trend-1", title: "Житло для працівників у Кракові", source: "threads-keyword-search", score: 86, explanation: "Свежий публичный сигнал Threads", discovered_at: new Date().toISOString() }] }),
};
