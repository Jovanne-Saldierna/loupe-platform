"""Phase 6B live-BigQuery transaction spike tooling.

Not imported by shared/, apps/, or tests/ -- this package exists purely
as an operator-run diagnostic tool. See README.md in this directory for
the exact commands, expected output, and cleanup procedure. Nothing in
this package is invoked automatically; it requires an authenticated
Google Cloud identity (ADC via `gcloud auth application-default login`,
or Google Cloud Shell's built-in credentials) and explicit `--yes`
confirmation before it touches any cloud resource.
"""
