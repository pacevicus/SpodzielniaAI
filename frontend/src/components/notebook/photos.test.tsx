import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { useState } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { ImageAnnotator } from "@/components/annotator/ImageAnnotator";
import { AtlasSubmissionPanel } from "@/components/notebook/AtlasSubmissionPanel";
import { PhotoEditor } from "@/components/notebook/PhotoEditor";
import { PhotosPanel } from "@/components/notebook/PhotosPanel";
import type { Domain, Label } from "@/lib/atlas";
import type { Submission } from "@/lib/atlas-submissions";
import type { Recognition } from "@/lib/recognition";
import { clampBox, type Mark, type Photo } from "@/lib/photos";
import type { Attachment } from "@/lib/upload";
import { API, renderWithClient } from "@/test/render";

const push = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// jsdom has no PointerEvent: a MouseEvent with a pointer id is all the annotator reads.
if (typeof window.PointerEvent === "undefined") {
  class PointerEventPolyfill extends MouseEvent {
    pointerId: number;
    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init);
      this.pointerId = init.pointerId ?? 1;
    }
  }
  window.PointerEvent = PointerEventPolyfill as unknown as typeof PointerEvent;
}

/** The annotator surface measures 1000 × 500 px at the page origin. */
function sizeSurface() {
  const surface = screen.getByTestId("annotator-surface");
  surface.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1000, height: 500, right: 1000, bottom: 500, x: 0, y: 0, toJSON: () => ({}) });
  return surface;
}

function mark(key: string, overrides: Partial<Mark> = {}): Mark {
  return { key, label: { id: "l1", name: "Matka pszczela", status: "ACTIVE" }, x: 0.1, y: 0.2, w: 0.3, h: 0.4, description: "", source: "USER", ...overrides };
}

function Harness({ initial = [] as Mark[], readOnly = false, onMarks }: { initial?: Mark[]; readOnly?: boolean; onMarks?: (m: Mark[]) => void }) {
  const [marks, setMarks] = useState(initial);
  const [selected, setSelected] = useState<string | null>(null);
  return (
    <ImageAnnotator
      src="/x.jpg"
      alt="ul"
      aspect={2}
      marks={marks}
      selected={selected}
      onSelect={setSelected}
      readOnly={readOnly}
      onChange={(m) => {
        setMarks(m);
        onMarks?.(m);
      }}
    />
  );
}

describe("ImageAnnotator", () => {
  it("draws a box by dragging and ignores a plain click", () => {
    let latest: Mark[] = [];
    render(<Harness onMarks={(m) => (latest = m)} />);
    const surface = sizeSurface();
    fireEvent.pointerDown(surface, { button: 0, clientX: 100, clientY: 100, pointerId: 1 });
    fireEvent.pointerMove(surface, { clientX: 400, clientY: 300, pointerId: 1 });
    fireEvent.pointerUp(surface, { clientX: 400, clientY: 300, pointerId: 1 });
    expect(latest).toHaveLength(1);
    const [box] = latest;
    expect([box.x, box.y, box.w, box.h].map((v) => Number(v.toFixed(3)))).toEqual([0.1, 0.2, 0.3, 0.4]);
    expect(box.label).toBeNull();
    expect(screen.getByRole("button", { name: "Oznaczenie 1: bez etykiety" })).toBeInTheDocument();

    // A click without dragging leaves no box behind.
    fireEvent.pointerDown(surface, { button: 0, clientX: 800, clientY: 400, pointerId: 2 });
    fireEvent.pointerUp(surface, { clientX: 801, clientY: 400, pointerId: 2 });
    expect(latest).toHaveLength(1);
  });

  it("moves a box and resizes it with a handle, staying inside the photo", () => {
    let latest: Mark[] = [];
    render(<Harness initial={[mark("a")]} onMarks={(m) => (latest = m)} />);
    const surface = sizeSurface();
    const box = screen.getByRole("button", { name: /Oznaczenie 1/ });
    // Drag the box far right: it stops at the edge (x + w = 1).
    fireEvent.pointerDown(box, { button: 0, clientX: 200, clientY: 200, pointerId: 1 });
    fireEvent.pointerMove(surface, { clientX: 2000, clientY: 200, pointerId: 1 });
    fireEvent.pointerUp(surface, { pointerId: 1 });
    expect(Number((latest[0].x + latest[0].w).toFixed(6))).toBe(1);
    expect(latest[0].y).toBeCloseTo(0.2);

    // The selected box shows handles; the south-east one resizes.
    const handle = box.querySelector('[data-handle="se"]')!;
    fireEvent.pointerDown(handle, { button: 0, clientX: 1000, clientY: 300, pointerId: 3 });
    fireEvent.pointerMove(surface, { clientX: 1000, clientY: 450, pointerId: 3 });
    fireEvent.pointerUp(surface, { pointerId: 3 });
    expect(latest[0].y + latest[0].h).toBeCloseTo(0.9);
  });

  it("is operable with the keyboard", async () => {
    let latest: Mark[] = [];
    render(<Harness initial={[mark("a"), mark("b", { x: 0.5 })]} onMarks={(m) => (latest = m)} />);
    const first = screen.getByRole("button", { name: /Oznaczenie 1/ });
    act(() => first.focus());
    await userEvent.keyboard("{ArrowRight}");
    expect(latest[0].x).toBeCloseTo(0.11);
    await userEvent.keyboard("{Shift>}{ArrowDown}{/Shift}");
    expect(latest[0].y).toBeCloseTo(0.202);
    await userEvent.keyboard("{Alt>}{ArrowRight}{/Alt}");
    expect(latest[0].w).toBeCloseTo(0.31);
    await userEvent.keyboard("{Delete}");
    expect(latest.map((m) => m.key)).toEqual(["b"]);
  });

  it("changes nothing when read-only", () => {
    let calls = 0;
    render(<Harness initial={[mark("a")]} readOnly onMarks={() => calls++} />);
    const surface = sizeSurface();
    fireEvent.pointerDown(surface, { button: 0, clientX: 100, clientY: 100, pointerId: 1 });
    fireEvent.pointerMove(surface, { clientX: 400, clientY: 300, pointerId: 1 });
    fireEvent.pointerUp(surface, { pointerId: 1 });
    expect(calls).toBe(0);
  });

  it("clamps boxes to the photo", () => {
    expect(clampBox({ x: 0.9, y: -0.2, w: 0.3, h: 0.0001 })).toEqual({ x: 0.7, y: 0, w: 0.3, h: 0.005 });
  });
});

// --- PhotoEditor --------------------------------------------------------------------------------

const BEES: Domain = { id: "d1", slug: "pszczelarstwo", name: "Pszczelarstwo", description: null, status: "ACTIVE" };
const QUEEN: Label = {
  id: "l1",
  domain_id: "d1",
  slug: "matka-pszczela",
  name: "Matka pszczela",
  description: null,
  synonyms: [],
  status: "ACTIVE",
  merged_into_id: null,
  is_own_proposal: false,
};

function photo(overrides: Partial<Photo> = {}): Photo {
  return {
    attachment_id: "a1",
    note_id: "n1",
    mime_type: "image/jpeg",
    width: 3000,
    height: 2000,
    domain_id: "d1",
    caption: "Ramka 3",
    version: 1,
    regions: [{ id: "r1", label: { id: "l1", name: "Matka pszczela", status: "ACTIVE" }, x: 0.1, y: 0.2, w: 0.3, h: 0.4, description: "znaczona", source: "USER" }],
    updated_at: null,
    ...overrides,
  };
}

let recognitions: Recognition[] = [];
afterEach(() => {
  recognitions = [];
});

function mountEditor(initial: Photo, put: (body: unknown) => Response | Promise<Response>, domains: Domain[] = [BEES]) {
  server.use(
    http.get(`${API}/atlas/domains`, () => HttpResponse.json(domains)),
    http.get(`${API}/atlas/labels`, () => HttpResponse.json([QUEEN])),
    http.get(`${API}/notes/n1/attachments/a1/annotations`, () => HttpResponse.json(initial)),
    http.get(`${API}/notes/n1`, () => HttpResponse.json({})),
    http.get(`${API}/atlas/submissions`, () => HttpResponse.json([])),
    http.get(`${API}/notes/n1/attachments/a1/recognitions`, () => HttpResponse.json(recognitions)),
    http.put(`${API}/notes/n1/attachments/a1/annotations`, async ({ request }) => put(await request.json())),
  );
  renderWithClient(<PhotoEditor noteId="n1" attachmentId="a1" />);
}

describe("PhotoEditor", () => {
  it("loads marks and saves the full set with the loaded version", async () => {
    let body: unknown = null;
    mountEditor(photo(), (b) => {
      body = b;
      return HttpResponse.json(photo({ version: 2, caption: "Ramka 3, ul 7" }));
    });
    const caption = await screen.findByLabelText("Opis zdjęcia");
    expect(caption).toHaveValue("Ramka 3");
    expect(screen.getByLabelText("Opis oznaczenia")).toHaveValue("znaczona");
    expect(screen.getByRole("button", { name: "Zapisz" })).toBeDisabled(); // nothing changed yet
    await userEvent.type(caption, ", ul 7");
    await userEvent.click(screen.getByRole("button", { name: "Zapisz" }));
    await waitFor(() =>
      expect(body).toEqual({
        domain_id: "d1",
        caption: "Ramka 3, ul 7",
        version: 1,
        regions: [{ label_id: "l1", x: 0.1, y: 0.2, w: 0.3, h: 0.4, description: "znaczona", source: "USER" }],
      }),
    );
    expect(await screen.findByText("Zapisano oznaczenia.")).toBeInTheDocument();
  });

  it("offers a reload when the photo changed elsewhere", async () => {
    mountEditor(photo(), () => HttpResponse.json({ detail: { error: "stale_version" } }, { status: 409 }));
    await userEvent.type(await screen.findByLabelText("Opis zdjęcia"), "!");
    await userEvent.click(screen.getByRole("button", { name: "Zapisz" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Zdjęcie zmieniło się w innej karcie");
    expect(within(alert).getByRole("button", { name: "Wczytaj ponownie" })).toBeInTheDocument();
  });

  it("asks in the app's dialog before a domain change drops the labels", async () => {
    const OTHER: Domain = { ...BEES, id: "d2", slug: "ogrod", name: "Ogród" };
    mountEditor(photo(), () => HttpResponse.json(photo()), [BEES, OTHER]);
    const select = await screen.findByLabelText("Dziedzina");
    await screen.findByRole("option", { name: "Ogród" });
    await userEvent.selectOptions(select, "d2");
    const dialog = screen.getByRole("dialog", { name: /Zmiana dziedziny usunie etykiety/ });
    await userEvent.click(within(dialog).getByRole("button", { name: "Anuluj" }));
    expect(select).toHaveValue("d1");
    expect(screen.getByRole("button", { name: "Zapisz" })).toBeDisabled();

    await userEvent.selectOptions(select, "d2");
    await userEvent.click(screen.getByRole("button", { name: "Zmień dziedzinę" }));
    expect(select).toHaveValue("d2");
    expect(screen.getByRole("button", { name: "Zapisz" })).toBeDisabled(); // the mark lost its label
  });

  it("asks before leaving with unsaved changes", async () => {
    mountEditor(photo(), () => HttpResponse.json(photo()));
    await userEvent.type(await screen.findByLabelText("Opis zdjęcia"), "!");
    await userEvent.click(screen.getByRole("link", { name: "← Notatka" }));
    expect(push).not.toHaveBeenCalled();
    await userEvent.click(within(screen.getByRole("dialog", { name: /niezapisane zmiany/ })).getByRole("button", { name: "Wyjdź bez zapisywania" }));
    expect(push).toHaveBeenCalledWith("/notebook/n1");
  });

  it("needs a domain first and a label on every mark", async () => {
    mountEditor(photo({ domain_id: null, version: 0, caption: null, regions: [] }), () => HttpResponse.json(photo()));
    expect(await screen.findByText(/Najpierw wybierz dziedzinę/)).toBeInTheDocument();
    await userEvent.selectOptions(await screen.findByLabelText("Dziedzina"), "d1");
    const surface = sizeSurface();
    fireEvent.pointerDown(surface, { button: 0, clientX: 100, clientY: 100, pointerId: 1 });
    fireEvent.pointerMove(surface, { clientX: 300, clientY: 300, pointerId: 1 });
    fireEvent.pointerUp(surface, { pointerId: 1 });
    expect(await screen.findByText(/1 bez etykiety/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Zapisz" })).toBeDisabled();
    await userEvent.type(screen.getByRole("combobox", { name: "Etykieta oznaczenia 1" }), "matka");
    await userEvent.click(await screen.findByRole("option", { name: /Matka pszczela/ }));
    expect(screen.getByRole("button", { name: "Zapisz" })).toBeEnabled();
  });

  it("is read-only in an archived domain", async () => {
    mountEditor(photo(), () => HttpResponse.json(photo()), [{ ...BEES, status: "ARCHIVED" }]);
    expect(await screen.findByText(/zdjęcie można tylko oglądać/)).toBeInTheDocument();
    expect(screen.getByLabelText("Opis zdjęcia")).toBeDisabled();
    expect(screen.queryByRole("button", { name: /Usuń oznaczenie/ })).not.toBeInTheDocument();
  });
});

describe("PhotosPanel", () => {
  it("lists only image attachments, linking to the editor", () => {
    const base = { note_id: "n1", size: 1, scan_status: "CLEAN", keep_location: false, created_at: "2026-10-21T10:00:00Z" } as const;
    const attachments = [
      { ...base, id: "a1", filename: "ul.jpg", mime_type: "image/jpeg" },
      { ...base, id: "a2", filename: "raport.pdf", mime_type: "application/pdf" },
    ] as Attachment[];
    render(<PhotosPanel noteId="n1" attachments={attachments} />);
    const link = screen.getByRole("link", { name: "Oznacz: ul.jpg" });
    expect(link).toHaveAttribute("href", "/notebook/n1/photos/a1");
    expect(screen.getByRole("img", { name: "ul.jpg" })).toHaveAttribute("src", "/api/notes/n1/attachments/a1/image");
    expect(screen.queryByText(/raport.pdf/)).not.toBeInTheDocument();
  });
});

// --- Atlas submission ---------------------------------------------------------------------------

function submission(overrides: Partial<Submission> = {}): Submission {
  return {
    id: "s1",
    status: "PREPARED",
    error: null,
    pii_replaced: 1,
    created_at: "2026-10-21T10:00:00Z",
    updated_at: "2026-10-21T10:00:00Z",
    atlas: {
      id: "i1",
      status: "DRAFT",
      width: 1200,
      height: 800,
      caption: "Matka z ula 7, właściciel <PESEL>",
      regions: [{ id: "r1", label: { id: "l1", name: "Matka pszczela", status: "ACTIVE" }, x: 0.1, y: 0.2, w: 0.3, h: 0.4, description: "znaczona" }],
      privacy_check: "ok",
      privacy_flags: ["people"],
      needs_people_confirmation: true,
      show_author: false,
      allow_export: false,
      review_alert: null,
    },
    ...overrides,
  };
}

describe("AtlasSubmissionPanel", () => {
  it("shows what would be shared and sends only with explicit choices", async () => {
    let sent: unknown = null;
    server.use(
      http.get(`${API}/atlas/submissions`, () => HttpResponse.json([submission()])),
      http.post(`${API}/atlas/submissions/s1/send`, async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json(submission({ status: "SENT" }));
      }),
    );
    renderWithClient(<AtlasSubmissionPanel noteId="n1" attachmentId="a1" canSubmit reason={null} />);
    await userEvent.click(await screen.findByRole("button", { name: "Sprawdź i wyślij" }));
    const dialog = screen.getByRole("dialog", { name: "Tak zobaczą to inni" });
    expect(within(dialog).getByText(/właściciel <PESEL>/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Zastąpiliśmy dane osobowe w opisach \(1\)/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Na zdjęciu mogą być: osoby lub części ciała/)).toBeInTheDocument();
    const sendButton = within(dialog).getByRole("button", { name: "Wyślij do ekspertów" });
    expect(sendButton).toBeDisabled(); // no default for the name question
    await userEvent.click(within(dialog).getByLabelText(/Publikuj anonimowo/));
    expect(sendButton).toBeDisabled(); // people still need confirming
    await userEvent.click(within(dialog).getByLabelText(/zgodziły się na jego udostępnienie/));
    await userEvent.click(sendButton);
    await waitFor(() => expect(sent).toEqual({ show_author: false, allow_export: false, confirm_no_people: true }));
  });

  it("explains a refused preparation and lets the member try again", async () => {
    server.use(http.get(`${API}/atlas/submissions`, () => HttpResponse.json([submission({ status: "FAILED", error: "duplicate_photo", atlas: null })])));
    renderWithClient(<AtlasSubmissionPanel noteId="n1" attachmentId="a1" canSubmit reason={null} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("jest już w atlasie");
    expect(screen.getByRole("button", { name: "Przekaż do atlasu" })).toBeEnabled();
  });

  it("starts preparation and waits for it", async () => {
    let posted: unknown = null;
    let list: Submission[] = [];
    server.use(
      http.get(`${API}/atlas/submissions`, () => HttpResponse.json(list)),
      http.post(`${API}/atlas/submissions`, async ({ request }) => {
        posted = await request.json();
        list = [submission({ status: "PENDING", atlas: null })];
        return HttpResponse.json(list[0], { status: 202 });
      }),
    );
    renderWithClient(<AtlasSubmissionPanel noteId="n1" attachmentId="a1" canSubmit reason={null} />);
    await userEvent.click(await screen.findByRole("button", { name: "Przekaż do atlasu" }));
    expect(await screen.findByText(/Przygotowujemy kopię zdjęcia/)).toBeInTheDocument();
    expect(posted).toEqual({ note_id: "n1", attachment_id: "a1" });
  });

  it("is disabled with a reason while changes are unsaved, and shows the review state", async () => {
    server.use(http.get(`${API}/atlas/submissions`, () => HttpResponse.json([])));
    const { unmount } = renderWithClient(<AtlasSubmissionPanel noteId="n1" attachmentId="a1" canSubmit={false} reason="Najpierw zapisz zmiany." />);
    expect(await screen.findByRole("button", { name: "Przekaż do atlasu" })).toBeDisabled();
    expect(screen.getByText("Najpierw zapisz zmiany.")).toBeInTheDocument();
    unmount();
    const inReview = submission({ status: "SENT" });
    inReview.atlas = { ...inReview.atlas!, status: "IN_REVIEW" };
    server.use(http.get(`${API}/atlas/submissions`, () => HttpResponse.json([inReview])));
    renderWithClient(<AtlasSubmissionPanel noteId="n1" attachmentId="a1" canSubmit reason={null} />);
    expect(await screen.findByText("W recenzji ekspertów")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Przekaż do atlasu" })).not.toBeInTheDocument();
  });
});

// --- Recognition ---------------------------------------------------------------------------------

function recognition(overrides: Partial<Recognition> = {}): Recognition {
  return {
    id: "rec1",
    status: "DONE",
    error: null,
    question: "Gdzie jest matka?",
    answer: "Matka jest w lewej części ramki.",
    grounded: true,
    vlm: "ok",
    suggestions: [{ label_id: "l1", label_name: "Matka pszczela", x: 0.2, y: 0.3, w: 0.2, h: 0.2, confidence: 0.87 }],
    citations: [{ image_id: "atl1", labels: ["Matka pszczela"], author_name: null }],
    created_at: "2026-10-21T10:00:00Z",
    ...overrides,
  };
}

describe("Recognition", () => {
  it("asks with the photo's domain and shows the grounded answer with sources", async () => {
    let posted: unknown = null;
    recognitions = [];
    server.use(
      http.post(`${API}/notes/n1/attachments/a1/recognize`, async ({ request }) => {
        posted = await request.json();
        recognitions = [recognition()];
        return HttpResponse.json(recognition({ status: "PENDING", answer: null, suggestions: [], citations: [] }), { status: 202 });
      }),
    );
    mountEditor(photo(), () => HttpResponse.json(photo()));
    await userEvent.type(await screen.findByLabelText("Pytanie (opcjonalnie)"), "Gdzie jest matka?");
    await userEvent.click(screen.getByRole("button", { name: "Rozpoznaj" }));
    await waitFor(() => expect(posted).toEqual({ question: "Gdzie jest matka?", domain_id: "d1", all_domains: false }));
    const result = await screen.findByRole("region", { name: "Wynik rozpoznawania" });
    expect(within(result).getByText("Matka jest w lewej części ramki.")).toBeInTheDocument();
    const source = within(result).getByRole("link");
    expect(source).toHaveAttribute("href", "/atlas/atl1");
    expect(source).toHaveTextContent("Członek spółdzielni");
    // The suggestion is drawn dashed on the photo until accepted or dismissed.
    expect(screen.getByRole("img", { name: "Podpowiedź: Matka pszczela" })).toBeInTheDocument();
  });

  it("turns an accepted suggestion into a mark to save", async () => {
    recognitions = [recognition()];
    mountEditor(photo(), () => HttpResponse.json(photo()));
    const list = await screen.findByRole("list", { name: "Podpowiedzi" });
    await userEvent.click(within(list).getByRole("button", { name: "Przyjmij" }));
    expect(screen.getByText("(z rozpoznawania)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Oznaczenie 2: Matka pszczela" })).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "Podpowiedź: Matka pszczela" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Zapisz" })).toBeEnabled();
  });

  it("says when the vision model was unavailable and hides dismissed suggestions", async () => {
    recognitions = [recognition({ vlm: "unavailable" })];
    mountEditor(photo(), () => HttpResponse.json(photo()));
    expect(await screen.findByText(/ramki są przybliżone/)).toBeInTheDocument();
    await userEvent.click(within(screen.getByRole("list", { name: "Podpowiedzi" })).getByRole("button", { name: "Odrzuć" }));
    expect(screen.queryByRole("list", { name: "Podpowiedzi" })).not.toBeInTheDocument();
  });
});

