import { useState } from "react";
import { CheckCircle2, PhoneCall, UserCheck, UsersRound } from "lucide-react";
import { useCandidateFunnel, useCandidates, useChangeCandidateStage, useConvertSignal, useSignals, useUpdateSignal } from "../api/hooks";
import type { CandidateSignal, CandidateStage, ContactChannel } from "../types";
import { Badge, Button, EmptyState, ErrorPanel, LoadingPanel, Modal, PageHeader, Toast } from "../components/ui";
import { labelFor } from "../i18n";

const channels: { value: ContactChannel; label: string; placeholder: string }[] = [
  { value: "TELEGRAM", label: "Telegram", placeholder: "@username" },
  { value: "INSTAGRAM", label: "Instagram", placeholder: "@username" },
  { value: "THREADS", label: "Threads", placeholder: "@username" },
  { value: "PHONE", label: "Телефон", placeholder: "+48 ..." },
  { value: "OTHER", label: "Другой канал", placeholder: "Ссылка или контакт" },
];

const stageOrder: CandidateStage[] = ["NEW", "CONTACTED", "QUALIFIED", "HIRED"];

export function CandidatesPage() {
  const query = useSignals();
  const funnel = useCandidateFunnel();
  const candidatesQuery = useCandidates();
  const update = useUpdateSignal();
  const convert = useConvertSignal();
  const changeStage = useChangeCandidateStage();
  const [status, setStatus] = useState("ALL");
  const [toast, setToast] = useState("");
  const [converting, setConverting] = useState<CandidateSignal | null>(null);
  const [channel, setChannel] = useState<ContactChannel>("TELEGRAM");
  const [contactValue, setContactValue] = useState("");
  const [consent, setConsent] = useState(false);

  if (query.isLoading) return <LoadingPanel />;
  if (!query.data) return <ErrorPanel />;
  const items = query.data.filter(item => status === "ALL" || item.status === status);
  const set = (item: CandidateSignal, next: CandidateSignal["status"]) => update.mutate({ id: item.id, status: next }, { onSuccess: () => setToast(`Статус сигнала: ${labelFor(next)}`) });

  const openConvert = (item: CandidateSignal) => { setConverting(item); setContactValue(item.username.startsWith("@") ? item.username : `@${item.username}`); setChannel("TELEGRAM"); setConsent(false); };
  const submitConvert = () => {
    if (!converting) return;
    convert.mutate({ id: converting.id, payload: { channel, contact_value: contactValue.trim(), consent_given: consent } }, {
      onSuccess: () => { setConverting(null); setToast("Кандидат создан: контакт подтверждён, запись добавлена в воронку."); },
      onError: () => setToast("Не удалось подтвердить контакт. Проверьте данные."),
    });
  };
  const advance = (id: string, stage: CandidateStage) => changeStage.mutate({ id, stage }, { onSuccess: () => setToast(`Этап кандидата: ${labelFor(stage)}`), onError: () => setToast("Этот переход недоступен из текущего этапа.") });

  const funnelStats = funnel.data ? [
    { label: "Сигналы в очереди", value: funnel.data.signals_new, tone: "text-slate-700" },
    { label: "Контакт подтверждён", value: funnel.data.contacts_confirmed, tone: "text-blue-700" },
    { label: "Квалифицированы", value: funnel.data.stage_qualified, tone: "text-violet-700" },
    { label: "Наняты", value: funnel.data.stage_hired, tone: "text-moss-700" },
  ] : [];

  return <><PageHeader title="Кандидаты" description="Сигнал — это вопрос. Кандидат — это человек, с которым подтверждён контакт. Каждый переход по воронке фиксируется вручную." actions={<select className="input w-44" aria-label="Фильтр статуса сигнала" value={status} onChange={e => setStatus(e.target.value)}><option value="ALL">Все сигналы</option>{["NEW", "REVIEWED", "CONTACTED", "CONVERTED", "IGNORED"].map(s => <option key={s} value={s}>{labelFor(s)}</option>)}</select>} />

    <section className="mb-4 grid gap-3 sm:grid-cols-4">
      {funnelStats.map(stat => <div key={stat.label} className="panel px-4 py-3">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{stat.label}</p>
        <p className={`mt-1 text-2xl font-bold tabular-nums ${stat.tone}`}>{stat.value}</p>
      </div>)}
    </section>

    <div className="mb-4 flex items-start gap-3 rounded-xl border border-blue-100 bg-blue-50 p-4 text-sm text-blue-900"><UsersRound className="mt-0.5 h-5 w-5 shrink-0" /><div><strong>Сигнал ещё не является кандидатом.</strong><p className="mt-0.5 text-xs text-blue-700">Создание кандидата требует подтверждённого канала связи. Это ручное решение, и оно попадает в историю.</p></div></div>

    <div className="panel overflow-hidden">{items.length === 0 ? <EmptyState title="Сигналов кандидатов нет" detail="Обнаруженные агентом сообщества сигналы появятся здесь." /> : <div className="overflow-x-auto"><table className="w-full min-w-[820px] text-left"><thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500"><tr><th className="px-4 py-3">Пользователь</th><th className="px-4 py-3">Выявленное намерение</th><th className="px-4 py-3">Вакансия</th><th className="px-4 py-3">Уверенность</th><th className="px-4 py-3">Статус</th><th className="px-4 py-3 text-right">Действия</th></tr></thead><tbody>{items.map(item => <tr key={item.id} className="table-row"><td className="px-4 py-3 font-semibold">{item.username}<div className="text-xs font-normal text-slate-400">Threads</div></td><td className="px-4 py-3 text-sm">{labelFor(item.intent)}</td><td className="px-4 py-3 text-sm text-slate-600">{item.vacancy ?? "Не сопоставлено"}</td><td className="px-4 py-3"><div className="flex items-center gap-2"><div className="h-1.5 w-20 overflow-hidden rounded-full bg-slate-100"><div className="h-full bg-moss-500" style={{ width: `${item.confidence * 100}%` }} /></div><span className="text-xs">{Math.round(item.confidence * 100)}%</span></div></td><td className="px-4 py-3"><Badge value={item.status} /></td><td className="px-4 py-3"><div className="flex justify-end gap-2">{item.status === "NEW" && <Button onClick={() => set(item, "REVIEWED")}><CheckCircle2 className="h-4 w-4" />Проверить</Button>}{!(["CONVERTED", "IGNORED"] as string[]).includes(item.status) && <Button variant="primary" onClick={() => openConvert(item)}><UserCheck className="h-4 w-4" />Подтвердить контакт</Button>}{item.status !== "IGNORED" && item.status !== "CONVERTED" && <Button onClick={() => set(item, "IGNORED")}>Игнорировать</Button>}</div></td></tr>)}</tbody></table></div>}</div>

    {candidatesQuery.data && candidatesQuery.data.length > 0 && <div className="panel mt-5 overflow-hidden">
      <div className="border-b border-slate-100 p-4"><h2 className="font-bold">Воронка кандидатов</h2><p className="mt-0.5 text-xs text-slate-500">Каждый переход — ручное решение с записью в историю.</p></div>
      <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-left"><thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500"><tr><th className="px-4 py-3">Кандидат</th><th className="px-4 py-3">Подтверждённый контакт</th><th className="px-4 py-3">Согласие</th><th className="px-4 py-3">Этап</th><th className="px-4 py-3 text-right">Следующий этап</th></tr></thead><tbody>{candidatesQuery.data.map(candidate => {
        const index = stageOrder.indexOf(candidate.status as CandidateStage);
        const next = index >= 0 && index < stageOrder.length - 1 ? stageOrder[index + 1] : null;
        return <tr key={candidate.id} className="table-row">
          <td className="px-4 py-3 font-semibold">{candidate.name}<div className="text-xs font-normal text-slate-400">@{candidate.username.replace("@", "")}</div></td>
          <td className="px-4 py-3 text-sm">{candidate.contact_value ?? "—"}<div className="text-xs text-slate-400">{candidate.channel ? labelFor(candidate.channel) : "канал не указан"}</div></td>
          <td className="px-4 py-3 text-sm">{candidate.consent_given ? <span className="text-moss-700">зафиксировано</span> : <span className="text-amber-700">не зафиксировано</span>}</td>
          <td className="px-4 py-3"><Badge value={candidate.status} /></td>
          <td className="px-4 py-3 text-right">{next && <Button onClick={() => advance(candidate.id, next)}><PhoneCall className="h-4 w-4" />{labelFor(next)}</Button>}</td>
        </tr>;
      })}</tbody></table></div>
    </div>}

    <Modal open={converting !== null} onClose={() => setConverting(null)} title="Подтверждение контакта" description="Кандидат создаётся только с подтверждённым каналом связи. Это решение попадёт в историю воронки.">
      <div className="space-y-4 p-5">
        <label><span className="label">Канал связи</span><select className="input" aria-label="Канал связи" value={channel} onChange={e => setChannel(e.target.value as ContactChannel)}>{channels.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
        <label><span className="label">Контакт</span><input className="input" aria-label="Контакт" required minLength={2} placeholder={channels.find(item => item.value === channel)?.placeholder} value={contactValue} onChange={e => setContactValue(e.target.value)} /></label>
        <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" aria-label="Согласие на контакт" checked={consent} onChange={e => setConsent(e.target.checked)} /><span>Человек дал согласие на контакт<span className="block text-xs text-slate-500">Без этого факта контакт всё равно фиксируется, но согласие остаётся неподтверждённым.</span></span></label>
        <div className="flex justify-end gap-2 border-t border-slate-100 pt-4"><Button onClick={() => setConverting(null)}>Отмена</Button><Button variant="primary" disabled={contactValue.trim().length < 2} loading={convert.isPending} onClick={submitConvert}>Создать кандидата</Button></div>
      </div>
    </Modal>
    {toast && <Toast message={toast} onClose={() => setToast("")} />}
  </>;
}
