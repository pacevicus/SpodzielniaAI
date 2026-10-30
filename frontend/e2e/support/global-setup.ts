/**
 * Before the suite: the stack must answer, and the dev realm must have `expert2` (an EXPERT
 * without OTP, for the MFA enforcement scenario). Also measures whether the configured
 * embedding model tells unrelated texts apart (E2E_EMBEDDINGS=ok|degenerate): scenarios that
 * depend on retrieval quality skip, with that reason, when it doesn't. The realm file defines it, but Keycloak
 * imports a realm only once, so an existing stack gets it through the admin API (allowed
 * from localhost only, KC_ADMIN_ALLOW). Resets the members' shield state (see below).
 */

import { execFileSync } from "node:child_process";

import { request } from "@playwright/test";

import { COMPOSE, email, REPO, secret, seed, SITE } from "./stack";

const EMBED_CHECK = `
import asyncio, math
from spoldzielnia_ai.core.settings import Settings
from spoldzielnia_ai.llm.gateway import LiteLLMGateway
a, b = asyncio.run(LiteLLMGateway(Settings().llm).embed(
    ["Jak leczyć warrozę kwasem szczawiowym zimą?", "SELECT count(*) FROM invoices WHERE paid"]))
print(sum(x * y for x, y in zip(a, b)) / math.sqrt(sum(x * x for x in a) * sum(y * y for y in b)))
`;

function embeddingsSeparate(): boolean {
  try {
    const out = execFileSync("docker", [...COMPOSE, "exec", "-T", "api", "python", "-c", EMBED_CHECK], {
      cwd: REPO,
      timeout: 120_000,
    });
    const cosine = Number(out.toString().trim().split("\n").pop());
    return Number.isFinite(cosine) && cosine < 0.8;
  } catch {
    return false;
  }
}

const AUTH = SITE.replace("://", "://auth.");

export default async function globalSetup(): Promise<void> {
  const http = await request.newContext({ ignoreHTTPSErrors: true });
  const health = await http.get(`${SITE}/api/healthz`).catch(() => null);
  if (!health?.ok()) throw new Error(`${SITE} is not up: run \`make up\` first`);
  // Inherited by the test workers.
  process.env.E2E_EMBEDDINGS = embeddingsSeparate() ? "ok" : "degenerate";
  // Chat questions from earlier runs raise the members' shield risk until /rag/ask demands
  // proof of work (401): every run starts from a clean slate, like the lockout scenario.
  for (const user of ["member1", "member2"]) seed({ action: "reset_shield", email: email(user) });

  const token = await http.post(`${AUTH}/realms/master/protocol/openid-connect/token`, {
    form: { grant_type: "password", client_id: "admin-cli", username: "admin", password: secret("keycloak_admin_password") },
  });
  if (!token.ok()) throw new Error(`Keycloak admin login failed: ${token.status()}`);
  const headers = { Authorization: `Bearer ${(await token.json()).access_token}` };
  const users = `${AUTH}/admin/realms/spoldzielnia/users`;
  for (const username of ["expert2", "member", "expert", "admin"]) {
    const existing = await (await http.get(`${users}?username=${username}&exact=true`, { headers })).json();
    if (existing.length === 0) {
      const roleMap: Record<string, string[]> = {
        member: ["MEMBER"],
        expert: ["MEMBER", "EXPERT"],
        admin: ["MEMBER", "ADMIN"],
        expert2: ["MEMBER", "EXPERT"],
      };
      const rolesToAssign = roleMap[username] || ["MEMBER"];
      const password = ["member", "expert", "admin"].includes(username) ? "super_tajne_haslo_123!" : secret("dev_user_password");
      const created = await http.post(users, {
        headers,
        data: {
          username, email: `${username}@spoldzielnia.localhost`, emailVerified: true,
          firstName: username.charAt(0).toUpperCase() + username.slice(1),
          lastName: username === "expert2" ? "Two" : "Default", enabled: true,
          credentials: [{ type: "password", value: password, temporary: false }],
        },
      });
      if (!created.ok()) throw new Error(`creating ${username} failed: ${created.status()}`);
      const [user] = await (await http.get(`${users}?username=${username}&exact=true`, { headers })).json();
      const roles = await (await http.get(`${AUTH}/admin/realms/spoldzielnia/roles`, { headers })).json();
      const wanted = roles.filter((r: { name: string }) => rolesToAssign.includes(r.name));
      await http.post(`${users}/${user.id}/role-mappings/realm`, { headers, data: wanted });
    }
  }
  await http.dispose();
}
