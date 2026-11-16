"use client";

import { useTranslations } from "next-intl";
import { useState } from "react";

import { BUCKETS, histogram, type UserCoverage } from "@/lib/admin";

const W = 560;
const H = 220;
const M = { top: 20, right: 12, bottom: 32, left: 36 };
const BAR = 24; // mark spec: columns no thicker than 24px
const RADIUS = 4;

/** A column with a 4px rounded data end and a square base. */
function columnPath(x: number, y: number, width: number, height: number): string {
  if (height <= 0) return "";
  const r = Math.min(RADIUS, height, width / 2);
  const base = y + height;
  return `M${x},${base} V${y + r} Q${x},${y} ${x + r},${y} H${x + width - r} Q${x + width},${y} ${x + width},${y + r} V${base} Z`;
}

function niceMax(value: number): number {
  if (value <= 4) return 4;
  const step = 10 ** Math.floor(Math.log10(value));
  return Math.ceil(value / step) * step;
}

/**
 * How many accounts sit how close to a coverage limit (their closest window). One series,
 * so no legend: the title names it. Each column is its own hover/focus target; the table
 * below carries every value without hovering.
 */
export function CoverageHistogram({ users }: { users: UserCoverage[] }) {
  const t = useTranslations("adminSecurity");
  const accounts = (count: number) => t("accounts", { count });
  const counts = histogram(users);
  const [active, setActive] = useState<number | null>(null);
  const max = niceMax(Math.max(...counts, 1));
  const plotW = W - M.left - M.right;
  const plotH = H - M.top - M.bottom;
  const band = plotW / counts.length;
  const y = (v: number) => M.top + plotH - (v / max) * plotH;
  const ticks = [0, max / 2, max];

  return (
    <figure className="flex flex-col gap-2">
      <figcaption className="text-sm font-medium">{t("histogram.title")}</figcaption>
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="group" aria-label={t("histogram.label")}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={M.left} x2={W - M.right} y1={y(t)} y2={y(t)} stroke="var(--viz-grid)" strokeWidth={1} />
              <text x={M.left - 6} y={y(t)} textAnchor="end" dominantBaseline="middle" fontSize={11} fill="var(--viz-muted)" className="tabular-nums">
                {t}
              </text>
            </g>
          ))}
          <line x1={M.left} x2={W - M.right} y1={y(0)} y2={y(0)} stroke="var(--viz-axis)" strokeWidth={1} />
          {counts.map((count, i) => {
            const width = Math.min(BAR, band * 0.6);
            const x = M.left + i * band + (band - width) / 2;
            const top = y(count);
            return (
              <g
                key={BUCKETS[i].label}
                tabIndex={0}
                role="button"
                aria-label={t("histogram.bucket", { bucket: BUCKETS[i].label, accounts: accounts(count) })}
                onPointerEnter={() => setActive(i)}
                onPointerLeave={() => setActive(null)}
                onFocus={() => setActive(i)}
                onBlur={() => setActive(null)}
                className="outline-none"
              >
                {/* The hit target is the whole band, not the painted pixels. */}
                <rect x={M.left + i * band} y={M.top} width={band} height={plotH} fill="transparent" />
                <path d={columnPath(x, top, width, y(0) - top)} fill="var(--viz-series)" opacity={active === null || active === i ? 1 : 0.55} />
                <text x={x + width / 2} y={top - 6} textAnchor="middle" fontSize={11} className="fill-text-muted">
                  {count}
                </text>
                <text x={M.left + i * band + band / 2} y={H - M.bottom + 16} textAnchor="middle" fontSize={11} className="fill-text-muted">
                  {BUCKETS[i].label}
                </text>
              </g>
            );
          })}
        </svg>
        {active !== null && (
          <div
            role="tooltip"
            className="pointer-events-none absolute top-0 rounded-lg border border-border bg-surface px-3 py-2 text-xs shadow"
            // Positioned by percentage of the band (CSSOM, not an inline style attribute).
            ref={(el) => {
              if (el) el.style.left = `${((M.left + (active + 0.5) * band) / W) * 100}%`;
            }}
          >
            <div className="text-sm font-semibold tabular-nums">{accounts(counts[active])}</div>
            <div className="text-text-muted">{t("histogram.ofLimit", { bucket: BUCKETS[active].label })}</div>
          </div>
        )}
      </div>
      <details className="text-sm">
        <summary className="cursor-pointer text-text-muted">{t("histogram.table")}</summary>
        <table className="mt-2 text-left">
          <caption className="sr-only">{t("histogram.table")}</caption>
          <thead>
            <tr>
              <th className="pr-6 font-medium">{t("histogram.closeness")}</th>
              <th className="font-medium">{t("histogram.accounts")}</th>
            </tr>
          </thead>
          <tbody>
            {counts.map((c, i) => (
              <tr key={BUCKETS[i].label}>
                <td className="pr-6">{BUCKETS[i].label}</td>
                <td className="tabular-nums">{c}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}
