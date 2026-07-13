# Data Quality Incident Triage

Engineering Reliability Layer of the [Loupe AI Analytics Platform](../../../README.md).

## Purpose

Gives a data engineer the same first ten minutes of investigation an experienced teammate would walk them through, automatically: what likely broke, what to check first, and what it affects downstream.

## Problem it solves

Data teams spend hours manually triaging quality issues: hunting for lineage, root cause, and downstream impact before they can even start fixing anything.

## Key capabilities

- Deterministic detection of data quality issues against defined checks
- Seeded reliability scenarios for walkthrough when no live incidents exist, clearly labeled as seeded and never substituted for a real incident
- Incident queue with severity and status
- AI-generated triage playbooks grounded in the incident's own evidence
- Suggested debugging SQL tied to the specific check that failed
- Read-only SQL sandbox for verifying hypotheses safely, with unsafe SQL rejected deterministically before it reaches the database or the AI layer
- Lineage from source table to governed metrics to downstream assets
- Audit trail of triage activity
- Ask Loupe incident helper, grounded in the selected incident

AI does not decide whether data is broken. Deterministic checks detect issues; Loupe AI generates grounded playbooks and explanations from what those checks already found.

## Run locally

```bash
cd frontend
npm install
npm run dev:triage
```

Runs on `http://localhost:3002`. Requires `NEXT_PUBLIC_API_BASE_URL` pointed at a running instance of the platform API (`api/`); see `frontend/.env.example`.

## Typecheck

```bash
npx tsc --noEmit
```

## Related documentation

- [Root README](../../../README.md)
- [Data Quality Incident Triage deep dive](../../../docs/data-quality-triage.md)
