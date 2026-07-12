# Metrics and Trust Contracts

## Certified metric definition

A metric is certified only when its name, business meaning, formula, measurement grain, time behavior, source tables, filters, owner, freshness expectation, and version are recorded and approved.

Metric labels alone are not contracts. Two queries called `revenue` can disagree legitimately if one is gross booked revenue and the other is delivered net revenue.

## Measurement grain vs. reporting grain

These are two different concepts, and a metric's catalog entry must never conflate them under one ambiguous "grain" field. Doing so is a real bug this platform has already made once (see the Phase 5 grain-mismatch correction): a catalog field populated with reporting-grain-shaped text ("one row per day, optionally sliced by category and/or state") silently drifted out of sync with what the actual queries returned, and nobody could tell whether that was a genuine metric-definition disagreement or just two valid reports of the same number.

**Measurement grain** is the atomic business entity a metric is *defined* over -- order, order item, user, session/event, and so on. It is a property of the metric itself (`shared.models.MetricDefinition.measurement_grain`) and never changes based on how any one query happens to group, filter, or aggregate the data. All five of Loupe's current catalog metrics (revenue, margin, return_rate, margin_leakage, channel_mix) share the same measurement grain: **order_item**. Every one of them is defined as a per-order_item quantity (or a per-order_item ratio, for return_rate and channel_mix) that is additive/summable across any grouping.

**Reporting grain** is the dimensional/temporal shape a *specific query* returns -- one row per day, one row per month, one row per category, one row per state, or one aggregate row for a whole selected window. Reporting grain belongs to the query, never to the catalog entry. It is declared in that query's own docstring and proven by that query's own regression test (see `apps/loupe_agent/metrics.py` and `tests/loupe_agent/test_query_contracts.py`), and in the UI/narration layer that renders the result (see `apps/loupe_agent/ui.py`'s `scope_caption()` and `apps/loupe_agent/chat.py`'s `reporting_note()`).

It is normal and correct for one measurement-grain metric to back many different, simultaneously valid reporting grains: a monthly trend, a per-category leaderboard, and a single whole-window KPI can all legitimately report `revenue` at the same time without disagreeing with each other or with the catalog. That is reuse of one definition across many valid views, not a "grain mismatch." A genuine grain mismatch is when two *definitions* of the same named metric declare different measurement grains (e.g. one team's `revenue` measured per order, another team's measured per order item) -- that comparison belongs in Metric Governance's Definition Diff (`apps/metric_governance/definition_diff.py`'s `compare_definitions()`, which compares `measurement_grain` field-by-field between two `MetricDefinition` objects), never between a catalog entry and any one query's output shape.

### Denominator grain (ratio metrics)

For a ratio metric, "measurement grain" additionally answers: at what grain are the numerator and denominator both counted, and are they counted in the same query over the same filter scope? Two worked examples from the current catalog:

- **return_rate**: numerator = order_items rows with `status='Returned'`, denominator = all order_items rows in the identical filter scope, both counted in the same `SELECT` (never a numerator and denominator sourced from two differently-scoped queries). It is not orders, not units, and not sessions.
- **channel_mix**: the denominator behind `paid_share_pct` is a `COUNT(*)` of order_items rows attributed to a traffic-source classification -- despite the SQL column alias `order_count` in `get_channel_mix_trend()`/`get_channel_mix_range()`, which reads as if it were order-grain. It counts order_item rows, one per line item, never distinct orders and never an events/session table. See `shared/metric_catalog.py`'s channel_mix entry and `tests/loupe_agent/test_measurement_grain.py` for the explicit regression coverage of this.

## Grain declaration (join validation)

Every SQL review should state the reviewed query's output grain, such as one row per day, order, order item, user, product, or channel -- this is reporting grain in the sense above, applied to ad hoc reviewed SQL rather than a catalogued query function. Join validation must compare the grains on both sides and flag possible fanout.

## Trust score

Trust scoring is deterministic and explainable. Inputs include:

- metric certification status
- source-table anomaly status
- definition mismatch count
- high-severity SQL findings
- approved-table coverage
- missing grain
- missing freshness expectations

The UI must expose the factors that changed the score. An LLM may summarize the score but cannot alter it.

## Suggested interpretation

- **High trust:** certified definition, healthy approved sources, declared grain, and no material review findings
- **Review required:** incomplete metadata, warnings, or a non-critical source incident
- **Do not rely:** active critical incident, unapproved source, severe definition mismatch, or unsafe query behavior

Numeric score thresholds should be versioned with the scoring function and covered by tests.

## Query safety

- Use named query parameters rather than string interpolation.
- Permit read-only statements for analytical execution.
- Apply bytes-scanned limits and timeouts where supported.
- Capture query metadata and audit references without logging credentials or sensitive parameter values.
- Ensure generated prose is grounded in the returned result set.

