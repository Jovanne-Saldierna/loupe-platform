# Loupe Commerce Intelligence

Business Performance Layer of the [Loupe AI Analytics Platform](../../../README.md).

## Purpose

Turns live commerce data into a decision-support surface a business leader can act on directly: what changed, where to focus, and why, in plain English.

## Key capabilities

- Live commerce dashboard backed by real warehouse queries
- Ask Loupe: answers plain-English business questions grounded in the current data window
- Return risk and margin leakage analysis
- Category and region performance views
- Channel mix analysis
- Scenario-style explanations of what is driving a metric

## Run locally

```bash
cd frontend
npm install
npm run dev:loupe
```

Runs on `http://localhost:3000`. Requires `NEXT_PUBLIC_API_BASE_URL` pointed at a running instance of the platform API (`api/`); see `frontend/.env.example`.

## Typecheck

```bash
npx tsc --noEmit
```

## Related documentation

- [Root README](../../../README.md)
- [Loupe Commerce Intelligence deep dive](../../../docs/loupe-agent.md)
