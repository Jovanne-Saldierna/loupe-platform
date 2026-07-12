# Loupe UI Visual Acceptance Contract

## Status

The files in this directory are the binding visual reference for the dedicated frontend phase. They supersede the stale universal dark Streamlit theme.

The approved mockup must not be reinterpreted, simplified, modernized, or restyled without explicit user approval.

## Locked reference

- `approved-ui-mockup.html`: exact approved interactive fragment
- SHA-256: `33cf1f77f30ef12797b3c499ab52c1de2d9b6f256b78a7a63b4b407bb9dc103c`
- `UI_PRODUCT_DIRECTION.md`: recovered product and workflow specification
- `loupe-reference.png`: Loupe Signal Intelligence reference
- `governance-reference.png`: Violet Ledger Governance reference
- `triage-reference.png`: Midnight Command Triage reference

Any modification to `approved-ui-mockup.html` must be rejected unless the user explicitly approves a new reference and checksum.

## Binding direction

- Three separately deployed applications and URLs
- One shared shadcn-inspired product system
- Loupe: bright, calm Signal Intelligence workspace
- Governance: precise Violet Ledger definition and SQL-review workspace
- Triage: dark Midnight Command reliability console
- Shared component language with genuinely distinct workflows and identities
- Lucide icons, modern tables, drawers, filters, evidence panels, and responsive layouts
- Real application data and honest unavailable states, never a fake portfolio shell
- Existing Python domain services remain framework-independent
- Intended final delivery: Next.js, TypeScript, and shadcn/ui over a typed FastAPI boundary

## Implementation acceptance

The frontend must preserve the approved reference's:

- information hierarchy and page composition
- navigation structure and density
- typography, spacing, surfaces, borders, and proportions
- application-specific visual identities
- tables, evidence panels, metric summaries, charts, and status treatments
- desktop composition and responsive design language

Mock values must be replaced with real API data without redesigning the composition. Loading, empty, error, unavailable, and mobile states must extend the same design language.

## Required workflow

1. Read all three files in this directory before proposing frontend code.
2. Inspect all three reference images.
3. Produce a component-and-token mapping without changing the design.
4. Implement one application shell at a time.
5. Capture implementation screenshots at the agreed reference widths.
6. Compare them with the locked references.
7. Request approval before any visual deviation.

Backend persistence work must finish before frontend implementation begins. Preserving this reference pack is documentation work only and does not authorize UI implementation during the active persistence phase.
