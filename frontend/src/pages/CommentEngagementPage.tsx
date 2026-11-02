import { useState } from "react";
import { Check, MessageCircle, RefreshCw } from "lucide-react";
import { useApproveCommentEngagement, useCommentEngagement, useGenerateCommentEngagement } from "../api/hooks";
import { Badge, Button, EmptyState, ErrorPanel, LoadingPanel, PageHeader, Toast } from "../components/ui";

export function CommentEngagementPage() {
  const query = useCommentEngagement();
  const generate = useGenerateCommentEngagement();
  const approve = useApproveCommentEngagement();
  const [editing, setEditing] = useState<{ campaign: number; variant: number; text: string } | null>(null);
  const [mode, setMode] = useState<"MONITOR" | "DRAFT" | "AUTO">("DRAFT");
  const [toast, setToast] = useState("");
  if (query.isLoading) return <LoadingPanel />;
  if (!query.data) return <ErrorPanel />;
  return <>
    <PageHeader title="Comment Engagement" description="Реальні коментарі Threads → аналіз → три варіанти → ручне підтвердження. Автопублікація вимкнена." />
    <div className="mb-4 flex flex-wrap items-center gap-3"><label className="text-sm font-semibold text-slate-700">Режим <select className="input ml-2 w-auto" value={mode} onChange={e => setMode(e.target.value as typeof mode)}><option value="MONITOR">Monitor</option><option value="DRAFT">Draft</option><option value="AUTO">Auto (тільки allowlist)</option></select></label><span className="text-xs text-slate-500">Auto не дає дозволу на публікацію: потрібні allowlist і операторський approval.</span></div>
    <div className="space-y-4">
      {query.data.length === 0 ? <EmptyState title="Коментарів для аналізу ще немає" detail="Синхронізуйте Community inbox. Дані з Meta без scope не підміняються вигаданими метриками." /> : query.data.map(campaign => <section key={campaign.id} className="panel p-5">
        <div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex items-center gap-2"><MessageCircle className="h-4 w-4 text-moss-600" /><h2 className="font-semibold">@{campaign.analysis.username ?? "невідомий"}</h2><Badge>{campaign.status}</Badge></div><p className="mt-2 text-sm text-slate-600">Тема: {String(campaign.analysis.topic ?? "інші")} · тон: {String(campaign.analysis.sentiment ?? "neutral")}</p></div><Button onClick={() => generate.mutate({ interaction_id: Number(campaign.analysis.interaction_id), mode })} loading={generate.isPending}><RefreshCw className="h-4 w-4" />Оновити варіанти</Button></div>
        <div className="mt-4 grid gap-3 lg:grid-cols-3">{campaign.variants.map(variant => <article key={variant.id} className="rounded-xl border border-slate-200 p-4"><div className="flex justify-between text-xs font-semibold uppercase tracking-wide text-slate-500"><span>{variant.style}</span><span>{variant.character_count}/500</span></div><p className="mt-3 min-h-24 text-sm leading-relaxed text-slate-700">{variant.content}</p><div className="mt-4 flex gap-2"><Button onClick={() => setEditing({ campaign: campaign.id, variant: variant.id, text: variant.content })}>Редагувати</Button><Button variant="primary" onClick={() => approve.mutate({ campaign_id: campaign.id, variant_id: variant.id }, { onSuccess: () => setToast("Варіант збережено як draft Threads") })}><Check className="h-4 w-4" />Підтвердити</Button></div></article>)}</div>
      </section>)}
    </div>
    {editing ? <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/30 p-4"><div className="w-full max-w-xl rounded-2xl bg-white p-5 shadow-xl"><h2 className="text-lg font-bold">Редагувати варіант</h2><textarea className="input mt-4 min-h-36 py-3" maxLength={500} value={editing.text} onChange={e => setEditing({ ...editing, text: e.target.value })} /><div className="mt-4 flex justify-end gap-2"><Button onClick={() => setEditing(null)}>Скасувати</Button><Button variant="primary" onClick={() => approve.mutate({ campaign_id: editing.campaign, variant_id: editing.variant, content: editing.text }, { onSuccess: () => { setEditing(null); setToast("Відредагований draft збережено") } })}>Підтвердити draft</Button></div></div></div> : null}
    {toast ? <Toast message={toast} onClose={() => setToast("")} /> : null}
  </>;
}

