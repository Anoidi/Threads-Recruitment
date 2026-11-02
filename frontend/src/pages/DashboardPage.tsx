import { ArrowRight, Bot, CalendarClock, CheckSquare, FilePlus2, MessageCircleMore, Radar, Sparkles } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useAccount, useDashboard, useHealth } from "../api/hooks";
import { Badge, ErrorPanel, LoadingPanel, PageHeader } from "../components/ui";
import { ContentStock } from "../components/ContentStock";
import { labelFor } from "../i18n";
import { formatWarsawDateTime } from "../utils/datetime";

const labels: Record<string, string> = {
  posts_today: "Публикаций сегодня", scheduled_posts: "Запланировано", drafts: "Черновики AI и ручные", ai_generated: "Создано AI",
  waiting_approval: "Ожидают одобрения", new_replies: "Новые ответы", new_mentions: "Новые упоминания",
  failed_jobs: "Ошибки задач",
  // The confirmed funnel. A raw signal is not a result, so it never shares a
  // card with a hired candidate.
  signals_in_queue: "Сигналы в очереди", contacts_confirmed: "Контакт подтверждён",
  candidates_qualified: "Квалифицированы", candidates_hired: "Наняты",
};
const quick = [
  ["Создать публикацию", "/content?create=1", FilePlus2], ["Создать с помощью ИИ", "/content?generate=1", Sparkles],
  ["Очередь на проверку", "/content?status=REVIEW", CheckSquare], ["Открыть сообщество", "/community", MessageCircleMore],
  ["Запустить разведчика", "/agents?run=SCOUT", Radar], ["Запустить стратега", "/agents?run=STRATEGIST", Bot],
] as const;
const time = (value: string) => formatWarsawDateTime(value, { hour: "2-digit", minute: "2-digit", month: "short", day: "numeric" });

export function DashboardPage() {
  const dashboard = useDashboard();
  const health = useHealth();
  const account = useAccount();
  const navigate = useNavigate();
  if (dashboard.isLoading || health.isLoading) return <LoadingPanel />;
  if (!dashboard.data || !health.data) return <ErrorPanel />;
  return <>
    <PageHeader title="Добрый день, Артём" description="Контент для рекрутинга, интерес кандидатов и состояние системы." actions={<button className="button-primary" onClick={() => navigate("/content?generate=1")}><Sparkles className="h-4 w-4" />Создать публикацию</button>} />
    {account.data && !account.data.mock_mode && (account.data.api_status === "BLOCKED" || account.data.token_status === "BLOCKED") && <div role="alert" className="mb-5 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"><strong>Публикации Threads приостановлены:</strong> Meta блокирует API-доступ.{account.data.last_error_trace_id && <> Trace ID: <span className="font-mono">{account.data.last_error_trace_id}</span>.</>} <button className="font-semibold underline" onClick={() => navigate("/account")}>Открыть диагностику</button></div>}
    <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">{Object.entries(dashboard.data.stats).map(([key, value]) => <div key={key} className="panel px-4 py-3"><div className="text-xs font-semibold uppercase tracking-wide text-slate-500">{labels[key]}</div><div className="mt-1 text-2xl font-bold text-ink">{value}</div></div>)}</section>
    {dashboard.data.content_stock && <ContentStock stock={dashboard.data.content_stock} />}
    {dashboard.data.funnel && <section className="panel mt-4 px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <span className="text-sm font-bold">Воронка кандидатов:</span>
        <span className="text-sm text-slate-600">сигналов в очереди <b className="tabular-nums text-ink">{dashboard.data.funnel.signals_in_queue}</b></span>
        <span className="text-sm text-slate-600">контакт подтверждён <b className="tabular-nums text-ink">{dashboard.data.funnel.contacts_confirmed}</b></span>
        <span className="text-sm text-slate-600">квалифицированы <b className="tabular-nums text-ink">{dashboard.data.funnel.candidates_qualified}</b></span>
        <span className="text-sm text-slate-600">наняты <b className="tabular-nums text-ink">{dashboard.data.funnel.candidates_hired}</b></span>
        <button className="text-sm font-semibold text-moss-700 underline" onClick={() => navigate("/candidates")}>Открыть воронку</button>
      </div>
      <p className="mt-1.5 text-xs text-slate-500">Сигнал — это вопрос. Кандидат — человек с подтверждённым контактом. Каждый переход фиксируется вручную.</p>
    </section>}
    {dashboard.data.automation && <div className="panel mt-4 px-4 py-3 text-sm"><strong>Автоматический режим:</strong> контент-план {dashboard.data.automation.content_plan ? "работает" : "выключен"}; публикация {dashboard.data.automation.auto_publish ? "включена" : "только после ручного одобрения"}. {dashboard.data.automation.last_generated_at ? `Последний AI-черновик: ${time(dashboard.data.automation.last_generated_at)}.` : "AI-черновиков пока нет."}</div>}
    <section className="mt-5 grid gap-5 xl:grid-cols-[1.25fr_.75fr]">
      <div className="space-y-5">
        <div className="panel"><div className="flex items-center justify-between border-b border-slate-100 px-5 py-4"><div><h2 className="font-bold">Быстрые действия</h2><p className="text-xs text-slate-500">Основные рабочие процессы рекрутинга</p></div></div><div className="grid gap-px bg-slate-100 sm:grid-cols-2 lg:grid-cols-3">{quick.map(([label, path, Icon]) => <button key={label} onClick={() => navigate(path)} className="flex items-center justify-between bg-white px-4 py-4 text-left text-sm font-semibold transition hover:bg-slate-50"><span className="flex items-center gap-2.5"><Icon className="h-4 w-4 text-moss-600" />{label}</span><ArrowRight className="h-4 w-4 text-slate-300" /></button>)}</div></div>
        <div className="panel"><div className="border-b border-slate-100 px-5 py-4"><h2 className="font-bold">Последние действия</h2></div><div>{dashboard.data.activity.map(item => <div key={item.id} className="flex gap-3 border-b border-slate-100 px-5 py-3 last:border-0"><div className="mt-1 h-2 w-2 shrink-0 rounded-full bg-moss-500" /><div className="min-w-0 flex-1"><div className="flex flex-wrap items-baseline justify-between gap-2"><p className="text-sm font-semibold">{labelFor(item.action)}</p><time className="text-xs text-slate-600">{time(item.created_at)}</time></div><p className="truncate text-xs text-slate-500">{labelFor(item.detail)} · {labelFor(item.actor)}</p></div></div>)}</div></div>
      </div>
      <div className="space-y-5">
        <div className="panel"><div className="border-b border-slate-100 px-5 py-4"><h2 className="font-bold">Состояние системы</h2></div><div className="grid grid-cols-2 gap-x-4 px-5 py-2">{Object.entries(health.data.services).map(([service, status]) => <div key={service} className="flex items-center justify-between border-b border-slate-100 py-3"><span className="text-sm">{labelFor(service)}</span><Badge value={status} /></div>)}</div></div>
        <div className="panel"><div className="flex items-center gap-2 border-b border-slate-100 px-5 py-4"><CalendarClock className="h-4 w-4 text-clay-500" /><h2 className="font-bold">Рекомендации ИИ</h2></div><div className="divide-y divide-slate-100">{dashboard.data.recommendations.map(item => <div key={item.id} className="p-4"><p className="text-sm font-semibold">{labelFor(item.title)}</p><p className="mt-1 text-xs leading-relaxed text-slate-500">{labelFor(item.detail)}</p>{item.confidence && <p className="mt-2 text-[11px] font-semibold uppercase text-slate-400">Уверенность {Math.round(item.confidence * 100)}%</p>}</div>)}</div></div>
      </div>
    </section>
  </>;
}

