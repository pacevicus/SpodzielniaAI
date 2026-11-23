/**
 * AI usage (stage 15, US-09): the member's own figures and the administrator's view. The rules
 * (inclusive UTC days, at most a year, the default last 30 days) are the backend's; the period
 * is checked here first so that a wrong range is explained before a request is made.
 */

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { components } from "@/lib/api-schema";
import { unwrap } from "@/lib/query";

export type MyUsage = components["schemas"]["MyUsageOut"];
export type AdminUsage = components["schemas"]["AdminUsageOut"];
export type UsageTotals = components["schemas"]["UsageTotalsOut"];

/** An inclusive period as calendar days (``YYYY-MM-DD``). */
export interface Period {
  from: string;
  to: string;
}

export const MAX_SPAN_DAYS = 366;
const DAY_MS = 86_400_000;

function isoDay(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/** The last 30 days up to today (UTC), as the backend defaults to. */
export function defaultPeriod(now: Date = new Date()): Period {
  const start = new Date(now.getTime() - 29 * DAY_MS);
  return { from: isoDay(start), to: isoDay(now) };
}

/** Why a period cannot be asked for, if it cannot; ``null`` when it can. */
export function periodProblem(period: Period): "invalid" | "tooLong" | null {
  if (!period.from || !period.to) return "invalid";
  if (period.from > period.to) return "invalid";
  const days = (Date.parse(period.to) - Date.parse(period.from)) / DAY_MS + 1;
  return days > MAX_SPAN_DAYS ? "tooLong" : null;
}

export function useMyUsage(period: Period) {
  return useQuery({
    queryKey: ["usage", "me", period.from, period.to],
    enabled: periodProblem(period) === null,
    queryFn: async (): Promise<MyUsage> =>
      unwrap(await api.GET("/me/usage", { params: { query: { from: period.from, to: period.to } } })),
  });
}

export function useAdminUsage(period: Period) {
  return useQuery({
    queryKey: ["usage", "admin", period.from, period.to],
    enabled: periodProblem(period) === null,
    queryFn: async (): Promise<AdminUsage> =>
      unwrap(await api.GET("/admin/usage", { params: { query: { from: period.from, to: period.to } } })),
  });
}
