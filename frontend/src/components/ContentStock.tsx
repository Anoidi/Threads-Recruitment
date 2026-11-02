import { Link } from "react-router-dom";
import type { ContentStock as Stock } from "../types";
import { formatWarsawDateTime } from "../utils/datetime";

const time = (value: string) => formatWarsawDateTime(value, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

export function ContentStock({ stock }: { stock: Stock }) {
  return <section className="panel mt-4 p-4" aria-label="Готовность контента">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 className="font-bold">Готовность контента</h2>
      <Link className="text-sm font-semibold text-moss-700 underline" to="/calendar">Открыть расписание</Link>
    </div>
    <p className="mt-1 text-xs text-slate-500">Цель — запас на {stock.target_days} дня. В расписание попадают проверенные тексты; после правки текст и факты проверяются заново.</p>
    <dl className="mt-3 grid gap-3 sm:grid-cols-4">
      <div><dt className="text-xs text-slate-500">Проверено в расписании</dt><dd className="text-xl font-bold">{stock.ready_scheduled}</dd></div>
      <div><dt className="text-xs text-slate-500">Готово в резерве</dt><dd className="text-xl font-bold">{stock.ready_reserve}</dd></div>
      <div><dt className="text-xs text-slate-500">Запас при {stock.posts_per_day} постах в день</dt><dd className="text-xl font-bold">{stock.coverage_days} дня</dd></div>
      <div><dt className="text-xs text-slate-500">Следующая публикация</dt><dd className="mt-1 text-sm font-semibold">{stock.next_publication_at ? time(stock.next_publication_at) : "Не запланирована"}</dd></div>
    </dl>
    {stock.missing_slots.length > 0 && <div className="mt-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-900">
      <strong>Не заполнено слотов: {stock.missing_slots.length}.</strong> Ближайший: {time(stock.missing_slots[0])}.
      <p className="mt-1">{stock.refill_batches_today >= stock.refill_daily_limit ? "Достигнут дневной предел генерации. Проверьте причины отклонения, чтобы система не создавала новые неподходящие тексты." : stock.auto_content_plan ? "При включённом автоматическом режиме система пополняет очередь каждый час: дорабатывает черновики и создаёт новые." : "Автоматический контент-план выключен."}</p>
    </div>}
    {stock.stale_scheduled_ids.length > 0 && <p className="mt-3 text-sm text-amber-800">У {stock.stale_scheduled_ids.length} запланированных постов изменилась версия или факты. Им нужна повторная подготовка.</p>}
    {stock.rejected_count > 0 && <details className="mt-3 text-sm">
      <summary className="cursor-pointer font-semibold">Не прошли проверку: {stock.rejected_count}</summary>
      <ul className="mt-2 space-y-2">{stock.rejected_posts.map(post => <li key={post.id} className="rounded-lg bg-slate-50 p-3">
        <Link className="font-semibold text-moss-700 underline" to={`/content?search=${post.id}`}>Пост #{post.id}</Link>
        <span className="ml-2 text-xs text-slate-500">Доработок: {post.repairs}/2</span>
        <p className="mt-1 text-slate-600">{post.reason}</p>
      </li>)}</ul>
      <Link className="mt-2 inline-block text-moss-700 underline" to="/vacancies">Проверить источники и актуальность вакансий</Link>
      <p className="mt-2 text-xs text-slate-500">После двух доработок нужна проверка редактора. Отклонённые тексты не занимают слоты.</p>
    </details>}
  </section>;
}
