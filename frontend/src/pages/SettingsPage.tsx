import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Brain, Save, ShieldCheck, Siren } from "lucide-react";
import { useSaveSettings, useSettings } from "../api/hooks";
import { autonomy } from "../api/client";
import type { AutonomyMode, Language, Settings } from "../types";
import { Badge, Button, ErrorPanel, LoadingPanel, Modal, PageHeader, Toast, Toggle } from "../components/ui";

const MODES: { value: AutonomyMode; label: string; detail: string }[] = [
  { value: "MANUAL", label: "Ручной", detail: "Каждая публикация и каждый ответ — только после вашего одобрения." },
  { value: "SEMI_AUTO", label: "Полуавтоматический", detail: "Одобренный контент планируется и выходит сам; ответы уходят сами по списку безопасных тем." },
  { value: "AUTO", label: "Автоматический", detail: "Система работает без подтверждений, но только при пройденной проверке фактов и текста." },
];

const matrixLabels: Record<string, string> = {
  auto: "Автоматически",
  review: "Требует одобрения",
  auto_after_approval: "Авто после одобрения контента",
  auto_after_qa: "Авто после проверки текста",
  auto_for_allowed_topics: "Авто для разрешённых тем",
  auto_within_policy: "Авто в рамках политики",
};

export function SettingsPage() {
  const query = useSettings(); const save = useSaveSettings(); const [form, setForm] = useState<Settings | null>(null); const [confirm, setConfirm] = useState(false); const [toast, setToast] = useState("");
  const client = useQueryClient();
  const autonomyQuery = useQuery({ queryKey: ["autonomy"], queryFn: () => autonomy.load() });
  const [stopConfirm, setStopConfirm] = useState(false);
  useEffect(() => { if (query.data && !form) setForm(query.data); }, [query.data, form]);
  if (query.isLoading || !form) return <LoadingPanel />; if (query.isError) return <ErrorPanel />;

  const policy = autonomyQuery.data;
  const setMode = (mode: AutonomyMode) => {
    if (mode === "AUTO" && policy && !policy.auto_available) {
      setToast(`Автоматический режим пока недоступен: ${policy.auto_blockers.join(" ")}`);
      return;
    }
    autonomy.update({ mode, reason: "Переключение режима оператором" }).then(next => {
      client.setQueryData(["autonomy"], next);
      setToast(`Режим: ${MODES.find(item => item.value === next.mode)?.label ?? next.mode}`);
    }).catch((error: Error) => setToast(error.message.includes("not available") ? "Автоматический режим пока недоступен" : "Не удалось сменить режим"));
  };
  const toggleStop = (enabled: boolean) => {
    autonomy.emergencyStop(enabled, enabled ? "Аварийная остановка оператором" : "Возобновление работы").then(next => {
      client.setQueryData(["autonomy"], next);
      setToast(enabled ? "Аварийная остановка включена: внешние действия заблокированы" : "Внешние действия возобновлены");
      setStopConfirm(false);
    }).catch(() => setToast("Не удалось изменить аварийную остановку"));
  };
  const saveLimits = (key: string, value: number) => {
    autonomy.update({ limits: { [key]: value } }).then(next => {
      client.setQueryData(["autonomy"], next);
      setToast("Лимит сохранён");
    }).catch(() => setToast("Не удалось сохранить лимит"));
  };
  const field = <K extends keyof Settings>(key: K, value: Settings[K]) => setForm(current => current ? { ...current, [key]: value } : current);
  const automationRows = [["auto_content_plan", "Автоматическая генерация постов", "Каждый день создавать новые посты из активных вакансий, вопросов аудитории и найденных тем."], ["auto_community_sync", "Автоматический сбор сообщества", "Регулярно забирать новые ответы и упоминания из Threads."], ["auto_post", "Автоматическая публикация", "После вашего одобрения ставить посты в очередь и публиковать их автоматически."], ["auto_reply", "Автоматические ответы", "Отправлять ответы с низким риском без отдельного одобрения."], ["auto_external_comment", "Автоматические внешние комментарии", "Разрешить агентам комментировать сторонние обсуждения."]] as const;
  const submit = () => { const dangerousChanged = query.data && ((!query.data.auto_post && form.auto_post) || (!query.data.auto_reply && form.auto_reply) || (!query.data.auto_external_comment && form.auto_external_comment)); if (dangerousChanged) setConfirm(true); else persist(); };
  const persist = () => save.mutate(form, { onSuccess: () => { setConfirm(false); setToast("Настройки сохранены"); } });
  return <><PageHeader title="Настройки" description="Управление брендом, входными данными агентов, обновлением, контент-планом и защитой внешних действий." actions={<Button variant="primary" onClick={submit} loading={save.isPending}><Save className="h-4 w-4" />Сохранить настройки</Button>} />
    <div className="grid gap-5 xl:grid-cols-2">
      <section className="panel p-5 xl:col-span-2">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2"><Brain className="h-5 w-5 text-moss-600" /><h2 className="font-bold">Автономность системы</h2></div>
          {policy && <span className="text-xs text-slate-500">версия политики {policy.version}</span>}
        </div>
        <p className="mt-1 text-xs leading-relaxed text-slate-500">Режим задаёт, что система делает сама. Решение всегда принимает серверная политика, а не агент.</p>
        {!policy ? <p className="mt-3 text-sm text-slate-500">Политика загружается…</p> : <>
          <div className="mt-4 grid gap-2 sm:grid-cols-3">
            {MODES.map(item => <button key={item.value} onClick={() => setMode(item.value)}
              className={`rounded-lg border p-3 text-left transition ${policy.mode === item.value ? "border-moss-500 bg-moss-50" : "border-slate-200 bg-white hover:bg-slate-50"}`}>
              <p className="text-sm font-bold text-slate-800">{item.label}</p>
              <p className="mt-1 text-xs leading-relaxed text-slate-500">{item.detail}</p>
              {item.value === "AUTO" && !policy.auto_available && <p className="mt-1 text-[11px] font-semibold text-amber-700">пока недоступен</p>}
            </button>)}
          </div>
          {policy.auto_blockers.length > 0 && <ul className="mt-3 space-y-1 rounded-lg border border-amber-200 bg-amber-50/60 p-3 text-xs text-amber-900">
            {policy.auto_blockers.map(item => <li key={item}>• {item}</li>)}
          </ul>}
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 p-3">
            <div>
              <p className="text-sm font-semibold text-slate-800">Аварийная остановка</p>
              <p className="text-xs text-slate-500">Мгновенно блокирует публикации и ответы. Сбор и аналитика продолжают работать.</p>
            </div>
            {policy.emergency_stop
              ? <Button variant="danger" onClick={() => toggleStop(false)}>Возобновить</Button>
              : <Button onClick={() => setStopConfirm(true)}><Siren className="h-4 w-4" />Остановить</Button>}
          </div>
          {policy.emergency_stop && <p className="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs font-semibold text-red-700">Внешние действия заблокированы аварийной остановкой.</p>}
        </>}
      </section>

      {policy && <section className="panel p-5 xl:col-span-2">
        <div className="flex items-center gap-2"><ShieldCheck className="h-5 w-5 text-moss-600" /><h2 className="font-bold">Что система делает сама</h2></div>
        {policy.matrix.length > 0 && <div className="mt-3 overflow-x-auto"><table className="w-full min-w-[560px] text-left text-xs">
          <thead className="bg-slate-50 uppercase tracking-wide text-slate-500"><tr><th className="px-3 py-2">Действие</th><th className="px-3 py-2">Ручной</th><th className="px-3 py-2">Полуавто</th><th className="px-3 py-2">Авто</th></tr></thead>
          <tbody>{policy.matrix.map(row => <tr key={row.action} className="border-t border-slate-100">
            <td className="px-3 py-2 font-medium text-slate-700">{row.label}</td>
            <td className="px-3 py-2 text-slate-600">{matrixLabels[row.manual] ?? row.manual}</td>
            <td className="px-3 py-2 text-slate-600">{matrixLabels[row.semi_auto] ?? row.semi_auto}</td>
            <td className="px-3 py-2 text-slate-600">{matrixLabels[row.auto] ?? row.auto}</td>
          </tr>)}</tbody>
        </table></div>}
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          {[["max_posts_per_day", "Публикаций в сутки"], ["max_replies_per_day", "Ответов в сутки"], ["max_publish_per_hour", "Публикаций в час"]].map(([key, label]) => <label key={key}>
            <span className="label">{label}</span>
            <input className="input" min="0" type="number" defaultValue={policy.limits[key] ?? 0}
              onBlur={event => { const value = Number(event.target.value); if (Number.isFinite(value) && value !== policy.limits[key]) saveLimits(key, value); }} />
          </label>)}
        </div>
        <p className="mt-2 text-xs text-slate-500">За 24 часа: публикаций {policy.usage.published_24h}, ответов {policy.usage.answered_24h}.</p>
      </section>}

      {policy && policy.decisions.length > 0 && <section className="panel p-5 xl:col-span-2">
        <h2 className="font-bold">Журнал решений политики</h2>
        <div className="mt-3 space-y-1.5">
          {policy.decisions.slice(0, 12).map(item => <div key={item.id} className="flex items-start gap-3 rounded-lg bg-slate-50 px-3 py-2">
            <Badge value={item.decision === "ALLOW" ? "ONLINE" : item.decision === "DENY" ? "BLOCKED" : "DEGRADED"}>{item.decision}</Badge>
            <div className="min-w-0">
              <p className="text-xs font-semibold text-slate-700">{item.action} · режим {item.mode} · политика v{item.policy_version}</p>
              <p className="text-xs text-slate-500">{item.reason}</p>
            </div>
          </div>)}
        </div>
      </section>}
      <section className="panel p-5"><h2 className="font-bold">Рабочая область</h2><div className="mt-4 grid gap-4 sm:grid-cols-2"><label><span className="label">Язык по умолчанию</span><select className="input" value={form.default_language} onChange={e => field("default_language", e.target.value as Language)}><option value="uk">Украинский</option><option value="ru">Русский</option><option value="pl">Польский</option><option value="en">Английский</option></select></label><label><span className="label">Язык контент-плана</span><select className="input" value={form.content_plan_language} onChange={e => field("content_plan_language", e.target.value as Language)}><option value="uk">Украинский</option><option value="ru">Русский</option><option value="pl">Польский</option><option value="en">Английский</option></select></label><label><span className="label">Часовой пояс</span><select className="input" value={form.timezone} onChange={e => field("timezone", e.target.value)}><option>Europe/Warsaw</option><option>Europe/Kyiv</option><option>UTC</option></select></label><label className="sm:col-span-2"><span className="label">Стиль бренда</span><textarea className="input min-h-24 py-2" value={form.brand_voice} onChange={e => field("brand_voice", e.target.value)} /></label><label className="sm:col-span-2"><span className="label">Имя рекрутера или команды</span><input className="input" value={form.recruiter_identity} onChange={e => field("recruiter_identity", e.target.value)} /></label><label className="sm:col-span-2"><span className="label">Шаблоны призыва · по одному в строке</span><textarea className="input min-h-24 py-2" value={form.cta_templates} onChange={e => field("cta_templates", e.target.value)} /></label><div className="sm:col-span-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900"><p className="font-semibold">Telegram-контакт не используется в публикациях</p><p className="mt-1 text-xs">Если телефон не указан, используйте призыв «Напишіть нам у приватні повідомлення» вместо Telegram-ника или ссылки.</p></div><label><span className="label">Телефон / контакт</span><input className="input" value={form.phone_contact} onChange={e => field("phone_contact", e.target.value)} /></label><label><span className="label">Публикаций в день (план аудитории)</span><input className="input" min={1} max={6} type="number" value={form.posts_per_day} onChange={e => field("posts_per_day", Number(e.target.value))} /></label></div></section>
      <section className="panel p-5"><h2 className="font-bold">Поиск и обработка</h2><div className="mt-4 grid gap-4 sm:grid-cols-2"><label><span className="label">Ключевые слова разведчика · по одному в строке</span><textarea className="input min-h-36 py-2" value={form.scout_keywords} onChange={e => field("scout_keywords", e.target.value)} /></label><label><span className="label">Игнорируемые ключевые слова</span><textarea className="input min-h-36 py-2" value={form.ignored_keywords} onChange={e => field("ignored_keywords", e.target.value)} /></label><label><span className="label">Интервал проверки (минуты)</span><input className="input" min="1" type="number" value={form.polling_interval_minutes} onChange={e => field("polling_interval_minutes", Number(e.target.value))} /></label><label><span className="label">Обновление аналитики (минуты)</span><input className="input" min="5" type="number" value={form.analytics_refresh_minutes} onChange={e => field("analytics_refresh_minutes", Number(e.target.value))} /></label><label><span className="label">Повторное чтение ответов (минуты)</span><input className="input" min="5" max="240" type="number" value={form.reply_scan_interval_minutes} onChange={e => field("reply_scan_interval_minutes", Number(e.target.value))} /></label></div><p className="mt-3 text-xs text-slate-500">Повторное чтение ответов задаёт, как часто ветка обсуждений под постом проверяется заново. Значение меньше 60 минут гарантирует, что новый ответ не попадёт за пределы окна синхронизации.</p></section>
      <section className="panel p-5 xl:col-span-2"><div className="flex items-center gap-2"><ShieldCheck className="h-5 w-5 text-moss-600" /><h2 className="font-bold">Защита публикаций</h2></div><p className="mt-1 text-xs text-slate-500">{query.data?.mock_mode ? "Политика автоматической публикации включена, но тестовый режим блокирует отправку в Threads." : "Рабочая публикация включена. Автоматические записи используют ссылку на источник и почасовой лимит; регулярно проверяйте подключение и источники."} Лимиты действуют для агентов и ручных действий.</p><div className="mt-4 grid gap-4 sm:grid-cols-2"><label><span className="label">Максимум публикаций в час</span><input className="input" min="1" type="number" value={form.max_posts_per_hour} onChange={e => field("max_posts_per_hour", Number(e.target.value))} /></label><label><span className="label">Максимум ответов в час</span><input className="input" min="1" type="number" value={form.max_replies_per_hour} onChange={e => field("max_replies_per_hour", Number(e.target.value))} /></label></div><div className="mt-5 divide-y divide-slate-100 rounded-lg border border-slate-200">{automationRows.map(([key, label, detail]) => <div key={key} className="flex items-center justify-between gap-4 p-4"><div><p className="text-sm font-semibold">{label}</p><p className="text-xs text-slate-500">{detail}</p></div><Toggle label={label} checked={form[key as keyof Settings] as boolean} onChange={value => field(key as "auto_content_plan" | "auto_community_sync" | "auto_post" | "auto_reply" | "auto_external_comment", value)} /></div>)}</div></section></div>
    <Modal open={stopConfirm} onClose={() => setStopConfirm(false)} title="Аварийная остановка" description="Все внешние действия будут остановлены: публикации и ответы перестанут выполняться автоматически. Сбор данных и аналитика продолжат работать." size="md"><div className="p-5">
      <div className="flex gap-3 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800"><AlertTriangle className="h-5 w-5 shrink-0" />Уже поставленные в очередь публикации остановятся и вернутся на одобрение.</div>
      <div className="mt-5 flex justify-end gap-2"><Button onClick={() => setStopConfirm(false)}>Отмена</Button><Button variant="danger" onClick={() => toggleStop(true)}>Остановить внешние действия</Button></div>
    </div></Modal>
    <Modal open={confirm} onClose={() => setConfirm(false)} title="Подтверждение автоматизации" description="Эта настройка может запускать внешние действия без одобрения каждого элемента." size="md"><div className="p-5"><div className="flex gap-3 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800"><AlertTriangle className="h-5 w-5 shrink-0" />Включение автоматизации повышает риски для аккаунта и соблюдения правил. Ограничения частоты остаются активными.</div><div className="mt-5 flex justify-end gap-2"><Button onClick={() => setConfirm(false)}>Оставить обязательную проверку</Button><Button variant="danger" onClick={persist}>Понимаю, включить</Button></div></div></Modal>{toast && <Toast message={toast} onClose={() => setToast("")} />}
  </>;
}

