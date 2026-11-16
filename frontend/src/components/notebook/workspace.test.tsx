import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { NoteWorkspace } from "@/components/notebook/NoteWorkspace";
import { NotebookList } from "@/components/notebook/NotebookList";
import { API, renderWithClient } from "@/test/render";
import { setTestLocale } from "@/test/rtl";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
// BlockNote needs a real browser; the workspace layout is what is under test here.
vi.mock("next/dynamic", () => ({ default: () => () => <div role="textbox" aria-label="Edytor notatki" /> }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const NOTE = "0190f5a8-0000-7000-8000-000000000001";

function note() {
  server.use(
    http.get(`${API}/notes/${NOTE}`, () =>
      HttpResponse.json({
        id: NOTE,
        title: "Przegląd wiosenny",
        body: "Rodzina 3 silna.",
        attachments: [],
        derivatives: [],
        created_at: "2026-10-19T10:00:00Z",
        updated_at: "2026-10-19T10:00:00Z",
      }),
    ),
    http.get(`${API}/capabilities`, () => HttpResponse.json({ features: {} })),
  );
}

describe("NoteWorkspace (12.3)", () => {
  it("has Źródła, Notatka and Zapytaj panels, with Notatka selected first", async () => {
    note();
    renderWithClient(<NoteWorkspace noteId={NOTE} />);
    const tabs = within(await screen.findByRole("tablist", { name: "Widok notatki" }));
    expect(tabs.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Źródła", "Notatka", "Zapytaj"]);
    expect(tabs.getByRole("tab", { name: "Notatka" })).toHaveAttribute("aria-selected", "true");

    const notePanel = screen.getByRole("region", { name: "Notatka" });
    expect(within(notePanel).getByLabelText("Tytuł notatki")).toHaveValue("Przegląd wiosenny");
    expect(within(notePanel).getByRole("textbox", { name: "Edytor notatki" })).toBeInTheDocument();
    const sources = screen.getByRole("region", { name: "Źródła" });
    expect(within(sources).getByRole("button", { name: "Dodaj stronę internetową" })).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Zapytaj" })).getByRole("heading", { name: "Zapytaj swój notatnik" })).toBeInTheDocument();
    // The phone hides the other panels with a container query; they stay mounted.
    expect(sources).toHaveClass("@max-3xl:hidden");
    expect(notePanel).not.toHaveClass("@max-3xl:hidden");
  });

  it("deletes the note only after confirming in the app's dialog", async () => {
    note();
    let deleted = 0;
    server.use(
      http.delete(`${API}/notes/${NOTE}`, () => {
        deleted += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithClient(<NoteWorkspace noteId={NOTE} />);
    await userEvent.click(await screen.findByRole("button", { name: "Usuń notatkę" }));
    const dialog = screen.getByRole("dialog", { name: /Usunąć notatkę wraz z załącznikami/ });
    await userEvent.click(within(dialog).getByRole("button", { name: "Anuluj" }));
    expect(deleted).toBe(0);

    await userEvent.click(screen.getByRole("button", { name: "Usuń notatkę" }));
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Usuń notatkę" }));
    await waitFor(() => expect(push).toHaveBeenCalledWith("/notebook"));
    expect(deleted).toBe(1);
  });

  it("switches panels on a phone without unmounting the editor", async () => {
    note();
    renderWithClient(<NoteWorkspace noteId={NOTE} />);
    const tabs = within(await screen.findByRole("tablist", { name: "Widok notatki" }));
    await userEvent.click(tabs.getByRole("tab", { name: "Zapytaj" }));
    expect(screen.getByRole("region", { name: "Zapytaj" })).not.toHaveClass("@max-6xl:hidden");
    expect(screen.getByRole("region", { name: "Notatka" })).toHaveClass("@max-3xl:hidden");
    expect(screen.getByRole("textbox", { name: "Edytor notatki" })).toBeInTheDocument();
    // The side tab list (two columns) follows the same state.
    const side = within(screen.getByRole("tablist", { name: "Panel boczny" }));
    expect(side.getByRole("tab", { name: "Zapytaj" })).toHaveAttribute("aria-selected", "true");
    expect(side.getByRole("tab", { name: "Zapytaj" })).toHaveAttribute("aria-controls", "nb-panel-ask");
  });
});

describe("NotebookList (12.3)", () => {
  it("shows an empty state without a second „Nowa notatka” button", async () => {
    server.use(http.get(`${API}/notes`, () => HttpResponse.json([])), http.get(`${API}/capabilities`, () => HttpResponse.json({ features: {} })));
    renderWithClient(<NotebookList />);
    expect(await screen.findByText("Nie masz jeszcze notatek.")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Nowa notatka" })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1, name: "Mój notatnik" })).toBeInTheDocument();
  });

  it("lists notes as cards that can be selected for a Wiki proposal", async () => {
    server.use(
      http.get(`${API}/notes`, () =>
        HttpResponse.json([{ id: NOTE, title: "Przegląd wiosenny", updated_at: "2026-10-19T10:00:00Z", created_at: "2026-10-19T10:00:00Z" }]),
      ),
      http.get(`${API}/capabilities`, () => HttpResponse.json({ features: {} })),
    );
    renderWithClient(<NotebookList />);
    const list = within(await screen.findByRole("list", { name: "Notatki" }));
    expect(list.getByRole("link", { name: "Przegląd wiosenny" })).toHaveAttribute("href", `/notebook/${NOTE}`);
    await userEvent.click(list.getByRole("checkbox", { name: "Zaznacz: Przegląd wiosenny" }));
    expect(screen.getByRole("button", { name: "Utwórz wniosek do Wiki (1)" })).toBeEnabled();
  });
});

describe("in English (14.3)", () => {
  it("has Sources, Note and Ask panels", async () => {
    setTestLocale("en");
    note();
    renderWithClient(<NoteWorkspace noteId={NOTE} />);
    const tabs = within(await screen.findByRole("tablist", { name: "Note view" }));
    expect(tabs.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Sources", "Note", "Ask"]);
    expect(within(screen.getByRole("region", { name: "Note" })).getByLabelText("Note title")).toHaveValue("Przegląd wiosenny");
    expect(within(screen.getByRole("region", { name: "Sources" })).getByRole("button", { name: "Add a web page" })).toBeInTheDocument();
    expect(within(screen.getByRole("region", { name: "Ask" })).getByRole("heading", { name: "Ask your notebook" })).toBeInTheDocument();
  });
});
