# Metric Governance Copilot

BI Trust Layer of the [Loupe AI Analytics Platform](../../../README.md).

## Purpose

Acts as a metric steward and semantic-layer copilot: the system of record for what a metric means, whether a submitted query respects that meaning, and what happens downstream if it does not.

## Problem it solves

Enterprises struggle with metric chaos: the same metric is defined differently across Finance, Product, BI, and Ops, and nobody can trace the downstream impact when someone changes the underlying logic.

## Key capabilities

- Governed metric catalog with certification status, owner, grain, and freshness expectations
- SQL review and safety checks against the governed definition
- Trust score with individual score contributions, not just a single number
- Metric alignment: expected versus observed contract comparison
- Definition diff and change-risk categorization
- Downstream impact mapping to dependent dashboards and assets
- Actionable governance recommendations
- Steward summary with a copyable governance brief
- Ask Loupe metric trust helper, grounded in the current review

AI never invents metrics, scores, or review findings. Trust scoring, SQL review, and completeness checks are deterministic; Ask Loupe explains those results from the same evidence already shown on screen.

## Run locally

```bash
cd frontend
npm install
npm run dev:governance
```

Runs on `http://localhost:3001`. Requires `NEXT_PUBLIC_API_BASE_URL` pointed at a running instance of the platform API (`api/`); see `frontend/.env.example`.

## Typecheck

```bash
npx tsc --noEmit
```

## Related documentation

- [Root README](../../../README.md)
- [Metric Governance Copilot deep dive](../../../docs/metric-governance.md)
- [Metrics and trust contracts](../../../docs/contracts.md)
