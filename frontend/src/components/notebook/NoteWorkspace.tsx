"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Link2, Trash2 } from "lucide-react";
import { useState } from "react";

import { AskPanel } from "@/components/notebook/AskPanel";
import { DerivativesPanel } from "@/components/notebook/DerivativesPanel";
import { PhotosPanel } from "@/components/notebook/PhotosPanel";
import { LinkDialog } from "@/components/notebook/LinkDialog";
import { UploadZone } from "@/components/notebook/UploadZone";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { cn } from "@/components/ui/cn";
import { Tabs } from "@/components/ui/Tabs";
import { ApiError } from "@/lib/api";
import { useAutosave, type SaveState } from "@/lib/autosave";
import { useDeleteNote, useNote, useSaveNote } from "@/lib/notebook";

const NoteEditor = dynamic(() => import("@/components/notebook/NoteEditor"), {
  ssr: false,
  loading: () => <EditorLoading />,
});

function EditorLoading() {
  const t = useTranslations("notebook.workspace");
  return <div className="min-h-64 rounded-xl border border-border bg-surface p-4 text-text-muted">{t("loadingEditor")}</div>;
}

type Panel = "sources" | "note" | "ask";

/** Open Notebook's notebook layout (12.3): Sources | Note | Ask. */
export function NoteWorkspace({ noteId }: { noteId: string }) {
  const t = useTranslations("notebook.workspace");
  const saveLabel = (state: SaveState) => (state === "idle" ? "" : t(`save.${state}`));
  const phoneTabs = (["sources", "note", "ask"] as const).map((key) => ({ key, label: t(`tabs.${key}`) }));
  const sideTabs = (["sources", "ask"] as const).map((key) => ({ key, label: t(`tabs.${key}`) }));
  const note = useNote(noteId);
  const save = useSaveNote(noteId);
  const remove = useDeleteNote();
  const router = useRouter();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [linkOpen, setLinkOpen] = useState(false);
  const [tab, setTab] = useState<Panel>("note");
  const autosave = useAutosave((change: { title?: string; body?: string }) => save.mutateAsync(change));

  if (note.isPending) return <p className="text-text-muted">{t("loading")}</p>;
  if (note.isError) {
    const missing = note.error instanceof ApiError && note.error.status === 404;
    return (
      <div className="flex flex-col gap-2">
        <h1 className="font-display text-2xl font-semibold">{missing ? t("notFound") : t("error")}</h1>
        <p className="text-text-muted">
          {missing ? t("notFoundBody") : t("loadFailed")}
        </p>
        <Link href="/notebook" className="text-primary-strong underline">
          {t("back")}
        </Link>
      </div>
    );
  }

  const data = note.data;
  // One DOM for every width; the workspace's own width (a container query, so the collapsed
  // menu counts) decides what shows: tabs below 48rem, the note beside Sources/Ask up to
  // 72rem, all three columns above (12.3). Hidden panels stay mounted: the editor keeps its state.
  const sources = cn(tab !== "sources" && "@max-3xl:hidden", tab === "ask" && "@3xl:@max-6xl:hidden");
  const ask = cn(tab !== "ask" && "@max-6xl:hidden");
  const side = "@6xl:sticky @6xl:top-0 @6xl:max-h-[calc(100dvh-4rem)] @6xl:overflow-y-auto";
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Link href="/notebook" className="text-sm text-text-muted hover:text-text">
          {t("backShort")}
        </Link>
        <span role="status" className="text-sm text-text-muted">
          {saveLabel(autosave.state)}
        </span>
      </div>
      <Tabs tabs={phoneTabs} value={tab} onChange={setTab} label={t("view")} idPrefix="nb" panelPrefix="nb" fill className="@3xl:hidden" />
      <div className="grid gap-6 @3xl:grid-cols-[minmax(0,1fr)_20rem] @6xl:grid-cols-[17rem_minmax(0,1fr)_20rem]">
        <section
          id="nb-panel-note"
          aria-label={t("tabs.note")}
          className={cn("flex min-w-0 flex-col gap-4 @6xl:col-start-2 @6xl:row-start-1", tab !== "note" && "@max-3xl:hidden")}
        >
          <label className="sr-only" htmlFor="note-title">
            {t("noteTitle")}
          </label>
          <input
            id="note-title"
            defaultValue={data.title}
            maxLength={200}
            onChange={(event) => {
              if (event.target.value.trim()) autosave.schedule({ title: event.target.value.trim() });
            }}
            className="w-full bg-transparent font-display text-2xl font-semibold outline-none md:text-3xl"
          />
          <NoteEditor markdown={data.body} onChange={(body) => autosave.schedule({ body })} />
          <div>
            <Button
              variant="ghost"
              className="text-danger"
              busy={remove.isPending}
              onClick={() => setConfirmDelete(true)}
            >
              <Trash2 aria-hidden className="size-4" />
              {t("delete")}
            </Button>
            <ConfirmDialog
              open={confirmDelete}
              title={t("confirmDelete")}
              confirmLabel={t("delete")}
              danger
              busy={remove.isPending}
              onCancel={() => setConfirmDelete(false)}
              onConfirm={async () => {
                await remove.mutateAsync(noteId);
                router.push("/notebook");
              }}
            />
          </div>
        </section>
        <div className="flex min-w-0 flex-col gap-4 @6xl:contents">
          <Tabs
            tabs={sideTabs}
            value={tab === "ask" ? "ask" : "sources"}
            onChange={setTab}
            label={t("sidePanel")}
            idPrefix="nb-side"
            panelPrefix="nb"
            fill
            className="hidden @3xl:@max-6xl:flex"
          />
          <section
            id="nb-panel-sources"
            aria-label={t("tabs.sources")}
            className={cn("flex min-w-0 flex-col gap-6 @6xl:col-start-1 @6xl:row-start-1", side, sources)}
          >
            <h2 className="hidden font-display text-sm font-semibold tracking-wide text-text-muted uppercase @6xl:block">{t("tabs.sources")}</h2>
            <Button variant="secondary" onClick={() => setLinkOpen(true)}>
              <Link2 aria-hidden className="size-4" />
              {t("addLink")}
            </Button>
            <UploadZone noteId={noteId} />
            <PhotosPanel noteId={noteId} attachments={data.attachments} />
            <DerivativesPanel noteId={noteId} derivatives={data.derivatives} />
          </section>
          <section id="nb-panel-ask" aria-label={t("tabs.ask")} className={cn("min-w-0 @6xl:col-start-3 @6xl:row-start-1", side, ask)}>
            <AskPanel />
          </section>
        </div>
      </div>
      <LinkDialog noteId={noteId} open={linkOpen} onClose={() => setLinkOpen(false)} />
    </div>
  );
}
