# Development and Operations

## Recommended repository layout

```text
.
|-- README.md
|-- docs/
|-- src/
|   |-- data_service.py
|   |-- review.py
|   |-- triage.py
|   `-- ui.py
|-- tests/
|-- .streamlit/
|   `-- config.toml
|-- pyproject.toml
`-- .env.example
```

## Local configuration

Keep secrets out of the repository. Document environment variable names in `.env.example`, including the Google Cloud project, credential strategy, Anthropic key, permitted dataset, and persistence configuration.

## Test strategy

At minimum, test:

- Revenue, margin, return-rate, and channel definitions
- SQL table extraction and each governance rule
- Join fanout and grain warnings
- Trust-score determinism and boundary conditions
- Every data-quality check and severity boundary
- Incident lifecycle transitions
- Audit event creation
- LLM grounding guards when results are empty or sources are degraded

Use fixtures for BigQuery responses so unit tests do not require live cloud access. Keep a small integration suite for parameter binding, permissions, and schema compatibility.

## Definition of done

A feature is complete when business logic is deterministic and tested, data access stays in the service layer, metric and grain semantics are documented, source-health effects are visible, important actions create audit events, and the UI does not overstate what the evidence supports.

## Production checklist

- Read-only analytical identity configured
- Narrow write identity configured for audit and incident storage, if required
- Query limits and timeouts enabled
- Certified catalog versioned
- Health checks and incident persistence enabled
- Secrets excluded from logs
- Audit retention defined
- Unit and integration tests passing
- Degraded-source behavior verified

