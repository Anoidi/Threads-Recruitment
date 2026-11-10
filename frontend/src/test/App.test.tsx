import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { mockApi, resetMockData } from "../api/mock";
import { formatExperiments, attribution } from "../api/client";
import { scheduleInputDefaults } from "../utils/datetime";

function renderAt(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={queryClient}><MemoryRouter initialEntries={[path]}><App /></MemoryRouter></QueryClientProvider>);
}

beforeEach(() => resetMockData());
afterEach(() => vi.restoreAllMocks());

describe("critical recruitment flows", () => {
  it("shows the next performance test and missing measurement coverage", async () => {
    const data = await mockApi.analytics();
    vi.spyOn(mockApi, "analytics").mockResolvedValue({ ...data, growth_learning: {
      measurement_window: "24–30ч", window_days: 30,
      coverage: { published: 12, comparable: 6, pending: 1, missing_window: 4, unavailable: 1, zero_views: 0 },
      recommendation: "Проверить другую подачу на той же теме.", limitations: [], groups: [],
      next_tests: [{ language: "uk", dimension: "format", status: "explore", preferred: "FAQ", comparison: "QUESTION", reason: "Разведка, выборка пока мала." }],
    } });
    renderAt("/analytics");
    expect(await screen.findByText("Что проверит следующий контент-план")).toBeInTheDocument();
    expect(screen.getByText("Проверить другую подачу на той же теме.")).toBeInTheDocument();
    expect(screen.getByText(/пропущено окно 4/)).toBeInTheDocument();
    expect(screen.getByText(/Разведка, выборка пока мала/)).toBeInTheDocument();
  });

  it("shows the comparable sample and uncertainty behind analytics advice", async () => {
    const data = await mockApi.analytics();
    vi.spyOn(mockApi, "analytics").mockResolvedValue({ ...data, editorial_analysis: {
      comparable_posts: 2, insufficient_data: true,
      cohorts: [{ content_type: "FAQ", language: "uk", age_bucket: "24–48h", posts: 2, engagement_rate: 5, evidence: "limited" }],
      limitations: ["Сигналы кандидатов не равны найму."],
    } });
    renderAt("/analytics");
    expect(await screen.findByText("Сопоставимых публикаций: 2 из 18.")).toBeInTheDocument();
    expect(screen.getByText("Недостаточно данных для сравнения форматов")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Группы сравнения" })).toHaveTextContent("малая выборка");
    expect(screen.queryByText("Рейтинг по качеству откликов кандидатов")).not.toBeInTheDocument();
  });
  it("shows the format experiment with its sample sizes and exclusions", async () => {
    vi.spyOn(formatExperiments, "load").mockResolvedValue({
      experiments: [],
      current: {
        experiment_id: 1, name: "Форматы: Ставка", status: "RUNNING",
        subject: "PAY", subject_label: "Ставка", fact: "23 зл/год netto", vacancy_id: 8,
        metric: "CANDIDATES", hypothesis: "Один и тот же факт в разных форматах.",
        started_at: null, concluded_at: null,
        variants: [
          { key: "FACT", format: "FACT", label: "Факт и что уточнить", assigned: 2, verified: 2, excluded: 0, published: 2, measured: 2, signals: 3, candidates: 1, median_views: 120, posts: [] },
          { key: "QUESTION", format: "QUESTION", label: "Вопрос аудитории", assigned: 2, verified: 1, excluded: 1, published: 1, measured: 1, signals: 0, candidates: 0, median_views: 40, posts: [] },
        ],
        excluded: [{ post_id: 9, variant: "QUESTION", reason: "format" }],
        verdict: "RESULT",
        verdict_text: "Больше подтверждённых кандидатов принёс формат «Факт и что уточнить»: 1 кандидат.",
        winner: { key: "FACT", label: "Факт и что уточнить", candidates: 1, signals: 3, published: 2 },
        caveats: ["Отклики кандидатов не равны подтверждённому найму."],
      },
    });
    renderAt("/analytics");
    expect(await screen.findByText("Эксперимент форматов: одна и та же фактура, разная подача")).toBeInTheDocument();
    expect(screen.getByText("23 зл/год netto")).toBeInTheDocument();
    expect(screen.getAllByText("Факт и что уточнить").length).toBeGreaterThan(0);
    expect(screen.getByText(/Больше подтверждённых кандидатов/)).toBeInTheDocument();
    // The number of posts and the exclusions are visible, not just the winner.
    expect(screen.getByText(/Из сравнения исключено постов/)).toBeInTheDocument();
  });
  it("compares the experiment inside equal windows and shows the candidate funnel", async () => {
    vi.spyOn(formatExperiments, "load").mockResolvedValue({
      experiments: [],
      current: {
        experiment_id: 1, name: "Форматы: Ставка", status: "RUNNING",
        subject: "PAY", subject_label: "Ставка", fact: "23 зл/год netto", vacancy_id: 8,
        metric: "CANDIDATES", hypothesis: "Один и тот же факт в разных форматах.",
        started_at: null, concluded_at: null,
        variants: [
          { key: "FACT", format: "FACT", label: "Факт", assigned: 2, verified: 2, excluded: 0, published: 2, measured: 2, signals: 3, candidates: 2, median_views: 120, unique_candidates: 2, window_posts: 2, posts: [] },
        ],
        excluded: [],
        verdict: "RESULT", verdict_text: "Больше кандидатов принёс формат «Факт».",
        winner: { key: "FACT", label: "Факт", candidates: 2, signals: 3, published: 2, posts: 2, candidates_per_post: 1 },
        caveats: [],
        primary_window: 24,
        statistics: { min_posts_per_variant: 2, clear_lead_ratio: 2, primary_window: 24, closed_windows: [24], posts_per_variant: { FACT: 2 }, shared_candidates: 0, sample_note: "Сравнение по 24 ч." },
        windows: [
          { hours: 24, label: "24 ч", complete: true, variants: [{ key: "FACT", label: "Факт", published: 2, posts: 2, signals: 3, candidates: 2, unique_people: 2, candidates_per_post: 1, views_median: 120 }] },
          { hours: 48, label: "48 ч", complete: false, variants: [{ key: "FACT", label: "Факт", published: 2, posts: 0, signals: 0, candidates: 0, unique_people: 0, candidates_per_post: null }] },
        ],
      },
    });
    vi.spyOn(attribution, "load").mockResolvedValue({
      quality: { signals_total: 4, signals_ignored: 0, signals_without_post: 1, signals_without_vacancy: 0, candidates_total: 2, candidates_without_contact: 0, candidates_without_consent: 1, duplicate_handles: 0, duplicate_candidate_records: 0, posts_with_signals: 2, identity: "threads_username → confirmed contact → record id", limitations: ["Сигнал — это интерес."] },
      posts: [],
      vacancies: [{ vacancy_id: 8, title: "Склад Краків", published_posts: 2, signals: 4, unique_people: 3, candidates: 2, contacted: 1, qualified: 1, hired: 0, rejected: 0, invited: null }],
      limitations: ["Порядок оценки: кандидаты → сигналы → охват."],
    });
    renderAt("/analytics");
    expect(await screen.findByText("Сравнение по равным окнам наблюдения")).toBeInTheDocument();
    expect(screen.getAllByText("не закрыто").length).toBeGreaterThan(0);
    expect(screen.getByText("2 канд. / 2 пост.")).toBeInTheDocument();
    expect(screen.getByText("Атрибуция кандидатов: от поста до человека")).toBeInTheDocument();
    expect(screen.getByText("Склад Краків")).toBeInTheDocument();
    // The interview stage is not tracked; it must read as N/A, not as zero.
    expect(screen.getByText("N/A")).toBeInTheDocument();
    expect(screen.getByText(/Сигналов без привязки к посту: 1/)).toBeInTheDocument();
  });
  it("preserves rich salary text while editing a vacancy", async () => {
    const user = userEvent.setup(); renderAt("/vacancies");
    await user.click(await screen.findByRole("button", { name: /изменить працівник складу/i }));

    const salary = screen.getByLabelText(/основная ставка/i);
    const studentSalary = screen.getByLabelText(/ставка для студентов/i);
    await user.clear(salary);
    await user.type(salary, "25,50–29,00 zł netto/год, после 3 месяцев 31,00 zł");
    await user.clear(studentSalary);
    await user.type(studentSalary, "31,40 zł netto/год для студентов");

    expect(salary).toHaveValue("25,50–29,00 zł netto/год, после 3 месяцев 31,00 zł");
    expect(studentSalary).toHaveValue("31,40 zł netto/год для студентов");
    await user.click(screen.getByRole("button", { name: /сохранить изменения/i }));

    expect(await screen.findByText(/25,50–29,00 zł netto\/год, после 3 месяцев 31,00 zł/i)).toBeInTheDocument();
  });

  it("keeps the housing and transport conditions when a vacancy is edited", async () => {
    const user = userEvent.setup(); renderAt("/vacancies");
    await user.click(await screen.findByRole("button", { name: /изменить працівник складу/i }));

    const housing = screen.getByLabelText(/жильё/i);
    const transport = screen.getByLabelText(/транспорт/i);
    expect(housing).toHaveValue("600 PLN/місяць, кімната на двох");
    expect(transport).toHaveValue("Корпоративний транспорт від зупинок");

    const title = screen.getByLabelText(/название/i);
    await user.clear(title);
    await user.type(title, "Працівник складу (новий графік)");
    await user.click(screen.getByRole("button", { name: /сохранить изменения/i }));

    // The conditions are source text, not a boolean flag: editing an
    // unrelated field must not replace them with "provided".
    expect(await screen.findByText(/600 PLN\/місяць, кімната на двох/)).toBeInTheDocument();
  });

  it("offers phone or private messages as CTAs and does not expose Telegram contact editing", async () => {
    renderAt("/settings");

    expect(await screen.findByLabelText(/телефон \/ контакт/i)).toBeEnabled();
    expect(screen.queryByLabelText(/контакт в telegram/i)).not.toBeInTheDocument();
    expect(screen.getByText(/telegram-контакт не используется в публикациях/i)).toBeInTheDocument();
    expect(screen.getByText(/если телефон не указан.*приватні повідомлення/i)).toBeInTheDocument();
  });

  it("approves a reviewed post and schedules it", async () => {
    const user = userEvent.setup(); renderAt("/content");
    await user.click(await screen.findByRole("button", { name: /одобрить/i }));
    expect(await screen.findByText("Одобрено")).toBeInTheDocument();
    await user.click(await screen.findByRole("button", { name: /^запланировать$/i }));
    const date = screen.getByLabelText(/дата и время публикации/i);
    await user.clear(date); await user.type(date, scheduleInputDefaults().defaultValue);
    await user.click(screen.getByRole("button", { name: /запланировать публикацию/i }));
    expect(await screen.findByText(/публикация запланирована/i)).toBeInTheDocument();
  });

  it("cancels a scheduled post through the schedule cancellation flow", async () => {
    const user = userEvent.setup(); renderAt("/calendar");
    const scheduledContent = await screen.findByText(/які документи потрібні/i);
    const card = scheduledContent.closest("article");
    expect(card).not.toBeNull();
    await user.click(within(card!).getByRole("button", { name: /^отменить$/i }));
    expect(await screen.findByText(/публикация снята с расписания/i)).toBeInTheDocument();
    const movedCard = (await screen.findByText(/які документи потрібні/i)).closest("article");
    expect(within(movedCard!).getByText(/не запланировано/i)).toBeInTheDocument();
  });

  it("reviews a community reply before approval", async () => {
    const user = userEvent.setup(); renderAt("/community");
    const message = await screen.findByText(/можу вийти вже завтра/i);
    await user.click(message);
    const reply = screen.getByLabelText(/ответ, предложенный ии/i);
    await user.clear(reply); await user.type(reply, "Дякуємо! Рекрутер напише вам приватно.");
    await user.click(screen.getByRole("button", { name: /^одобрить ответ$/i }));
    expect(await screen.findByText(/ответ одобрен/i)).toBeInTheDocument();
    await user.click(message);
    await user.click(screen.getByRole("button", { name: /отправить одобренный ответ/i }));
    expect(await screen.findByText(/ответ поставлен в очередь/i)).toBeInTheDocument();
  });

  it("converts an intentional candidate signal only after a contact is confirmed", async () => {
    const user = userEvent.setup(); renderAt("/candidates");
    const row = (await screen.findByText("@marta_krk")).closest("tr");
    expect(row).not.toBeNull();
    await user.click(within(row!).getByRole("button", { name: /подтвердить контакт/i }));
    // The contact has to be stated before a candidate record can be created.
    const contact = await screen.findByLabelText(/^контакт$/i);
    await user.clear(contact);
    await user.type(contact, "@marta_krk");
    await user.click(screen.getByRole("button", { name: /создать кандидата/i }));
    expect(await screen.findByText(/контакт подтверждён/i)).toBeInTheDocument();
  });

  it("toggles an agent and runs it", async () => {
    const user = userEvent.setup(); renderAt("/agents");
    const analyst = (await screen.findByText("ANALYST")).closest("article");
    expect(analyst).not.toBeNull();
    await user.click(within(analyst!).getByRole("switch"));
    const enabledSwitch = await screen.findByRole("switch", { name: /отключить агента analyst/i });
    const enabledCard = enabledSwitch.closest("article");
    await user.click(within(enabledCard!).getByRole("button", { name: /запустить/i }));
    expect(await screen.findByText(/агент analyst поставлен в очередь/i)).toBeInTheDocument();
    expect(await within(enabledCard!).findByText(/analyst mock run completed safely/i)).toBeInTheDocument();
    expect(await screen.findByText(/агент analyst завершил задачу/i)).toBeInTheDocument();
  });

  it("renders an agent role the page does not know about", async () => {
    // A role added on the backend must not blank the whole page.
    const base = await mockApi.agents();
    vi.spyOn(mockApi, "agents").mockResolvedValue([
      ...base,
      { id: "FUTURE_AGENT", name: "FUTURE_AGENT" as never, enabled: true, status: "IDLE", route: "free-default" },
    ]);
    renderAt("/agents");
    expect(await screen.findByText("FUTURE_AGENT")).toBeInTheDocument();
    expect(await screen.findByText("ANALYST")).toBeInTheDocument();
  });

  it("shows connected Threads state and can test it", async () => {
    const user = userEvent.setup(); renderAt("/account");
    expect(await screen.findByText("@hire_demo_pl")).toBeInTheDocument();
    expect(await screen.findByText(/подключён тестовый аккаунт/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /проверить подключение/i }));
    expect(await screen.findByText(/проверка подключения выполнена успешно/i)).toBeInTheDocument();
  });

  it("shows the three supported video aspect ratios and does not fake video in browser mock mode", async () => {
    const user = userEvent.setup(); renderAt("/media");
    await user.click(await screen.findByRole("button", { name: /^видео$/i }));
    expect(screen.getByRole("button", { name: /1:1/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /16:9/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /9:16/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /создать локально/i })).toBeDisabled();
    expect(screen.getByText(/включите локальный ffmpeg fallback/i)).toBeInTheDocument();
  });
});


it("shows the server reason when calendar rescheduling fails", async () => {
  vi.spyOn(mockApi, "transitionPost").mockRejectedValue(new Error("Время публикации должно быть в будущем"));
  const user = userEvent.setup(); renderAt("/calendar");
  const card = (await screen.findByText(/які документи потрібні/i)).closest("article")!;
  await user.click(within(card).getByRole("button", { name: "Перенести" }));
  fireEvent.change(screen.getByLabelText("Дата и время публикации"), { target: { value: scheduleInputDefaults().defaultValue } });
  await user.click(screen.getByRole("button", { name: "Сохранить расписание" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Время публикации должно быть в будущем");
});

it("offers publish now for a scheduled post in the calendar", async () => {
  const post = (await mockApi.posts()).find(p => p.status === "SCHEDULED")!;
  const publish = vi.spyOn(mockApi, "transitionPost").mockResolvedValue({ ...post, status: "PUBLISHING" });
  const user = userEvent.setup(); renderAt("/calendar");
  const card = (await screen.findByText(/які документи потрібні/i)).closest("article")!;
  await user.click(within(card).getByRole("button", { name: "Опубликовать сейчас" }));
  expect(publish).toHaveBeenCalledWith(post.id, "publish", undefined);
});
