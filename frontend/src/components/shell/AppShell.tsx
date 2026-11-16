"use client";

import { Menu } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { useBranding } from "@/lib/branding-context";
import { saveCollapsed } from "@/lib/sidebar";
import { closeModal, isBackdropClick, openModal, supportsClosedBy } from "@/lib/top-layer";

import { BrandMark } from "./BrandMark";
import { Sidebar } from "./Sidebar";

/**
 * Open Notebook–style layout (12.1): the menu on the left, the workspace on the right. md+: the
 * page fills the window and only the workspace scrolls. Phones: a top bar opens the menu as a
 * drawer, and the document scrolls (so the browser's toolbars can collapse).
 */
export function AppShell({
  initialCollapsed,
  banner,
  children,
}: {
  initialCollapsed: boolean;
  banner?: ReactNode;
  children: ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(initialCollapsed);
  const [drawer, setDrawer] = useState(false);
  const { org_name } = useBranding();
  const t = useTranslations("shell");
  return (
    <div className="flex min-h-dvh md:h-dvh md:overflow-hidden">
      <div className="hidden md:flex">
        <Sidebar
          mode="desktop"
          collapsed={collapsed}
          onToggle={() => {
            saveCollapsed(!collapsed);
            setCollapsed(!collapsed);
          }}
          onExpand={() => setDrawer(true)}
        />
      </div>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex items-center gap-2 border-b border-border bg-surface px-2 pt-[max(0.5rem,env(safe-area-inset-top))] pb-2 md:hidden">
          <button
            type="button"
            aria-label={t("openMenu")}
            aria-expanded={drawer}
            onClick={() => setDrawer(true)}
            className="inline-flex size-11 items-center justify-center rounded-lg text-text hover:bg-surface-muted"
          >
            <Menu aria-hidden className="size-6" />
          </button>
          <Link href="/" className="flex min-w-0 items-center gap-2 font-display text-lg font-semibold text-primary-strong">
            <BrandMark />
            <span className="truncate">{org_name}</span>
          </Link>
        </header>
        <main id="content" tabIndex={-1} className="flex flex-1 flex-col outline-none md:overflow-y-auto">
          {banner}
          {/* A size container: pages lay out by the workspace's width, so the collapsed menu counts. */}
          <div className="@container mx-auto flex w-full max-w-7xl flex-1 flex-col px-4 py-6 md:px-8 md:py-8">{children}</div>
        </main>
      </div>
      <Drawer open={drawer} onClose={() => setDrawer(false)}>
        <Sidebar mode="drawer" onClose={() => setDrawer(false)} />
      </Drawer>
    </div>
  );
}

/**
 * The menu over the workspace: a native modal `<dialog>` (focus trap, Escape, focus return). It
 * slides in and out (`.drawer` in globals.css); a tap on the backdrop closes it (`closedby`, with
 * a click-point fallback for browsers without it).
 */
function Drawer({ open, onClose, children }: { open: boolean; onClose: () => void; children: ReactNode }) {
  const t = useTranslations("shell");
  const ref = useRef<HTMLDialogElement>(null);
  // The content stays mounted while the dialog animates out.
  const [shown, setShown] = useState(open);
  if (open && !shown) setShown(true);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open) openModal(dialog);
    else
      void closeModal(dialog).then(() => {
        if (!dialog.open) setShown(false);
      });
  }, [open]);
  return (
    <dialog
      ref={ref}
      aria-label={t("menu")}
      // Not in React's types yet; light dismiss for browsers that support it.
      {...({ closedby: "any" } as object)}
      onCancel={(event) => {
        // Escape and light dismiss: let the state close it, so it animates out.
        event.preventDefault();
        onClose();
      }}
      onClose={() => {
        if (open) onClose();
      }}
      onClick={(event) => {
        if (!supportsClosedBy() && isBackdropClick(event.currentTarget, event)) onClose();
      }}
      className="drawer fixed inset-y-0 left-0 m-0 h-dvh max-h-none w-72 max-w-[85vw] border-0 bg-sidebar p-0 shadow-xl"
    >
      {shown && children}
    </dialog>
  );
}
