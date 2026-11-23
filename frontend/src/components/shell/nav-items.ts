import {
  Bot,
  BookOpen,
  ClipboardCheck,
  Coins,
  Database,
  Gauge,
  FilePen,
  HandHeart,
  Images,
  LockKeyhole,
  MessagesSquare,
  NotebookPen,
  Palette,
  ShieldCheck,
  Tags,
  Users,
  type LucideIcon,
} from "lucide-react";

import { hasRole, type Me, type Role } from "@/lib/session";
import type { Messages } from "@/messages";

/** Labels are message keys: `shell.nav.<key>` (and `home.cards.<key>` on the start page). */
export type NavKey = keyof Messages["shell"]["nav"];
export type SectionKey = keyof Messages["shell"]["sections"];

export interface NavItem {
  href: string;
  key: NavKey;
  icon: LucideIcon;
}

export interface NavSection {
  key: SectionKey;
  role?: Role;
  items: NavItem[];
}

/** One menu for the sidebar, the rail and the phone drawer (the API enforces the roles regardless). */
export const SECTIONS: NavSection[] = [
  {
    key: "work",
    items: [
      { href: "/notebook", key: "notebook", icon: NotebookPen },
      { href: "/me/drafts", key: "drafts", icon: FilePen },
      { href: "/me/contributions", key: "contributions", icon: HandHeart },
      { href: "/me/usage", key: "usage", icon: Gauge },
    ],
  },
  {
    key: "knowledge",
    items: [
      { href: "/wiki", key: "wiki", icon: BookOpen },
      { href: "/atlas", key: "atlas", icon: Images },
      { href: "/chat", key: "chat", icon: MessagesSquare },
    ],
  },
  {
    key: "experts",
    role: "EXPERT",
    items: [
      { href: "/review", key: "review", icon: ClipboardCheck },
      { href: "/review/atlas", key: "atlasReview", icon: Images },
      { href: "/review/labels", key: "labels", icon: Tags },
    ],
  },
  {
    key: "admin",
    role: "ADMIN",
    items: [
      { href: "/admin/security", key: "security", icon: ShieldCheck },
      { href: "/admin/llm", key: "llm", icon: Bot },
      { href: "/admin/users", key: "users", icon: Users },
      { href: "/admin/atlas", key: "adminAtlas", icon: Database },
      { href: "/admin/branding", key: "branding", icon: Palette },
      { href: "/admin/usage", key: "adminUsage", icon: Coins },
    ],
  },
];

export const PRIVACY_ITEM: NavItem = { href: "/privacy", key: "privacy", icon: LockKeyhole };

export function visibleSections(me: Me | undefined): NavSection[] {
  if (!me) return [];
  return SECTIONS.filter((section) => !section.role || hasRole(me, section.role));
}

/** The longest href that is the path or a parent of it, so `/review/atlas` doesn't also mark `/review`. */
export function activeHref(path: string, hrefs: string[]): string | undefined {
  return hrefs
    .filter((href) => path === href || path.startsWith(`${href}/`))
    .sort((a, b) => b.length - a.length)[0];
}
