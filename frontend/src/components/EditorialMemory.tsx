import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { editorialMemory } from '../api/client';
import { Button } from './ui';

export function EditorialMemory() {
  const client = useQueryClient();
  const query = useQuery({ queryKey: ['editorial-memory'], queryFn: editorialMemory.list });
  const decision = useMutation({ mutationFn: ({ id, status }: { id: string; status: string }) => editorialMemory.decide(id, status),
    onSuccess: () => client.invalidateQueries({ queryKey: ['editorial-memory'] }) });
  return <section className="panel mt-4 p-4"><h2 className="font-bold">Редакторская память</h2>
    <p className="mt-2 text-sm text-slate-500">Укажите причину при редактировании. Подтверждённые предпочтения учитываются в следующих AI-текстах и контент-планах.</p>
    {query.isError && <p role="alert">Не удалось загрузить предпочтения.</p>}
    {decision.isError && <p role="alert">Не удалось сохранить решение.</p>}
    {query.data?.length === 0 && <p className="mt-3 text-sm">Пока нет правок с выбранной категорией стиля.</p>}
    {query.data?.map(rule => <article key={rule.id} className="mt-3 rounded border p-3">
      <p className="text-sm">{rule.rule}</p><p className="text-xs text-slate-500">{rule.language.toUpperCase()} · {rule.content_type} · {rule.occurrences} публикаций · {rule.status}</p>
      <details className="my-2 text-sm"><summary>Правки и причины</summary>{rule.examples.map((example, index) => <div key={index} className="my-2"><p>Было: {example.before}</p><p>Стало: {example.after}</p><p>Причина: {example.reason || 'Не указана; категория выбрана оператором'}</p></div>)}</details>
      <div className="flex gap-2"><Button disabled={decision.isPending} onClick={() => decision.mutate({ id: rule.id, status: 'CONFIRMED' })}>Применять</Button>
      <Button disabled={decision.isPending} onClick={() => decision.mutate({ id: rule.id, status: rule.status === 'CONFIRMED' ? 'DISABLED' : 'REJECTED' })}>{rule.status === 'CONFIRMED' ? 'Отключить' : 'Отклонить'}</Button></div>
    </article>)}
  </section>;
}
