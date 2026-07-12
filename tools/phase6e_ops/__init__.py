"""Phase 6E operator scripts: guarded, opt-in, human-run commands against
the isolated `loupe_platform_test` BigQuery dataset only.

Nothing in this package is imported by shared/ or apps/ -- these are
standalone CLI entry points, run the same way tools/phase6b_spike/ is
run: by a human operator from an authenticated local terminal or Cloud
Shell, never by this agent, never by application startup.
"""
