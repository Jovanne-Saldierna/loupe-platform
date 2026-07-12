# Data Quality Incident Triage Agent

The Data Quality Incident Triage Agent is the Reliability Layer. It monitors warehouse tables, detects deterministic anomalies, creates incidents, and optionally uses Claude to turn structured incidents into readable response playbooks.

## Deterministic checks

- Freshness delays
- Row-count or volume drift
- Null spikes
- Duplicate keys
- Schema drift
- Query exceptions

AI does not decide whether data is broken. The check result, threshold, and deterministic rule make that decision.

## Incident record

- `incident_id`
- `created_at`
- `dataset`
- `table_id`
- `check_type`
- `severity`
- `status`
- `observed_value`
- `expected_value`
- `sql`
- `affected_metrics`
- `affected_dashboards`
- `playbook`

Useful operational additions include `owner`, `acknowledged_at`, `resolved_at`, `resolution_notes`, and the rule version that generated the incident.

## Severity baseline

- **High:** certified source affected, major freshness failure, major row-count collapse, or critical metric impact
- **Medium:** warning-level drift, partial degradation, non-critical source, or plausible downstream impact
- **Low:** minor anomaly, exploratory source, metadata warning, or no known downstream asset

## Lifecycle

```text
detected -> open -> acknowledged -> investigating -> mitigated -> resolved
```

Rechecks may resolve an incident only when the deterministic recovery criteria are satisfied. The resulting source status must be available to both Loupe responses and governance SQL reviews.

