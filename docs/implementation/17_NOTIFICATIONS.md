# Stage 17 — Notifications: in the app and by email (US-11)

> **Story ([US-11](../USER_STORIES_LOG.md#us-11)):** as an expert or author I want to be told when
> something needs me or when a decision was made on my article, so that I do not have to keep
> checking the application.

## Refined requirement

Refinement decisions (accepted 2026-11-05):

| Topic | Decision |
|---|---|
| Channels | **In the app** (a bell in the menu and a `/notifications` page) **and by email**. No web push. |
| Email default | On, and each person can turn it off per event type. In-app notifications cannot be turned off. |
| Email content | **Event type, count and a link only.** No article titles or text. Emails are batched and plain text. |
| Without SMTP | Email is off; the app works normally and an admin sees that email is not configured. |
| Events | Review requested; decision on my article; revision required; overdue reviews (weekly summary). Stage 18 adds „source changed”. |
| Out of scope | Following individual articles, web push, chat webhooks, notifications about chat or private notes, emails sent by admins. |

## Acceptance criteria

```gherkin
Scenario: an expert is told about a new draft
  When member1 submits an article for review
  Then within a minute expert1 sees the bell count go up and an entry linking to the review
  And member1, as its author, does not receive this notification
  And expert1 receives an email with the event type and a link, but no title or text

Scenario: an author is told about a decision
  When expert1 approves, rejects or requests changes on member1's article
  Then member1 is told which decision was made
  And expert1, who made it, is not notified

Scenario: withdrawn consent does not reveal who withdrew
  When a co-author withdraws consent and the article requires revision
  Then experts and the remaining co-authors are told revision is required
  And the notification does not say who withdrew or which blocks

Scenario: overdue reviews are summarised
  Given 7 articles are overdue
  Then each expert receives at most one summary a week with the count
  And no summary is sent when nothing is overdue

Scenario: access is checked when reading
  Given member1 has a notification about an article they can no longer open
  Then the entry shows a neutral text with no title and no link

Scenario: email preferences and failures
  When member1 turns off email for decisions
  Then they still see decisions in the app, but get no email
  When the mail server is unavailable
  Then delivery is retried, no notification is lost, and no user action fails

Scenario: another person's notifications
  When member2 requests member1's notification by id
  Then the API answers 404
```

## Design

### Data (migration `0031_notifications`)

| Table | Holds | Notes |
|---|---|---|
| `notification_event` | `id, code, article_id, actor_id, params JSONB, created_at, fanned_out_at` | Written in the transaction of the change itself. Codes and ids only. |
| `notification` | `id, user_id, code, article_id, params, dedupe_key, created_at, read_at, emailed_at` | RLS: a user selects and updates only their own rows. Unique `(user_id, dedupe_key)`. |
| `notification_preference` | `user_id, code, email_enabled` | No row means enabled. |

### Rules

- **R1. PostgreSQL, not Redis.** ICS keeps its inbox in Redis and accepts losing it. Here, a lost
  „review requested” means a stalled article, and events must commit with the change that caused
  them, so both live in the same database.
- **R2. Event, then fan-out.** `change_status` and the consent service record one event. The acting
  user cannot and should not write other people's rows. A beat task on `worker-core` (every 30 s, like
  the vector outbox) works out recipients and inserts `notification` rows, with `dedupe_key` making a
  redelivered task harmless. The RLS insert policy permits this; read and update stay owner-only.
- **R3. Recipients.** Requested: users with `Role.EXPERT`, minus the article's authors. Decision:
  authors, minus the actor. Revision required: experts plus remaining authors, minus the actor; `params`
  never includes the actor or block ids. Hidden (honeypot) articles never produce an event, checked
  both when recording and when fanning out.
- **R4. Stored codes, read-time text.** Rows hold no title or sentence. `GET /notifications` joins the
  article under the current reader's rights, using the same visibility rule as `GET /wiki/articles/{id}`,
  and adds the title only if that passes. Otherwise it returns `article: null`. The frontend translates
  codes through `next-intl`.
- **R5. Polling.** The bell uses TanStack Query at 60 s (`GET /notifications/unread-count`, paused in a
  hidden tab). Server-sent events would need changes to the proxy and to the API's worker model for a
  benefit nobody needs here.
- **R6. Email is a batch of codes.** A beat task every 15 minutes collects unread, unemailed rows per
  user with email enabled and an address on file, and skips blocked accounts. It queues one job per
  user. The message is plain text from `i18n.py` in the user's language: counts per event type plus one
  link to `/notifications`. This is what keeps titles, which could still contain personal data, away
  from the mail provider, in line with iron rule 4.
- **R7. A separate sender.** New service `worker-notify` (api image, queue `notify.email`) is a dedicated
  service on `backend` and `egress`, like `worker-egress`, with a `smtp_password` secret. It holds no KEK and no LLM,
  Qdrant or MinIO credentials. `worker-core` stays off the internet. `SMTP_HOST`, `SMTP_PORT` and
  `SMTP_FROM` are operator settings shared with Keycloak's; TLS is required outside development.
  Development uses `mailpit`, reached through an added network in `compose.dev.yaml`.
- **R8. Overdue summary.** A daily beat task counts overdue articles per expert (excluding their own)
  and creates one row with `dedupe_key = overdue:<ISO week>`. Needs Stage 15.
- **R9. Retention.** A nightly task deletes notifications older than 90 days and fanned-out events
  older than 7 days. Users can mark one or all as read.
- **R10. Limits and audit.** Preference writes are limited and CSRF-protected (fail open: not
  sensitive). Sending is not audited per message; a structured log records counts, never addresses.

### API

`GET /notifications` (paged), `GET /notifications/unread-count`, `POST /notifications/{id}/read`,
`POST /notifications/read-all`, `GET/PUT /me/notification-preferences`.

## Chunks

- **17.1 Data and events.** Migration with RLS, models, `notifications/events.py`, hooks in governance
  and consent. *Tests:* RLS isolation, transaction rollback drops the event, no event for honeypots,
  no actor in revision events, table registration.
- **17.2 Fan-out.** Beat task, recipients, deduplication, retention, overdue summary. *Tests:*
  integration per event and recipient rule, redelivery, a former author, expert exclusion.
- **17.3 API.** Endpoints, read-time access check, preferences, `gen:api`. *Tests:* integration (404 for
  others, title only with access, neutral entry after withdrawal, CSRF).
- **17.4 Email.** `worker-notify`, compose changes, batching, templates in `i18n.py`, retries, admin
  status of email configuration. Update `DEPLOYMENT_DOCKER.md`, the DR rehearsal if affected, and
  `.env.example`. *Tests:* against Mailpit: no title or article text in any message, both languages,
  batching, preference respected, SMTP failure retried. A network test proves `worker-core` still has
  no route out.
- **17.5 Frontend.** Bell in the sidebar, rail, drawer and phone bar; `/notifications`; preferences in
  the account area; deep links into `/review?article=<id>`. *Tests:* Vitest in both languages, an
  accessible count, 360 px.
- **17.6 E2E and closure.** E2E scenario 19 (bell and Mailpit), `SECURITY_REVIEW.md`, plan sections.

## Risks and notes

- **Roles can be out of date.** `app_user.roles` mirrors the identity provider and refreshes at login.
  A newly appointed expert who has not signed in gets no notifications, and a former expert may receive
  a neutral email until their next login. Reads still use their current session, so no title leaks.
  Accepted for this stage.
- **Deliverability is on the operator.** SPF, DKIM and a reputable relay are needed, or emails land in
  spam. `DEPLOYMENT_DOCKER.md` will say so.
- **A new outbound route.** `worker-notify` is the first application worker sending to an
  operator-chosen host, so it must have only the secrets it needs. Address-based enumeration is not
  possible, because recipients come only from `app_user`.
- **Email addresses go to the mail provider.** This is necessary for email. It should be added to the
  `/privacy` page and any data processing agreement.
- **The email body is intentionally less useful.** People must open the app to see what changed. That
  is the price of keeping article titles out of mail systems.
