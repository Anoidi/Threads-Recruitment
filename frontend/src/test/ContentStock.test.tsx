import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { expect, it } from "vitest";
import { ContentStock } from "../components/ContentStock";
import type { ContentStock as Stock } from "../types";

const stock: Stock = {
  target_days: 3, posts_per_day: 3, ready_scheduled: 4, ready_reserve: 2, coverage_days: 2,
  next_publication_at: "2026-10-28T17:30:00Z", missing_slots: ["2026-10-29T08:00:00Z"],
  covered_slots: 4, stale_scheduled_ids: [10], rejected_count: 1,
  rejected_posts: [{ id: 12, reason: "Условия не подтверждены", repairs: 2 }],
  auto_content_plan: true, timezone: "Europe/Warsaw", refill_batches_today: 1, refill_daily_limit: 4,
};

it("shows readiness, missing slots and actionable rejection reasons", async () => {
  render(<MemoryRouter><ContentStock stock={stock} /></MemoryRouter>);
  expect(screen.getByRole("region", { name: "Готовность контента" })).toHaveTextContent("Не заполнено слотов: 1");
  await userEvent.click(screen.getByText("Не прошли проверку: 1"));
  expect(screen.getByText("Условия не подтверждены")).toBeVisible();
  expect(screen.getByRole("link", { name: "Пост #12" })).toHaveAttribute("href", "/content?search=12");
});

it("explains when bounded generation stops for the day", () => {
  render(<MemoryRouter><ContentStock stock={{ ...stock, refill_batches_today: 4 }} /></MemoryRouter>);
  expect(screen.getByText(/Достигнут дневной предел генерации/)).toBeInTheDocument();
});
