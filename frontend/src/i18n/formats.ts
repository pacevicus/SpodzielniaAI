import type { Formats } from "next-intl";

/** Named date and number formats (14.2), replacing the `Intl.DateTimeFormat("pl-PL")` calls. */
export const formats = {
  dateTime: {
    short: { dateStyle: "short" },
    medium: { dateStyle: "medium" },
    long: { dateStyle: "long" },
    dateTime: { dateStyle: "medium", timeStyle: "short" },
    dateTimeShort: { dateStyle: "short", timeStyle: "short" },
    dateTimeSeconds: { dateStyle: "short", timeStyle: "medium" },
  },
  number: {
    integer: { maximumFractionDigits: 0 },
    percent: { style: "percent", maximumFractionDigits: 0 },
    money: { minimumFractionDigits: 2, maximumFractionDigits: 4 },
  },
} satisfies Formats;
