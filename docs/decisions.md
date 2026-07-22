# Decision log

Design rulings made while building this kit, and the roadmap items deferred out
of them. One line of rationale each; the code is the full story.

## Decisions

| Decision | Date | Rationale |
|---|---|---|
| Config over code: engine prompt sections (SPEAKING RULES, STYLE, GOAL, FLOW, QUALIFY, IDENTITY body, medical block) are engine-owned and **not** client-configurable | 2026-07-18 | The differences between clients are narrow (facts, voice, greeting, guardrails); making the shared craft configurable would recreate the bespoke-per-client trap the kit exists to avoid. |
| Deterministic pipeline; the LLM owns dialogue only | 2026-07-18 | Deploy, diff, gating, SMS, and evals are plain Python so they're testable and idempotent; the model is confined to the live conversation where its strengths are. |
| Lockfile idempotency + dry-run as the default | 2026-07-18 | `config/clients/<slug>.lock.json` maps language→{agent_id,llm_id}; plans diff desired vs live and re-runs converge to NOOP, so applying twice is safe and the default command mutates nothing. |
| Voice IDs are config, not secrets | 2026-07-18 | A Retell `voice_id` (stock or account custom) is not sensitive; it belongs in the client config next to the greeting, not in `.env`. |
| post_call caller/derived split + explicit role markers | 2026-07-18 | `source: caller\|derived` decides what CAPTURING DETAILS asks for vs. what's summarized after; `role: phone\|consent` resolves the SMS fields deterministically instead of by name heuristics (heuristics remain as a documented fallback). |
| sms_consent requires the whole pipeline | 2026-07-18 | A booking SMS only sends when sms_consent + booking.url + captured consent + a normalizable phone all hold, the service is running, and Twilio is configured; the deploy warns when consent is on but no SMS backend exists. |
| DTMF: prompt follows config | 2026-07-18 | Production keeps `allow_user_dtmf = True`; rather than fight it, the SPEAKING RULES text tells the agent keyed digits may arrive as input and to read them back — config and prompt agree. |
| Sanitized-public / private-config split | 2026-07-18 | The tracked `client.example.yaml` is obviously fictional; real client configs, lockfiles, capture dumps, and the SMS ledger are all gitignored so nothing private is ever committed. |
| Static evals gate CI; transcript evals run out-of-band | 2026-07-19 | Layer 1 needs no network and runs on every push; Layer 2 needs real transcripts, so it stays a local/manual tool until sanitized fixtures exist. |
| Retell list endpoints: verified none deprecated; hardened to the paginated contract | 2026-07-22 | The June 2026 legacy-list deprecation notice covers `/v2/list-calls` (→ `/v3/list-calls`) and several unversioned GET list endpoints, but **not** `list-agents`. We already use `/v2/list-agents` (current) and `/v3/list-calls` (the replacement), so no endpoint change was needed. Both list callers now page the documented `{items, pagination_key, has_more}` envelope instead of reading a single page. |

## Roadmap (deferred, not built)

| Item | Date recorded | Rationale for deferral |
|---|---|---|
| LLM-judge eval layer (Layer 3) | 2026-07-20 | Deterministic checks land first; a judged layer for tone/faithfulness is additive and needs a rubric + cost budget. |
| Voice pre-flight inside `plan` | 2026-07-20 | We caught an invented voice id by hand; the planner should validate every `voice_id` against `list-voices` before apply. |
| Incremental per-language lockfile writes | 2026-07-20 | `apply` writes the lockfile once at the end; a mid-apply failure can orphan resources. Write per language so partial applies self-heal. |
| Field ownership / explicit unset | 2026-07-20 | The differ only sees emitted fields; omitted ones (e.g. `webhook_url` when unset) can't be detected or cleared through a plan. |
| Delete / deprovision support | 2026-07-20 | There is no `--delete`; tearing a client down is a manual API call. A first-class deprovision (with lockfile cleanup) is needed. |
| Hours "closed day" model | 2026-07-20 | `Hours` is `{days, open, close}`; a closed day renders as "Closed to Closed". A first-class closed concept would read better. |
| Per-client SMS numbers | 2026-07-20 | One global `TWILIO_FROM_NUMBER` today; multi-client at scale needs a per-client sender and A2P/toll-free registration per number. |
| Named tunnel / VPS deployment for the webhook service | 2026-07-20 | The quick tunnel is ephemeral; production post-call handling needs a stable, always-on host. |
