# Case studies

This kit is used to deploy real client receptionists, but those deployments —
their configs, prompts, phone numbers, and call transcripts — are private by
design (`config/clients/`, `capture/`, and the SMS ledger are all gitignored).
So this directory is a truthful **index**, not a showcase with client data.

What actually exists in the deployment history (no client specifics):

- The kit was reverse-engineered from three captured production Retell agents
  (one bilingual DMA receptionist pair, one single-language studio agent) and
  then re-expressed through the config schema; the re-expression was validated
  section-by-section against the originals.
- A fictional example client (**Acme Wellness**, a med spa) is deployed live as
  the test bed: two agents (en-US, es-419), used to exercise the full loop —
  deploy, test call, post-call webhook, consent-gated booking SMS, and both eval
  layers against the real transcripts.
- A fresh-clone deployment of a second fictional client (**Blue Palm Studio**)
  was run and timed end-to-end to measure onboarding time, then torn down.

When a real, shareable case study exists (with client permission and all PII
removed), it will be added here as its own file. Until then, this index is the
honest record.
