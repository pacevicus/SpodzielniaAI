"use client";

/**
 * AI usage (stage 15, US-09): a member sees their own tokens and estimated cost; the
 * administrator sees every member, the system's own work, and the totals for a period. Figures
 * are estimates, priced per model in the ``/admin/llm`` configuration.
 */

import { Coins, Gauge } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { DataTable, TBody, Td, Th, THead, Tr } from "@/components/ui/DataTable";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";
import { Loading } from "@/components/ui/Skeleton";
import {
  defaultPeriod,
  periodProblem,
  useAdminUsage,
  useMyUsage,
  type Period,
  type UsageTotals,
} from "@/lib/usage";

const INPUT = "min-h-11 rounded-lg border border-border bg-surface px-3 py-2";

function PeriodForm({ value, onApply }: { value: Period; onApply: (period: Period) => void }) {
  const t = useTranslations("usage");
  const [draft, setDraft] = useState(value);
  const problem = periodProblem(draft);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (problem === null) onApply(draft);
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3" aria-label={t("period.legend")}>
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm">
          {t("period.from")}
          <input
            type="date"
            className={INPUT}
            value={draft.from}
            max={draft.to || undefined}
            onChange={(e) => setDraft({ ...draft, from: e.target.value })}
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          {t("period.to")}
          <input
            type="date"
            className={INPUT}
            value={draft.to}
            min={draft.from || undefined}
            onChange={(e) => setDraft({ ...draft, to: e.target.value })}
          />
        </label>
        <Button type="submit" variant="secondary" disabled={problem !== null}>
          {t("period.show")}
        </Button>
      </div>
      {problem && (
        <p role="alert" className="text-sm text-danger">
          {t(`period.${problem === "invalid" ? "invalid" : "tooLong"}`)}
        </p>
      )}
    </form>
  );
}

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <Card className="flex flex-col gap-1 p-4">
      <div className="text-sm text-text-muted">{label}</div>
      <div className="text-2xl font-semibold tabular-nums">{value}</div>
      {note && <div className="text-xs text-text-muted">{note}</div>}
    </Card>
  );
}

function Totals({ totals, currency }: { totals: UsageTotals; currency: string }) {
  const t = useTranslations("usage");
  const format = useFormatter();
  return (
    <div className="flex flex-col gap-3">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label={t("totals.calls")} value={format.number(totals.calls)} />
        <Stat label={t("totals.tokens")} value={format.number(totals.total_tokens)} />
        <Stat label={t("totals.promptTokens")} value={format.number(totals.prompt_tokens)} />
        <Stat
          label={t("totals.cost")}
          value={format.number(Number(totals.cost), "money")}
          note={t("totals.costNote", { currency })}
        />
      </div>
      {totals.unpriced_calls > 0 && (
        <p className="text-sm text-text-muted">
          {t("unpriced", { count: totals.unpriced_calls })}
        </p>
      )}
    </div>
  );
}

type LlmKey = Parameters<ReturnType<typeof useTranslations<"adminLlm">>>[0];

/** The task's name, or its code when the catalogue has no name for it (e.g. PLAYGROUND). */
function purposeLabel(t: ReturnType<typeof useTranslations<"adminLlm">>, purpose: string): string {
  const key = `purposes.${purpose}.label` as LlmKey;
  return t.has(key) ? t(key) : purpose;
}

function ByPurpose({
  rows,
  heading,
}: {
  rows: { purpose: string; totals: UsageTotals }[];
  heading: string;
}) {
  const t = useTranslations("usage");
  const tLlm = useTranslations("adminLlm");
  const format = useFormatter();
  return (
    <section className="flex flex-col gap-2">
      <h2 className="text-lg font-semibold">{heading}</h2>
      <DataTable>
        <THead>
          <Tr>
            <Th>{t("member.byPurpose")}</Th>
            <Th align="right">{t("totals.calls")}</Th>
            <Th align="right">{t("totals.tokens")}</Th>
            <Th align="right">{t("totals.cost")}</Th>
          </Tr>
        </THead>
        <TBody>
          {rows.map((row) => (
            <Tr key={row.purpose}>
              <Td primary>{purposeLabel(tLlm, row.purpose)}</Td>
              <Td label={t("totals.calls")} align="right">{format.number(row.totals.calls)}</Td>
              <Td label={t("totals.tokens")} align="right">{format.number(row.totals.total_tokens)}</Td>
              <Td label={t("totals.cost")} align="right">{format.number(Number(row.totals.cost), "money")}</Td>
            </Tr>
          ))}
        </TBody>
      </DataTable>
    </section>
  );
}

export function MemberUsage() {
  const t = useTranslations("usage");
  const [period, setPeriod] = useState<Period>(() => defaultPeriod());
  const usage = useMyUsage(period);
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("member.title")} description={t("member.description")} />
      <PeriodForm value={period} onApply={setPeriod} />
      {usage.isPending && periodProblem(period) === null && <Loading label={t("loading")} />}
      {usage.isError && (
        <p role="alert" className="text-sm text-danger">
          {t("failed")}
        </p>
      )}
      {usage.data && (
        <>
          <Totals totals={usage.data.totals} currency={usage.data.currency} />
          {usage.data.totals.calls === 0 ? (
            <EmptyState icon={Gauge} title={t("member.empty")} />
          ) : (
            <ByPurpose rows={usage.data.by_purpose} heading={t("member.byPurpose")} />
          )}
        </>
      )}
    </div>
  );
}

export function AdminUsage() {
  const t = useTranslations("usage");
  const format = useFormatter();
  const [period, setPeriod] = useState<Period>(() => defaultPeriod());
  const usage = useAdminUsage(period);
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("admin.title")} description={t("admin.description")} />
      <PeriodForm value={period} onApply={setPeriod} />
      {usage.isPending && periodProblem(period) === null && <Loading label={t("loading")} />}
      {usage.isError && (
        <p role="alert" className="text-sm text-danger">
          {t("failed")}
        </p>
      )}
      {usage.data && (
        <>
          <Totals totals={usage.data.total} currency={usage.data.currency} />
          <section className="flex flex-col gap-2">
            <h2 className="text-lg font-semibold">{t("admin.members")}</h2>
            {usage.data.members.length === 0 && (
              <EmptyState icon={Coins} title={t("admin.noMembers")} />
            )}
            <DataTable>
              <THead>
                <Tr>
                  <Th>{t("admin.member")}</Th>
                  <Th align="right">{t("totals.calls")}</Th>
                  <Th align="right">{t("totals.tokens")}</Th>
                  <Th align="right">{t("totals.cost")}</Th>
                </Tr>
              </THead>
              <TBody>
                {usage.data.members.map((row) => (
                  <Tr key={row.user_id}>
                    <Td primary>{row.display_name ?? t("admin.member")}</Td>
                    <Td label={t("totals.calls")} align="right">{format.number(row.totals.calls)}</Td>
                    <Td label={t("totals.tokens")} align="right">{format.number(row.totals.total_tokens)}</Td>
                    <Td label={t("totals.cost")} align="right">{format.number(Number(row.totals.cost), "money")}</Td>
                  </Tr>
                ))}
                {/* Always listed, even when no member used AI in the period (15.4). */}
                <Tr>
                  <Td primary>{t("admin.system")}</Td>
                  <Td label={t("totals.calls")} align="right">{format.number(usage.data.system.calls)}</Td>
                  <Td label={t("totals.tokens")} align="right">{format.number(usage.data.system.total_tokens)}</Td>
                  <Td label={t("totals.cost")} align="right">{format.number(Number(usage.data.system.cost), "money")}</Td>
                </Tr>
              </TBody>
            </DataTable>
            <p className="text-xs text-text-muted">{t("admin.priceNote")}</p>
          </section>
          <ByPurpose rows={usage.data.by_purpose} heading={t("admin.byPurpose")} />
        </>
      )}
    </div>
  );
}
