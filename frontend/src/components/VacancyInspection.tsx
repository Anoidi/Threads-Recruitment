import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Button } from './ui';

import { request } from '../api/client';

function call(path: string, value?: unknown) {
  return request<any>(path, value === undefined ? undefined : {
    method: 'POST', body: JSON.stringify(value),
  });
}
export function VacancyInspection({ id, onChanged }: { id: string; onChanged: () => void }) {
  const report = useQuery({ queryKey: ['vacancy-inspection', id], queryFn: () => call(`/vacancies/${id}/inspection`) });
  const history = useQuery({ queryKey: ['vacancy-history', id], queryFn: () => call(`/vacancies/${id}/history`) });
  const [preview, setPreview] = useState<{ source_hash: string; changes: { field: string; before: string; after: string }[] } | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [success, setSuccess] = useState('');
  const run = async (fn: () => Promise<unknown>) => { setBusy(true); setError(''); try { await fn(); await report.refetch(); await history.refetch(); onChanged(); } catch (e) { setError(String(e)); } finally { setBusy(false); } };
  const data = report.data;
  return <div className="space-y-3 p-5">
    {report.isError && <p role="alert">Не удалось загрузить паспорт вакансии.</p>}
    {success && <p role="status" className="text-moss-700">{success}</p>}
    {data?.freshness && <p className="text-sm">Актуальность источника: {data.freshness.status === 'FRESH' ? 'подтверждена' : 'требует подтверждения'}</p>}
    {data && <><p>{data.status === 'READY' ? 'Основные поля заполнены' : 'Нужна проверка'}: {data.issues.join(' · ')}</p>
      {data.stale && <p className="text-amber-800">Источник старше 14 дней — перепроверьте актуальность.</p>}
      {data.duplicates.map((d: { id: number; reason: string }) => <p key={d.id} className="text-amber-800">Возможный дубль #{d.id}: {d.reason}</p>)}
      <h3 className="font-bold">Ставки и условия</h3>
      {data.rates.map((r: { amount: string; amount_max?: string; currency: string; basis?: string; student: boolean; condition: string }, index: number) => <p key={index}>{r.student ? 'Студентам' : 'Обычная'}: {r.amount}{r.amount_max ? `–${r.amount_max}` : ''} {r.currency}/час · {r.basis || 'netto/brutto не указано'}<br />{r.condition}</p>)}
      <h3 className="font-bold">Подтверждённые поля</h3>
      {Object.entries(data.fields).map(([key, raw]) => { const field = raw as { value?: string; status: string }; return <p key={key} className="text-sm">{key}: {field.value || 'Отсутствует в источнике'} · {field.status}</p>; })}
    </>}
    <div className="flex flex-wrap gap-2"><Button disabled={busy} onClick={() => run(async () => { setPreview(await call(`/vacancies/${id}/repair-preview`)); setSelected([]); })}>Перепарсить: показать изменения</Button>
    <Button disabled={busy} onClick={() => run(async () => { await call(`/vacancies/${id}/verify-source`, {}); setSuccess("Источник подтверждён. Автоматическая подготовка постов запущена."); })}>Я проверил актуальность источника</Button>
    <Button disabled={busy} onClick={() => run(() => call(`/vacancies/${id}/draft-parts`, {}))}>Создать связанные черновики</Button></div>
    {preview && <div><p>Выберите поля для замены из сохранённого источника:</p>{preview.changes.map(c => <label key={c.field} className="block my-2 text-sm"><input type="checkbox" checked={selected.includes(c.field)} onChange={e => setSelected(s => e.target.checked ? [...s, c.field] : s.filter(k => k !== c.field))} /> {c.field}: {c.before || 'Пусто'} → {c.after}</label>)}<Button disabled={busy || !selected.length} onClick={() => run(async () => { await call(`/vacancies/${id}/repair`, { source_hash: preview.source_hash, fields: selected }); setPreview(null); })}>Применить выбранные поля</Button></div>}
    {error && <p role="alert" className="text-red-700">{error}</p>}
    <details><summary>История изменений</summary>{history.data?.map((event: { action: string; created_at: string; detail: unknown }, index: number) => <pre key={index} className="whitespace-pre-wrap text-xs my-2">{event.created_at} · {event.action}\n{JSON.stringify(event.detail, null, 2)}</pre>)}</details>
  </div>;
}
