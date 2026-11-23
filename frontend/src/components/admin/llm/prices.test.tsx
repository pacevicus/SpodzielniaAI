import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it } from "vitest";

import { ModelsPanel } from "@/components/admin/llm/Catalog";
import type { LlmModel, LlmStatus, Provider } from "@/lib/llm-admin";
import { API, renderWithClient } from "@/test/render";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// Only what the models table reads; the rest of the status is not used by this panel.
const STATUS = { allow_cloud: false, local_endpoints: [], cloud_hosts: [] } as unknown as LlmStatus;
const PROVIDER = { id: "p1", name: "local", kind: "LOCAL", allowed: true, disabled: false } as unknown as Provider;
const MODEL: LlmModel = {
  id: "m1",
  provider_id: "p1",
  model_name: "bielik-11b",
  capability: "CHAT",
  temperature: null,
  max_tokens: null,
  embed_dimension: null,
  notes: null,
  disabled: false,
  in_active: false,
  last_probe_at: null,
  last_probe_ok: null,
  last_probe_report: null,
  price_in_per_million: null,
  price_out_per_million: null,
};

describe("model prices (15.3)", () => {
  it("saves the price of a model as decimal strings", async () => {
    let body: unknown = null;
    server.use(
      http.patch(`${API}/admin/llm/models/m1`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ...MODEL, price_in_per_million: "2.5", price_out_per_million: "8" });
      }),
    );
    renderWithClient(<ModelsPanel status={STATUS} providers={[PROVIDER]} models={[MODEL]} />);
    await userEvent.click(screen.getByRole("button", { name: "Ustaw cenę" }));
    await userEvent.type(screen.getByLabelText(/Tokeny wejściowe/), "2.5");
    await userEvent.type(screen.getByLabelText(/Tokeny wyjściowe/), "8");
    await userEvent.click(screen.getByRole("button", { name: "Zapisz cenę" }));
    await waitFor(() => expect(body).not.toBeNull());
    expect(body).toEqual({ price: { price_in_per_million: "2.5", price_out_per_million: "8" } });
  });

  it("refuses a price with more than six decimal places without asking the server", async () => {
    let asked = 0;
    server.use(
      http.patch(`${API}/admin/llm/models/m1`, () => {
        asked += 1;
        return HttpResponse.json(MODEL);
      }),
    );
    renderWithClient(<ModelsPanel status={STATUS} providers={[PROVIDER]} models={[MODEL]} />);
    await userEvent.click(screen.getByRole("button", { name: "Ustaw cenę" }));
    await userEvent.type(screen.getByLabelText(/Tokeny wejściowe/), "0.0000001");
    await userEvent.type(screen.getByLabelText(/Tokeny wyjściowe/), "1");
    await userEvent.click(screen.getByRole("button", { name: "Zapisz cenę" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(asked).toBe(0);
  });
});
