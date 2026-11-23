import { defineConfig, devices } from "@playwright/test";

/**
 * E2E against the running dev stack (`make up`, then `make e2e`): Caddy, Keycloak with the dev
 * realm users, the API, workers and the local LLM. Serial: scenarios share one stack, and
 * some (lockout, revoke) change account state.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 240_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  globalSetup: "./e2e/support/global-setup.ts",
  use: {
    baseURL: process.env.E2E_SITE ?? "https://spoldzielnia.localhost",
    ignoreHTTPSErrors: true, // Caddy's local CA in dev
    trace: "retain-on-failure",
    locale: "pl-PL",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    // US-06: the shell on a tablet and a phone (scenario 14), plus scenarios that must hold there too.
    { name: "tablet", testMatch: /14-app-shell/, use: { ...devices["Desktop Chrome"], viewport: { width: 768, height: 1024 }, hasTouch: true } },
    { name: "mobile", testMatch: /(1-auth|7-headers|14-app-shell|16-language|17-ai-usage)\.spec/, use: { ...devices["Pixel 7"] } },
  ],
});
