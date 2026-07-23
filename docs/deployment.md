# Deploying a new client

This is the complete operator guide for standing up a bilingual AI voice
receptionist for one client. Every command is copy-paste runnable. It is written
from the scripts as they exist in this repo.

> Conventions: commands are shown for a POSIX shell. On Windows PowerShell,
> replace `cp` with `copy`, `source .venv/bin/activate` with
> `.venv\Scripts\Activate.ps1`, and `python` with `.\.venv\Scripts\python.exe`
> if you prefer not to activate the venv.

## 1. Prerequisites

- **Python 3.11+** (`requires-python = ">=3.11"`).
- **git**.
- A **Retell account** and an **API key** (`RETELL_API_KEY`). Create the key in
  the Retell dashboard.
- The stock voices used by your config must exist on your Retell account. The
  shipped example uses `retell-Tamsin` (en-US) and
  `cartesia-Hailey-Spanish-latin-america` (es-419), both stock voices.

You do **not** need a database — the kit is stateless apart from a small
per-client lockfile (see below).

## 2. Clone and install

```bash
git clone https://github.com/danielfmonzon/dma-deploy-kit.git
cd dma-deploy-kit
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## 3. Configure your environment

```bash
cp .env.example .env               # Windows: copy .env.example .env
```

Edit `.env` and set at minimum:

```
RETELL_API_KEY=<your Retell API key>
```

The other variables are only needed for the optional post-call service (see
step 8). `.env` is gitignored and must never be committed.

## 4. Create the client config

Each client is one YAML file under `config/clients/` (that whole directory is
gitignored — real client configs stay private).

```bash
cp config/client.example.yaml config/clients/<slug>.yaml
```

Then edit `config/clients/<slug>.yaml`. The example file is heavily commented and
is the schema of record; read those comments as you edit. The fields:

- `client`: `slug` (lowercase-hyphen), `business_name`, `vertical`, `timezone`
  (IANA, validated), optional `assistant_name`, optional `alert_email`.
- `languages`: one entry per language (`code`, `voice_id`, `greeting`, optional
  `language_notes`, optional `sample_lines`). Codes must be unique.
- `facts`: `description` plus optional `address`, `phone`, `email`, `hours`,
  `services`, `faq` — the only facts the agent may state.
- `booking`: optional `url`, `sms_consent` (bool).
- `escalation`: `contact_name`, `escalate_when`, optional `handoff_message`.
- `guardrails`: `never_say`, `off_limits`, `preset` (`medical_adjacent` | `none`).
- `agent`: runtime knobs (`max_call_duration_ms`, `ambient_sound`, expressive
  mode/tags, `pronunciation`, `knowledge_base_ids`).
- `post_call`: the structured fields extracted after each call. Each has `name`,
  `type` (`string`|`boolean`|`enum`|`number`), `description`, optional `source`
  (`caller`|`derived`) and `role` (`phone`|`consent`).

The loader validates everything and reports **all** problems at once with
YAML-path locations, so a typo fails loudly. You can preview the compiled prompt
without deploying:

```bash
python scripts/render_prompt.py config/clients/<slug>.yaml            # all languages
python scripts/render_prompt.py config/clients/<slug>.yaml --language en-US
```

## 5. Dry-run the deployment

```bash
python scripts/deploy_client.py config/clients/<slug>.yaml
```

This is read-only. With no lockfile yet it plans a `CREATE` for each language and
makes **no** mutation calls. If `booking.sms_consent` is true you will also see an
SMS warning (see step 8). Review the plan.

## 6. Apply

```bash
python scripts/deploy_client.py config/clients/<slug>.yaml --apply
```

For each language this creates a Retell LLM (with the compiled prompt) and an
agent (with voice, engine constants, and the post-call analysis schema), then
writes `config/clients/<slug>.lock.json` mapping each language code to its
`{agent_id, llm_id}`. The lockfile is gitignored and is what makes re-runs
idempotent: run the dry-run again and it should report `NOOP` for every language.

Editing the config and re-running `--apply` computes a field-level diff against
live state and issues only the needed `update-agent` / `update-retell-llm` calls.

## 7. Test the agent

Place a test call from the **Retell dashboard** (the agent appears as
`<business_name> — <language code>`). There is no CLI test-call command in this
repo; use the dashboard's test/call feature.

## 8. (Optional) Post-call service: alerts + SMS

The webhook service turns Retell `call_analyzed` events into lead alerts and, when
consented, booking-link SMS. It is entirely optional and not required to run the
agents.

Additional `.env` values:

- `RETELL_WEBHOOK_KEY` — the Retell API key that carries the "webhook" badge; it
  signs the `X-Retell-Signature` header. The service refuses to start without it.
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` — when all three
  are set, booking SMS send via Twilio; otherwise the service logs them only
  (DebugSms). Toll-free FROM numbers require Twilio Toll-Free Verification before
  carriers deliver.
- `SMTP_HOST`/`SMTP_PORT`/`SMTP_USER`/`SMTP_PASSWORD`/`SMTP_FROM` — for
  `EmailAlert`, used only when a client config sets `alert_email`.

Run the service locally (logs to `postcall.log`, INFO level):

```bash
python scripts/run_webhook.py                 # 127.0.0.1:8010
python scripts/run_webhook.py --host 127.0.0.1 --port 8010   # explicit
curl http://127.0.0.1:8010/healthz            # {"status":"ok","managed_agents":N}
```

The service resolves which client/agent a webhook belongs to by scanning
`config/clients/*.lock.json`, so deploy your clients first.

To receive real webhooks, expose the port with a public tunnel and point the
agents at it:

```bash
# a cloudflared quick tunnel needs no account; it prints an https URL
cloudflared tunnel --url http://127.0.0.1:8010
export WEBHOOK_BASE_URL=https://<subdomain>.trycloudflare.com
python scripts/deploy_client.py config/clients/<slug>.yaml           # dry-run: webhook_url UPDATE
python scripts/deploy_client.py config/clients/<slug>.yaml --apply   # wire it
```

> **Ephemeral-tunnel caveat.** A cloudflared *quick* tunnel URL is temporary and
> changes every time the tunnel restarts. The agents' `webhook_url` points at the
> current URL, so the tunnel must stay running for the duration of testing. For
> anything durable use a named tunnel or a stable host.

**Teardown.** Stop the service and tunnel, then clear the wiring so the agents
don't point at a dead URL. With `WEBHOOK_BASE_URL` unset the engine omits the
field (so a plan won't detect or clear it) — clear it explicitly via the API by
PATCHing each agent's `webhook_url` to `null` (Retell: "Set to null to remove
webhook url from this agent") and confirm with `get-agent`.

## 9. Evals

**Layer 1 — static prompt policy checks** (no network, also runs in CI):

```bash
python evals/run_static.py
```

Checks the example config plus any local `config/clients/*.yaml`, and exits
nonzero on any policy finding (missing guardrail lines, wrong-language greeting,
malformed structure, etc.).

**Layer 2 — deterministic transcript checks** (needs the Retell API + real calls):

```bash
python scripts/fetch_calls.py                 # fetch transcripts (lockfile-restricted)
python evals/run_transcripts.py               # run checks, print per-call verdicts
```

`fetch_calls.py` is hard-restricted to the agent_ids in
`config/clients/acme-wellness.lock.json`; it will not fetch calls for any other
agent. Transcripts are saved under `capture/calls/` (gitignored).

**Comparing runs — regression check** (pure reader, no network):

Every runner writes a JSON *run record* under `var/evals/runs/`. `compare_runs.py`
diffs two records of the same layer and flags a **regression only when a check
newly fires** — prompt fingerprints may change freely, so a prompt edit with no
new finding is not a regression.

```bash
# explicit: baseline vs candidate
python evals/compare_runs.py var/evals/runs/<old>.json var/evals/runs/<new>.json

# or just the two most recent records of a layer
python evals/compare_runs.py --layer static --latest
```

Example verdict:

```
VERDICT: OK — no new findings (1 resolved, 3 persisting)
```

Exit 0 = OK, 1 = regression (a new finding, printed in full), 2 = usage/selection
error (bad args, cross-layer compare, or fewer than two records). A NOTE is printed
when the two runs used different call sets — finding deltas may then reflect the
data, not the prompts.

**Judged evals (Layer 4)** — advisory, **requires `ANTHROPIC_API_KEY`**:

```bash
python evals/run_judge.py --dry-run       # keyless smoke test, no network
python evals/run_judge.py --max-calls 5   # real run: judges up to N calls
```

An LLM judges each transcript against a fixed rubric (booking intent handled,
hallucinated commitments vs. the business facts, unresolved caller requests). Every
"fail" must quote a verbatim span from a cited turn — a claim the judge can't ground
in the transcript is downgraded to a `judge_citation_unverified` finding rather than
asserted. `--max-calls` caps how many calls are judged per run (cost control) and
`--dry-run` runs a canned all-pass judge with no network or key. **This layer sends
transcript content to the Anthropic API**, so it is advisory-only and never runs in
CI (CI has no key and must never need one). Add `--strict` to exit 1 on findings.
