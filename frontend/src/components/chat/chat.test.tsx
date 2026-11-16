import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ChatView, type Solver } from "@/components/chat/ChatView";
import type { Ask, AskOutcome, RagAnswer } from "@/lib/chat";
import { renderWithClient } from "@/test/render";
import { setTestLocale } from "@/test/rtl";

const ARTICLE = "0190f5a8-0000-7000-8000-0000000000a1";

function answer(markdown: string, overrides: Partial<RagAnswer> = {}): AskOutcome {
  return {
    kind: "answer",
    answer: {
      answer_markdown: markdown,
      citations: [
        {
          article_id: ARTICLE,
          article_title: "Leczenie warrozy",
          wiki_url: `https://spoldzielnia.localhost/wiki/${ARTICLE}`,
          last_verified_at: "2026-10-01T10:00:00Z",
          authors: ["Anna Kowalska"],
          approving_experts: ["dr Jan Nowak"],
        },
      ],
      no_official_position: false,
      request_id: "r1",
      ...overrides,
    },
  };
}

async function askQuestion(text = "Jak leczyć warrozę zimą?") {
  await userEvent.type(screen.getByLabelText("Twoje pytanie"), text);
  await userEvent.click(screen.getByRole("button", { name: "Zapytaj" }));
}

describe("ChatView", () => {
  it("starts with an empty state whose example questions fill the composer (12.4)", async () => {
    renderWithClient(<ChatView ask={async () => answer("Odpowiedź [1].")} />);
    expect(screen.getByText("Zadaj pierwsze pytanie")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Kiedy poszerzać gniazdo wiosną?" }));
    expect(screen.getByLabelText("Twoje pytanie")).toHaveValue("Kiedy poszerzać gniazdo wiosną?");
    await userEvent.click(screen.getByRole("button", { name: "Zapytaj" }));
    expect(await screen.findByRole("list", { name: "Rozmowa" })).toHaveTextContent("Kiedy poszerzać gniazdo wiosną?");
    expect(screen.queryByText("Zadaj pierwsze pytanie")).not.toBeInTheDocument();
  });

  it("renders the answer with citation cards", async () => {
    renderWithClient(<ChatView ask={async () => answer("Kwas szczawiowy podaje się zimą **w okresie bezczerwiowym** [1].")} />);
    await askQuestion();
    const cards = await screen.findByRole("list", { name: "Źródła" });
    expect(cards).toHaveTextContent("Leczenie warrozy");
    expect(cards).toHaveTextContent("Autorzy: Anna Kowalska");
    expect(cards).toHaveTextContent("Zatwierdzili: dr Jan Nowak");
    expect(screen.getByRole("link", { name: "[1]" })).toHaveAttribute("href", "#cite-1");
    expect(screen.getByRole("link", { name: "[1] Leczenie warrozy" })).toHaveAttribute("href", `/wiki/${ARTICLE}`);
    expect(document.getElementById("cite-1")).not.toBeNull();
  });

  it.each([
    '<img src=x onerror="alert(1)">',
    "[klik](javascript:alert(1))",
    "<svg><script>alert(1)</script></svg>",
    '<iframe src="https://evil.example"></iframe>',
    "[x](data:text/html;base64,PHNjcmlwdD4=)",
    "![piksel](https://evil.example/track.gif)",
  ])("keeps a hostile answer inert: %s", async (payload) => {
    renderWithClient(<ChatView ask={async () => answer(`Odpowiedź ${payload} [1]`)} />);
    await askQuestion();
    await screen.findByRole("list", { name: "Źródła" });
    // The conversation holds the answer; the page's own icons (svg) live outside it.
    const conversation = screen.getByRole("list", { name: "Rozmowa" });
    expect(conversation.querySelector("img, script, iframe, svg, object, embed")).toBeNull();
    for (const link of conversation.querySelectorAll("a")) {
      expect(link.getAttribute("href") ?? "").toMatch(/^(#|\/|https:\/\/)/);
    }
  });

  it("shows 'no official position' distinctly", async () => {
    const outcome = answer("Brak oficjalnego stanowiska ekspertów spółdzielni w tej sprawie.", {
      no_official_position: true,
      citations: [],
    });
    renderWithClient(<ChatView ask={async () => outcome} />);
    await askQuestion();
    expect(await screen.findByRole("note")).toHaveTextContent("Brak oficjalnego stanowiska ekspertów");
  });

  it("solves a proof-of-work challenge and asks again with the solution", async () => {
    const ask = vi.fn<Ask>(async (_q, solution) =>
      solution ? answer("Odpowiedź [1].") : { kind: "pow", puzzle: { nonce: "abc", difficulty: 18, algorithm: "sha256" } },
    );
    let finish: (counter: string) => void = () => {};
    const solve = vi.fn<Solver>((_nonce, _difficulty, onProgress) => {
      onProgress(8192);
      return new Promise<string>((resolve) => (finish = resolve));
    });
    renderWithClient(<ChatView ask={ask} solve={solve} />);
    await askQuestion();

    expect(await screen.findByText(/Weryfikacja bezpieczeństwa w toku/)).toBeInTheDocument();
    // pl-PL groups digits only from five-digit numbers on ("8192", "65 536").
    expect(screen.getByText(/Sprawdzono 8[\s\u00a0\u202f]?192/)).toBeInTheDocument();
    expect(solve).toHaveBeenCalledWith("abc", 18, expect.any(Function));
    finish("4242");
    await screen.findByRole("list", { name: "Źródła" });
    expect(ask).toHaveBeenLastCalledWith("Jak leczyć warrozę zimą?", "abc:4242");
  });

  it("sends the member to log in again for an MFA step-up", async () => {
    renderWithClient(<ChatView ask={async () => ({ kind: "mfa", stepUp: "/api/auth/login?prompt=login&acr_values=mfa" })} />);
    await askQuestion();
    const link = await screen.findByRole("link", { name: "Zaloguj ponownie z MFA" });
    expect(link.getAttribute("href")).toContain("prompt=login&acr_values=mfa&return_to=");
  });

  it("replaces the conversation with a lock notice", async () => {
    renderWithClient(<ChatView ask={async () => ({ kind: "locked" })} />);
    await askQuestion();
    expect(await screen.findByRole("heading", { name: "Konto zostało zablokowane" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Twoje pytanie")).toBeNull();
  });

  it("explains a throttle in minutes", async () => {
    renderWithClient(<ChatView ask={async () => ({ kind: "throttled", retryAfterSeconds: 1800 })} />);
    await askQuestion();
    expect(await screen.findByRole("alert")).toHaveTextContent("Spróbuj ponownie za 30 min.");
  });

  it("does not send on the Enter that confirms an IME composition", async () => {
    const ask = vi.fn<Ask>(async () => answer("Ok [1]."));
    renderWithClient(<ChatView ask={ask} />);
    const box = screen.getByLabelText("Twoje pytanie");
    await userEvent.type(box, "ミツバチの病気");
    fireEvent.keyDown(box, { key: "Enter", isComposing: true });
    fireEvent.keyDown(box, { key: "Enter", keyCode: 229 }); // Safari: compositionend came first
    expect(ask).not.toHaveBeenCalled();
    fireEvent.keyDown(box, { key: "Enter" });
    await waitFor(() => expect(ask).toHaveBeenCalledTimes(1));
  });

  it("does not ask twice while an answer is pending", async () => {
    let release: (o: AskOutcome) => void = () => {};
    const ask = vi.fn<Ask>(() => new Promise<AskOutcome>((resolve) => (release = resolve)));
    renderWithClient(<ChatView ask={ask} />);
    await askQuestion("Pierwsze pytanie?");
    await userEvent.type(screen.getByLabelText("Twoje pytanie"), "Drugie pytanie?{Enter}");
    expect(ask).toHaveBeenCalledTimes(1);
    release(answer("Ok [1]."));
    await waitFor(() => expect(screen.getByRole("button", { name: "Zapytaj" })).not.toHaveAttribute("aria-busy"));
  });
});

describe("in English (14.3)", () => {
  it("asks and answers in English, dates included", async () => {
    setTestLocale("en");
    renderWithClient(<ChatView ask={async () => answer("Oxalic acid is given in winter [1].")} />);
    expect(screen.getByRole("heading", { name: "Ask the cooperative" })).toBeInTheDocument();
    expect(screen.getByText("Ask your first question")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "How do I treat varroa in winter?" }));
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    const sources = await screen.findByRole("list", { name: "Sources" });
    expect(sources).toHaveTextContent(/Verified \d{1,2} [A-Z][a-z]{2} 20\d\d/);
  });
});
