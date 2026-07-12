# Loupe Platform UI Product Direction — Recovered

## Recovery status

This document reconstructs the UI side conversation associated with Codex thread ID `019f4ae0-c687-7101-a633-8c229823f694`.

The original side branch is no longer available as a navigable Codex thread. The following survived locally:

- The complete sequence of the user's prompts.
- The generated interactive dashboard mockup.
- The original side-thread identifier.
- The explicit product and architecture decisions confirmed by the user.

The original assistant prose did not survive as a readable session transcript. This document therefore separates confirmed decisions from implementation recommendations and does not invent missing quotations or color values.

## Surviving artifact

Interactive mockup:

`/Users/jovannesaldierna/.codex/visualizations/2026/07/10/019f4ae0-c687-7101-a633-8c229823f694/loupe-product-mockups.html`

The mockup contains switchable views for all three applications and responsive behavior for desktop, tablet, and mobile widths.

## Recovered user requests

The conversation began with the following requirements:

1. Create three different theme and brand examples for the three dashboards.
2. Cover colors, overall visual treatment, widgets, and interface elements.
3. Favor a modern, clean product aesthetic.
4. Use shadcn/ui documentation and Lucide icons as primary design references.
5. Produce visual mockups, not only written descriptions.
6. Make the applications polished enough to feature prominently on a Lovable-built portfolio website and LinkedIn.
7. Avoid shortcuts or a separate low-fidelity portfolio version.
8. Keep all three applications connected to live BigQuery data rather than sample data in the portfolio experience.
9. Treat the work as a production-grade platform, not three disposable prototypes.

Reference sites supplied by the user:

- `https://ui.shadcn.com/`
- `https://lucide.dev/icons/sliders-horizontal`
- `https://lovable.dev/projects/775e5c0f-3e77-4d87-85a8-464647001df4`

## Confirmed product decisions

### Three separate applications

The platform remains three distinct applications:

1. Loupe Analytics Agent
2. Metric Governance Copilot
3. Data Quality Incident Triage Agent

Each application should receive its own URL. The applications may include links to the other two for convenient navigation.

The separation represents a realistic production boundary. Different employees may need access to different applications. Authentication and role enforcement are intentionally outside the current build scope, but the application boundaries should not prevent that future model.

### Shared visual system, differentiated applications

Confirmed direction:

- All three applications use the same component system and design language.
- Each application configures its own accent, density, workflow, navigation, and data presentation.
- The products should feel like one coordinated platform family without looking like three copies of the same dashboard.
- The distinction must be meaningful at the workflow level, not merely a color swap.

### Shared platform communication

The applications communicate through persisted state and APIs or shared service contracts. They are not merged into one application merely because they share components and data.

### No separate portfolio-only application

There should not be a polished fake portfolio shell backed by sample data while a different application contains the real logic. The application shown in the portfolio is the real application.

### Authentication excluded

Authentication and authorization are not part of the present build. The architecture should preserve separate application boundaries without implementing access control now.

## Confirmed visual language

The visual reference is shadcn/ui's product language:

- Clean application shells
- Strong typography hierarchy
- Quiet surfaces and restrained borders
- Compact, functional cards
- Modern data tables
- Lucide line icons
- Clear empty, loading, warning, and error states
- Purposeful spacing rather than decorative density
- Responsive behavior
- Product-quality drawers, dialogs, filters, tabs, command surfaces, and navigation where workflows require them

The surviving mockup uses semantic design tokens such as `--foreground`, `--muted-foreground`, `--card`, `--border`, `--primary`, and `--viz-series-*`. It does not hardcode a universal black background into the artifact. Therefore the old Streamlit master-guide colors must not be treated as the recovered UI decision.

## Recovered mockup directions

### Loupe — Signal Intelligence

Role: Assistant Layer

Primary page shown: Commerce Intelligence

Navigation shown:

- Overview
- Ask Loupe
- Performance
- Customers
- Products
- Scenarios

Key interface elements:

- BigQuery live-source badge
- Date-range control
- Revenue, margin, orders, and return-rate KPI cards
- Revenue-performance time series
- Grounded Loupe insight card
- Evidence affordance
- Ask Loupe prompt surface
- Metric definition, reporting grain, date window, and source-health context

The experience should be decision-oriented and approachable for business users while retaining evidence and governance context.

### Metric Governance — Violet Ledger

Role: Definition Layer

Primary page shown: SQL Governance Review

Navigation shown in the recovered product direction includes governance-centered workflows such as overview, catalog, SQL review, definition comparison, lineage, and audit history.

Key interface elements:

- Submitted BigQuery SQL panel
- Copy action
- Deterministic trust-score ring
- Review findings
- Metric alignment comparison
- Certified or pending-validation version context
- Source-table, grain, and definition evidence
- Remediation recommendations

The interface should be denser and more technical than Loupe because its primary users inspect definitions, SQL, lineage, and review evidence.

### Data Quality Triage — Midnight Command

Role: Reliability Layer

Primary page shown: Warehouse Health

Key interface elements:

- Warehouse health summary
- Deterministic check volume
- Incident timeline
- Active incident queue
- Severity and age
- Observed versus expected values
- Affected metrics and assets
- Playbook and lifecycle actions
- Filtering and prioritization controls

The experience should feel operational and urgent without becoming visually noisy. Severity communicates state; it should not turn the entire interface into a red alert screen.

## Component architecture

Shared component concepts:

- Application shell
- Sidebar and top navigation
- Page header and eyebrow
- KPI/stat card
- Data table
- Status badge
- Metric-definition badge
- Source-health indicator
- Trust-score visualization
- Query/code panel
- Filter and date controls
- Empty/unavailable state
- Evidence panel
- Incident timeline
- Prompt and conversational answer surface
- Chart container and tooltip conventions

Each application provides its own navigation configuration, accent tokens, chart palette, terminology, density, and workflow-specific compositions.

## Streamlit versus production frontend

The recovered conversation explicitly questioned whether the result would remain a Streamlit application.

Confirmed distinction:

- Streamlit can reproduce much of the shadcn visual language using disciplined layout, CSS, and component conventions.
- It cannot provide a truly pixel-perfect shadcn implementation because Streamlit does not expose the same React component markup and interaction control.
- A real shadcn/ui implementation normally means a React or Next.js frontend.
- Python, BigQuery, deterministic checks, and AI services remain the product backend regardless of frontend technology.

The conversation did not collapse the three products into one application. A frontend change would alter their delivery technology, not their product identity or backend contracts.

## Real-world target architecture discussed

The production-oriented direction considered:

```text
Separate application URLs
        |
Shared shadcn/ui component system
        |
React or Next.js frontends
        |
Typed API boundary such as FastAPI
        |
Shared Python domain services
        |
BigQuery, persisted metric catalog, incidents, and audit state
```

The current migration may keep Streamlit temporarily, but it must not rewrite domain logic into Streamlit. Shared Python services should remain framework-independent so the frontend can be replaced without rebuilding the analytical platform.

## What was not recovered and must not be guessed

- The original assistant's exact prose.
- Any exact hex palette that may have appeared only in assistant text and not in the artifact.
- Final approval of a specific frontend migration date.
- Final production hosting provider for each application.

These decisions should be made in a dedicated UI implementation phase using the surviving mockup and this recovered brief as inputs.

## Binding correction to older documentation

Any older Loupe documentation that states all three applications must use a universal dark background, fixed green/purple palette, or identical Streamlit shell is stale for UI implementation purposes.

The binding recovered direction is:

> Three separate production applications, one coordinated shadcn-inspired product system, distinct workflow identities, live data, real shared state, and no portfolio-only fake experience.

