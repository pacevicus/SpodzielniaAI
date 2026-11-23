import { screen, waitFor, within } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { AdminUsage, MemberUsage } from "@/components/usage/UsagePanels";
import { defaultPeriod, periodProblem } from "@/lib/usage";
import { API, renderWithClient } from "@/test/render";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const TOTALS = {
  calls: 3,
  unpriced_calls: 0,
  prompt_tokens: 100,
  completion_tokens: 50,
  total_tokens: 150,
  cost_micro: 600,
  cost: "0.0006",
};

const MEMBER = {
  period_from: "2026-10-25",
  period_to: "2026-11-23",
  currency: "PLN",
  totals: TOTALS,
  by_purpose: [{ purpose: "RAG", totals: TOTALS }],
};

const ADMIN = {
  period_from: "2026-10-25",
  period_to: "2026-11-23",
  currency: "PLN",
  system: { ...TOTALS, calls: 1, total_tokens: 40, prompt_tokens: 40, completion_tokens: 0, cost: "0.0001", cost_micro: 80, unpriced_calls: 1 },
  total: { ...TOTALS, calls: 4, total_tokens: 190 },
  members: [{ user_id: "u1", display_name: "Anna Nowak", totals: TOTALS }],
  by_purpose: [{ purpose: "RAG", totals: TOTALS }],
};

describe("AI usage", () => {
  it("shows a member their own totals and the split by task", async () => {
    server.use(http.get(`${API}/me/usage`, () => HttpResponse.json(MEMBER)));
    renderWithClient(<MemberUsage />);
    // The total appears in the card and in the table row for the one purpose.
    expect((await screen.findAllByText("150")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("3").length).toBeGreaterThan(0);
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  it("tells a member about calls that had no model price", async () => {
    server.use(
      http.get(`${API}/me/usage`, () =>
        HttpResponse.json({ ...MEMBER, totals: { ...TOTALS, unpriced_calls: 2 } }),
      ),
    );
    renderWithClient(<MemberUsage />);
    expect(await screen.findByText(/2 .*bez ceny|2 calls without a model price/)).toBeInTheDocument();
  });

  it("shows the administrator every member and the system row", async () => {
    server.use(http.get(`${API}/admin/usage`, () => HttpResponse.json(ADMIN)));
    renderWithClient(<AdminUsage />);
    expect(await screen.findByText("Anna Nowak")).toBeInTheDocument();
    const rows = await screen.findAllByRole("row");
    expect(rows.some((row) => within(row).queryByText(/Praca systemowa|System work/))).toBe(true);
  });

  it("refuses a reversed period before asking the server", async () => {
    let asked = 0;
    server.use(
      http.get(`${API}/me/usage`, () => {
        asked += 1;
        return HttpResponse.json(MEMBER);
      }),
    );
    renderWithClient(<MemberUsage />);
    await screen.findAllByText("150");
    const [from] = screen.getAllByLabelText(/Od|From/);
    fireEvent.change(from!, { target: { value: "2030-01-01" } });
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Pokaż|Show/ })).toBeDisabled();
    await waitFor(() => expect(asked).toBe(1));
  });
});

describe("period rules", () => {
  it("defaults to the last 30 days, inclusive", () => {
    const period = defaultPeriod(new Date("2026-11-23T12:00:00Z"));
    expect(period).toEqual({ from: "2026-10-25", to: "2026-11-23" });
  });

  it("accepts a year and refuses longer or reversed periods", () => {
    expect(periodProblem({ from: "2025-01-01", to: "2026-01-01" })).toBeNull();
    expect(periodProblem({ from: "2025-01-01", to: "2026-01-02" })).toBe("tooLong");
    expect(periodProblem({ from: "2026-02-02", to: "2026-02-01" })).toBe("invalid");
  });
});
