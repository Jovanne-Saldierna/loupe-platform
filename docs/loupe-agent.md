# Loupe E-Commerce Agent

Loupe is the Assistant Layer: a conversational analytics assistant and interactive dashboard for live e-commerce performance.

## Supported questions

- Revenue and growth
- Gross margin
- Returns and return rate
- Margin leakage
- Paid and organic channel mix
- Geographic performance
- Scenario simulations

## Evidence contract

- Claude may explain only values present in returned BigQuery results or deterministic metadata.
- Queries must be parameterized, read-only, and auditable.
- The response must identify the metric definition and relevant time window.
- Unsupported conclusions and fabricated numbers are prohibited.
- If a source is degraded or under active incident, warn the user before presenting conclusions.

## Business logic requirements

### Revenue

Every revenue result must state whether it is gross, net, delivered-only, or another certified version. The query and display must use the same definition.

### Return rate

The numerator and denominator must be explicit. For example, returned order items divided by eligible fulfilled order items is not interchangeable with returned orders divided by all orders.

### Margin

Margin uses sale price and product cost at the declared measurement grain (order_item). Gross margin dollars and gross margin percentage should remain distinct metrics. See docs/contracts.md's "Measurement grain vs. reporting grain" for why a response's actual reporting grain (day/month/category/state/whole-window) is a separate, per-query fact and must be stated alongside the number, never inferred from the catalog.

### Margin leakage

Rank leakage by absolute margin dollars lost. A high return percentage on negligible sales should not automatically outrank a material dollar loss.

### Channel mix

Compare paid and organic share over a declared trailing window, commonly 24 months. Document the source field and channel classification.

## Response structure

A production response should contain:

1. Direct answer
2. Metric definition and date range
3. Supporting result or visualization
4. Source-health or trust warning, when applicable
5. Query evidence or audit reference

