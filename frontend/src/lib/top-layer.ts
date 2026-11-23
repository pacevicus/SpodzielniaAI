/**
 * Opening and closing native modal `<dialog>`s with an exit animation.
 *
 * Browsers that transition `overlay` and discrete `display` keep the dialog in the top layer while
 * the CSS exit transition runs. The others (Firefox, Safari today) close instantly, so there we set
 * `data-closing` first (the CSS drops the open styles), wait for the transitions, then close.
 */

let displayTransition: boolean | undefined;

/** Probes discrete `display` transitions (Firefox parses `allow-discrete` without honouring it). */
function canTransitionDisplay(): boolean {
  if (displayTransition !== undefined) return displayTransition;
  if (!window.CSS?.supports?.("transition-behavior", "allow-discrete") || !document.body) return false;
  const probe = document.createElement("div");
  // CSSOM, not a style attribute (CSP). A browser that cannot parse the value ignores it, so the
  // probe hides at once and the check correctly fails; `important` beats reduced-motion resets.
  probe.style.setProperty("transition", "display 1s allow-discrete", "important");
  probe.style.setProperty("display", "block");
  document.body.appendChild(probe);
  void getComputedStyle(probe).display;
  probe.style.setProperty("display", "none");
  displayTransition = getComputedStyle(probe).display === "block";
  probe.remove();
  return displayTransition;
}

function supportsTopLayerExit(): boolean {
  return Boolean(window.CSS?.supports?.("overlay", "auto")) && canTransitionDisplay();
}

export function openModal(dialog: HTMLDialogElement): void {
  if (dialog.open) return;
  dialog.removeAttribute("data-closing");
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", ""); // environments without <dialog> support (jsdom)
}

export async function closeModal(dialog: HTMLDialogElement): Promise<void> {
  if (!dialog.open) return;
  if (typeof dialog.close !== "function") {
    dialog.removeAttribute("open");
    return;
  }
  if (!supportsTopLayerExit() && typeof dialog.getAnimations === "function") {
    dialog.setAttribute("data-closing", "");
    const animations = dialog.getAnimations({ subtree: true });
    if (animations.length > 0) {
      await Promise.race([
        Promise.allSettled(animations.map((a) => a.finished)),
        new Promise((resolve) => setTimeout(resolve, 1000)),
      ]);
    }
    // Reopened while the exit ran: openModal cleared the flag, so stay open.
    if (!dialog.hasAttribute("data-closing")) return;
    dialog.removeAttribute("data-closing");
  }
  dialog.close();
}

/** `closedby="any"` light dismiss; the fallback for browsers without it checks the click point. */
export const supportsClosedBy = (): boolean =>
  typeof HTMLDialogElement !== "undefined" && "closedBy" in HTMLDialogElement.prototype;

export function isBackdropClick(dialog: HTMLDialogElement, event: { target: EventTarget; clientX: number; clientY: number }): boolean {
  if (event.target !== dialog) return false; // a click on the content
  const rect = dialog.getBoundingClientRect();
  const inside = rect.top <= event.clientY && event.clientY <= rect.bottom && rect.left <= event.clientX && event.clientX <= rect.right;
  return !inside;
}
