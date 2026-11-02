import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Camera, CheckCircle2, ExternalLink, KeyRound, Link2, Music2, PlugZap, RefreshCw, ShieldCheck, Unplug, UserRound } from "lucide-react";
import { useAccount, useAccountAction, useSocialAccounts, useSocialAction } from "../api/hooks";
import { capabilities } from "../api/client";
import type { SocialAccount } from "../types";
import { Badge, Button, ErrorPanel, LoadingPanel, PageHeader, Toast } from "../components/ui";
import { formatDateTime, labelFor } from "../i18n";

const formatDate = formatDateTime;

const parameterLabels: Record<string, string> = {
  state: "Параметр состояния OAuth недействителен. Попробуйте ещё раз.",
  token_exchange: "Meta не вернула рабочий токен. Проверьте THREADS_APP_ID и THREADS_APP_SECRET.",
  auth: "Сохранённый токен Threads отклонён. Проверьте соединение или подключитесь заново.",
  rate_limit: "Threads ограничил частоту запросов. Подождите и повторите попытку.",
  unavailable: "Threads API не ответил вовремя. Проверьте подключение и повторите попытку.",
  config: "В приложении не настроены данные Meta. Сначала их должен указать администратор.",
  denied: "Подтверждение было отменено до подключения аккаунта.",
};

const permissionLabels: Record<string, string> = {
  threads_basic: "Чтение данных профиля",
  threads_content_publish: "Создание и публикация записей",
  threads_manage_replies: "Чтение и управление ответами",
  threads_manage_insights: "Просмотр статистики аккаунта и публикаций",
  threads_read_replies: "Чтение входящих ответов",
  threads_manage_mentions: "Работа с упоминаниями",
  threads_keyword_search: "Поиск публичных обсуждений Threads",
};

export function AccountPage() {
  const query = useAccount();
  const socialQuery = useSocialAccounts();
  const action = useAccountAction();
  const socialAction = useSocialAction();
  const capabilitiesQuery = useQuery({ queryKey: ["threads-capabilities"], queryFn: () => capabilities.load() });
  const [toast, setToast] = useState<{ message: string; tone?: "success" | "error" } | null>(null);
  const [accessToken, setAccessToken] = useState("");

  useEffect(() => {
    if (!query.data) return;
    const params = new URLSearchParams(window.location.search);
    const connected = params.get("threads_connected");
    const error = params.get("threads_error");
    const socialConnected = params.get("social_connected");
    const socialError = params.get("social_error");
    if (connected === "1") {
      setToast({ message: "Аккаунт Threads успешно подключён." });
      window.history.replaceState({}, "", window.location.pathname);
    } else if (error) {
      const label = parameterLabels[error] ?? `Ошибка подключения: ${error}`;
      setToast({ message: label, tone: "error" });
      window.history.replaceState({}, "", window.location.pathname);
    } else if (socialConnected) {
      setToast({ message: `Аккаунт ${socialConnected === "instagram" ? "Instagram" : "TikTok"} успешно подключён.` });
      window.history.replaceState({}, "", window.location.pathname);
    } else if (socialError) {
      setToast({ message: `Не удалось подключить социальный аккаунт: ${socialError}`, tone: "error" });
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, [query.data]);

  if (query.isLoading) return <LoadingPanel />;
  if (!query.data) return <ErrorPanel message="Не удалось загрузить данные аккаунта Threads." />;

  const account = query.data;
  const blocked = account.api_status === "BLOCKED" || account.token_status === "BLOCKED";
  const publishingReady = account.mock_mode || (account.publishing_ready ?? (account.connected && !blocked));
  const perform = (type: "connect" | "test" | "disconnect") => {
    action.mutate(type, {
      onSuccess: next => {
        // The connection check also re-reads the granted permissions, so the
        // report below must be refreshed together with the account.
        capabilitiesQuery.refetch();
        setToast(next.api_status === "BLOCKED"
          ? { message: `Meta блокирует доступ к API${next.last_error_trace_id ? `. Trace ID: ${next.last_error_trace_id}` : ""}`, tone: "error" }
          : { message: type === "connect" ? "Аккаунт Threads подключён." : type === "test" ? "Проверка подключения выполнена успешно." : "Аккаунт Threads отключён." });
      },
      onError: () => setToast({ message: "Не удалось выполнить действие с аккаунтом Threads.", tone: "error" }),
    });
  };

  const importToken = () => {
    action.mutate({ action: "token", payload: { access_token: accessToken } }, {
      onSuccess: () => { setAccessToken(""); setToast({ message: "Аккаунт Threads безопасно подключён." }); },
      onError: () => setToast({ message: "Meta отклонила токен или Threads API недоступен.", tone: "error" }),
    });
  };

  const performSocial = (platform: SocialAccount["platform"], actionName: "connect" | "test" | "disconnect") => {
    socialAction.mutate({ platform, action: actionName }, {
      onSuccess: () => setToast({ message: actionName === "connect" ? "Открываем официальное окно авторизации." : actionName === "test" ? "Подключение проверено." : "Аккаунт отключён." }),
      onError: () => setToast({ message: "Не удалось выполнить действие с социальным аккаунтом.", tone: "error" }),
    });
  };

  return <>
    <PageHeader
      title="Аккаунт Threads"
      description="Управление подключением Meta для публикаций, работы с сообществом и аналитики."
      actions={account.connected ? <Button loading={action.isPending && action.variables === "test"} onClick={() => perform("test")}><RefreshCw className="h-4 w-4" />Проверить подключение</Button> : <Button variant="primary" loading={action.isPending && action.variables === "connect"} onClick={() => perform("connect")}><Link2 className="h-4 w-4" />Подключить Threads</Button>}
    />

    <section className="grid gap-5 xl:grid-cols-[1.15fr_.85fr]">
      <div className="space-y-5">
        <div className="panel p-5">
          <div className="flex items-center gap-2"><ShieldCheck className="h-5 w-5 text-moss-600" /><h2 className="font-bold">Разрешения Threads</h2></div>
          <p className="mt-1 text-xs leading-relaxed text-slate-500">Что Meta разрешила этому аккаунту. Каждое отсутствующее разрешение — это часть аудитории, которую система не видит.</p>
          {!capabilitiesQuery.data ? <p className="mt-3 text-sm text-slate-500">Отчёт о разрешениях недоступен.</p> : <>
            <p className={`mt-3 rounded-lg px-3 py-2 text-sm font-medium ${capabilitiesQuery.data.complete ? "bg-emerald-50 text-emerald-800" : "bg-amber-50 text-amber-900"}`}>{capabilitiesQuery.data.summary}</p>
            <div className="mt-3 space-y-1.5">
              {capabilitiesQuery.data.scopes.map(scope => <div key={scope.scope} className="flex items-start gap-3 rounded-lg bg-slate-50 px-3 py-2">
                <span className={`mt-0.5 text-xs font-bold ${scope.granted ? "text-emerald-700" : "text-amber-700"}`}>{scope.granted ? "ЕСТЬ" : "НЕТ"}</span>
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-slate-700">{scope.title} <span className="font-mono text-[11px] text-slate-400">{scope.scope}</span></p>
                  <p className="text-xs text-slate-500">{scope.granted ? scope.unlocks : scope.cost}</p>
                </div>
              </div>)}
            </div>
            {capabilitiesQuery.data.audience_impact.length > 0 && <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50/60 p-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-amber-800">Что из-за этого не видно</p>
              <ul className="mt-1.5 list-disc space-y-1 pl-4 text-xs text-amber-900">{capabilitiesQuery.data.audience_impact.map(item => <li key={item}>{item}</li>)}</ul>
              <p className="mt-2 text-xs text-amber-800">Запросить: {capabilitiesQuery.data.where}</p>
            </div>}
            {capabilitiesQuery.data.how_to_get && capabilitiesQuery.data.how_to_get.length > 0 && <ol className="mt-3 space-y-1.5 rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-700">
              {capabilitiesQuery.data.how_to_get.map(step => <li key={step}>{step}</li>)}
            </ol>}
          </>}
        </div>
        {!account.mock_mode && <form className="panel p-5" onSubmit={event => { event.preventDefault(); importToken(); }}>
          <div className="flex items-center gap-2"><KeyRound className="h-5 w-5 text-moss-600" /><h2 className="font-bold">{account.connected ? "Заменить токен Meta" : "Подключить с помощью токена Meta"}</h2></div>
          <p className="mt-1 text-xs leading-relaxed text-slate-500">Используйте официальный генератор пользовательских токенов, если локальный OAuth недоступен. Новый токен будет проверен до замены зашифрованного сохранённого токена.</p>
          <label className="mt-4 block"><span className="label">Токен доступа Threads</span><input aria-label="Токен доступа Threads" className="input font-mono" type="password" autoComplete="new-password" required minLength={20} value={accessToken} onChange={event => setAccessToken(event.target.value)} /></label>
          <Button className="mt-3" type="submit" variant="primary" disabled={accessToken.length < 20} loading={action.isPending && typeof action.variables === "object"}><ShieldCheck className="h-4 w-4" />{account.connected ? "Проверить и заменить" : "Проверить и подключить"}</Button>
        </form>}
        <div className="panel overflow-hidden">
          <div className={`flex flex-col gap-4 border-b p-5 sm:flex-row sm:items-center sm:justify-between ${blocked ? "border-red-200 bg-red-50/70" : publishingReady ? "border-emerald-100 bg-emerald-50/50" : "border-slate-100 bg-slate-50"}`}>
            <div className="flex items-center gap-3">
              <div className={`rounded-xl p-3 ${blocked ? "bg-red-100 text-red-700" : publishingReady ? "bg-emerald-100 text-emerald-700" : "bg-slate-200 text-slate-500"}`}>{publishingReady ? <PlugZap className="h-6 w-6" /> : <Unplug className="h-6 w-6" />}</div>
              <div><div className="flex flex-wrap items-center gap-2"><h2 className="font-bold">{account.mock_mode ? "Подключён тестовый аккаунт" : blocked ? "Доступ Meta заблокирован" : publishingReady ? "Аккаунт готов к публикации" : account.connected ? "Токен подключён, публикация недоступна" : "Аккаунт не подключён"}</h2><Badge value={blocked ? "BLOCKED" : account.token_status}>{account.mock_mode ? "ТЕСТОВЫЙ РЕЖИМ" : blocked ? "API: ЗАБЛОКИРОВАН" : `ТОКЕН: ${labelFor(account.token_status)}`}</Badge></div><p className="mt-1 text-sm text-slate-600">{account.mock_mode ? "Включена безопасная имитация. Действия на этой странице не обращаются к Meta." : blocked ? "Токен сохранён, но Meta не разрешает вызовы API. Запланированные публикации приостановлены." : publishingReady ? "Рабочая интеграция доступна для одобренных процессов." : account.connected ? "Проверьте доступ API перед публикацией." : "Подключите профессиональный профиль Threads, чтобы включить рабочие процессы."}</p></div>
            </div>
            {account.connected && <Button variant="danger" loading={action.isPending && action.variables === "disconnect"} onClick={() => perform("disconnect")}><Unplug className="h-4 w-4" />Отключить</Button>}
          </div>

          <dl className="grid gap-px bg-slate-100 sm:grid-cols-2">
            <div className="bg-white p-4 sm:p-5"><dt className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-400"><UserRound className="h-3.5 w-3.5" />Имя пользователя</dt><dd className="mt-2 font-semibold text-slate-800">{account.username ?? "Не подключён"}</dd></div>
            <div className="bg-white p-4 sm:p-5"><dt className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-slate-400"><KeyRound className="h-3.5 w-3.5" />ID профиля</dt><dd className="mt-2 font-mono text-sm font-semibold text-slate-800">{account.profile_id ?? "—"}</dd></div>
            <div className="bg-white p-4 sm:p-5"><dt className="text-xs font-semibold uppercase tracking-wide text-slate-400">Токен истекает</dt><dd className="mt-2 text-sm font-semibold text-slate-800">{formatDate(account.token_expires_at)}</dd></div>
            <div className="bg-white p-4 sm:p-5"><dt className="text-xs font-semibold uppercase tracking-wide text-slate-400">Последняя синхронизация</dt><dd className="mt-2 text-sm font-semibold text-slate-800">{formatDate(account.last_sync)}</dd></div>
          </dl>
        </div>

        {(blocked || account.last_error) && <div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">
          <strong>{blocked ? "Meta заблокировала доступ к Threads API." : "Последняя ошибка подключения:"}</strong>
          {account.last_error && <p className="mt-1 break-words">{account.last_error}</p>}
          <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2">
            {account.last_error_code && <div><dt className="font-semibold">Код Meta</dt><dd>{account.last_error_code}{account.last_error_subcode ? ` / ${account.last_error_subcode}` : ""}</dd></div>}
            {account.last_error_trace_id && <div><dt className="font-semibold">Meta Trace ID</dt><dd className="break-all font-mono">{account.last_error_trace_id}</dd></div>}
            {account.blocked_at && <div><dt className="font-semibold">Заблокировано</dt><dd>{formatDate(account.blocked_at)}</dd></div>}
            {account.scheduled_posts_affected !== undefined && <div><dt className="font-semibold">Приостановлено публикаций</dt><dd>{account.scheduled_posts_affected}</dd></div>}
          </dl>
          {blocked && <p className="mt-3">Откройте Meta for Developers → Alerts, устраните ограничение, затем нажмите «Проверить подключение».</p>}
        </div>}
      </div>

      <div className="space-y-5">
        <div className="panel p-5">
          <div className="flex items-center gap-2"><ShieldCheck className="h-5 w-5 text-moss-600" /><h2 className="font-bold">Предоставленные разрешения</h2></div>
          <p className="mt-1 text-xs leading-relaxed text-slate-500">Здесь показаны только разрешения, возвращённые Meta.</p>
          {account.permissions.length > 0 ? <ul className="mt-4 space-y-3">{account.permissions.map(permission => <li key={permission} className="flex items-start gap-3 text-sm"><CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" /><div><p className="font-semibold text-slate-700">{permissionLabels[permission] ?? permission.replace(/_/g, " ")}</p><p className="mt-0.5 break-all font-mono text-[11px] text-slate-400">{permission}</p></div></li>)}</ul> : <p className="mt-5 rounded-lg bg-slate-50 px-3 py-5 text-center text-sm text-slate-500">Разрешения не предоставлены.</p>}
        </div>

        <div className="panel p-5">
          <h2 className="font-bold">Состояние подключения</h2>
          <dl className="mt-4 space-y-3 text-sm">
            <div className="flex items-center justify-between gap-4 border-b border-slate-100 pb-3"><dt className="text-slate-500">Последний успешный вызов API</dt><dd className="text-right font-semibold text-slate-700">{formatDate(account.last_successful_call)}</dd></div>
            <div className="flex items-center justify-between gap-4"><dt className="text-slate-500">Статус токена</dt><dd><Badge value={account.connected ? "ONLINE" : "OFFLINE"}>{account.connected ? "Подключён" : "Отключён"}</Badge></dd></div>
            <div className="flex items-center justify-between gap-4"><dt className="text-slate-500">Готовность публикации</dt><dd><Badge value={publishingReady ? "ONLINE" : blocked ? "BLOCKED" : "DEGRADED"}>{publishingReady ? "Готово" : blocked ? "Заблокировано Meta" : "Требуется проверка"}</Badge></dd></div>
          </dl>
          <a className="mt-5 inline-flex items-center gap-1.5 text-xs font-semibold text-moss-700 hover:text-moss-600" href="https://www.threads.net/" target="_blank" rel="noreferrer">Открыть Threads <ExternalLink className="h-3.5 w-3.5" /></a>
        </div>
      </div>
    </section>

    <section className="panel mt-5 p-5">
      <div className="flex items-center gap-2"><Link2 className="h-5 w-5 text-moss-600" /><h2 className="font-bold">Instagram и TikTok</h2></div>
      <p className="mt-1 text-xs leading-relaxed text-slate-500">Подключение выполняется напрямую через официальные OAuth API. Пароли и токены в браузере не вводятся.</p>
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        {(socialQuery.data ?? []).map(social => {
          const instagram = social.platform === "INSTAGRAM";
          const Icon = instagram ? Camera : Music2;
          const title = instagram ? "Instagram" : "TikTok";
          return <article key={social.platform} className={`rounded-xl border p-4 ${social.connected ? "border-emerald-200 bg-emerald-50/40" : "border-slate-200 bg-slate-50/60"}`}>
            <div className="flex items-start justify-between gap-3"><div className="flex items-center gap-3"><div className="rounded-lg bg-white p-2 text-slate-700"><Icon className="h-5 w-5" /></div><div><h3 className="font-semibold">{title}</h3><p className="text-xs text-slate-500">{social.connected ? social.username ?? "Подключён" : "Не подключён"}</p></div></div><Badge value={social.connected ? social.token_status : social.provider_configured ? "OFFLINE" : "CONFIG"}>{social.connected ? labelFor(social.token_status) : social.provider_configured ? "Не подключён" : "Не настроен"}</Badge></div>
            <p className="mt-3 text-xs leading-relaxed text-slate-600">{instagram ? "Нужен профессиональный аккаунт Instagram и разрешение публикации контента." : "Для публичных автопубликаций TikTok-приложению потребуется аудит; до этого доступен приватный режим."}</p>
            {social.last_error && <p className="mt-2 text-xs text-red-700">{social.last_error}</p>}
            <div className="mt-4 flex flex-wrap gap-2">{social.connected ? <><Button onClick={() => performSocial(social.platform, "test")} loading={socialAction.isPending}><RefreshCw className="h-4 w-4" />Проверить</Button><Button variant="danger" onClick={() => performSocial(social.platform, "disconnect")} loading={socialAction.isPending}><Unplug className="h-4 w-4" />Отключить</Button></> : <Button variant="primary" disabled={!social.provider_configured} onClick={() => performSocial(social.platform, "connect")} loading={socialAction.isPending}><Link2 className="h-4 w-4" />{social.provider_configured ? "Подключить" : "Настроить OAuth"}</Button>}</div>
          </article>;
        })}
      </div>
      {!socialQuery.data && socialQuery.isLoading && <p className="mt-4 text-sm text-slate-500">Загружаем состояние подключений…</p>}
    </section>

    {toast && <Toast message={toast.message} tone={toast.tone} onClose={() => setToast(null)} />}
  </>;
}

