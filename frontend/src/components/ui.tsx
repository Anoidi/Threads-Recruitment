import type { ButtonHTMLAttributes, ReactNode } from "react";
import { AlertCircle, CheckCircle2, Loader2, X, XCircle } from "lucide-react";
import type { PostStatus, Risk, ServiceState } from "../types";
import { labelFor } from "../i18n";

export function PageHeader({ title, description, actions }: { title: string; description: string; actions?: ReactNode }) {
  return <header className="mb-5 flex flex-col justify-between gap-3 sm:flex-row sm:items-end"><div><h1 className="text-2xl font-bold tracking-tight text-ink">{title}</h1><p className="mt-1 text-sm text-slate-600">{description}</p></div>{actions && <div className="flex flex-wrap gap-2">{actions}</div>}</header>;
}

export function Button({ variant = "secondary", loading, children, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger"; loading?: boolean }) {
  return <button className={variant === "primary" ? "button-primary" : variant === "danger" ? "button-danger" : "button-secondary"} disabled={loading || props.disabled} {...props}>{loading && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}{children}</button>;
}

const tones: Record<string, string> = {
  ONLINE: "bg-emerald-50 text-emerald-700 ring-emerald-200", PUBLISHED: "bg-emerald-50 text-emerald-700 ring-emerald-200", APPROVED: "bg-emerald-50 text-emerald-700 ring-emerald-200", VALID: "bg-emerald-50 text-emerald-700 ring-emerald-200", LOW: "bg-emerald-50 text-emerald-700 ring-emerald-200", ANSWERED: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  DEGRADED: "bg-amber-50 text-amber-700 ring-amber-200", REVIEW: "bg-amber-50 text-amber-700 ring-amber-200", NEEDS_REVIEW: "bg-amber-50 text-amber-700 ring-amber-200", SCHEDULED: "bg-blue-50 text-blue-700 ring-blue-200", PUBLISHING: "bg-blue-50 text-blue-700 ring-blue-200", QUEUED: "bg-blue-50 text-blue-700 ring-blue-200", RUNNING: "bg-blue-50 text-blue-700 ring-blue-200", MEDIUM: "bg-amber-50 text-amber-700 ring-amber-200", EXPIRING: "bg-amber-50 text-amber-700 ring-amber-200",
  OFFLINE: "bg-red-50 text-red-700 ring-red-200", FAILED: "bg-red-50 text-red-700 ring-red-200", HIGH: "bg-red-50 text-red-700 ring-red-200", MISSING: "bg-red-50 text-red-700 ring-red-200", BLOCKED: "bg-red-50 text-red-700 ring-red-200", ESCALATED: "bg-red-50 text-red-700 ring-red-200",
  DRAFT: "bg-slate-100 text-slate-600 ring-slate-200", AI_GENERATED: "bg-violet-50 text-violet-700 ring-violet-200", IDLE: "bg-slate-100 text-slate-600 ring-slate-200", NEW: "bg-violet-50 text-violet-700 ring-violet-200", IGNORED: "bg-slate-100 text-slate-600 ring-slate-200", ARCHIVED: "bg-slate-100 text-slate-600 ring-slate-200",
};
export function Badge({ children, value }: { children?: ReactNode; value?: PostStatus | Risk | ServiceState | string }) { const key = String(value ?? children); return <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-bold uppercase tracking-wide ring-1 ring-inset ${tones[key] ?? "bg-slate-100 text-slate-600 ring-slate-200"}`}>{children ?? labelFor(key)}</span>; }

export function LoadingPanel() { return <div className="panel flex min-h-44 items-center justify-center text-sm text-slate-500"><Loader2 className="mr-2 h-4 w-4 animate-spin" />Загрузка рабочей области…</div>; }
export function ErrorPanel({ message = "Не удалось загрузить этот раздел." }: { message?: string }) { return <div className="panel flex min-h-32 items-center justify-center gap-2 p-6 text-sm text-red-700"><AlertCircle className="h-5 w-5" />{message}</div>; }
export function EmptyState({ title, detail, action }: { title: string; detail: string; action?: ReactNode }) { return <div className="flex flex-col items-center justify-center px-6 py-14 text-center"><div className="mb-3 rounded-full bg-slate-100 p-3"><CheckCircle2 className="h-5 w-5 text-slate-500" /></div><h3 className="font-semibold">{title}</h3><p className="mt-1 max-w-sm text-sm text-slate-500">{detail}</p>{action && <div className="mt-4">{action}</div>}</div>; }

export function Modal({ title, description, open, onClose, children, size = "lg" }: { title: string; description?: string; open: boolean; onClose: () => void; children: ReactNode; size?: "md" | "lg" | "xl" }) {
  if (!open) return null;
  return <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/35 p-0 sm:items-center sm:p-5" role="presentation" onMouseDown={e => { if (e.currentTarget === e.target) onClose(); }}><section role="dialog" aria-modal="true" aria-labelledby="modal-title" className={`max-h-[92vh] w-full overflow-auto rounded-t-2xl bg-white shadow-2xl sm:rounded-2xl ${size === "xl" ? "max-w-4xl" : size === "md" ? "max-w-lg" : "max-w-2xl"}`}><header className="sticky top-0 z-10 flex items-start justify-between border-b border-slate-100 bg-white/95 px-5 py-4 backdrop-blur"><div><h2 id="modal-title" className="text-lg font-bold">{title}</h2>{description && <p className="mt-1 text-sm text-slate-500">{description}</p>}</div><button className="rounded-lg p-2 text-slate-500 hover:bg-slate-100" onClick={onClose} aria-label="Закрыть"><X className="h-5 w-5" /></button></header>{children}</section></div>;
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (checked: boolean) => void; label: string }) { return <button type="button" role="switch" aria-checked={checked} aria-label={label} onClick={() => onChange(!checked)} className={`relative h-6 w-11 rounded-full transition ${checked ? "bg-moss-600" : "bg-slate-300"}`}><span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition ${checked ? "left-[22px]" : "left-0.5"}`} /></button>; }
export function Toast({ message, tone = "success", onClose }: { message: string; tone?: "success" | "error"; onClose: () => void }) { return <div role="status" className={`fixed bottom-5 right-5 z-[70] flex max-w-sm items-center gap-3 rounded-xl border bg-white px-4 py-3 text-sm shadow-xl ${tone === "error" ? "border-red-200 text-red-700" : "border-emerald-200 text-slate-700"}`}>{tone === "error" ? <XCircle className="h-5 w-5" /> : <CheckCircle2 className="h-5 w-5 text-emerald-600" />}<span className="flex-1">{message}</span><button onClick={onClose} aria-label="Закрыть уведомление"><X className="h-4 w-4" /></button></div>; }
