import { BarChart3, Database, Eye, FlaskConical, Lightbulb, MessageCircle, Radar, Target, Users } from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useAnalytics } from "../api/hooks";
import { audienceProfile, attribution, formatExperiments } from "../api/client";
import { ErrorPanel, LoadingPanel, PageHeader } from "../components/ui";
import { labelFor } from "../i18n";
import { useQuery } from "@tanstack/react-query";

const compactNumber = new Intl.NumberFormat("ru-RU", { notation: "compact", maximumFractionDigits: 1 });
const decimal = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 1 });

export function AnalyticsPage() {
  const query = useAnalytics();
  const audience = useQuery({ queryKey: ["audience-profile"], queryFn: () => audienceProfile.load(30) });
  const experiment = useQuery({ queryKey: ["format-experiments"], queryFn: () => formatExperiments.load() });
  const attributionData = useQuery({ queryKey: ["attribution"], queryFn: () => attribution.load() });

  if (query.isLoading) return <LoadingPanel />;
  if (!query.data) return <ErrorPanel message="Не удалось загрузить аналитику." />;

  const data = query.data;
  const totals = data.performance.reduce(
    (result, point) => ({
      impressions: result.impressions + point.impressions,
      replies: result.replies + point.replies,
      signals: result.signals + point.signals,
    }),
    { impressions: 0, replies: 0, signals: 0 },
  );
  const replyRate = totals.impressions ? (totals.replies / totals.impressions) * 100 : 0;

  const metrics = [
    { label: "Показы", value: data.coverage.posts_with_metrics ? compactNumber.format(totals.impressions) : "Нет данных", Icon: Eye, tone: "text-blue-600 bg-blue-50" },
    { label: "Ответы", value: data.coverage.posts_with_metrics ? compactNumber.format(totals.replies) : "Нет данных", Icon: MessageCircle, tone: "text-violet-600 bg-violet-50" },
    { label: "Сигналы кандидатов", value: compactNumber.format(totals.signals), Icon: Target, tone: "text-clay-600 bg-clay-50" },
    { label: "Доля ответов", value: totals.impressions ? `${decimal.format(replyRate)}%` : "Нет данных", Icon: BarChart3, tone: "text-moss-600 bg-moss-50" },
  ];
  const radarStatus = {
    available: "Публичный поиск Threads работает",
    unavailable: "Публичный поиск Threads недоступен",
    no_recent_data: "Нет свежего подтверждения работы публичного поиска",
  }[data.coverage.public_threads];

  return <>
    <PageHeader
      title="Аналитика"
      description="Результаты контента и интерес кандидатов из подключённого аккаунта Threads."
      actions={<div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold text-slate-700">{labelFor(data.range)}</div>}
    />

    <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {metrics.map(({ label, value, Icon, tone }) => <div key={label} className="panel flex items-center gap-3 px-4 py-3">
        <div className={`rounded-lg p-2.5 ${tone}`}><Icon className="h-5 w-5" aria-hidden /></div>
        <div><p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</p><p className="mt-0.5 text-2xl font-bold tabular-nums text-ink">{value}</p></div>
      </div>)}
    </section>

    <section className="panel mt-5 p-5">
      <div className="flex items-center gap-2"><Users className="h-5 w-5 text-clay-600" aria-hidden /><h2 className="font-bold">Аудитория: что спрашивают и на чём мы не ответили</h2></div>
      {!audience.data ? <p className="mt-4 text-sm text-slate-500">Профиль аудитории собирается из ответов и упоминаний в вашем аккаунте.</p> : <>
        <p className="mt-1 text-xs text-slate-500">Окно {audience.data.window_days} дней · вопросов {audience.data.sample.questions} · прочих комментариев {audience.data.sample.other_comments} · сигналов {audience.data.sample.signals}</p>
        <p className="mt-3 rounded-lg bg-moss-50 px-3 py-2 text-sm font-medium text-moss-800">{audience.data.recommendation}</p>
        {audience.data.gaps.length > 0 && <div className="mt-4">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Темы без ответа — приоритет контент-плана</p>
          <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {audience.data.gaps.map(gap => <div key={gap.theme} className="rounded-lg border border-amber-200 bg-amber-50/60 p-3">
              <p className="text-sm font-semibold text-slate-800">{gap.label}</p>
              <p className="mt-1 text-xs text-slate-600">спросили {gap.asked} раз · ответили {gap.published} · разрыв {gap.deficit}</p>
            </div>)}
          </div>
        </div>}
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Спрос аудитории</p>
            <div className="mt-2 space-y-1.5">
              {audience.data.demands.slice(0, 6).map(item => <div key={item.theme} className="flex items-baseline justify-between gap-3 rounded-lg bg-slate-50 px-3 py-2">
                <span className="text-sm text-slate-700">{item.label}</span>
                <span className="text-xs font-bold tabular-nums text-slate-600">{item.asked}</span>
              </div>)}
              {audience.data.demands.length === 0 && <p className="text-sm text-slate-500">Вопросов пока не собрано.</p>}
            </div>
          </div>
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Что отвечено</p>
            <div className="mt-2 space-y-1.5">
              {audience.data.coverage.slice(0, 6).map(item => <div key={item.theme} className="flex items-baseline justify-between gap-3 rounded-lg bg-slate-50 px-3 py-2">
                <span className="text-sm text-slate-700">{item.label}</span>
                <span className="text-xs font-bold tabular-nums text-slate-600">{item.published}{item.median_views != null ? ` · медиана ${item.median_views}` : ""}</span>
              </div>)}
              {audience.data.coverage.length === 0 && <p className="text-sm text-slate-500">Editorial-публикаций пока нет.</p>}
            </div>
          </div>
        </div>
        {audience.data.limitations.length > 0 && <ul className="mt-4 list-disc space-y-1 pl-4 text-xs text-slate-500">{audience.data.limitations.map(item => <li key={item}>{item}</li>)}</ul>}
      </>}
    </section>

    <section className="panel mt-5 p-5">
      <div className="flex items-center gap-2"><FlaskConical className="h-5 w-5 text-violet-600" aria-hidden /><h2 className="font-bold">Эксперимент форматов: одна и та же фактура, разная подача</h2></div>
      {!experiment.data?.current ? <p className="mt-4 text-sm text-slate-500">Эксперимент ещё не запущен. Его запускает агент EXPERIMENTER: он берёт один подтверждённый факт из вакансии и делает из него посты в разных форматах.</p> : <>
        <p className="mt-1 text-xs text-slate-500">Факт, который не меняется: <span className="font-semibold text-slate-700">{experiment.data.current.fact}</span> · тема {experiment.data.current.subject_label} · статус {experiment.data.current.status === "RUNNING" ? "идёт" : "завершён"}</p>
        <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 text-sm font-medium text-slate-700">{experiment.data.current.verdict_text}</p>
        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[540px] text-left text-sm">
            <thead><tr className="text-xs uppercase tracking-wide text-slate-400">
              <th className="py-2 pr-3 font-semibold">Формат</th>
              <th className="py-2 pr-3 font-semibold">Постов</th>
              <th className="py-2 pr-3 font-semibold">Опубликовано</th>
              <th className="py-2 pr-3 font-semibold">Кандидатов</th>
              <th className="py-2 pr-3 font-semibold">Сигналов</th>
              <th className="py-2 font-semibold">Просмотры</th>
            </tr></thead>
            <tbody>
              {experiment.data.current.variants.map(variant => <tr key={variant.key} className="border-t border-slate-100">
                <td className="py-2 pr-3 font-semibold text-slate-700">{variant.label}</td>
                <td className="py-2 pr-3 tabular-nums text-slate-600">{variant.assigned}{variant.excluded > 0 ? ` (−${variant.excluded})` : ""}</td>
                <td className="py-2 pr-3 tabular-nums text-slate-600">{variant.published}</td>
                <td className="py-2 pr-3 tabular-nums font-bold text-slate-700">{variant.candidates}</td>
                <td className="py-2 pr-3 tabular-nums text-slate-600">{variant.signals}</td>
                <td className="py-2 tabular-nums text-slate-600">{variant.median_views ?? "—"}</td>
              </tr>)}
            </tbody>
          </table>
        </div>
        {(experiment.data.current.windows ?? []).length > 0 && <div className="mt-5">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Сравнение по равным окнам наблюдения</p>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full min-w-[520px] text-left text-sm">
              <thead><tr className="text-xs uppercase tracking-wide text-slate-400">
                <th className="py-2 pr-3 font-semibold">Окно</th>
                {(experiment.data.current.windows ?? [])[0]?.variants.map(variant => <th key={variant.key} className="py-2 pr-3 font-semibold">{variant.label}</th>)}
                <th className="py-2 font-semibold">Состояние</th>
              </tr></thead>
              <tbody>
                {(experiment.data.current.windows ?? []).map(item => <tr key={item.hours} className="border-t border-slate-100">
                  <td className="py-2 pr-3 font-semibold text-slate-700">{item.label}</td>
                  {item.variants.map(variant => <td key={variant.key} className="py-2 pr-3 tabular-nums text-slate-600">
                    {variant.posts > 0 ? `${variant.candidates} канд. / ${variant.posts} пост.` : "нет данных"}
                  </td>)}
                  <td className="py-2 text-xs font-semibold">
                    <span className={item.complete ? "text-moss-700" : "text-amber-700"}>{item.complete ? "закрыто у всех" : "не закрыто"}</span>
                  </td>
                </tr>)}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-xs text-slate-500">{experiment.data.current.statistics?.sample_note} Пост без замера после 24/48/72 ч в это окно не входит, поэтому свежий пост не проигрывает старому.</p>
        </div>}
        {experiment.data.current.excluded.length > 0 && <p className="mt-3 text-xs text-amber-700">Из сравнения исключено постов: {experiment.data.current.excluded.length}. Пост, который не соответствует своему формату или потерял подтверждённый факт, не зачтён как проигравший.</p>}
        <ul className="mt-3 list-disc space-y-1 pl-4 text-xs text-slate-500">{experiment.data.current.caveats.map(item => <li key={item}>{item}</li>)}</ul>
      </>}
    </section>

    <section className="panel mt-5 p-5">
      <div className="flex items-center gap-2"><Users className="h-5 w-5 text-moss-600" aria-hidden /><h2 className="font-bold">Атрибуция кандидатов: от поста до человека</h2></div>
      <p className="mt-1 text-xs text-slate-500">Сигнал — это интерес. Кандидат — это подтверждённый контакт. Порядок оценки: кандидаты → сигналы → охват; просмотры не являются целью.</p>
      {!attributionData.data ? <p className="mt-4 text-sm text-slate-500">Данные атрибуции ещё не загружены.</p> : <>
        {attributionData.data.vacancies.filter(row => row.published_posts > 0 || row.signals > 0).length === 0 ? <p className="mt-4 text-sm text-slate-500">Пока нет вакансий с публикациями или сигналами: атрибутировать нечего.</p> : <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead><tr className="text-xs uppercase tracking-wide text-slate-400">
              <th className="py-2 pr-3 font-semibold">Вакансия</th>
              <th className="py-2 pr-3 font-semibold">Публикаций</th>
              <th className="py-2 pr-3 font-semibold">Сигналов</th>
              <th className="py-2 pr-3 font-semibold">Людей</th>
              <th className="py-2 pr-3 font-semibold">Кандидатов</th>
              <th className="py-2 pr-3 font-semibold">Дошли до этапа</th>
              <th className="py-2 font-semibold">Приглашения</th>
            </tr></thead>
            <tbody>
              {attributionData.data.vacancies.filter(row => row.published_posts > 0 || row.signals > 0).map(row => <tr key={row.vacancy_id} className="border-t border-slate-100">
                <td className="py-2 pr-3 font-semibold text-slate-700">{row.title}</td>
                <td className="py-2 pr-3 tabular-nums text-slate-600">{row.published_posts}</td>
                <td className="py-2 pr-3 tabular-nums text-slate-600">{row.signals}</td>
                <td className="py-2 pr-3 tabular-nums text-slate-600">{row.unique_people}</td>
                <td className="py-2 pr-3 tabular-nums font-bold text-slate-700">{row.candidates}</td>
                <td className="py-2 pr-3 text-xs text-slate-600">связались {row.contacted} · квалиф. {row.qualified} · наняты {row.hired} · отказ {row.rejected}</td>
                <td className="py-2 text-xs text-slate-400">{row.invited === null ? "N/A" : row.invited}</td>
              </tr>)}
            </tbody>
          </table>
        </div>}
        <ul className="mt-3 list-disc space-y-1 pl-4 text-xs text-slate-500">
          <li>{attributionData.data.quality.identity} — по этой связке человек считается один раз.</li>
          {attributionData.data.quality.signals_without_post > 0 && <li className="text-amber-700">Сигналов без привязки к посту: {attributionData.data.quality.signals_without_post} — их нельзя отнести к публикации.</li>}
          {attributionData.data.quality.signals_without_vacancy > 0 && <li className="text-amber-700">Сигналов без вакансии: {attributionData.data.quality.signals_without_vacancy} — публикация не привязана к вакансии, поэтому в воронку вакансии они не попадают.</li>}
          {attributionData.data.quality.duplicate_candidate_records > 0 && <li className="text-amber-700">Найдено повторных записей кандидатов: {attributionData.data.quality.duplicate_candidate_records} — одна и та же связка встречается больше одного раза.</li>}
          {attributionData.data.quality.candidates_without_consent > 0 && <li>Кандидатов без записанного согласия: {attributionData.data.quality.candidates_without_consent}.</li>}
          {attributionData.data.limitations.map(item => <li key={item}>{item}</li>)}
        </ul>
      </>}
    </section>

    <section className="mt-5 grid gap-4 lg:grid-cols-[1fr_1.6fr]">
      <div className="panel p-5">
        <div className="flex items-center gap-2"><Database className="h-5 w-5 text-moss-600" aria-hidden /><h2 className="font-bold">Покрытие данных</h2></div>
        <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
          <div className="rounded-lg bg-slate-50 p-3"><p className="text-xs text-slate-500">Публикации с метриками</p><p className="mt-1 text-xl font-bold">{data.coverage.posts_with_metrics} / {data.coverage.published_posts}</p></div>
          <div className="rounded-lg bg-slate-50 p-3"><p className="text-xs text-slate-500">Публичные темы</p><p className="mt-1 text-xl font-bold">{data.coverage.public_topics}</p></div>
        </div>
        <p className={`mt-3 text-sm font-medium ${data.coverage.public_threads === "available" ? "text-moss-700" : "text-amber-700"}`}>{radarStatus}</p>
        {data.coverage.public_threads_detail && <p className="mt-1 text-xs text-slate-500">{data.coverage.public_threads_detail}</p>}
        <p className="mt-3 text-xs leading-relaxed text-slate-500">Точные просмотры и ответы доступны по вашему аккаунту. По всей платформе радар оценивает частоту и свежесть публичных тем, а не скрытую статистику чужих публикаций.</p>
      </div>
      <div className="panel p-5">
        <div className="flex items-center gap-2"><Radar className="h-5 w-5 text-violet-600" aria-hidden /><h2 className="font-bold">Радар тем Threads и новостей</h2></div>
        {data.trends.length === 0 ? <p className="mt-5 text-sm text-slate-500">Сигналы ещё не собраны. Разведчик обновляет их автоматически.</p> : <div className="mt-4 grid gap-2 sm:grid-cols-2">
          {data.trends.slice(0, 6).map(trend => <article key={trend.id} className="rounded-lg border border-slate-100 bg-slate-50/70 p-3">
            <div className="flex items-center justify-between gap-3"><span className="text-[11px] font-bold uppercase tracking-wide text-slate-400">{trend.source === "threads-keyword-search" ? "Threads" : "Новости"}</span><span className="text-xs font-bold text-moss-700">{trend.score}</span></div>
            <p className="mt-1 line-clamp-2 text-sm font-semibold text-slate-700">{trend.title}</p>
            {trend.explanation && <p className="mt-1 line-clamp-2 text-xs text-slate-500">{trend.explanation}</p>}
          </article>)}
        </div>}
      </div>
    </section>

    <section className="mt-5 grid gap-5 xl:grid-cols-[1.6fr_1fr]">
      <div className="panel min-w-0 p-4 sm:p-5">
        <div className="mb-5 flex flex-wrap items-start justify-between gap-2">
          <div><h2 className="font-bold">Динамика результатов</h2><p className="mt-0.5 text-xs text-slate-500">Показы, ответы и сигналы кандидатов во времени</p></div>
          <span className="text-xs font-semibold text-slate-400">{data.sample_size} опубликованных публикаций</span>
        </div>
        <div className="h-72 w-full" aria-label="График динамики результатов">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data.performance} margin={{ top: 4, right: 8, left: -18, bottom: 0 }}>
              <CartesianGrid stroke="#e2e8f0" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="date" axisLine={false} tickLine={false} tick={{ fill: "#64748b", fontSize: 11 }} dy={8} />
              <YAxis axisLine={false} tickLine={false} tick={{ fill: "#94a3b8", fontSize: 11 }} tickFormatter={value => compactNumber.format(value)} />
              <Tooltip contentStyle={{ border: "1px solid #e2e8f0", borderRadius: 10, boxShadow: "0 8px 24px rgba(15, 23, 42, .08)", fontSize: 12 }} />
              <Legend wrapperStyle={{ paddingTop: 18, fontSize: 12 }} />
              <Line type="monotone" dataKey="impressions" name="Показы" stroke="#398158" strokeWidth={2.5} dot={{ r: 3, fill: "#398158" }} activeDot={{ r: 5 }} />
              <Line type="monotone" dataKey="replies" name="Ответы" stroke="#7c3aed" strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="signals" name="Сигналы кандидатов" stroke="#b96842" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="panel min-w-0 p-4 sm:p-5">
        <div className="mb-5"><h2 className="font-bold">Структура контента</h2><p className="mt-0.5 text-xs text-slate-500">Вовлечённость по типам контента</p></div>
        <div className="h-72 w-full" aria-label="График вовлечённости по типам контента">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data.by_content_type} layout="vertical" margin={{ top: 0, right: 12, left: 2, bottom: 0 }}>
              <CartesianGrid stroke="#e2e8f0" strokeDasharray="3 3" horizontal={false} />
              <XAxis type="number" axisLine={false} tickLine={false} tick={{ fill: "#94a3b8", fontSize: 11 }} unit="%" />
              <YAxis type="category" dataKey="name" tickFormatter={value => labelFor(String(value))} width={70} axisLine={false} tickLine={false} tick={{ fill: "#475569", fontSize: 11 }} />
              <Tooltip cursor={{ fill: "#f8fafc" }} formatter={(value: number) => [`${value}%`, "Вовлечённость"]} contentStyle={{ border: "1px solid #e2e8f0", borderRadius: 10, fontSize: 12 }} />
              <Bar dataKey="engagement" name="Вовлечённость" fill="#398158" radius={[0, 5, 5, 0]} barSize={18} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </section>

    <section className="mt-5 grid gap-5 xl:grid-cols-[1.6fr_1fr]">
      <div className="panel overflow-hidden">
        <div className="border-b border-slate-100 px-4 py-4 sm:px-5"><h2 className="font-bold">Лучшие публикации</h2><p className="mt-0.5 text-xs text-slate-500">Обзор сигналов и реакций, не оценка качества кандидатов</p></div>
        {data.top_posts.length === 0 ? <p className="px-5 py-10 text-center text-sm text-slate-500">Данных по опубликованным материалам пока нет.</p> : <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead className="border-b border-slate-100 bg-slate-50/80 text-[11px] font-bold uppercase tracking-wide text-slate-500"><tr><th className="px-5 py-3">Публикация</th><th className="px-4 py-3 text-right">Показы</th><th className="px-4 py-3 text-right">Ответы</th><th className="px-5 py-3 text-right">Сигналы</th></tr></thead>
            <tbody>{data.top_posts.map(post => <tr key={post.id} className="table-row"><td className="max-w-xl px-5 py-4 font-medium text-slate-700">{post.content}</td><td className="px-4 py-4 text-right tabular-nums text-slate-600">{post.impressions.toLocaleString("ru-RU")}</td><td className="px-4 py-4 text-right tabular-nums text-slate-600">{post.replies.toLocaleString("ru-RU")}</td><td className="px-5 py-4 text-right font-bold tabular-nums text-moss-700">{post.signals.toLocaleString("ru-RU")}</td></tr>)}</tbody>
          </table>
        </div>}
      </div>

      <aside className="panel h-fit border-moss-100 bg-moss-50/40 p-5">
        <div className="flex items-center gap-2"><Lightbulb className="h-5 w-5 text-moss-600" aria-hidden /><h2 className="font-bold">Рекомендация аналитика</h2></div>
        <p className="mt-3 text-sm leading-relaxed text-slate-700">{labelFor(data.recommendation)}</p>
        <div className="mt-4 border-t border-moss-100 pt-3 text-xs text-slate-500">{data.editorial_analysis ? `Сопоставимых публикаций: ${data.editorial_analysis.comparable_posts} из ${data.sample_size}.` : "Основания сравнения пока не получены."}</div>
        {data.editorial_analysis && <div className="mt-3 text-xs text-slate-600">
          <p className="font-semibold">{data.editorial_analysis.insufficient_data ? "Недостаточно данных для сравнения форматов" : "Есть предварительная гипотеза, не доказательство"}</p>
          <p className="mt-2">Сравниваем один язык и одинаковое окно после публикации. Нужно минимум по 3 поста двух форматов; посты младше 24 часов исключены.</p>
          {data.editorial_analysis.cohorts.length > 0 && <ul className="mt-3 space-y-2" aria-label="Группы сравнения">{data.editorial_analysis.cohorts.map(group => <li key={`${group.content_type}-${group.language}-${group.age_bucket}`}>
            {labelFor(group.content_type)} · {group.language.toUpperCase()} · {group.age_bucket}: {group.posts} постов, {decimal.format(group.engagement_rate)}% реакций к просмотрам{group.posts < 3 ? " (малая выборка)" : ""}
          </li>)}</ul>}
          <ul className="mt-3 list-disc space-y-1 pl-4">{data.editorial_analysis.limitations.map(item => <li key={item}>{item}</li>)}</ul>
        </div>}
      </aside>
    </section>
  </>;
}
