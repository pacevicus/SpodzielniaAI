/** Shared E2E support: logins through Keycloak, backend fixtures, page probes. */

import { execFileSync } from "node:child_process";
import { createHmac } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import type { APIResponse, Browser, Page } from "@playwright/test";

export const REPO = resolve(__dirname, "../../..");
export const SITE = process.env.E2E_SITE ?? "https://spoldzielnia.localhost";
export const COMPOSE = ["compose", "-f", "compose.yaml", "-f", "compose.dev.yaml"];

export function secret(name: string): string {
  return readFileSync(resolve(REPO, "secrets", name), "utf8").trim();
}

export const email = (user: string) => `${user}@spoldzielnia.localhost`;

/** `make mfa-off` is in effect (MFA_REQUIRED=false in `.env`). */
export function mfaOff(): boolean {
  try {
    return /^MFA_REQUIRED=false\s*$/m.test(readFileSync(resolve(REPO, ".env"), "utf8"));
  } catch {
    return false;
  }
}

// --- TOTP (RFC 6238, as Keycloak: HMAC-SHA1 keyed with the raw secret, 6 digits, 30 s) ----

const step = () => Math.floor(Date.now() / 30_000);
let lastStep = -1;

function totp(seed: string, at: number): string {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(at));
  const digest = createHmac("sha1", Buffer.from(seed)).update(counter).digest();
  const offset = digest[digest.length - 1] & 15;
  return String((digest.readUInt32BE(offset) & 0x7fffffff) % 1_000_000).padStart(6, "0");
}

/** Keycloak refuses a code it has already accepted: wait for the next window if needed. */
async function freshCode(): Promise<string> {
  while (step() === lastStep) await new Promise((r) => setTimeout(r, 500));
  lastStep = step();
  return totp(secret("dev_totp_seed"), lastStep);
}

export const OTP_USERS = new Set(["expert1", "expert", "admin1", "admin"]);

async function otpRequested(page: Page): Promise<boolean> {
  const otp = page.locator("#otp");
  await Promise.race([otp.waitFor(), page.waitForURL((url) => url.origin === SITE)]);
  return otp.isVisible();
}

/** Log `user` in through the real OIDC flow and land on `path`. */
export async function login(page: Page, user: string, path = "/"): Promise<Page> {
  await page.goto(`/api/auth/login?return_to=${encodeURIComponent(path)}`);
  await page.fill("#username", user);
  const password = ["member", "expert", "admin"].includes(user) ? "super_tajne_haslo_123!" : secret("dev_user_password");
  await page.fill("#password", password);
  await page.click("#kc-login");
  // With `make mfa-off` Keycloak skips the OTP step and goes straight back to the site.
  if (OTP_USERS.has(user) && (await otpRequested(page))) {
    await page.fill("#otp", await freshCode());
    await page.click("#kc-login");
  }
  await page.waitForURL((url) => url.origin === SITE && url.pathname === path.split("?")[0]);
  return page;
}

export async function session(browser: Browser, user: string, path = "/"): Promise<Page> {
  const context = await browser.newContext();
  return login(await context.newPage(), user, path);
}

// --- Backend fixtures (e2e/support/seed.py in the api container) --------------------------

export function seed<T>(args: Record<string, unknown>): T {
  const out = execFileSync("docker", [...COMPOSE, "exec", "-T", "-e", "E2E_ARGS", "api", "python", "-"], {
    cwd: REPO,
    input: readFileSync(resolve(__dirname, "seed.py")),
    env: { ...process.env, E2E_ARGS: JSON.stringify(args) },
    timeout: 180_000,
  });
  const lines = out.toString().trim().split("\n");
  return JSON.parse(lines[lines.length - 1]) as T;
}

// --- In-page helpers --------------------------------------------------------------------

/** An API call with the page's cookies and its CSRF token, like the frontend makes. */
export async function apiCall(page: Page, method: "GET" | "POST" | "PUT", path: string, data?: unknown): Promise<APIResponse> {
  const cookies = await page.context().cookies(SITE);
  const csrf = cookies.find((c) => c.name === "__Host-csrf")?.value ?? "";
  const headers: Record<string, string> = method === "GET" ? {} : { Origin: SITE, "X-CSRF-Token": csrf };
  return page.request.fetch(`${SITE}/api${path}`, { method, data, headers });
}

/** Record CSP violations from the first script on. */
export async function watchCsp(page: Page): Promise<() => Promise<string[]>> {
  await page.addInitScript(() => {
    (window as unknown as { __csp: string[] }).__csp = [];
    document.addEventListener("securitypolicyviolation", (e) =>
      (window as unknown as { __csp: string[] }).__csp.push(`${e.violatedDirective} ${e.blockedURI}`),
    );
  });
  return () => page.evaluate(() => (window as unknown as { __csp: string[] }).__csp);
}

export const EMBEDDINGS_DEGENERATE =
  "the stack's embedding model maps unrelated texts to near-identical vectors (`make smoke` " +
  "shows it): retrieval is random, so whether an answer cites an article proves nothing";

export const embeddingsOk = () => process.env.E2E_EMBEDDINGS === "ok";
