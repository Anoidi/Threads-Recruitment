import { FormEvent, useState } from "react";
import { Film, HardDrive, Image as ImageIcon, RefreshCw, Sparkles, WandSparkles } from "lucide-react";
import { useCreateMediaGeneration, useMediaGenerations, useMediaStatus, useRetryMediaGeneration } from "../api/hooks";
import type { MediaGenerationInput, MediaKind } from "../types";
import { Badge, Button, EmptyState, ErrorPanel, LoadingPanel, PageHeader, Toast } from "../components/ui";
import { formatDateTime, labelFor } from "../i18n";

const bytes = (value?: number) => value === undefined ? "—" : value < 1024 ? `${value} B` : value < 1_048_576 ? `${(value / 1024).toFixed(1)} KB` : `${(value / 1_048_576).toFixed(1)} MB`;
const videoPresets = {
  "1:1": [512, 512],
  "16:9": [768, 432],
  "9:16": [432, 768],
} as const;

export function MediaPage() {
  const status = useMediaStatus();
  const generations = useMediaGenerations();
  const create = useCreateMediaGeneration();
  const retry = useRetryMediaGeneration();
  const [kind, setKind] = useState<MediaKind>("IMAGE");
  const [prompt, setPrompt] = useState("");
  const [negativePrompt, setNegativePrompt] = useState("");
  const [width, setWidth] = useState(512);
  const [height, setHeight] = useState(512);
  const [seed, setSeed] = useState(-1);
  const [aspectRatio, setAspectRatio] = useState<keyof typeof videoPresets>("9:16");
  const [toast, setToast] = useState<{ message: string; tone?: "success" | "error" } | null>(null);

  if (status.isLoading && generations.isLoading) return <LoadingPanel />;
  if (!status.data) return <ErrorPanel message="Не удалось получить состояние локальной генерации." />;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const value: MediaGenerationInput = { kind, prompt: prompt.trim(), negative_prompt: negativePrompt.trim() || undefined, width, height, seed };
    create.mutate(value, {
      onSuccess: () => { setPrompt(""); setNegativePrompt(""); setToast({ message: "Задание генерации поставлено в очередь." }); },
      onError: () => setToast({ message: "Не удалось поставить генерацию в очередь. Проверьте локальный медиа-движок.", tone: "error" }),
    });
  };

  const workflowReady = kind === "IMAGE" ? status.data.image_workflow_configured : status.data.video_workflow_configured;
  const canGenerate = kind === "VIDEO" ? status.data.video_available : status.data.mode === "mock" || workflowReady;
  const selectAspect = (ratio: keyof typeof videoPresets) => {
    setAspectRatio(ratio);
    setWidth(videoPresets[ratio][0]);
    setHeight(videoPresets[ratio][1]);
  };

  return <>
    <PageHeader title="Медиа" description="Локальная генерация изображений и видео для будущих публикаций Instagram и TikTok." />

    <section className="grid gap-5 xl:grid-cols-[.85fr_1.15fr]">
      <div className="space-y-5">
        <div className="panel p-5">
          <div className="flex items-start justify-between gap-3"><div className="flex items-center gap-2"><HardDrive className="h-5 w-5 text-moss-600" /><h2 className="font-bold">Локальный медиа-движок</h2></div><Badge value={status.data.status}>{labelFor(status.data.status)}</Badge></div>
          <p className="mt-2 text-sm text-slate-600">{status.data.detail}</p>
          {(status.data.mode === 'mock' || status.data.video_engine === 'local_ffmpeg') && <p role="status" className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">{status.data.mode === 'mock' ? 'Изображения сейчас тестовые: модель не вызывается. ' : ''}{status.data.video_engine === 'local_ffmpeg' ? 'Видео — анимированная карточка с текстом, не AI-сцена. Для настоящей генерации нужны модель и workflow ComfyUI.' : ''}</p>}
          <dl className="mt-4 grid gap-2 text-xs sm:grid-cols-2">
            <div className="rounded-lg bg-slate-50 p-3"><dt className="text-slate-500">Режим</dt><dd className="mt-1 font-semibold">{status.data.mode === "mock" ? "Тестовый" : "Реальный локальный"}</dd></div>
            <div className="rounded-lg bg-slate-50 p-3"><dt className="text-slate-500">Workflow изображения</dt><dd className="mt-1 font-semibold">{status.data.image_workflow_configured ? "Настроен" : "Не указан"}</dd></div>
            <div className="rounded-lg bg-slate-50 p-3"><dt className="text-slate-500">Движок видео</dt><dd className="mt-1 font-semibold">{status.data.video_engine === "local_ffmpeg" ? "Локальный FFmpeg" : status.data.video_engine === "comfyui" ? "ComfyUI" : "Недоступен"}</dd></div>
            <div className="rounded-lg bg-slate-50 p-3"><dt className="text-slate-500">Wan text encoder</dt><dd className="mt-1 font-semibold">{status.data.wan_text_encoder_configured ? "Указан" : "Не указан"}</dd></div>
          </dl>
          <p className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-relaxed text-amber-900">{status.data.hardware_note}</p>
        </div>

        <form className="panel p-5" onSubmit={submit}>
          <div className="flex items-center gap-2"><WandSparkles className="h-5 w-5 text-moss-600" /><h2 className="font-bold">Новое медиа</h2></div>
          <div className="mt-4 grid grid-cols-2 gap-2">
            <button type="button" onClick={() => setKind("IMAGE")} className={`flex items-center justify-center gap-2 rounded-lg border px-3 py-2 text-sm font-semibold ${kind === "IMAGE" ? "border-moss-500 bg-moss-50 text-moss-700" : "border-slate-200"}`}><ImageIcon className="h-4 w-4" />Изображение</button>
            <button type="button" onClick={() => { setKind("VIDEO"); selectAspect(aspectRatio); }} className={`flex items-center justify-center gap-2 rounded-lg border px-3 py-2 text-sm font-semibold ${kind === "VIDEO" ? "border-moss-500 bg-moss-50 text-moss-700" : "border-slate-200"}`}><Film className="h-4 w-4" />Видео</button>
          </div>
          <label className="mt-4 block"><span className="label">Описание</span><textarea className="input min-h-28" required minLength={3} maxLength={10000} value={prompt} onChange={event => setPrompt(event.target.value)} placeholder="Например: современный склад в Кракове, вертикальный кадр, естественный свет" /></label>
          <label className="mt-3 block"><span className="label">Исключить (необязательно)</span><input className="input" value={negativePrompt} onChange={event => setNegativePrompt(event.target.value)} placeholder="текст, логотипы, размытость" /></label>
          {kind === "VIDEO" && <div className="mt-3"><span className="label">Формат видео</span><div className="grid grid-cols-3 gap-2">{(Object.keys(videoPresets) as Array<keyof typeof videoPresets>).map(ratio => <button key={ratio} type="button" onClick={() => selectAspect(ratio)} className={`rounded-lg border px-2 py-2 text-sm font-semibold ${aspectRatio === ratio ? "border-moss-500 bg-moss-50 text-moss-700" : "border-slate-200"}`}>{ratio}<span className="mt-0.5 block text-[10px] font-normal text-slate-500">{videoPresets[ratio][0]}×{videoPresets[ratio][1]}</span></button>)}</div></div>}
          <div className="mt-3 grid grid-cols-3 gap-3">
            <label><span className="label">Ширина</span><input className="input" disabled={kind === "VIDEO"} type="number" min={64} max={2048} step={64} value={width} onChange={event => setWidth(Number(event.target.value))} /></label>
            <label><span className="label">Высота</span><input className="input" disabled={kind === "VIDEO"} type="number" min={64} max={2048} step={64} value={height} onChange={event => setHeight(Number(event.target.value))} /></label>
            <label><span className="label">Seed</span><input className="input" type="number" min={-1} max={2147483647} value={seed} onChange={event => setSeed(Number(event.target.value))} /></label>
          </div>
          {!canGenerate && <p className="mt-3 text-xs text-red-700">Для видео включите локальный FFmpeg fallback или укажите API-workflow ComfyUI.</p>}
          <Button className="mt-4" type="submit" variant="primary" disabled={!canGenerate || prompt.trim().length < 3} loading={create.isPending}><Sparkles className="h-4 w-4" />Создать локально</Button>
        </form>
      </div>

      <div className="panel overflow-hidden">
        <div className="border-b border-slate-100 p-5"><h2 className="font-bold">Последние генерации</h2><p className="mt-1 text-xs text-slate-500">Файлы доступны только локально и проверяются перед показом.</p></div>
        {!generations.data?.length ? <EmptyState title="Генераций пока нет" detail="Создайте первое тестовое изображение. В mock-режиме модель и видеокарта не используются." /> : <div className="grid gap-4 p-4 sm:grid-cols-2">
          {generations.data.map(item => <article key={item.id} className="overflow-hidden rounded-xl border border-slate-200 bg-white">
            {item.asset_url && item.kind === "IMAGE" && <img src={item.asset_url} alt={item.prompt} className="aspect-square w-full bg-slate-100 object-cover" />}
            {item.asset_url && item.kind === "VIDEO" && <video src={item.asset_url} controls className={`${item.width === item.height ? "aspect-square" : item.width > item.height ? "aspect-video" : "aspect-[9/16]"} max-h-80 w-full bg-black object-contain`} />}
            {!item.asset_url && <div className="grid aspect-video place-items-center bg-slate-100 text-slate-400">{item.kind === "IMAGE" ? <ImageIcon className="h-8 w-8" /> : <Film className="h-8 w-8" />}</div>}
            <div className="p-4"><div className="flex items-center justify-between gap-2"><Badge value={item.status}>{labelFor(item.status)}</Badge><span className="text-[11px] text-slate-400">{formatDateTime(item.created_at)}</span></div><p className="mt-3 line-clamp-3 text-sm font-medium text-slate-700">{item.prompt}</p><p className="mt-2 text-xs text-slate-500">{item.width}×{item.height} · {bytes(item.byte_size)}</p>{item.error && <p className="mt-2 text-xs text-red-700">{item.error}</p>}{item.status === "FAILED" && <Button className="mt-3" onClick={() => retry.mutate(item.id)} loading={retry.isPending}><RefreshCw className="h-4 w-4" />Повторить</Button>}</div>
          </article>)}
        </div>}
      </div>
    </section>
    {toast && <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} />}
  </>;
}
