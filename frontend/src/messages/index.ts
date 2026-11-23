/**
 * The message catalogues (14.2): one JSON file per area and language, ICU syntax.
 * Generated layout: add a namespace by adding `pl/<name>.json` and `en/<name>.json` here.
 */
import plAdminAtlas from "./pl/adminAtlas.json";
import plAdminBranding from "./pl/adminBranding.json";
import plAdminLlm from "./pl/adminLlm.json";
import plAdminSecurity from "./pl/adminSecurity.json";
import plAdminUsers from "./pl/adminUsers.json";
import plAtlas from "./pl/atlas.json";
import plChat from "./pl/chat.json";
import plCommon from "./pl/common.json";
import plContributions from "./pl/contributions.json";
import plErrors from "./pl/errors.json";
import plHome from "./pl/home.json";
import plNotebook from "./pl/notebook.json";
import plPrivacy from "./pl/privacy.json";
import plReview from "./pl/review.json";
import plShell from "./pl/shell.json";
import plTitles from "./pl/titles.json";
import plUsage from "./pl/usage.json";
import plWiki from "./pl/wiki.json";
import enAdminAtlas from "./en/adminAtlas.json";
import enAdminBranding from "./en/adminBranding.json";
import enAdminLlm from "./en/adminLlm.json";
import enAdminSecurity from "./en/adminSecurity.json";
import enAdminUsers from "./en/adminUsers.json";
import enAtlas from "./en/atlas.json";
import enChat from "./en/chat.json";
import enCommon from "./en/common.json";
import enContributions from "./en/contributions.json";
import enErrors from "./en/errors.json";
import enHome from "./en/home.json";
import enNotebook from "./en/notebook.json";
import enPrivacy from "./en/privacy.json";
import enReview from "./en/review.json";
import enShell from "./en/shell.json";
import enTitles from "./en/titles.json";
import enUsage from "./en/usage.json";
import enWiki from "./en/wiki.json";

const pl = {
  adminAtlas: plAdminAtlas,
  adminBranding: plAdminBranding,
  adminLlm: plAdminLlm,
  adminSecurity: plAdminSecurity,
  adminUsers: plAdminUsers,
  atlas: plAtlas,
  chat: plChat,
  common: plCommon,
  contributions: plContributions,
  errors: plErrors,
  home: plHome,
  notebook: plNotebook,
  privacy: plPrivacy,
  review: plReview,
  shell: plShell,
  titles: plTitles,
  usage: plUsage,
  wiki: plWiki,
};

const en = {
  adminAtlas: enAdminAtlas,
  adminBranding: enAdminBranding,
  adminLlm: enAdminLlm,
  adminSecurity: enAdminSecurity,
  adminUsers: enAdminUsers,
  atlas: enAtlas,
  chat: enChat,
  common: enCommon,
  contributions: enContributions,
  errors: enErrors,
  home: enHome,
  notebook: enNotebook,
  privacy: enPrivacy,
  review: enReview,
  shell: enShell,
  titles: enTitles,
  usage: enUsage,
  wiki: enWiki,
};

export type Messages = typeof pl;

/** English is checked against Polish key for key (messages.test.ts); typed as Polish's shape. */
export const MESSAGES: Record<"pl" | "en", Messages> = { pl, en: en as Messages };
