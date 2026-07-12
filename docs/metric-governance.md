# Metric Governance Copilot

Metric Governance Copilot is the Definition Layer. It prevents metric drift by storing certified definitions, reviewing SQL against approved logic, reconciling mismatches, showing lineage, and recording governance activity.

## Views

- Overview
- Catalog
- Definition Diff
- SQL Review
- Lineage
- Audit Trail

The Overview includes Certified Metrics, Definition Diffs, Approved Tables, and Metrics at a Glance.

## Catalog contract

Each metric record includes:

- Metric name
- Owner
- Description and formula
- Grain
- Freshness expectation
- Certified status
- Approved source tables
- Required filters or exclusions
- Downstream dashboards or assets
- Version and last review timestamp

## SQL review contract

`review_sql` returns structured data containing:

- `referenced_tables`
- `findings`
- `suggested_next_actions`
- severity-coded issues
- approved source alignment
- metric definition alignment
- deterministic trust score

Checks include unapproved or unknown tables, `SELECT *`, missing date filters, missing or unclear grain, non-certified metric logic, unsafe joins, incomplete approved-table coverage, and active source incidents.

## Audit events

Audit logging is dynamic and passes through `src/data_service.py`. At minimum, record:

- SQL review submitted and completed
- Metric catalog viewed or changed
- Definition diff generated
- Incident status checked
- Chat question asked
- AI response generated
- Trust score calculated

Audit records should contain an event ID, timestamp, actor or session, event type, subject, outcome, and enough structured context to reproduce the decision without storing secrets.

