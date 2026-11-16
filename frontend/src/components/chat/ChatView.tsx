"use client";

import { MessagesSquare, SendHorizontal } from "lucide-react";
import Link from "next/link";
import { useFormatter, useTranslations } from "next-intl";
import { useRef, useState, type FormEvent } from "react";

import { StepUpNotice } from "@/components/auth/RequireRole";
import { SafeMarkdown } from "@/components/markdown/SafeMarkdown";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { cn } from "@/components/ui/cn";
import { EmptyState } from "@/components/ui/EmptyState";
import { ModelMissing } from "@/components/ui/ModelMissing";
import { PageHeader } from "@/components/ui/PageHeader";
import { loginUrl, NotAuthenticated } from "@/lib/api";
import { isModelMissing, useFeature } from "@/lib/capabilities";
import { askCooperative, type Ask, type Citation, type RagAnswer } from "@/lib/chat";
import { solveInBackground } from "@/lib/pow";

type Turn =
  | { id: number; question: string; state: "asking" }
  | { id: number; question: string; state: "solving"; tried: number; difficulty: number }
  | { id: number; question: string; state: "answered"; answer: RagAnswer }
  | { id: number; question: string; state: "mfa"; stepUp: string }
  | { id: number; question: string; state: "error"; message: string };

const MAX_CHARS = 1000;
const EXAMPLES = ["varroa", "brood", "foulbrood"] as const;

export type Solver = typeof solveInBackground;

/**
 * Questions to the cooperative's canonical knowledge. The conversation lives only in this
 * page's memory. Answers arrive as syntheses with article-level citations, rendered inert
 * (SafeMarkdown). The shield's step-up challenges are handled here: proof-of-work is solved
 * in a Web Worker, MFA sends the member to log in again, a lock ends the conversation.
 */
export function ChatView({ ask = askCooperative, solve = solveInBackground }: { ask?: Ask; solve?: Solver }) {
  const t = useTranslations("chat");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [locked, setLocked] = useState(false);
  const available = useFeature("wiki_answers");
  const nextId = useRef(0);
  const busy = turns.some((t) => t.state === "asking" || t.state === "solving");

  const update = (id: number, turn: Turn) => setTurns((current) => current.map((t) => (t.id === id ? turn : t)));

  async function run(id: number, text: string) {
    try {
      let outcome = await ask(text);
      if (outcome.kind === "pow") {
        const { nonce, difficulty } = outcome.puzzle;
        update(id, { id, question: text, state: "solving", tried: 0, difficulty });
        const counter = await solve(nonce, difficulty, (tried) =>
          update(id, { id, question: text, state: "solving", tried, difficulty }),
        );
        outcome = await ask(text, `${nonce}:${counter}`);
      }
      switch (outcome.kind) {
        case "answer":
          update(id, { id, question: text, state: "answered", answer: outcome.answer });
          return;
        case "mfa":
          update(id, { id, question: text, state: "mfa", stepUp: outcome.stepUp });
          return;
        case "locked":
          setLocked(true);
          return;
        case "throttled":
          update(id, {
            id,
            question: text,
            state: "error",
            message: t("throttled", { minutes: Math.ceil(outcome.retryAfterSeconds / 60) }),
          });
          return;
        case "pow":
          update(id, { id, question: text, state: "error", message: t("powFailed") });
          return;
      }
    } catch (error) {
      if (error instanceof NotAuthenticated) {
        window.location.assign(loginUrl("/chat"));
        return;
      }
      const message = isModelMissing(error) ? t("modelMissing") : t("failed");
      update(id, { id, question: text, state: "error", message });
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const text = question.trim();
    if (text.length < 3 || busy) return;
    const id = nextId.current++;
    setTurns((current) => [...current, { id, question: text, state: "asking" }]);
    setQuestion("");
    void run(id, text);
  }

  if (locked) return <LockedNotice />;
  return (
    // A full-height conversation with the composer stuck to the bottom of the viewport (12.4).
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
      />
      {!available && <ModelMissing what={t("feature")} />}
      {turns.length === 0 ? (
        <EmptyState icon={MessagesSquare} title={t("emptyTitle")}>
          <p>{t("notSaved")}</p>
          <ul className="mt-3 flex flex-wrap justify-center gap-2" aria-label={t("examplesLabel")}>
            {EXAMPLES.map((key) => t(`examples.${key}`)).map((example) => (
              <li key={example}>
                <button
                  type="button"
                  onClick={() => setQuestion(example)}
                  className="rounded-full border border-border bg-surface px-3 py-1.5 text-xs text-text hover:border-primary pointer-coarse:min-h-10"
                >
                  {example}
                </button>
              </li>
            ))}
          </ul>
        </EmptyState>
      ) : (
        <ol className="flex flex-col gap-6" aria-label={t("conversation")}>
          {turns.map((turn) => (
            <li key={turn.id} className="flex flex-col gap-3">
              <p className="max-w-[85%] self-end rounded-2xl bg-primary/15 px-4 py-2 text-sm break-words">{turn.question}</p>
              <TurnBody turn={turn} />
            </li>
          ))}
        </ol>
      )}
      <form
        onSubmit={submit}
        className="sticky bottom-0 -mx-4 mt-auto flex flex-col gap-2 border-t border-border bg-bg px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] md:mx-0 md:rounded-t-xl md:border-x md:px-3"
      >
        <label htmlFor="chat-question" className="sr-only">
          {t("question")}
        </label>
        <textarea
          id="chat-question"
          rows={2}
          maxLength={MAX_CHARS}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              // Enter that confirms an IME composition (Japanese, Chinese, Korean...) must not send;
              // Safari reports it only through keyCode 229.
              if (event.nativeEvent.isComposing || event.keyCode === 229) return;
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
          placeholder={t("placeholder")}
          // Grows with the question up to 40% of the screen (field-sizing; fixed 2 rows elsewhere).
          className="resize-none rounded-xl border border-border bg-surface px-3 py-2 text-sm field-sizing-content min-h-[calc(2lh+1rem)] max-h-[40dvh]"
        />
        <div className="flex items-center justify-between text-xs text-text-muted">
          <span>
            {question.length}/{MAX_CHARS}
          </span>
          <Button type="submit" busy={busy} disabled={!available || question.trim().length < 3}>
            <SendHorizontal aria-hidden className="size-4" />
            {t("submit")}
          </Button>
        </div>
      </form>
    </div>
  );
}

function TurnBody({ turn }: { turn: Turn }) {
  const t = useTranslations("chat");
  switch (turn.state) {
    case "asking":
      return <p className="text-sm text-text-muted" role="status">{t("searching")}</p>;
    case "solving":
      return (
        <Card role="status" className="text-sm">
          <p>{t("solving")}</p>
          <p className="mt-1 text-xs text-text-muted">{t("tried", { tried: turn.tried, total: 2 ** turn.difficulty })}</p>
        </Card>
      );
    case "mfa":
      return <StepUpNotice stepUp={turn.stepUp} reason={t("mfa")} />;
    case "error":
      return (
        <p role="alert" className="text-sm text-danger">
          {turn.message}
        </p>
      );
    case "answered":
      return <AnswerCard answer={turn.answer} />;
  }
}

export function AnswerCard({ answer }: { answer: RagAnswer }) {
  const t = useTranslations("chat");
  return (
    <Card className={cn("flex flex-col gap-4", answer.no_official_position && "border-dashed bg-surface-muted")}>
      {answer.no_official_position ? (
        <div role="note" className="text-sm">
          <p className="font-medium">{t("noPosition")}</p>
          <p className="mt-1 text-text-muted">{answer.answer_markdown}</p>
        </div>
      ) : (
        <SafeMarkdown markdown={answer.answer_markdown} citations />
      )}
      {answer.citations.length > 0 && (
        <ol className="grid gap-2 @2xl:grid-cols-2" aria-label={t("sources")}>
          {answer.citations.map((c, i) => (
            <CitationCard key={c.article_id} citation={c} number={i + 1} />
          ))}
        </ol>
      )}
    </Card>
  );
}

function CitationCard({ citation, number }: { citation: Citation; number: number }) {
  const t = useTranslations("chat");
  const format = useFormatter();
  return (
    <li id={`cite-${number}`} className="rounded-lg border border-border p-3 text-xs">
      <Link href={`/wiki/${citation.article_id}`} className="font-medium text-primary-strong underline">
        [{number}] {citation.article_title}
      </Link>
      <p className="mt-1 text-text-muted">
        {citation.last_verified_at ? t("verified", { date: format.dateTime(new Date(citation.last_verified_at), "medium") }) : t("notVerified")}
      </p>
      {citation.authors.length > 0 && <p className="text-text-muted">{t("authors", { names: citation.authors.join(", ") })}</p>}
      {citation.approving_experts.length > 0 && (
        <p className="text-text-muted">{t("approvedBy", { names: citation.approving_experts.join(", ") })}</p>
      )}
    </li>
  );
}

function LockedNotice() {
  const t = useTranslations("chat");
  return (
    <div role="alert" className="fixed inset-0 z-40 flex items-center justify-center bg-bg/95 p-6">
      <Card className="max-w-md text-center">
        <h1 className="font-display text-xl font-semibold">{t("lockedTitle")}</h1>
        <p className="mt-2 text-sm text-text-muted">{t("lockedBody")}</p>
      </Card>
    </div>
  );
}
