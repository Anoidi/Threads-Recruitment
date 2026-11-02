import { useState } from "react";
import { CalendarDays, Clock3, RefreshCw, XCircle } from "lucide-react";
import { usePostAction, usePosts } from "../api/hooks";
import type { Post, PostStatus } from "../types";
import { VacancyInspection } from "../components/VacancyInspection";
import { Badge, Button, ErrorPanel, LoadingPanel, Modal, PageHeader, Toast } from "../components/ui";
import { formatLanguage } from "../i18n";
import { formatWarsawDateTime, fromWarsawDateTimeInput, scheduleInputDefaults, toWarsawDateTimeInput } from "../utils/datetime";

const lanes: { label: string; statuses: PostStatus[] }[] = [
  { label: "Черновики", statuses: ["DRAFT", "AI_GENERATED", "REVIEW", "APPROVED"] },
  { label: "Запланировано", statuses: ["SCHEDULED", "PUBLISHING"] },
  { label: "Опубликовано", statuses: ["PUBLISHED"] },
  { label: "Ошибки", statuses: ["FAILED"] },
];
const format = (value?: string) => value ? formatWarsawDateTime(value, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "Не запланировано";

export function CalendarPage() {
  const [inspecting, setInspecting] = useState<string | null>(null);
  const posts = usePosts(); const action = usePostAction(); const [selected, setSelected] = useState<Post | null>(null); const [date, setDate] = useState(""); const [toast, setToast] = useState("");
  if (posts.isLoading) return <LoadingPanel />; if (!posts.data) return <ErrorPanel />;
  const defaults = scheduleInputDefaults();
  const openSchedule = (post: Post) => { action.reset(); setDate(post.scheduled_at ? toWarsawDateTimeInput(post.scheduled_at) : defaults.defaultValue); setSelected(post); };
  const run = (post: Post, next: string) => action.mutate({ id: post.id, action: next, version: post.version }, {
    onSuccess: () => setToast(next === "publish" ? "Публикация отправлена в очередь на отправку сейчас" : next === "approve" ? "Публикация одобрена" : "Текст отправлен на проверку"),
  });
  const calendarMonth = new Intl.DateTimeFormat("ru-RU", { timeZone: "Europe/Warsaw", month: "long", year: "numeric" }).format(new Date());
  return <><PageHeader title="Календарь контента" description="Планирование по времени Europe/Warsaw. В системе время хранится в UTC." actions={<div className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-semibold capitalize"><CalendarDays className="mr-2 inline h-4 w-4 text-moss-600" />{calendarMonth}</div>} />
    {action.isError && !selected && <p role="alert" className="mb-4 rounded-lg bg-red-50 p-3 text-red-800">{action.error.message}</p>}
    <div className="grid gap-4 xl:grid-cols-4">{lanes.map(lane => { const items = posts.data!.filter(p => lane.statuses.includes(p.status)); return <section key={lane.label} className="min-h-64 rounded-xl border border-slate-200 bg-slate-100/60"><header className="flex items-center justify-between px-4 py-3"><h2 className="text-sm font-bold">{lane.label}</h2><span className="rounded-full bg-white px-2 py-0.5 text-xs font-bold text-slate-500">{items.length}</span></header><div className="space-y-2 p-2 pt-0">{items.map(post => <article key={post.id} className="rounded-lg border border-slate-200 bg-white p-3 shadow-sm"><div className="flex items-start justify-between gap-2"><Badge value={post.status} /><span className="text-[11px] uppercase text-slate-400">{formatLanguage(post.language)}</span></div><p className="mt-2 line-clamp-3 text-sm leading-relaxed">{post.hook ?? post.content}</p><div className="mt-3 flex items-center gap-1 text-xs text-slate-500"><Clock3 className="h-3.5 w-3.5" />{format(post.scheduled_at ?? post.published_at)}</div>{post.failure_reason && <p role="alert" className="mt-3 rounded-md bg-red-50 px-2 py-1.5 text-xs text-red-700">{post.failure_reason}</p>}<div className="mt-3 flex flex-wrap gap-2">
      {post.related_vacancy_id && <Button onClick={() => setInspecting(post.related_vacancy_id!)}>Подтвердить источник</Button>}
      {(["DRAFT", "AI_GENERATED"] as PostStatus[]).includes(post.status) && <Button disabled={action.isPending} onClick={() => run(post, "submit")}>На проверку</Button>}
      {(post.status === "REVIEW" || post.status === "FAILED") && <Button disabled={action.isPending} onClick={() => run(post, "approve")}>{post.status === "FAILED" ? "Одобрить повторно" : "Одобрить"}</Button>}
      {(["APPROVED", "SCHEDULED"] as PostStatus[]).includes(post.status) && <Button disabled={action.isPending} onClick={() => run(post, "publish")}>Опубликовать сейчас</Button>}
    </div>{(["APPROVED", "SCHEDULED"] as PostStatus[]).includes(post.status) && <div className="mt-3 flex gap-2"><Button onClick={() => openSchedule(post)}><RefreshCw className="h-3.5 w-3.5" />{post.status === "SCHEDULED" ? "Перенести" : "Запланировать"}</Button>{post.status === "SCHEDULED" && <Button onClick={() => action.mutate({ id: post.id, action: "cancel-schedule", version: post.version }, { onSuccess: () => setToast("Публикация снята с расписания") })}><XCircle className="h-3.5 w-3.5" />Отменить</Button>}</div>}</article>)}</div></section>; })}</div>
    <Modal open={inspecting !== null} onClose={() => setInspecting(null)} title="Подтверждение источника вакансии">{inspecting && <VacancyInspection id={inspecting} onChanged={() => { posts.refetch(); }} />}</Modal>
    <Modal open={selected !== null} onClose={() => setSelected(null)} title={selected?.status === "SCHEDULED" ? "Перенести публикацию" : "Запланировать публикацию"} size="md"><form className="space-y-4 p-5" onSubmit={e => { e.preventDefault(); if (selected) action.mutate({ id: selected.id, action: "schedule", scheduledAt: fromWarsawDateTimeInput(date), version: selected.version }, { onSuccess: () => { setSelected(null); setToast("Расписание обновлено"); } }); }}><label><span className="label">Дата и время публикации · Europe/Warsaw</span><input aria-label="Дата и время публикации" className="input" type="datetime-local" required min={defaults.min} value={date} onChange={e => setDate(e.target.value)} /></label>{action.isError && <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-800">{action.error.message}</p>}<div className="flex justify-end gap-2"><Button type="button" onClick={() => setSelected(null)}>Отмена</Button><Button type="submit" variant="primary" loading={action.isPending}>Сохранить расписание</Button></div></form></Modal>{toast && <Toast message={toast} onClose={() => setToast("")} />}
  </>;
}

