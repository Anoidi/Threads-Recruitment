import { useEffect, useRef, useState } from "react";
import { Bot, Clock3, MessageSquareReply, PenLine, Play, Radar, Route, Sparkles } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { useAgentRun, useAgents, useAgentToggle } from "../api/hooks";
import { aiUsage as loadAiUsage } from "../api/client";
import type { AgentConfig } from "../types";
import type { AiUsageReport } from "../api/client";
import { Badge, Button, ErrorPanel, LoadingPanel, PageHeader, Toast, Toggle } from "../components/ui";
import { labelFor } from "../i18n";
import { formatWarsawDateTime } from "../utils/datetime";

type GuardReport = { checked_at: string; last_autonomous_scan?: string | null; summary: { total: number; high: number; medium: number; low: number }; findings: { severity: string; title: string; detail: string }[] };

// What each assistant really does. Only two of them call a model at all, and
// the daily content comes from the scheduler, not from these buttons.
const details: Record<string, { description: string; Icon: typeof Bot; model: boolean; route?: string }> = {
  SCOUT: { description: "Собирает публичные обсуждения и новости как сырьё для контент-плана. Без модели: только чтение публичных источников.", Icon: Radar, model: false },
  STRATEGIST: { description: "Предлагает один новый ракурс контента по активным вакансиям, в первую очередь по тем, по которым нет обращений. Обращается к модели.", Icon: Sparkles, model: true, route: "free-reasoning" },
  WRITER: { description: "Создаёт один черновик из тех же данных, что и ежедневный контент-план. Обращается к модели; при отказе подставляет ракурс по подтверждённым условиям.", Icon: PenLine, model: true, route: "free-default" },
  COMMUNITY: { description: "Забирает входящие ответы и упоминания из Threads и раскладывает их по темам. Модель используется только для разбора новых комментариев.", Icon: MessageSquareReply, model: false },
  ANALYST: { description: "Считает честный отчёт по метрикам: сопоставимость выборки, ограничения и выводы. Без модели: расчёт по сохранённым данным.", Icon: Bot, model: false },
  REPLIER: { description: "Пишет конкретный ответ на вопрос кандидата по подтверждённым условиям вакансии вместо общего шаблона. Обращается к модели; отправка — после одобрения.", Icon: PenLine, model: true, route: "free-default" },
  EXPERIMENTER: { description: "Проверяет форматы: в одном запуске одна и та же тема, но разные форматы, чтобы было что сравнивать. Оценивает результат по кандидатам, а не по просмотрам.", Icon: Radar, model: true, route: "free-reasoning" },
};

// A new role on the backend must never break this page.
const fallbackDetail = { description: "Дополнительный помощник системы.", Icon: Bot, model: false };

const formatDate = (value?: string) => value ? formatWarsawDateTime(value) : "Никогда";

const formatDuration = (value?: number) => value === undefined ? "—" : value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${value}ms`;

export function AgentsPage() {
  const query = useAgents();
  const toggle = useAgentToggle();
  const run = useAgentRun();
  const [params, setParams] = useSearchParams();
  const [toast, setToast] = useState<{ message: string; tone?: "success" | "error" } | null>(null);
  const [trackedRun, setTrackedRun] = useState<{ id: string; name: AgentConfig["name"]; seenActive: boolean } | null>(null);
  const [guard, setGuard] = useState<GuardReport | null>(null);
  const [usage, setUsage] = useState<AiUsageReport | null>(null);
  const autoRunHandled = useRef(false);
  useEffect(() => { let alive = true; const load = () => fetch("/api/threads/quality-guard").then(r => r.ok ? r.json() : null).then(v => { if (alive) setGuard(v); }).catch(() => undefined); load(); const timer = window.setInterval(load, 30000); return () => { alive = false; window.clearInterval(timer); }; }, []);
  // AI spend accounting (audit I-4): shown near the agents so it is clear who
  // actually calls a model, what it costs and whether a route fell back.
  useEffect(() => { let alive = true; const load = () => loadAiUsage.load().then(v => { if (alive) setUsage(v); }).catch(() => undefined); load(); const timer = window.setInterval(load, 60000); return () => { alive = false; window.clearInterval(timer); }; }, []);

  const runAgent = (agent: AgentConfig) => {
    run.mutate(agent.id, {
      onSuccess: result => {
        setTrackedRun({ id: agent.id, name: agent.name, seenActive: result.status === "QUEUED" || result.status === "RUNNING" });
        setToast({ message: `Агент ${agent.name} поставлен в очередь.` });
      },
      onError: () => setToast({ message: `Не удалось поставить агента ${agent.name} в очередь.`, tone: "error" }),
    });
  };

  useEffect(() => {
    const requestedName = params.get("run");
    if (!query.data || !requestedName || autoRunHandled.current) return;
    const agent = query.data.find(item => item.name === requestedName.toUpperCase());
    autoRunHandled.current = true;
    setParams({}, { replace: true });
    if (!agent) {
      setToast({ message: `Агент ${requestedName} не найден.`, tone: "error" });
      return;
    }
    if (!agent.enabled) {
      setToast({ message: `Агент ${agent.name} отключён. Включите его перед запуском.`, tone: "error" });
      return;
    }
    runAgent(agent);
  }, [params, query.data, setParams]);

  useEffect(() => {
    if (!trackedRun || !query.data) return;
    const agent = query.data.find(item => item.id === trackedRun.id);
    if (!agent) return;
    if (agent.status === "QUEUED" || agent.status === "RUNNING") {
      if (!trackedRun.seenActive) setTrackedRun(current => current ? { ...current, seenActive: true } : current);
      return;
    }
    if (!trackedRun.seenActive) return;
    if (agent.status === "FAILED") setToast({ message: `Агент ${agent.name} завершился с ошибкой: ${agent.last_error ?? "причина не указана"}`, tone: "error" });
    else if (agent.status === "BLOCKED") setToast({ message: `Агент ${agent.name} приостановлен: сначала разблокируйте Meta Threads.`, tone: "error" });
    else setToast({ message: `Агент ${agent.name} завершил задачу.` });
    setTrackedRun(null);
  }, [query.data, trackedRun]);

  if (query.isLoading) return <LoadingPanel />;
  if (!query.data) return <ErrorPanel message="Не удалось загрузить настройки агентов." />;

  const enabledCount = query.data.filter(agent => agent.enabled).length;
  const runningCount = query.data.filter(agent => agent.status === "QUEUED" || agent.status === "RUNNING").length;

  const toggleAgent = (agent: AgentConfig, enabled: boolean) => {
    toggle.mutate({ id: agent.id, enabled }, {
      onSuccess: () => setToast({ message: `Агент ${agent.name} ${enabled ? "включён" : "отключён"}.` }),
      onError: () => setToast({ message: `Не удалось обновить агента ${agent.name}.`, tone: "error" }),
    });
  };

  return <>
    <PageHeader
      title="ИИ-агенты"
      description="Разовые задачи по требованию. Ежедневный контент создаёт планировщик на странице «Контент», а не эти кнопки."
      actions={<div className="flex gap-2"><span className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-600">{enabledCount}/{query.data.length} включено</span>{runningCount > 0 && <span className="rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs font-semibold text-blue-700">{runningCount} выполняется</span>}</div>}
    />

    <div className="mb-5 flex gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 text-xs leading-relaxed text-slate-600 shadow-panel">
      <Route className="mt-0.5 h-4 w-4 shrink-0 text-moss-600" aria-hidden />
      <p>Ручной запуск выполняет одну задачу и никогда ничего не публикует: результат всегда остаётся черновиком. Агенты выполняются отдельным обработчиком, поэтому запуск не задерживает запланированные публикации. Модель используют только STRATEGIST и WRITER; ANALYST, SCOUT и COMMUNITY считают по сохранённым данным и публичным источникам.</p>
    </div>
    {guard && <section className="mb-5 rounded-xl border border-amber-200 bg-amber-50/60 p-4 shadow-panel"><div className="flex items-center justify-between gap-3"><div><h2 className="font-bold text-slate-800">Quality Guard</h2><p className="mt-1 text-xs text-slate-600">Автономная проверка данных, черновиков и повторяющихся ошибок. Ничего не исправляет и не публикует автоматически.</p></div><span className="text-xs font-semibold text-slate-600">Последняя проверка: {formatDate(guard.last_autonomous_scan || guard.checked_at)}</span></div><div className="mt-3 flex flex-wrap gap-2 text-xs"><Badge value={guard.summary.total ? "DEGRADED" : "READY"}>{guard.summary.total ? `${guard.summary.total} замечаний` : "Нарушений не найдено"}</Badge><span className="rounded-lg bg-white px-2 py-1 text-red-700">high: {guard.summary.high}</span><span className="rounded-lg bg-white px-2 py-1 text-amber-700">medium: {guard.summary.medium}</span><span className="rounded-lg bg-white px-2 py-1 text-slate-600">low: {guard.summary.low}</span></div>{guard.findings.slice(0, 3).map((f, i) => <p key={i} className="mt-2 text-xs text-slate-700"><b>{f.title}:</b> {f.detail}</p>)}</section>}

    {usage && (usage.totals_by_route.length > 0 || usage.recent.length > 0) && (
      <section className="mb-5 rounded-xl border border-slate-200 bg-white p-4 shadow-panel">
        <h2 className="font-bold text-slate-800">Учёт вызовов ИИ</h2>
        <p className="mt-1 text-xs text-slate-600">Токены и время каждого обращения к модели. Mock-режим в журнал не пишет. Запасная модель видна по колонке факта: если для маршрута отвечает другая модель — сработал запасной вариант. Последние вызовы — ниже, ошибки выделены красным.</p>
        {usage.totals_by_route.length > 0 && (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full min-w-[560px] text-left text-xs">
              <thead><tr className="border-b border-slate-100 text-slate-400">
                <th className="py-1.5 pr-3 font-semibold uppercase tracking-wide">Маршрут</th>
                <th className="py-1.5 pr-3 text-right font-semibold uppercase tracking-wide">Вызовы</th>
                <th className="py-1.5 pr-3 text-right font-semibold uppercase tracking-wide">Ошибки</th>
                <th className="py-1.5 pr-3 text-right font-semibold uppercase tracking-wide">Токены запроса</th>
                <th className="py-1.5 pr-3 text-right font-semibold uppercase tracking-wide">Токены ответа</th>
                <th className="py-1.5 text-right font-semibold uppercase tracking-wide">Среднее время</th>
              </tr></thead>
              <tbody className="tabular-nums">
                {usage.totals_by_route.map(t => (
                  <tr key={t.route} className="border-b border-slate-50 text-slate-700">
                    <td className="py-1.5 pr-3 font-mono font-semibold">{t.route}</td>
                    <td className="py-1.5 pr-3 text-right">{t.calls}</td>
                    <td className={`py-1.5 pr-3 text-right ${t.errors > 0 ? "text-red-700" : ""}`}>{t.errors}</td>
                    <td className="py-1.5 pr-3 text-right">{t.prompt_tokens.toLocaleString("ru-RU")}</td>
                    <td className="py-1.5 pr-3 text-right">{t.completion_tokens.toLocaleString("ru-RU")}</td>
                    <td className="py-1.5 text-right">{formatDuration(Math.round(t.avg_duration_ms))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {usage.recent.length > 0 && (
          <ul className="mt-3 divide-y divide-slate-50 text-xs">
            {usage.recent.slice(0, 8).map(call => (
              <li key={call.id} className="flex flex-wrap items-center gap-x-3 gap-y-0.5 py-1.5">
                <span className="text-slate-400">{formatDate(call.created_at)}</span>
                <span className="font-mono font-semibold text-slate-700">{call.route}</span>
                <span className="text-slate-500">{call.resolved_model ? `модель: ${call.resolved_model}` : "модель не сообщена"}</span>
                <span className="tabular-nums text-slate-500">{call.total_tokens.toLocaleString("ru-RU")} токенов</span>
                <span className="tabular-nums text-slate-500">{formatDuration(call.duration_ms)}</span>
                {call.status === "error" && <span className="text-red-700" title={call.error}>ошибка: {call.error.slice(0, 120)}</span>}
              </li>
            ))}
          </ul>
        )}
      </section>
    )}


    <section className="grid gap-4 lg:grid-cols-2">
      {query.data.map(agent => {
        const detail = details[agent.name] ?? fallbackDetail;
        const { description, Icon } = detail;
        const isBusy = (run.isPending && run.variables === agent.id) || (toggle.isPending && toggle.variables?.id === agent.id);
        return <article key={agent.id} className={`panel overflow-hidden transition ${agent.enabled ? "" : "bg-slate-50/60"}`}>
          <div className="flex items-start justify-between gap-4 border-b border-slate-100 p-4 sm:p-5">
            <div className="flex min-w-0 gap-3">
              <div className={`rounded-xl p-2.5 ${agent.enabled ? "bg-moss-50 text-moss-600" : "bg-slate-100 text-slate-400"}`}><Icon className="h-5 w-5" aria-hidden /></div>
              <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h2 className="font-bold tracking-tight">{agent.name}</h2><Badge value={agent.status === "BLOCKED" ? "DEGRADED" : agent.status}>{agent.status === "BLOCKED" ? "ПАУЗА META" : undefined}</Badge></div><p className="mt-1 text-sm leading-relaxed text-slate-500">{description}</p></div>
            </div>
            <Toggle checked={agent.enabled} onChange={enabled => toggleAgent(agent, enabled)} label={`${agent.enabled ? "Отключить" : "Включить"} агента ${agent.name}`} />
          </div>

          <div className="grid gap-px bg-slate-100 sm:grid-cols-3">
            <div className="bg-white px-4 py-3"><p className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Маршрут модели</p><p className="mt-1 truncate font-mono text-xs font-semibold text-slate-700" title={agent.route}>{detail.model ? agent.route : "не используется"}</p></div>
            <div className="bg-white px-4 py-3"><p className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Последний запуск</p><p className="mt-1 text-xs font-semibold text-slate-700">{formatDate(agent.last_run)}</p></div>
            <div className="bg-white px-4 py-3"><p className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">Длительность</p><p className="mt-1 flex items-center gap-1 text-xs font-semibold tabular-nums text-slate-700"><Clock3 className="h-3.5 w-3.5 text-slate-400" />{formatDuration(agent.last_duration_ms)}</p></div>
          </div>

          <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between sm:px-5">
            <div className="min-w-0 text-xs">
              {agent.status === "BLOCKED" ? <p className="break-words text-amber-700" title={agent.last_error}>{agent.last_result ? labelFor(agent.last_result) : "Ожидает разблокировки Meta Threads."}</p> : agent.last_error ? <p className="break-words text-red-700" title={agent.last_error}>{agent.last_error}</p> : agent.status === "QUEUED" ? <p className="text-blue-700">Задача ожидает свободного обработчика.</p> : agent.status === "RUNNING" ? <p className="text-blue-700">Агент выполняет задачу…</p> : <p className="break-words text-slate-500" title={agent.last_result}>{agent.last_result ? labelFor(agent.last_result) : "Результатов запусков пока нет"}</p>}
            </div>
            <Button variant="primary" loading={isBusy} disabled={!agent.enabled || agent.status === "QUEUED" || agent.status === "RUNNING"} onClick={() => runAgent(agent)}>
              <Play className="h-4 w-4" />Запустить
            </Button>
          </div>
        </article>;
      })}
    </section>

    {toast && <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} />}
  </>;
}


