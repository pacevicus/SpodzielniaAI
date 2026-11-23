# Security Review: Spółdzielnia AI (Stage 6 release gate)

This document closes Stage 6 (chunk 6.7). It maps each item of the **OWASP API Security Top 10
(2023)** and the **OWASP Top 10 for LLM Applications (2025)** to the chunk that implements the control
and the tests that prove it. Then it lists the accepted risks that the scanners are told to ignore.

Test locations: `tests/unit/…` and `tests/integration/…` (pytest), `frontend/src/**.test.ts(x)`
(Vitest), `frontend/e2e/N-*.spec.ts` (Playwright against the compose stack).

## How to re-run the gate

| Check | Command | Expected |
|---|---|---|
| Lint, types, unit and integration | `uv run ruff check . && uv run pyright && uv run pytest` | green |
| Frontend | `cd frontend && npm run lint && npm run typecheck && npm test` | green |
| Scanners | `make audit` (pip-audit, bandit, npm audit, gitleaks, trivy) | `audit: clean` |
| Stack | `make build && make up && make smoke` | all checks OK |
| E2E | `make e2e` (MFA on: `make mfa-on`) | 12 spec files, 24 tests green |
| Disaster recovery | `make dr-rehearsal` (6.8) | `dr-rehearsal: … passed every check` |
| Audit chain | `spoldzielnia audit-verify` or `GET /admin/audit-log/verify` | `ok: true` |

## OWASP API Security Top 10 (2023)

| # | Risk | Control (chunk) | Proof |
|---|---|---|---|
| API1 | Broken Object Level Authorization | Postgres RLS on `private_notes` and their children, driven by `app.current_user_id` set per session; `FORCE ROW LEVEL SECURITY` binds the table owner too; another member's note is a 404, the same as a missing one (1.3, 1.4, 2.x) | `integration/test_rls_isolation.py` (whole file, including `test_setting_does_not_leak_to_pooled_connection` and `test_force_rls_binds_table_owner`); `test_upload_to_other_users_note_is_404`, `test_link_on_other_users_note_is_404`, `test_other_users_note_cannot_be_promoted`; `test_members_act_only_on_their_own_blocks`; `unit/test_rls_dependency.py`; E2E `2-isolation` |
| API2 | Broken Authentication | Keycloak OIDC with PKCE through a BFF; opaque `__Host-` session cookie, tokens encrypted at rest in Redis; refresh rotation; absolute lifetime; MFA (`amr` contains `otp`) for EXPERT and ADMIN; step-up `max_age` for revoke; lock drops sessions (1.2); admin user management (Stage 8): Keycloak keeps every credential, temporary passwords are generated server-side, shown once and never logged, audited or stored; a block disables the Keycloak user and ends every app and IdP session; new experts/admins must enrol TOTP | `unit/test_auth_sessions.py` (`test_login_sets_hardened_session_cookies`, `test_redis_holds_no_usable_secrets`, `test_absolute_lifetime_ends_session`, `test_expired_access_rotates_refresh_token`, `test_locked_user_is_423_and_loses_session`); `unit/test_auth_oidc.py`; `integration/test_keycloak_login.py`; `test_login_never_unlocks_a_locked_user`; `test_revoke_needs_confirmation_and_a_recent_login`; `unit/test_keycloak_admin.py::test_passwords_never_reach_the_logs`; `integration/test_user_admin.py` (`test_create_with_a_temporary_password`, `test_block_ends_every_session_and_unblock_needs_step_up`, `test_an_elevated_account_must_enrol_totp`); `integration/test_admin_users_api.py::test_block_ends_open_sessions_at_once`; `unit/test_auth_sessions.py::test_revoke_user_ends_every_family_of_that_user_only`; E2E `1-auth`, `10-user-admin` |
| API3 | Broken Object Property Level Authorization | Pydantic v2 request models with `extra="forbid"`; response models list their fields explicitly (no ORM passthrough); `owner_id`/`author_id` always come from the session, never the body; Qdrant chunks never leave the API (Anti-Distillation, 5.x) | `test_owner_cannot_hand_note_to_another_user`, `test_insert_for_another_owner_is_rejected`; `test_request_validation` (promotion); `test_normal_answer_is_synthesis_with_citations_only`; `test_committed_openapi_schema_is_current` (the client contract can't drift quietly) |
| API4 | Unrestricted Resource Consumption | Streaming upload size caps (413), zip and decompression bomb limits, per-category limits (2.1, 2.2); promotion daily quota (3.4); question length bounds; PoW at HIGH risk, coverage windows 24h/7d (5.x) | `test_oversized_upload_is_413_while_streaming`, `test_zip_bomb_is_422`, `test_decompression_bomb_image_is_refused`, `unit/test_file_validation.py`; `test_daily_limit`; `test_notebook_question_length_is_bounded`; `unit/test_coverage.py`; `test_high_risk_requires_proof_of_work_for_each_request` |
| API5 | Broken Function Level Authorization | Role dependencies (MEMBER / EXPERT+MFA / ADMIN+MFA) on every router; an expert can't decide on their own article; the UI's `RequireRole` is presentation only; user management grants only the three app roles, needs a fresh second factor for EXPERT/ADMIN grants and unblocks, and refuses self-demotion, self-blocking and removing the last admin; the Keycloak service account has `view-users`/`query-users`/`manage-users` only (1.2, 4.x, 5.x, 6.6, 7.4, 8.x) | `test_reviews_need_expert_role_and_mfa`, `test_reviewer_cannot_decide_on_own_article`, `test_quorum_needs_distinct_experts`; `test_admin_routes_need_admin_with_mfa`; `integration/test_admin_console.py::test_authorization_matrix`; `integration/test_admin_llm_api.py::test_authorization_matrix`; `integration/test_admin_users_api.py` (`test_authorization_matrix`, `test_privilege_grants_need_a_fresh_login`, `test_invalid_input_is_rejected`); `integration/test_user_admin.py` (`test_admins_cannot_lock_out_the_organisation`, `test_two_admins_demoting_each_other_at_once`); `integration/test_keycloak_admin.py::test_the_service_account_cannot_touch_the_realm`; E2E `1-auth`, `9-llm-config`, `10-user-admin` |
| API6 | Unrestricted Access to Sensitive Business Flows | The cooperative's core asset is the corpus: the anti-distillation shield (risk engine, HLL coverage, semantic anomaly, honeypots, verbatim verifier, watermarking, PoW, lock) (5.1–5.6); promotion quota; revoke with confirmation and step-up (4.x) | `integration/test_distillation_attack.py` (cut off before 15% of a 200-block corpus; a bot that pays the PoW still ends locked); `integration/test_shield_api.py`; `integration/test_honeypot.py`; `integration/test_verbatim.py`; `unit/test_watermark.py`; E2E `6-lockout` |
| API7 | Server Side Request Forgery | `safe_fetch`: scheme allow-list, DNS resolution with every answer checked against private, loopback, link-local and metadata ranges, the connection pinned to the validated IP, redirects re-validated per hop (2.3). Admin-entered LLM endpoints (7.1): only origins on the `.env` allowlists, no credentials/query in URLs, redirects not followed; cloud traffic only through `llm-egress` (CONNECT to allowlisted hosts on :443, every resolved address public, tunnel pinned to it), which workers cannot reach (7.3) | `unit/test_safe_fetch.py` (`test_any_private_answer_blocks_the_host`, `test_connection_is_pinned_to_validated_ip`, `test_redirect_to_metadata_is_blocked_at_hop_two`); `integration/test_links_api.py::test_ssrf_targets_are_blocked`; `unit/test_llm_endpoints.py`; `unit/test_connect_proxy.py`; `test_keys_are_write_only_and_urls_are_checked`; smoke "llm-egress refuses …", "worker-core cannot reach llm-egress" |
| API8 | Security Misconfiguration | Security headers on API and HTML; nonce CSP with `strict-dynamic` and Trusted Types; CSRF proof (double-submit bound to the session, plus an Origin check); OpenAPI and docs hidden in prod; secrets only from `/run/secrets`, never in `repr` or logs; distroless non-root images (1.1, 6.1, D-x) | `unit/test_security_headers.py`; `test_unsafe_requests_need_csrf_proof`, `test_csrf_tokens_are_bound_to_the_session`; `test_openapi_hidden_in_prod`; `unit/test_settings.py`, `test_rendered_json_never_contains_secret`; `frontend/src/lib/trusted-types.test.ts`, `api.test.ts`; E2E `7-headers`; `make audit` (trivy) |
| API9 | Improper Inventory Management | One API surface behind Caddy, with no versioned leftovers; the OpenAPI schema is committed and drift-tested, and the frontend types are generated from it; internal services (Postgres, Qdrant, Redis, MinIO, ClamAV) stay on internal networks: in `compose.yaml` only the proxy publishes ports, and the dev overlay binds the others to `127.0.0.1` (D-x, 6.1) | `test_committed_openapi_schema_is_current`; `integration/test_stack_smoke.py`; `infra/scripts/smoke.sh` |
| API10 | Unsafe Consumption of APIs | Every upstream answer is untrusted: LLM output is schema-validated (`complete_structured`), citations are checked against what was sent, and output is verified before release; fetched pages, PDFs and transcripts pass injection screening and quarantine; ClamAV failure means 503 and nothing stored (2.x, 3.x, 5.x) | `test_citing_a_source_that_was_not_given_falls_back`; `unit/test_llm_gateway.py` (`test_valid_json_is_parsed_and_schema_is_sent`, schema-echo recovery); `test_scanner_down_is_503_and_nothing_stored`; `integration/test_injection_pipeline.py` |

## OWASP Top 10 for LLM Applications (2025)

| # | Risk | Control (chunk) | Proof |
|---|---|---|---|
| LLM01 | Prompt Injection | Layered screening of every ingested derivative (invisible characters stripped, hidden text removed, a rule plus classifier detector, quarantine with human confirmation) (3.2); untrusted content sits inside a fresh random boundary per prompt; extraction intent raises risk and tightens the prompt (5.x); a note with an injection stops promotion | `integration/test_injection_pipeline.py` (whole file); `unit/test_injection_detection.py`; `test_prompt_messages_use_a_fresh_boundary_each_time`; `test_injection_in_a_note_stops_the_promotion`; `test_extraction_intent_adds_risk_and_tightens_the_prompt`; `frontend` quarantine component tests |
| LLM02 | Sensitive Information Disclosure | PII scrubbing (Presidio, spaCy `pl_core_news_lg`, Polish identifier recognisers) before anything leaves the notebook, with a second pass on LLM output; sensitive purposes are forced onto the local model and never fall back to the cloud (`force_local_llm=True`) (3.1, 3.3); with admin-configurable models (Stage 7) the local-only rule holds in four layers: service, Postgres triggers on routes/snapshots/providers, locality re-derived from the allowlists on load, and the gateway refusing any non-local route for a local-only call; provider API keys envelope-encrypted, write-only, never logged or audited; RLS keeps private notes private; GPS stripped by default (2.2) | `unit/test_scrubber.py`, `unit/test_pl_identifiers.py`; `test_pii_reconstructed_by_the_llm_is_scrubbed_again`; `test_force_local_with_only_a_cloud_route_is_refused`, `test_sensitive_purposes_stay_local_even_without_the_flag`, `test_local_failure_never_falls_back_to_cloud`; `unit/test_llm_config_store.py::test_a_tampered_route_to_the_cloud_is_refused`, `test_resolve_derives_locality_again_and_drops_disallowed_endpoints`; `integration/test_llm_config.py` (`test_local_only_purposes_refuse_cloud_models_in_service_and_database`, `test_a_tampered_snapshot_cannot_route_private_data_to_the_cloud`, `test_api_keys_are_encrypted_write_only_and_bound_to_their_row`); `test_notebook_qa_answers_from_own_notes_only`; `unit/test_sanitize.py`; E2E `3-uploads` |
| LLM03 | Supply Chain | Locked dependencies (`uv.lock`, `package-lock.json`); pip-audit, npm audit, trivy on every image, gitleaks; base images pinned by digest; models load from local directories only (`local_files_only`) (6.7, D-x) | `make audit`; CI job `security`; `injection_model.py` B615 annotation (see Accepted risks) |
| LLM04 | Data and Model Poisoning | Nothing enters the canonical corpus without expert review: a quorum of distinct experts with MFA, no self-review, optimistic version checks (4.1); injection screening upstream (3.2); disputed and degraded articles leave retrieval at once (4.x) | `integration/test_governance.py` (whole file); `test_disputed_leaves_retrieval_and_reapproval_restores_it`; `test_coauthor_revoke_cascade`; E2E `4-happy-path`, `5-exit-strategy` |
| LLM05 | Improper Output Handling | The UI renders model and wiki text through `SafeMarkdown` (react-markdown, no raw HTML, `rehype-sanitize`, URL allow-list), with the Trusted Types default policy (DOMPurify) behind it; the API returns validated structures, never raw model text where structure is expected (6.1, 6.3, 6.5) | `frontend/src/components/markdown/SafeMarkdown.test.tsx`; `frontend/src/lib/trusted-types.test.ts`; E2E `8-xss`, `7-headers` (an inline script injection is blocked and reported) |
| LLM06 | Excessive Agency | Models have no tools, no network and no write access: each call is a fixed purpose with a fixed output schema, routed by the gateway; every side effect (promotion, approval, revoke) is a human action through an authorised endpoint (3.x, 5.x). Changing which model serves a purpose is an audited admin action (draft, probe, activation, rollback) (7.1, 7.4) | `unit/test_llm_gateway.py` (`test_cloud_only_for_allowed_purposes`); `test_only_the_guard_and_its_wiring_import_rag_service` (only `AntiDistillationGuard` reaches retrieval); `test_draft_probe_activate_and_roll_back` |
| LLM07 | System Prompt Leakage | Prompts hold no secrets, keys or authorisation logic; authorisation lives in the API and RLS. Verbatim and n-gram output checks refuse dumps of the context, and the watermark attributes any leaked paraphrase (5.x) | `unit/test_output_verifier.py` (`test_json_dump_of_source_sentences_is_blocked`, `test_long_verbatim_span_inside_a_long_source_is_blocked`); `test_full_quote_is_refused_with_citations_and_audited`; `test_detector_singles_out_the_account_over_100_candidates` |
| LLM08 | Vector and Embedding Weaknesses | Every vector carries `author_id` and `consent_status`; retrieval filters on `ACTIVE` and re-checks Postgres (the source of truth) and repairs drift; the transactional outbox keeps Qdrant in step; private-notebook vectors are scoped to their owner; honeypot vectors never reach synthesis; raw chunks never leave the API (4.2, 4.3, 5.x) | `integration/test_vector_index.py`, `test_vector_outbox.py`; `test_block_suspended_in_postgres_is_dropped_and_repaired`; `test_suspend_then_resume_restores_retrieval`; `test_honeypots_never_reach_synthesis`; `test_notebook_qa_answers_from_own_notes_only`; smoke check "embedding model separates unrelated texts" |
| LLM09 | Misinformation | Answers come only from expert-approved canonical articles, with checked citations; a citation to a source the model was not given falls back to a refusal; with nothing above the score threshold there is no answer and no LLM call; superseded articles drop out at once (4.x, 5.x) | `test_grounded_answer_cites_the_article`, `test_citing_a_source_that_was_not_given_falls_back`, `test_below_threshold_is_no_answer`, `test_nothing_retrieved_means_no_llm_call`; `test_approve_with_supersede_removes_old_vectors_immediately`; E2E `4-happy-path` |
| LLM10 | Unbounded Consumption | Risk-scaled `top_k`, PoW per request at HIGH risk, lock at CRITICAL, coverage limits; bounded question and upload sizes; promotion quota; Celery job retries are bounded and a rejected request isn't retried (3.4, 5.x). The admin playground and probes never retrieve (import-linter contract), take bounded prompts and are rate limited per admin (7.5) | `test_elevated_risk_halves_top_k_silently`, `test_high_risk_requires_proof_of_work_for_each_request`, `test_critical_risk_locks_the_account`; `unit/test_guard.py`; `test_no_local_model_fails_after_retries`, `test_a_rejected_request_fails_without_retrying`; `test_playground_is_rate_limited`, `test_probes_are_rate_limited`; lint-imports "LLM playground and probes never retrieve content"; E2E `6-lockout` |

## Stage 7 additions (admin-configurable LLM models, US-01)

- **Cloud and creator consent.** With `LLM_ALLOW_CLOUD=true` the cooperative chat may send
  canonical wiki text to a cloud API. Enable it only after the cooperative's board confirms that
  creators' consent covers it. The activation dialog lists every purpose that would go to the cloud.
- **Tamper resistance.** A database write that bypasses the service cannot route private-data
  purposes to the cloud: triggers refuse it, the loading process re-derives locality from the
  allowlists, and the gateway refuses non-local routes for local-only calls
  (`llm_route_refused_total` must stay 0).
- **Probe reports** store pass/fail, timings, token counts and exception class names only; the
  playground's answers are returned to the admin and never stored (the audit log keeps a SHA-256
  of the prompt).

## Stage 8 additions (admin user management, US-02)

- **Least privilege for the API.** The `spoldzielnia-admin` service account holds
  `realm-management` `view-users`, `query-users` and `manage-users` only (`make keycloak-setup`
  removes anything else). It can read realm role names, which Keycloak needs for mapping, but
  cannot change the realm, clients or roles (integration test against the real realm). The API
  maps only `MEMBER`/`EXPERT`/`ADMIN`, resolved from the user's available roles, so a request
  can never grant `realm-admin`. The admin REST API is reachable only on the internal network;
  Caddy still refuses `/admin` on `auth.<domain>` from outside `KC_ADMIN_ALLOW`.
- **No lock-out of the organisation.** Self-demotion, self-blocking and demoting or blocking the
  last active admin are refused; admin rows are locked in id order before the target, so two
  admins acting on each other serialize (tested with both transactions open at once).
- **Temporary passwords** are 16 characters from an unambiguous alphabet, returned once with
  `Cache-Control: no-store`, rate limited (10 per admin per hour by default), and never written
  to logs, the audit log, Redis or the database. Prefer the e-mail link where the cooperative
  has SMTP.
- **Separate locks.** An admin block (`blocked_*`) and the shield's lock (`is_locked`) are
  separate columns; unblocking never lifts the shield's lock, and a Keycloak sync never lifts
  an admin block.
- **Drift.** Keycloak is written first. If the mirror write fails afterwards, the next login or a
  sync reconciles roles; a user disabled in the Keycloak console becomes blocked on sync.

## Stage 9 additions (visual atlas, US-03)

Invariants A1–A12 of `docs/implementation/09_IMAGE_ATLAS.md` and their proof:

| # | Control | Proof |
|---|---|---|
| A1 Private photos stay private (API1) | `annotated_image`, `image_annotation`, `atlas_submission` under RLS FORCE on `owner_id`; captions/descriptions are ciphertext; image bytes read through the owner's RLS session | `integration/test_photo_annotations.py::test_photos_and_annotations_are_private` (rows, bytes, no-user session, ciphertext at rest); `test_atlas_recognition.py::test_rules_and_accepting_a_suggestion` |
| A2 Inert image bytes (API8) | `nosniff`, `inline`, `default-src 'none'; sandbox`, `private, no-store`, CORP same-origin; no SVG accepted | `test_preview_and_full_image_are_served_safely`; `test_prepare_preview_and_send` |
| A3 Nothing identifying is shared (LLM02) | shared copies re-encoded from pixels (no EXIF/GPS/ICC/filename); Presidio scrub of caption and descriptions; local VLM privacy flags with explicit confirmation for people | `test_atlas_submissions.py::test_prepare_preview_and_send`, `test_people_need_confirmation_and_vlm_outage_is_visible`; E2E `11-atlas` |
| A4 Block-level provenance | `author_id` + `consent_status` on `atlas_image`, every `atlas_region` and every Qdrant point; tombstone CHECKs on revoke | `test_prepare_preview_and_send`; `unit/test_atlas_vectors.py::test_payload_has_provenance_and_no_text`; `test_atlas_index.py::test_approval_indexes_photo_and_marks_with_provenance` |
| A5 Only verified content is shared (API1/API5) | `atlas.access.can_view`; mandatory Qdrant filter (`consent_status = ACTIVE`, `is_active`) | `unit/test_atlas_rules.py::test_visibility_matrix`; `test_atlas_review.py::test_quorum_and_locking`; `test_atlas_index.py::test_consent_reaches_the_index_at_once` |
| A6 Revoke really removes | objects deleted after commit, texts wiped, regions REVOKED, points deleted through the outbox, exports flag revoked photos | `test_atlas_consent.py::test_revoke_is_final_and_removes_everything`; `test_atlas_export.py::test_revokes_after_export_show_up_and_links_expire` |
| A7 No scraping of the atlas (API4/API6, LLM10) | atlas coverage budget (`covatlas:` HLL, shield windows and floor), thumbnails only in lists, 48 per page, preview/full image rate limit; recognition returns answer, boxes and photo ids only (no vectors, no scores) | `test_atlas_review.py::test_viewing_the_atlas_spends_coverage`, `test_full_images_are_rate_limited`; `test_atlas_recognition.py::test_recognition_is_grounded_local_and_cites_visible_photos` |
| A8 Recognition stays local (AGENTS.md §3.4) | photos decrypted only in worker-core; SigLIP offline; VLM purpose with `force_local_llm=True`; no examples → no model call | `test_recognition_is_grounded_local_and_cites_visible_photos`, `test_no_similar_examples_means_no_guess`; `tests/ml/test_image_embeddings.py` |
| A9 Deliberate exports | `atlas.export_enabled=false` by default; ADMIN + MFA + login ≤ 5 min; 5 per day; per-photo opt-in; salted ids; token stored as SHA-256, single use, 24 h; audited with the manifest hash | `test_atlas_export.py` (all) |
| A10 Governed labels | members only propose; EXPERT/ADMIN + MFA curate; ADMIN + MFA manage domains; merges re-point shared marks and their points | `test_atlas_labels.py::test_authorization_matrix`, `test_merge_deprecate_and_archived_domain`; `test_atlas_index.py::test_label_merge_repoints_points` |
| A11 Hostile images and boxes (API4) | 2.2/2.3 rules on upload; Pillow pixel limit on every rendition; boxes validated in the API and by DB CHECKs; 50 marks per photo | `test_boxes_are_validated`, `test_limits_and_schema_bounds` |
| A12 Author's choice of name | name only with `show_author`; reviewers never see authors; export authors are salted HMACs | `test_atlas_review.py::test_gallery_filters_and_author_choice`, `test_authorization_and_author_privacy_in_review`; `test_atlas_consent.py::test_name_and_export_choices`; `test_coco_export_contents_and_single_use_download` |

- **LLM01 (prompt injection).** Captions and descriptions pass the injection guard before they
  are shared; the VLM's recognition answer is scanned too and replaced if flagged. Text in
  photos is declared data in both prompts.
- **New privileges.** worker-core now holds the object-storage credential (it reads private
  originals to make shared copies and to recognise photos). Same root-credential TODO as
  worker-media.

## Stage 10 additions (every model over a configurable API, US-04)

AGENTS.md rule 4 changed from "local LLM only" to "providers cleared for private data". Invariants
P1–P9 of `docs/implementation/10_MODEL_PROVIDERS_API.md` and their proof:

| # | Control | Proof |
|---|---|---|
| P1 Private purposes use cleared providers only (LLM02) | `PRIVATE_PURPOSES` (PII_SCRUB, PRIVATE_NOTE, PROMOTION, VLM, EMBED, TRANSCRIBE, OCR, IMAGE_EMBED) enforced in four places: env ceiling, config service (409 `provider_not_cleared`), database triggers (0019, 0022–0024), gateway (`llm_route_refused_total`); `private_data=True` forces it for any purpose; pinned deployments (index generations) obey the same policy | `unit/test_llm_config_store.py::test_a_route_to_an_uncleared_deployment_is_refused`; `integration/test_llm_config.py::test_private_purposes_refuse_uncleared_providers_in_service_and_database`, `test_a_tampered_snapshot_cannot_route_private_data_to_the_cloud`, `test_transcription_is_a_private_purpose_with_its_own_models`; `unit/test_transcription.py::test_an_uncleared_provider_never_receives_a_recording`; `unit/test_documents.py::test_an_uncleared_provider_never_sees_a_scan`; `unit/test_image_embedding.py::test_an_uncleared_provider_never_receives_a_photo`; `unit/test_index_generations.py::test_a_pinned_deployment_obeys_the_private_data_policy`; `unit/test_llm_endpoints.py::test_private_purposes_match_the_gateway_and_the_migration` |
| P2 The operator sets the ceiling (API8) | `LLM_PRIVATE_DATA_ENDPOINTS` (default: the local endpoints; `[]` = none) and `LLM_ALLOW_CLOUD`; clearance re-validated against env on every load, so a tampered row clears nothing | `test_clearing_needs_the_operator_list_and_the_cloud_ceiling`; `unit/test_llm_config_store.py::test_resolve_takes_clearance_from_the_provider_row_within_the_env_list`; `test_seeding_clears_the_local_endpoint_only_within_the_operator_list` |
| P3 Clearing is deliberate and recorded (API5) | ADMIN + MFA + login ≤ 5 min; region, retention and contract required (DB CHECK); audited `LLM_PROVIDER_CLEARED/UNCLEARED`; un-clearing takes effect at once (Redis signal) and stops its purposes and index generations | `integration/test_admin_llm_api.py::test_clearing_a_provider_needs_step_up_and_the_operator_list`; `test_unclearing_stops_private_purposes_at_once_without_a_new_revision`; `test_index_generations.py::test_un_clearing_the_provider_stops_its_generation`; `test_a_cleared_provider_cannot_change_its_endpoint_until_uncleared` |
| P4 Members know where their data goes | `GET /privacy/processors` (cleared providers: host, region, retention, contract, since, purposes; built-in engines; no keys or URLs); one-time banner per member after a new cloud provider is cleared (`app_user.processors_seen_at`) | `integration/test_processors_api.py` (both); Vitest `privacy.test.tsx`; E2E `12-model-providers` |
| P5 No silent fallback | `ModelNotConfigured` for every purpose without a usable model; work parks as WAITING_FOR_MODEL (`model_wait`) and resumes after activation; interactive features answer 503 `model_not_configured`; a routed model that fails is retried, never replaced by a built-in engine | `integration/test_capabilities_api.py::test_without_models_features_are_off_and_refused_up_front`; `test_media_pipeline.py::test_an_image_waits_for_a_vision_model_then_resumes`, `test_a_recording_waits_for_a_transcription_model_then_goes_to_its_api`, `test_a_scan_waits_for_an_ocr_model_while_text_documents_complete`; `unit/test_transcription.py::test_a_routed_model_is_used_instead_of_the_builtin_even_when_it_fails`; `unit/test_documents.py::test_a_routed_ocr_model_is_never_replaced_by_the_built_in_one`; `unit/test_llm_gateway.py::test_local_failure_never_falls_back_to_cloud`; E2E `12-model-providers` |
| P6 Endpoints are not an SSRF primitive (API7) | S3/S4 allowlists unchanged; cloud calls of the api and both workers leave only through `llm-egress` (CONNECT to `LLM_CLOUD_HOSTS`, public addresses only); transcription and image-embedding clients follow no redirects; workers have no other route out | `unit/test_llm_endpoints.py::test_malformed_or_unsafe_urls_are_refused`; `unit/test_transcription.py::test_cloud_needs_allow_cloud_and_goes_through_the_proxy`; `unit/test_llm_config_store.py::test_cloud_deployments_go_through_the_egress_proxy`; smoke: workers reach `llm-egress` and have no internet, `llm-egress` refuses other hosts and the backend network |
| P7 Keys stay secret | envelope-encrypted, write-only, never logged or returned (also not on the members' page) | `test_api_keys_are_encrypted_write_only_and_bound_to_their_row`; `test_keys_are_write_only_and_urls_are_checked`; `test_processors_api.py` (no key, no URL path) |
| P8 Re-embedding never mixes vector spaces | `index_generation` (TEXT and IMAGE families) records model and dimension; queries embed with the serving generation's model and search its collections; writes go to every writable generation, consent to every live one; swap when complete | `test_index_generations.py::test_queries_use_the_serving_generations_model_until_the_swap`, `test_a_consent_change_during_the_build_reaches_the_new_generation`, `test_cancel_before_the_swap_keeps_the_old_generation_serving`; `test_atlas_generations.py` (switch built-in → API, consent during the build); E2E `12-model-providers` (rebuild and swap) |
| P9 Audio and documents leave only for cleared providers | TRANSCRIBE and OCR are private purposes (P1); bytes are sent inline from the worker's decrypted tmpfs file (multipart WAV segments, JPEG `data:` URIs), never as links; transcripts and OCR text are derivatives and pass the injection screening | `unit/test_transcription.py::test_a_25_minute_recording_is_three_requests_with_stitched_offsets`; `unit/test_documents.py::test_scanned_pages_go_to_the_ocr_model_and_keep_their_place`; `test_media_pipeline.py` (10.4/10.5 scenarios) |

- **Accepted for this stage:** built-in engines (Whisper, Docling OCR, SigLIP) are reported as
  available by the api from the operator's `LLM_BUILTIN_*` switches, since the api cannot see
  the workers' model volume; a switch on without the model installed shows a feature as on while
  its work waits. Multimodal embedding APIs are not standardised: one request shape is supported
  and an incompatible server fails the model probe before it can be activated.

## Stage 11 additions (removing a model provider, US-05)

| Control | Proof |
|---|---|
| R1 Removal never breaks what runs (API5): refused while the ACTIVE revision, a SERVING/BUILDING index generation or an open draft uses the provider (409 `provider_in_use`/`provider_in_index`/`provider_in_draft`); ADMIN + MFA session + CSRF like every `/admin/llm` write | `integration/test_provider_removal.py::test_a_provider_the_running_configuration_needs_is_not_removed`, `test_admin_removes_a_provider_and_gets_the_refreshed_list` |
| Keys and clearance do not outlive the provider (P7): a deleted provider takes its encrypted key with it; an archived one has key, hint and clearance erased; the audit entry carries no key | `test_an_unused_provider_is_deleted_with_its_models`, `test_a_provider_history_names_is_archived` |
| An archived provider never comes back (P1, P5): hidden from every lookup (404); rollback to a revision that used it is refused (`provider_removed`); config loading and index generations treat it as missing (no fallback); database triggers of 0026 refuse un-archiving, clearing, routing and activating a snapshot that names it | `test_an_archived_provider_never_resolves`, `test_the_database_keeps_an_archived_provider_out_of_use` |

- **Accepted:** no step-up for removal, since it only narrows where data can go (like disabling).
  The history of revisions keeps an archived provider's name and URL (no key), as the audit log
  does.

## Stage 12 additions (Open Notebook–style shell and mobile support, US-06)

| Control | Proof |
|---|---|
| CSP unchanged (`style-src 'self' 'nonce-…'`, Trusted Types): the shell, rail, drawer and every restyled page use classes only; no `style=` attribute, no injected styles. Icons are bundled React SVG (`lucide-react`), not fetched from another origin and never written with `innerHTML` | E2E `14-app-shell` "the menu is on the left…" (`watchCsp` reports no violation); `7-headers` also runs in the `mobile` project |
| The menu is a convenience, not a control: links follow the account's roles, and the API still enforces every role and MFA | Vitest `shell.test.tsx` role tests; E2E `14-app-shell` "menu sections follow the account's roles", `1-auth` (also on `mobile`) |
| The `sb` cookie holds only `1` or `0` (menu collapsed), `SameSite=Lax`, `Secure` over HTTPS, no `HttpOnly` because the page writes it. The server only compares it with `"1"` | `lib/sidebar.ts`; Vitest "collapses to the rail and remembers it in the cookie" |
| Logging out from the menu still clears the query cache before navigating; the phone drawer is only rendered while open | Vitest "logs out and forgets the account's cached data" |
| Answers stay inert after the restyle: the chat XSS cases now look inside the conversation (the page's own icons are SVG) | Vitest `chat.test.tsx` "keeps a hostile answer inert" |

- **New dependencies** (pinned exact, each release at least two weeks old): `lucide-react` 1.55.0,
  `tailwind-merge` 3.7.0 (runtime, no dependencies); `@axe-core/playwright` 4.13.0 (dev).

## Stage 13 additions (organisation branding, US-07)

| Control | Proof |
|---|---|
| R2 No admin input reaches CSS but `#rrggbb`: colours are parsed to `#rrggbb`, the palette is derived on the server and holds only known tokens, migration 0027's CHECKs refuse anything else (including in `palette` via `jsonb_path_exists`), and the frontend drops the whole palette on any odd value. The style block carries the request's nonce: the CSP is unchanged | `test_branding_palette.py::test_anything_but_a_hex_colour_is_refused`, `test_branding.py::test_the_database_refuses_what_could_reach_css`, Vitest `brandingCss (rule R2)`, E2E `15-branding` (`watchCsp` empty) |
| R3 Logos are plain images: PNG/JPEG/WebP by content (SVG, GIF, HEIC, executables refused), ≤ 2 MB and 4096 px (decompression bombs refused before decoding), ClamAV (fail closed), re-encoded from pixels only; the original is never stored | `test_anything_but_a_plain_image_is_refused`, `test_a_logo_is_re_encoded_into_versioned_files`, `test_an_infected_logo_is_refused_and_a_down_scanner_fails_closed` |
| Public endpoints expose nothing sensitive: `GET /branding` and the current logo files only; any other file name or version is 404; the page accepts only same-origin `/api/branding/files/…` URLs | `test_everyone_reads_the_default_branding`, Vitest `sanitizeBranding` |
| Changes are ADMIN + MFA + CSRF, rate limited (30/h), audited without image bytes; the name is NFC with no control, format or bidi characters | `test_only_an_admin_with_mfa_changes_branding`, `test_unreadable_or_malformed_values_are_refused`, `test_writes_are_rate_limited_per_admin` |

- **Accepted:** an administrator can brand the cooperative to resemble another organisation; that
  is within their authority, every change is audited with the author, and the Keycloak sign-in page
  stays unbranded. Pillow parses admin uploads in the API process (bounded as above).

## Stage 14 additions (UI language: Polish and English, US-08)

| Control | Proof |
|---|---|
| R1 The `lang` cookie and the account carry `pl` or `en` only: the API accepts exactly those two codes (`PUT /me/locale` 422 otherwise, never case-folded or trimmed), migration 0028's CHECK refuses anything else, and every reader (API, Next.js layout, the global error page) treats any other value as Polish. The cookie never reaches HTML except as `<html lang>` from a fixed map | `test_i18n.py::test_anything_but_the_two_codes_is_polish`, `test_ui_language.py::test_only_the_two_codes_are_accepted`, `::test_a_forged_cookie_is_ignored_at_sign_in`, `::test_the_database_refuses_another_locale`, E2E `16-language` (forged cookie → `lang="pl"`) |
| The choice is a preference, not an authority: `PUT /me/locale` needs a session and CSRF like every write, is rate limited (30/h, fails open: a display preference), and is not audited. The cookie is readable by the page (a signed-out visitor's switch) and carries nothing sensitive | `test_ui_language.py::test_a_write_needs_csrf_and_a_session`, `::test_changes_are_rate_limited` |
| Translation is not a way around anti-distillation (AGENTS.md rule 3). English answers keep the word-level verbatim checks and add a cross-language check: answer and source sentences are compared by meaning with the configured embedding model, and a source whose words are mostly matched is refused (`reason: translation`, audited, risk added). If that check cannot run, the answer is written again in Polish and checked as usual: an English answer is never returned unchecked. Asking for a full translation ("przetłumacz całość", "translate the entire article") counts as extraction intent (strict prompt, risk) | `test_output_verifier.py::test_extraction_intent`, `test_translation.py`, `test_answer_language.py::test_a_translated_source_is_refused_in_english_and_audited`, `::test_an_uncheckable_english_answer_is_given_in_polish` |
| The per-account watermark covers English answers: its own table of interchangeable English forms (slots prefixed `en:`, Polish slot names unchanged so earlier answers are still attributed), detection observes both tables | `test_watermark.py::test_english_detector_singles_out_the_account`, `::test_polish_slots_keep_their_names` |
| No new data leaves for a provider: English answers use the model already configured for RAG (or for private notes); only the prompt's language rule changes. The private-data rule (AGENTS.md rule 4) is unchanged | `test_rag.py::test_the_answer_language_is_the_askers` |
| Messages are text, never HTML: the catalogues are rendered as React text, rich text (`<strong>`, links) only through `t.rich` with components chosen in code; CSP and Trusted Types are unchanged | E2E `16-language` (`watchCsp` empty, axe clean in English); Vitest `messages.test.ts` (ICU parses, same arguments in both languages) |

- **Accepted:** the English watermark table is smaller than the Polish one (54 pairs against
  180), because many English words are both a noun and a verb and a swap must never change the
  grammar or the meaning. English answers carry fewer slots each, so attributing a leak needs a
  few more of them.
- **Accepted:** the cross-language thresholds (`verbatim_xlang_similarity` 0.86, share 0.5, at
  least 20 words) are a strict starting point, calibrated on test pairs only. A faithful but
  close English summary of a short block can be refused; that errs on the side of the knowledge
  base. Re-check on real English answers.

## Chunk 6.8 additions (production deployment, backup and restore)

| Control | Proof |
|---|---|
| Production cannot start with dev settings (API8): `compose.prod.yaml` requires `REGISTRY`, `VERSION`, `SITE_DOMAIN`, `ACME_EMAIL`; `deploy.sh` refuses `MFA_REQUIRED=false`, a `localhost` domain and a non-prod environment; only the proxy publishes ports; every service has memory/CPU limits | `unit/test_deploy_config.py::test_prod_compose_refuses_to_start_without_its_settings`, `test_prod_compose_runs_registry_images_with_acme_and_limits`; `require_prod_env` in `infra/scripts/lib-ops.sh` |
| No test accounts on a server (API2): the production realm import has only the service account and no password/TOTP placeholders, `sslRequired=all`, self-registration off; it keeps the dev export's flows, OTP and brute-force settings | `unit/test_deploy_config.py` (realm tests, drift check against the dev export) |
| First admin without a shared password: a one-time password printed once, UPDATE_PASSWORD and CONFIGURE_TOTP required at first login; the password reaches Keycloak over stdin, not argv | `infra/scripts/create-admin.sh` (run against the dev realm, 2026-10-23) |
| Supply chain of releases (LLM03): tag builds are trivy-scanned before the push; images carry an SBOM and `mode=max` provenance; tags `vX.Y.Z` and `sha-<commit>` | `.github/workflows/publish.yml`; `docker-bake.hcl` `release` |
| Backups are confidential and verifiable: `age` public-key encryption on the server (the identities never live there); the KEK travels in a separate bundle with its own recipients; plaintext exists only in a 0700 staging directory removed on exit; a manifest with SHA-256 per file, row counts and the last audit entry | `make dr-rehearsal` (checksums, `STRICT=1` row counts, objects, audit chain through the backup's head, smoke) |
| A restore cannot silently overwrite: it stops on a non-empty database (`FORCE=1`) and on a secret that differs from the bundle (`FORCE_SECRETS=1`, old copies kept) | `infra/scripts/restore.sh` |
| No database or storage password on the host's command lines: `pg_dump`/`pg_restore` run inside the postgres container over peer authentication; `mc` builds its credentials inside the `backup` container | `infra/scripts/backup.sh`, `compose.yaml` `backup` |

- **Accepted:** Redis is not backed up. After a restore:
  - members log in again;
  - Celery messages queued at backup time are lost. Work parked as WAITING_FOR_MODEL is in
    Postgres and resumes; a file that was mid-processing may need uploading again;
  - the shield's 24 h/7 d/30 d coverage windows restart empty, once.

  Locks and risk scores are in Postgres and survive.
- **Accepted:** Postgres is dumped before the objects are mirrored, without stopping the API. An
  object deleted in between (a revoke purge) is missing from that backup, and its row points at
  nothing. Run backups at a quiet hour; the next night's backup is consistent again.

## Stage 15 additions (AI usage monitoring, US-09)

| Control | Proof |
|---|---|
| R1 Counters only: a usage event has no field for content, so no prompt, answer, audio or image can reach the counters. The table stores day, owner, purpose, model code, call and token counts, and micro-unit cost | `llm/usage.py` `UsageEvent` (type), `test_llm_usage.py::test_a_call_is_counted_for_its_owner_with_the_reported_tokens` |
| A member sees only their own figures: `GET /me/usage` filters by the authenticated id in the query | `test_ai_usage.py::test_a_member_sees_only_their_own_usage` (integration, not run here) |
| The administrator view needs ADMIN and MFA (`elevated_router`), and a member or an expert is refused | `test_ai_usage.py::test_members_and_experts_cannot_read_the_administrator_view` (integration, not run here) |
| Prices: both or neither, non-negative, at most 1 000 000 per million tokens, at most six decimals. Enforced by database CHECKs (migration 0029, model) and by `PriceIn` | migration `0029_ai_usage` CHECKs; `test_llm_usage.py::test_price_rejects_invalid_values`; `test_ai_usage.py::test_prices_are_set_and_cleared_through_the_model_api` (integration) |
| Price changes are audited through the existing model-change event (the event records that a price changed, not its value) | `llm/config_service.py` `update_model` (`changes["priced"]`) |
| Failure to write usage never fails the answer or the job, and the log carries the error type only | `test_llm_usage.py::test_a_failed_usage_write_never_fails_the_call`; `llm/usage.py` `UsageRecorder.record` |
| A call that fails is not counted, so the counters are never inflated by errors | `test_llm_usage.py::test_a_failed_call_is_not_counted` |
| Owners cannot leak between requests: the request owner is per asyncio task; a worker job's owner is set and restored around the job | `test_llm_usage.py::test_a_request_owner_does_not_leak_into_another_request`, `::test_a_job_owner_is_restored_after_the_job` |
| Read-only endpoints: no CSRF needed, no audit entry; the period is bounded (at most 366 days) and validated with machine codes | `test_llm_usage.py::test_period_*`; `test_ai_usage.py::test_a_bad_period_is_refused_with_a_code` (integration) |
| Deleting a user deletes their counters (`ON DELETE CASCADE`), in line with the erasure path | `test_ai_usage.py::test_the_counters_are_deleted_with_the_user` (integration) |

- **Accepted:** usage is written after the answer, so a database outage during a call undercounts
  it (R6 in the plan). A fee stage needs a reconciliation step before money is charged.
- **Accepted:** the cost is an estimate from the configured price, not the provider's invoice.
  The UI says "szacunkowy" / "estimate".
- **Accepted:** the counters are not RLS-protected (see *Changes against the plan* in the stage
  file). Their isolation is the API filter and the ADMIN + MFA gate.

## Accepted risks

Every entry is the reason a scanner is told to stay quiet. Re-check each entry when its trigger changes.

| Finding | Where | Why accepted | Re-check when |
|---|---|---|---|
| CVE-2016-3616, CVE-2020-13790 (libjpeg) | `.trivyignore`; worker images | pikepdf's manylinux wheel vendors RHEL 8's libjpeg-turbo `1.5.3-14.el8_10`. Trivy compares that RHEL build with Debian's fixed versions, which is a cross-ecosystem mismatch. Both CVEs are in cjpeg's input readers (`rdppm.c`, cjpeg), which neither libjpeg nor pikepdf uses, and the RHEL build postdates its fix for CVE-2020-13790 | pikepdf ships a new wheel |
| `braces` stack exhaustion (via `micromatch` → `fast-glob` → `@next/eslint-plugin-next` → `eslint-config-next`), 5 high findings | `frontend` **dev** dependencies | Lint-time only: absent from the runtime image (`npm audit --omit=dev` reports 0). The only fix is a breaking downgrade of `eslint-config-next`. `make audit` reports it for information and fails only on runtime findings | `eslint-config-next` updates |
| Bandit B104 (bind to `0.0.0.0`) | `core/settings.py` `api_host` | Binds inside the container; only Caddy on the internal network reaches it | the API runs outside a container |
| Bandit B108 (`/tmp` path) | `core/settings.py` `schedule_file` | Celery beat's schedule file sits on the container's tmpfs, owned by the non-root user | the beat service changes |
| Bandit B608 (SQL string building) | `audit/verify.py` | The interpolated piece is a module constant (`CANONICAL_ROW_SQL`); no input reaches it | that query is edited |
| Bandit B608 (SQL string building) | migrations `0012_llm_config.py`, `0019_provider_clearance.py` (trigger functions) | The DDL interpolates module constants (the private purposes' enum arrays); migrations take no input | a migration builds SQL from anything but constants |
| Bandit B615 (Hugging Face download without a revision) | `llm/injection_model.py` | Loads from a local directory with `local_files_only=True`, so it never downloads | model loading changes |
| gitleaks `generic-api-key` | `.gitleaksignore`; `frontend/src/components/admin/users/users.test.tsx:133` (commit `4149fe7`) | A made-up temporary password in a Vitest fixture that the mocked API returns; it never existed in any realm | the fixture changes |
| `pl-core-news-lg` not audited | `pip-audit` | A spaCy model wheel from GitHub releases, not on PyPI, so there is no advisory feed. It is data (weights), pinned by URL and hash in `uv.lock` | spaCy model upgrade |
| Coverage-ratio cut-off isn't shown end to end in E2E | E2E `6-lockout` | The dev corpus is tiny, so the absolute floor (gap G14) dominates the ratio. The ratio cut-off before 15% is proven on a 200-block corpus in `test_distillation_attack.py`; E2E proves the step-up → lock path and the admin view | — |
| Client-side deterrents (lazy render, no bulk export, wiki watermark) | 6.3 | Deterrence only; the server-side shield is the control | — |
| Exported datasets cannot be recalled | 9.7 | An archive already downloaded is outside the system. Export is off by default, needs a fresh MFA login and a per-photo opt-in, and every manifest is audited; later revokes are flagged on the export | the export policy changes |
| Recognition threshold not calibrated on real photos | 9.9 `atlas.recognition_min_score` | Set to 0.80 provisionally; synthetic E2E photos look alike to the model. Calibrate with `spoldzielnia atlas benchmark` once the atlas holds real photos | the atlas has ~50 approved photos per domain |

## Dependency exceptions

Departures from the dependency rules in `AGENTS.md` §7 (exact pins, releases at least two
weeks old). Each one is a security fix. Remove the entry once its trigger is met.

| Package | Pinned | Rule broken | Why | Proof | Remove when |
|---|---|---|---|---|---|
| `filelock` | 4.2.1 via `[tool.uv] override-dependencies` | Release 12 days old (2026-10-17); overrides litellm's `filelock<4.0` | 3.32.7 had 13 advisories, among them CVE-2026-112940 (HIGH, arbitrary file overwrite), and there is no fixed 3.x release. litellm uses filelock only in its proxy CLI, which this project does not run. Hugging Face Hub and PyTorch use only `FileLock`, `SoftFileLock`, `BaseFileLock` and `Timeout`, which 4.2.1 keeps | full `uv run pytest` (1452 passed, including `ml`), `make smoke`, `make dr-rehearsal` and a clean `make audit` on 2026-10-29 | litellm allows `filelock>=4`: drop the override |
| `setuptools` | 84.1.0 via `[tool.uv] constraint-dependencies` | Release 10 days old (2026-10-19) | GHSA-xmj3-9gf7-h52w in 84.0.0 (pulled in by spaCy) | same | 2026-11-02: drop the constraint and lock normally |

`pip-audit` audits the exported pins without resolving them (`--no-deps --disable-pip`).
pip's resolver knows neither the override nor the PyTorch CPU index (`torch==…+cpu`).

## Environment-dependent checks

- **Embedding quality.** The smoke check "embedding model separates unrelated texts" and E2E `4-happy-path`
  depend on the configured embedding model. A degenerate model (unrelated texts with cosine ≥ 0.8)
  fails the smoke check, and global setup skips scenario 4 with a clear reason instead of letting it
  fail at random. LM Studio's GGUF Qwen3-Embedding-4B is degenerate (0.944); EmbeddingGemma
  separates (0.56), and the dev stack uses it since 2026-10-23. `rag.score_threshold` is calibrated per model. Switching the embedding model re-embeds into a new index generation (10.3); recalibrate after the swap.
- **Promotion quota.** The promotion daily quota (20 per member) is real, so repeated test runs against the
  same dev stack can hit it until UTC midnight.
