"use client";

import { useTranslations } from "next-intl";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { closeModal, openModal } from "@/lib/top-layer";

import { Button } from "./Button";

/**
 * A native modal `<dialog>`: focus trap, Escape and backdrop come from the browser. Escape is
 * routed through `onClose`, so the parent's state stays the source of truth and the dialog can
 * animate out. No light dismiss: a stray tap must not cancel a half-filled form or a confirmation.
 */
export function Dialog({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = `${useId()}-title`;
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
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClose={() => {
        if (open) onClose();
      }}
      aria-labelledby={titleId}
      // A bottom sheet on a phone (12.2): full width, scrolls inside, clear of the home indicator.
      className="modal m-auto max-h-[90dvh] w-full max-w-lg overflow-y-auto rounded-xl border border-border bg-surface p-6 text-text max-md:mb-0 max-md:max-w-none max-md:rounded-b-none max-md:pb-[max(1.5rem,env(safe-area-inset-bottom))]"
    >
      {shown && (
        <>
          <h2 id={titleId} className="mb-4 font-display text-lg font-semibold">
            {title}
          </h2>
          {children}
        </>
      )}
    </dialog>
  );
}

/** A yes/no question in the app's own dialog (instead of the browser's unstyled `confirm()`). */
export function ConfirmDialog({
  open,
  title,
  confirmLabel,
  danger = false,
  busy = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  confirmLabel: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const common = useTranslations("common");
  return (
    <Dialog open={open} onClose={onCancel} title={title}>
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onCancel}>
          {common("cancel")}
        </Button>
        <Button variant={danger ? "danger" : "primary"} busy={busy} onClick={onConfirm}>
          {confirmLabel}
        </Button>
      </div>
    </Dialog>
  );
}
