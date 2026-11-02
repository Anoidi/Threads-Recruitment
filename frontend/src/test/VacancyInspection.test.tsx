import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { VacancyInspection } from "../components/VacancyInspection";
import { setOperatorToken, clearOperatorToken } from "../api/auth";

afterEach(() => { clearOperatorToken(); vi.unstubAllGlobals(); });

it("authenticates inspection and source confirmation with the operator session", async () => {
  setOperatorToken("test-operator");
  const fetcher = vi.fn(async (url: string) => new Response(JSON.stringify(
    url.endsWith("/history") ? [] : url.endsWith("/verify-source") ? { verified: true } :
    { status: "READY", issues: [], rates: [], fields: {}, duplicates: [], freshness: { status: "FRESH" } }
  ), { status: 200, headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetcher);
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <VacancyInspection id="18" onChanged={() => {}} />
  </QueryClientProvider>);
  await screen.findByText("Ставки и условия");
  await userEvent.click(screen.getByRole("button", { name: "Я проверил актуальность источника" }));
  expect(await screen.findByText(/Источник подтверждён/)).toBeVisible();
  for (const call of fetcher.mock.calls) {
    expect((call as unknown as [string, RequestInit])[1].headers).toMatchObject({ "X-Operator-Token": "test-operator" });
  }
  expect(fetcher.mock.calls.some(call => call[0].endsWith("/verify-source"))).toBe(true);
});
