"use client";

import { useFormatter, useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";

import { StepUpNotice } from "@/components/auth/RequireRole";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { DataTable, TBody, Td, Th, THead, Tr } from "@/components/ui/DataTable";
import { Dialog } from "@/components/ui/Dialog";
import { useToast } from "@/components/ui/Toast";
import { StepUpRequired } from "@/lib/api";
import {
  CAPABILITIES,
  generates,
  llmErrorKey,
  removalBlocked,
  useClearProvider,
  useCreateModel,
  useCreateProvider,
  useDeleteProvider,
  useModelDisabled,
  useProbe,
  useProviderDisabled,
  useTestProvider,
  useUnclearProvider,
  useUpdateModel,
  useUpdateProvider,
  type Capability,
  type Connection,
  type LlmModel,
  type LlmStatus,
  type Provider,
} from "@/lib/llm-admin";

const INPUT = "rounded-lg border border-border bg-surface px-3 py-2";

/** What the backend will classify ``url`` as (it decides; this only explains up front). */
export function previewKind(url: string, status: Pick<LlmStatus, "local_endpoints" | "cloud_hosts">): "LOCAL" | "CLOUD" | null {
  let parsed: URL;
  try {
    parsed = new URL(url.trim());
  } catch {
    return null;
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) return null;
  if (status.local_endpoints.includes(parsed.origin)) return "LOCAL";
  if (parsed.protocol === "https:" && !parsed.port && status.cloud_hosts.includes(parsed.hostname)) return "CLOUD";
  return null;
}

export function KindBadge({ kind }: { kind: "LOCAL" | "CLOUD" }) {
  const t = useTranslations("adminLlm.kind");
  return kind === "LOCAL" ? <Badge tone="ok">{t("LOCAL")}</Badge> : <Badge tone="warn">{t("CLOUD")}</Badge>;
}

type Translate = ReturnType<typeof useTranslations<"adminLlm">>;

function keyLabel(t: Translate, provider: Provider): string {
  if (provider.inherit_env_key) return t("providers.keyFromServer");
  if (provider.key_hint) return `••••${provider.key_hint}`;
  return provider.key_set ? t("providers.keySet") : t("providers.keyNone");
}

// --- Providers ----------------------------------------------------------------------------------

export function ProvidersPanel({ status, providers, models }: { status: LlmStatus; providers: Provider[]; models: LlmModel[] }) {
  const t = useTranslations("adminLlm");
  const [editing, setEditing] = useState<Provider | "new" | null>(null);
  const [clearing, setClearing] = useState<Provider | null>(null);
  const [unclearing, setUnclearing] = useState<Provider | null>(null);
  const [removing, setRemoving] = useState<Provider | null>(null);
  const [tests, setTests] = useState<Record<string, Connection | string>>({});
  const test = useTestProvider();
  const disable = useProviderDisabled();
  const toast = useToast();

  async function runTest(provider: Provider) {
    try {
      const result = await test.mutateAsync(provider.id);
      setTests((current) => ({ ...current, [provider.id]: result }));
    } catch (error) {
      setTests((current) => ({ ...current, [provider.id]: t(`errors.${llmErrorKey(error, "testFailed")}`) }));
    }
  }

  async function toggle(provider: Provider) {
    try {
      await disable.mutateAsync({ id: provider.id, disabled: !provider.disabled });
    } catch (error) {
      toast(t(`errors.${llmErrorKey(error)}`), "error");
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <Card className="flex flex-col gap-1 text-sm">
        <p>
          {t("providers.localServers")} <code>{status.local_endpoints.join(", ") || "—"}</code>
        </p>
        <p>
          {t("providers.cloudApis")} <code>{status.cloud_hosts.join(", ") || "—"}</code>
          {!status.allow_cloud && t("providers.cloudOff")}
        </p>
        <p>
          {t("providers.privateEndpoints")} <code>{status.private_data_endpoints.join(", ") || "—"}</code>
          {status.private_data_endpoints.length === 0 && t("providers.privateOff")}
        </p>
        <p className="text-text-muted">{t("providers.listsHint")}</p>
      </Card>
      <div className="flex justify-end">
        <Button onClick={() => setEditing("new")}>{t("providers.add")}</Button>
      </div>
      <DataTable>
        <THead>
          <Tr>
            <Th>{t("providers.name")}</Th>
            <Th>{t("providers.kind")}</Th>
            <Th>{t("providers.address")}</Th>
            <Th>{t("providers.apiKey")}</Th>
            <Th>{t("providers.privateData")}</Th>
            <Th>{t("providers.state")}</Th>
            <Th>{t("providers.actions")}</Th>
          </Tr>
        </THead>
        <TBody>
          {providers.map((p) => {
            const result = tests[p.id];
            const removal = removalBlocked(p);
            const blocked = removal ? t(`removal.${removal.key}`, removal.values) : null;
            return (
              <Tr key={p.id}>
                <Td primary className="font-medium">{p.name}</Td>
                <Td label={t("providers.kind")}>
                  <KindBadge kind={p.kind} />
                </Td>
                <Td label={t("providers.address")}>
                  <code className="break-all">{p.base_url}</code>
                </Td>
                <Td label={t("providers.apiKey")}>{keyLabel(t, p)}</Td>
                <Td label={t("providers.privateData")}>
                  <ClearanceCell provider={p} onClear={() => setClearing(p)} onUnclear={() => setUnclearing(p)} />
                </Td>
                <Td label={t("providers.state")}>
                  <div className="flex flex-wrap gap-1">
                    {p.in_active && <Badge tone="primary">{t("providers.inActive")}</Badge>}
                    {p.disabled && <Badge>{t("providers.disabled")}</Badge>}
                    {!p.allowed && <Badge tone="danger">{t("providers.notAllowed")}</Badge>}
                  </div>
                  {result !== undefined && <ConnectionResult result={result} />}
                </Td>
                <Td>
                  <div className="flex flex-wrap gap-1">
                    <Button
                      variant="secondary"
                      busy={test.isPending && test.variables === p.id}
                      disabled={p.kind === "CLOUD" && !status.allow_cloud}
                      onClick={() => void runTest(p)}
                    >
                      {t("providers.test")}
                    </Button>
                    <Button variant="ghost" onClick={() => setEditing(p)}>
                      {t("providers.edit")}
                    </Button>
                    <Button variant="ghost" disabled={p.in_active && !p.disabled} onClick={() => void toggle(p)}>
                      {p.disabled ? t("providers.enable") : t("providers.disable")}
                    </Button>
                    <Button
                      variant="ghost"
                      disabled={blocked !== null}
                      title={blocked ?? undefined}
                      aria-label={t("providers.removeLabel", { name: p.name })}
                      onClick={() => setRemoving(p)}
                    >
                      {t("providers.remove")}
                    </Button>
                  </div>
                  {blocked && <p className="mt-1 text-xs text-text-muted">{blocked}</p>}
                </Td>
              </Tr>
            );
          })}
        </TBody>
      </DataTable>
      <ProviderDialog status={status} provider={editing} onClose={() => setEditing(null)} />
      <ClearanceDialog provider={clearing} onClose={() => setClearing(null)} />
      <UnclearDialog provider={unclearing} onClose={() => setUnclearing(null)} />
      <RemoveDialog provider={removing} models={models.filter((m) => m.provider_id === removing?.id).length} onClose={() => setRemoving(null)} />
    </div>
  );
}

// --- Removal (11.1) ------------------------------------------------------------------------------

export function RemoveDialog({ provider, models, onClose }: { provider: Provider | null; models: number; onClose: () => void }) {
  const t = useTranslations("adminLlm");
  const common = useTranslations("common");
  const remove = useDeleteProvider();
  const toast = useToast();

  async function confirm() {
    if (!provider) return;
    try {
      await remove.mutateAsync(provider.id);
      toast(t("removeDialog.done", { name: provider.name }), "ok");
      onClose();
    } catch (error) {
      toast(t(`errors.${llmErrorKey(error, "removeFailed")}`), "error");
    }
  }

  return (
    <Dialog open={provider !== null} onClose={onClose} title={t("removeDialog.title", { name: provider?.name ?? "" })}>
      <div className="flex flex-col gap-3 text-sm">
        <p>{t("removeDialog.body", { count: models, cleared: provider?.private_data_cleared ? t("removeDialog.cleared") : "" })}</p>
        {provider?.removal_archives ? (
          <p className="text-text-muted">{t("removeDialog.archives")}</p>
        ) : (
          <p className="text-text-muted">{t("removeDialog.deletes")}</p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            {common("cancel")}
          </Button>
          <Button variant="danger" busy={remove.isPending} onClick={() => void confirm()}>
            {t("removeDialog.submit")}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

// --- Clearance for private data (10.1) -----------------------------------------------------------

export function ClearanceCell({ provider, onClear, onUnclear }: { provider: Provider; onClear: () => void; onUnclear: () => void }) {
  const t = useTranslations("adminLlm.clearance");
  if (provider.private_data_cleared) {
    return (
      <div className="flex flex-col items-start gap-1">
        <Badge tone="ok">{t("cleared")}</Badge>
        <span className="text-xs text-text-muted">
          {t("details", {
            region: provider.cleared_region ?? "",
            retention: provider.cleared_retention ?? "",
            contract: provider.cleared_contract_ref ?? "",
          })}
        </span>
        <Button variant="ghost" onClick={onUnclear}>
          {t("withdraw")}
        </Button>
      </div>
    );
  }
  if (!provider.clearable) {
    return <span className="text-xs text-text-muted">{t("notClearable")}</span>;
  }
  return (
    <Button variant="secondary" disabled={provider.disabled} onClick={onClear}>
      {t("clear")}
    </Button>
  );
}

function ClearanceDialog({ provider, onClose }: { provider: Provider | null; onClose: () => void }) {
  const t = useTranslations("adminLlm.clearance");
  return (
    <Dialog open={provider !== null} onClose={onClose} title={t("dialogTitle", { name: provider?.name ?? "" })}>
      {provider !== null && <ClearanceForm key={provider.id} provider={provider} onDone={onClose} />}
    </Dialog>
  );
}

export function ClearanceForm({ provider, onDone }: { provider: Provider; onDone: () => void }) {
  const t = useTranslations("adminLlm");
  const common = useTranslations("common");
  const [region, setRegion] = useState("");
  const [retention, setRetention] = useState("");
  const [contract, setContract] = useState("");
  const clear = useClearProvider();
  const toast = useToast();

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      await clear.mutateAsync({ id: provider.id, clearance: { region, retention, contract_ref: contract } });
      toast(t("clearance.done", { name: provider.name }), "ok");
      onDone();
    } catch (error) {
      if (!(error instanceof StepUpRequired)) toast(t(`errors.${llmErrorKey(error)}`), "error");
    }
  }

  if (clear.error instanceof StepUpRequired) {
    return (
      <StepUpNotice
        stepUp={clear.error.stepUp}
        reason={t("clearance.stepUp")}
      />
    );
  }
  return (
    <form onSubmit={submit} className="flex flex-col gap-3 text-sm">
      <p>
        {t.rich("clearance.intro", {
          url: provider.base_url,
          external: provider.kind === "CLOUD" ? t("clearance.external") : t("clearance.internal"),
          code: (chunks) => <code className="break-all">{chunks}</code>,
        })}
      </p>
      <p className="text-text-muted">{t("clearance.members")}</p>
      <label className="flex flex-col gap-1">
        {t("clearance.region")}
        <input className={INPUT} required minLength={2} maxLength={64} value={region} onChange={(e) => setRegion(e.target.value)} placeholder={t("clearance.regionPlaceholder")} />
      </label>
      <label className="flex flex-col gap-1">
        {t("clearance.retention")}
        <input className={INPUT} required minLength={2} maxLength={64} value={retention} onChange={(e) => setRetention(e.target.value)} placeholder={t("clearance.retentionPlaceholder")} />
      </label>
      <label className="flex flex-col gap-1">
        {t("clearance.contract")}
        <input className={INPUT} required minLength={2} maxLength={200} value={contract} onChange={(e) => setContract(e.target.value)} placeholder={t("clearance.contractPlaceholder")} />
      </label>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="ghost" onClick={onDone}>
          {common("cancel")}
        </Button>
        <Button type="submit" busy={clear.isPending}>
          {t("clearance.submit")}
        </Button>
      </div>
    </form>
  );
}

function UnclearDialog({ provider, onClose }: { provider: Provider | null; onClose: () => void }) {
  const t = useTranslations("adminLlm");
  const common = useTranslations("common");
  const unclear = useUnclearProvider();
  const toast = useToast();

  async function confirm() {
    if (!provider) return;
    try {
      await unclear.mutateAsync(provider.id);
      toast(t("clearance.withdrawn", { name: provider.name }), "ok");
      onClose();
    } catch (error) {
      toast(t(`errors.${llmErrorKey(error)}`), "error");
    }
  }

  return (
    <Dialog open={provider !== null} onClose={onClose} title={t("clearance.withdrawTitle", { name: provider?.name ?? "" })}>
      <div className="flex flex-col gap-3 text-sm">
        <p>{t("clearance.withdrawBody")}</p>
        {provider?.in_active && <p className="text-warn">{t("clearance.withdrawActive")}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            {common("cancel")}
          </Button>
          <Button variant="danger" busy={unclear.isPending} onClick={() => void confirm()}>
            {t("clearance.withdraw")}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

function ConnectionResult({ result }: { result: Connection | string }) {
  const t = useTranslations("adminLlm.connection");
  if (typeof result === "string") return <p className="mt-1 text-danger">{result}</p>;
  if (!result.ok) {
    return (
      <p className="mt-1 text-danger" role="status">
        {t("failed", { detail: result.status ? ` (HTTP ${result.status})` : result.error ? ` (${result.error})` : "" })}
      </p>
    );
  }
  return (
    <p className="mt-1 text-ok" role="status">
      {t("ok", { latency: result.latency_ms ?? 0, models: result.models?.length ? result.models.join(", ") : t("noList") })}
    </p>
  );
}

function ProviderDialog({ status, provider, onClose }: { status: LlmStatus; provider: Provider | "new" | null; onClose: () => void }) {
  const t = useTranslations("adminLlm.providerForm");
  const isNew = provider === "new";
  const current = provider !== "new" ? provider : null;
  return (
    <Dialog open={provider !== null} onClose={onClose} title={isNew ? t("newTitle") : t("editTitle", { name: current?.name ?? "" })}>
      {provider !== null && <ProviderForm key={current?.id ?? "new"} status={status} current={current} onDone={onClose} />}
    </Dialog>
  );
}

function ProviderForm({ status, current, onDone }: { status: LlmStatus; current: Provider | null; onDone: () => void }) {
  const t = useTranslations("adminLlm");
  const common = useTranslations("common");
  const [name, setName] = useState(current?.name ?? "");
  const [baseUrl, setBaseUrl] = useState(current?.base_url ?? "");
  const [apiKey, setApiKey] = useState(""); // never pre-filled: keys are write-only
  const [clearKey, setClearKey] = useState(false);
  const [structured, setStructured] = useState(current?.structured_output ?? "");
  const [timeout, setTimeout_] = useState(current?.timeout_seconds ? String(current.timeout_seconds) : "");
  const create = useCreateProvider();
  const update = useUpdateProvider();
  const toast = useToast();
  const kind = previewKind(baseUrl, status);
  const cloudBlocked = kind === "CLOUD" && !status.allow_cloud;
  const error = create.error ?? update.error;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const structured_output = structured === "json_schema" || structured === "prompt" ? structured : null;
    const timeout_seconds = timeout ? Number(timeout) : null;
    try {
      if (current) {
        await update.mutateAsync({
          id: current.id,
          patch: {
            base_url: baseUrl.trim(),
            structured_output,
            timeout_seconds,
            ...(apiKey ? { api_key: apiKey } : {}),
            clear_api_key: clearKey,
          },
        });
      } else {
        await create.mutateAsync({
          name: name.trim(),
          base_url: baseUrl.trim(),
          litellm_provider: "openai",
          api_key: apiKey || null,
          structured_output,
          timeout_seconds,
        });
      }
      toast(t("providerForm.saved"), "ok");
      onDone();
    } catch {
      // shown below
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3 text-sm">
      {!current && (
        <label className="flex flex-col gap-1">
          {t("providerForm.name")}
          <input className={INPUT} value={name} onChange={(e) => setName(e.target.value)} pattern="[a-z0-9][a-z0-9._\-]{0,63}" required />
        </label>
      )}
      <label className="flex flex-col gap-1">
        {t("providerForm.url")}
        <input
          className={INPUT}
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="http://llm:11434/v1"
          required
          aria-describedby="kind-hint"
        />
      </label>
      <p id="kind-hint" role="status" className="text-xs">
        {baseUrl.trim() === "" ? null : kind === "LOCAL" ? (
          <span className="text-ok">{t("providerForm.local")}</span>
        ) : kind === "CLOUD" ? (
          cloudBlocked ? (
            <span className="text-danger">{t("providerForm.cloudBlocked")}</span>
          ) : (
            <span className="text-warn">{t("providerForm.cloud")}</span>
          )
        ) : (
          <span className="text-danger">{t("providerForm.notAllowed")}</span>
        )}
      </p>
      <label className="flex flex-col gap-1">
        <span>
          {t("providerForm.apiKey")}{" "}
          {current?.key_set && <span className="text-text-muted">{t("providerForm.keySaved", { key: keyLabel(t, current) })}</span>}
        </span>
        <input
          className={INPUT}
          type="password"
          autoComplete="off"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder={current ? t("providerForm.keepKey") : t("providerForm.optional")}
        />
      </label>
      {current?.key_set && !current.inherit_env_key && (
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={clearKey} onChange={(e) => setClearKey(e.target.checked)} />
          {t("providerForm.clearKey")}
        </label>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1">
          {t("providerForm.json")}
          <select className={INPUT} value={structured} onChange={(e) => setStructured(e.target.value)}>
            <option value="">{t("providerForm.jsonDefault")}</option>
            <option value="json_schema">{t("providerForm.jsonSchema")}</option>
            <option value="prompt">{t("providerForm.jsonPrompt")}</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          {t("providerForm.timeout")}
          <input className={INPUT} type="number" min={1} max={600} value={timeout} onChange={(e) => setTimeout_(e.target.value)} />
        </label>
      </div>
      {error && (
        <p role="alert" className="text-danger">
          {t(`errors.${llmErrorKey(error)}`)}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onDone}>
          {common("cancel")}
        </Button>
        <Button type="submit" disabled={kind === null || cloudBlocked} busy={create.isPending || update.isPending}>
          {common("save")}
        </Button>
      </div>
    </form>
  );
}

// --- Models -------------------------------------------------------------------------------------

const CHECKS = ["reply", "json_json_schema", "json_prompt", "image", "embed", "transcribe", "ocr", "image_embed"] as const;

function isCheck(name: string): name is (typeof CHECKS)[number] {
  return (CHECKS as readonly string[]).includes(name);
}

interface ProbeCheck {
  name: string;
  ok: boolean;
  latency_ms?: number;
  tokens?: number | null;
  error?: string;
  dimension?: number;
  cosine_unrelated?: number;
  word_error_rate?: number;
}

export function ProbeReport({ report }: { report: Record<string, unknown> }) {
  const t = useTranslations("adminLlm");
  const checks = (Array.isArray(report.checks) ? report.checks : []) as ProbeCheck[];
  const recommended = report.recommended_structured_output as string | null | undefined;
  return (
    <div className="flex flex-col gap-1 text-xs" aria-label={t("probe.report")}>
      <ul className="flex flex-col gap-0.5">
        {checks.map((c) => (
          <li key={c.name} className={c.ok ? "text-ok" : "text-danger"}>
            {c.ok ? "✓" : "✗"} {isCheck(c.name) ? t(`checks.${c.name}`) : c.name}
            {c.latency_ms !== undefined && ` · ${c.latency_ms} ms`}
            {c.tokens ? t("probe.tokens", { count: c.tokens }) : ""}
            {c.dimension !== undefined && t("probe.dimension", { dimension: c.dimension })}
            {c.cosine_unrelated !== undefined && t("probe.unrelated", { value: c.cosine_unrelated })}
            {c.word_error_rate !== undefined && t("probe.wer", { percent: Math.round(c.word_error_rate * 100) })}
            {c.error && ` · ${c.error}`}
          </li>
        ))}
      </ul>
      {recommended !== undefined && (
        <p>
          {t("probe.recommended")}{" "}
          <strong>
            {recommended === "json_schema" ? "response_format" : recommended === "prompt" ? t("providerForm.jsonPrompt") : t("probe.none")}
          </strong>
        </p>
      )}
      {report.dimension_matches === false && (
        <p className="text-danger">{t("probe.dimensionMismatch", { expected: String(report.expected_dimension) })}</p>
      )}
    </div>
  );
}

export function ModelsPanel({ status, providers, models }: { status: LlmStatus; providers: Provider[]; models: LlmModel[] }) {
  const t = useTranslations("adminLlm");
  const format = useFormatter();
  const probe = useProbe();
  const disable = useModelDisabled();
  const toast = useToast();
  const [pricing, setPricing] = useState<LlmModel | null>(null);
  const byProvider = new Map(providers.map((p) => [p.id, p]));

  async function runProbe(model: LlmModel) {
    try {
      const result = await probe.mutateAsync(model.id);
      toast(result.report.ok ? t("models.probeOk") : t("models.probeFailed"), result.report.ok ? "ok" : "error");
    } catch (error) {
      toast(t(`errors.${llmErrorKey(error, "testFailed")}`), "error");
    }
  }

  async function toggle(model: LlmModel) {
    try {
      await disable.mutateAsync({ id: model.id, disabled: !model.disabled });
    } catch (error) {
      toast(t(`errors.${llmErrorKey(error)}`), "error");
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <AddModel status={status} providers={providers} />
      <DataTable>
        <THead>
          <Tr>
            <Th>{t("models.model")}</Th>
            <Th>{t("models.type")}</Th>
            <Th>{t("models.parameters")}</Th>
            <Th>{t("prices.header")}</Th>
            <Th>{t("models.lastTest")}</Th>
            <Th>{t("providers.actions")}</Th>
          </Tr>
        </THead>
        <TBody>
          {models.map((m) => {
            const provider = byProvider.get(m.provider_id);
            const cloudBlocked = provider?.kind === "CLOUD" && !status.allow_cloud;
            return (
              <Tr key={m.id}>
                <Td primary>
                  <div className="font-medium">
                    {provider?.name}/{m.model_name}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {provider && <KindBadge kind={provider.kind} />}
                    {m.in_active && <Badge tone="primary">{t("providers.inActive")}</Badge>}
                    {m.disabled && <Badge>{t("providers.disabled")}</Badge>}
                  </div>
                </Td>
                <Td label={t("models.type")}>{t(`capabilities.${m.capability}`)}</Td>
                <Td label={t("models.parameters")} className="text-xs text-text-muted">
                  {m.capability === "EMBED" || m.capability === "IMAGE_EMBED"
                    ? t("models.dimension", { dimension: m.embed_dimension ?? "?" })
                    : m.capability === "TRANSCRIBE"
                      ? t("models.polish")
                      : t("models.generation", {
                          temperature: m.temperature ?? t("models.default"),
                          tokens: m.max_tokens ?? t("models.default"),
                        })}
                </Td>
                <Td label={t("prices.header")} className="text-xs">
                  {m.price_in_per_million != null && m.price_out_per_million != null
                    ? `${m.price_in_per_million} / ${m.price_out_per_million}`
                    : t("prices.none")}
                </Td>
                <Td label={t("models.lastTest")}>
                  {m.last_probe_at ? (
                    <>
                      <span className={m.last_probe_ok ? "text-ok" : "text-danger"}>
                        {m.last_probe_ok ? t("models.passed") : t("models.failed")}
                      </span>{" "}
                      <span className="text-xs text-text-muted">{format.dateTime(new Date(m.last_probe_at), "dateTime")}</span>
                      {m.last_probe_report && <ProbeReport report={m.last_probe_report} />}
                    </>
                  ) : (
                    <span className="text-text-muted">{t("models.notTested")}</span>
                  )}
                </Td>
                <Td>
                  <div className="flex flex-wrap gap-1">
                    <Button
                      variant="secondary"
                      busy={probe.isPending && probe.variables === m.id}
                      disabled={cloudBlocked || m.disabled}
                      onClick={() => void runProbe(m)}
                    >
                      {t("models.test")}
                    </Button>
                    <Button variant="ghost" onClick={() => setPricing(m)}>
                      {t("prices.edit")}
                    </Button>
                    <Button variant="ghost" disabled={m.in_active && !m.disabled} onClick={() => void toggle(m)}>
                      {m.disabled ? t("providers.enable") : t("providers.disable")}
                    </Button>
                  </div>
                </Td>
              </Tr>
            );
          })}
        </TBody>
      </DataTable>
      <ModelPriceDialog model={pricing} onClose={() => setPricing(null)} />
    </div>
  );
}

/** A price per million tokens: plain decimal, at most six places (the database stores micro-units). */
const PRICE_PATTERN = /^\d+(\.\d{1,6})?$/;

function ModelPriceDialog({ model, onClose }: { model: LlmModel | null; onClose: () => void }) {
  const t = useTranslations("adminLlm");
  return (
    <Dialog open={model !== null} onClose={onClose} title={t("prices.title")}>
      {model && <PriceForm key={model.id} model={model} onDone={onClose} />}
    </Dialog>
  );
}

function PriceForm({ model, onDone }: { model: LlmModel; onDone: () => void }) {
  const t = useTranslations("adminLlm");
  const update = useUpdateModel();
  const toast = useToast();
  const [inPrice, setInPrice] = useState(model.price_in_per_million ?? "");
  const [outPrice, setOutPrice] = useState(model.price_out_per_million ?? "");
  const [invalid, setInvalid] = useState(false);

  async function save(price: { price_in_per_million: string; price_out_per_million: string } | null) {
    try {
      await update.mutateAsync({ id: model.id, patch: { price } });
      toast(t("prices.saved"), "ok");
      onDone();
    } catch (error) {
      toast(t(`errors.${llmErrorKey(error)}`), "error");
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const inValue = inPrice.trim();
    const outValue = outPrice.trim();
    if (!PRICE_PATTERN.test(inValue) || !PRICE_PATTERN.test(outValue)) {
      setInvalid(true);
      return;
    }
    setInvalid(false);
    void save({ price_in_per_million: inValue, price_out_per_million: outValue });
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <p className="text-sm text-text-muted">{t("prices.hint")}</p>
      <label className="flex flex-col gap-1 text-sm">
        {t("prices.inLabel")}
        <input className={INPUT} inputMode="decimal" value={inPrice} onChange={(e) => setInPrice(e.target.value)} />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        {t("prices.outLabel")}
        <input className={INPUT} inputMode="decimal" value={outPrice} onChange={(e) => setOutPrice(e.target.value)} />
      </label>
      {invalid && (
        <p role="alert" className="text-sm text-danger">
          {t("prices.invalid")}
        </p>
      )}
      <div className="flex flex-wrap justify-end gap-2">
        {model.price_in_per_million != null && (
          <Button variant="ghost" busy={update.isPending} onClick={() => void save(null)}>
            {t("prices.clear")}
          </Button>
        )}
        <Button type="submit" busy={update.isPending}>
          {t("prices.save")}
        </Button>
      </div>
    </form>
  );
}

function AddModel({ status, providers }: { status: LlmStatus; providers: Provider[] }) {
  const t = useTranslations("adminLlm");
  const usable = providers.filter((p) => !p.disabled && p.allowed);
  const [providerId, setProviderId] = useState(usable[0]?.id ?? "");
  const [name, setName] = useState("");
  const [capability, setCapability] = useState<Capability>("CHAT");
  const [temperature, setTemperature] = useState("");
  const [maxTokens, setMaxTokens] = useState("");
  const [discovered, setDiscovered] = useState<string[]>([]);
  const create = useCreateModel();
  const test = useTestProvider();
  const toast = useToast();
  const provider = providers.find((p) => p.id === providerId);

  async function discover() {
    try {
      const result = await test.mutateAsync(providerId);
      setDiscovered(result.models ?? []);
      if (!result.ok) toast(t("models.noList"), "error");
    } catch (error) {
      toast(t(`errors.${llmErrorKey(error, "listFailed")}`), "error");
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      await create.mutateAsync({
        provider_id: providerId,
        model_name: name.trim(),
        capability,
        temperature: generates(capability) && temperature ? Number(temperature) : null,
        max_tokens: generates(capability) && maxTokens ? Number(maxTokens) : null,
        embed_dimension: null, // the probe records it; each index version keeps its own

        notes: null,
      });
      toast(t("models.added"), "ok");
      setName("");
    } catch (error) {
      toast(t(`errors.${llmErrorKey(error)}`), "error");
    }
  }

  return (
    <Card>
      <form onSubmit={submit} className="flex flex-col gap-3 text-sm" aria-label={t("models.addTitle")}>
        <h2 className="font-display text-lg font-semibold">{t("models.addTitle")}</h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <label className="flex flex-col gap-1">
            {t("models.provider")}
            <select className={INPUT} value={providerId} onChange={(e) => setProviderId(e.target.value)}>
              {usable.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.kind === "LOCAL" ? t("models.local") : t("models.cloud")})
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            {t("models.nameOnServer")}
            <input className={INPUT} list="discovered-models" value={name} onChange={(e) => setName(e.target.value)} required />
            <datalist id="discovered-models">
              {discovered.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
          </label>
          <label className="flex flex-col gap-1">
            {t("models.type")}
            <select className={INPUT} value={capability} onChange={(e) => setCapability(e.target.value as Capability)}>
              {CAPABILITIES.map((c) => (
                <option key={c} value={c}>
                  {t(`capabilities.${c}`)}
                </option>
              ))}
            </select>
          </label>
          {generates(capability) ? (
            <div className="grid grid-cols-2 gap-2">
              <label className="flex flex-col gap-1">
                {t("models.temperature")}
                <input className={INPUT} type="number" min={0} max={2} step={0.1} value={temperature} onChange={(e) => setTemperature(e.target.value)} />
              </label>
              <label className="flex flex-col gap-1">
                {t("models.maxTokens")}
                <input className={INPUT} type="number" min={1} value={maxTokens} onChange={(e) => setMaxTokens(e.target.value)} />
              </label>
            </div>
          ) : capability === "EMBED" || capability === "IMAGE_EMBED" ? (
            <p className="self-end text-xs text-text-muted">{t("models.embedHint")}</p>
          ) : (
            <p className="self-end text-xs text-text-muted">{t("models.transcribeHint")}</p>
          )}
        </div>
        <div className="flex flex-wrap justify-end gap-2">
          <Button
            variant="secondary"
            disabled={!provider || (provider.kind === "CLOUD" && !status.allow_cloud)}
            busy={test.isPending}
            onClick={() => void discover()}
          >
            {t("models.discover")}
          </Button>
          <Button type="submit" disabled={!providerId || !name.trim()} busy={create.isPending}>
            {t("models.add")}
          </Button>
        </div>
      </form>
    </Card>
  );
}
