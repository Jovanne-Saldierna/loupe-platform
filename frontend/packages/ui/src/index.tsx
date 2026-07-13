import type { ComponentType, ReactNode } from "react";
import { Activity, ArrowRight, CheckCircle2, Copy, ShieldCheck, Sparkles, TrendingDown, TrendingUp } from "lucide-react";
import "./styles.css";
import "./themes.css";

export function PlatformLinks({ active }: { active: "loupe" | "governance" | "triage" }) {
  const links = [
    { id: "loupe", label: "Loupe", icon: Sparkles, href: process.env.NEXT_PUBLIC_LOUPE_URL ?? "http://localhost:3000" },
    { id: "governance", label: "Governance", icon: ShieldCheck, href: process.env.NEXT_PUBLIC_GOVERNANCE_URL ?? "http://localhost:3001" },
    { id: "triage", label: "Triage", icon: Activity, href: process.env.NEXT_PUBLIC_TRIAGE_URL ?? "http://localhost:3002" },
  ] as const;
  return <nav className="platform-links" aria-label="Loupe platform applications">{links.map(({ id, label, icon: Icon, href }) => <a key={id} className={`platform-link ${active === id ? "active" : ""}`} href={href}><Icon size={15} />{label}</a>)}</nav>;
}

export function AppShell({
  active,
  brand,
  brandIcon: BrandIcon,
  navigation,
  children,
}: {
  active: "loupe" | "governance" | "triage";
  brand: string;
  brandIcon: ComponentType<{ size?: number }>;
  navigation: { label: string; icon: ComponentType<{ size?: number }>; active?: boolean; onClick?: () => void }[];
  children: ReactNode;
}) {
  return <div className={`product product-${active}`}><div className="app-shell"><aside className="sidebar"><div className="brand"><BrandIcon size={17} />{brand}</div><nav className="app-nav" aria-label={`${brand} sections`}>{navigation.map(({ label, icon: Icon, active: selected, onClick }) => <button type="button" className={`nav-item ${selected ? "active" : ""}`} aria-current={selected ? "page" : undefined} onClick={onClick} key={label}><Icon size={17} />{label}</button>)}</nav></aside><main className="workspace">{children}</main></div></div>;
}

export function Badge({ children, tone = "accent" }: { children: ReactNode; tone?: "accent" | "neutral" | "warning" }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`card ${className}`}>{children}</section>;
}

export function Unavailable({ message }: { message: string }) {
  return <Card className="unavailable"><strong>Live data unavailable</strong><p>{message}</p></Card>;
}

// --- Dashboard primitives ---------------------------------------------------
// Reusable, presentation-only building blocks extracted verbatim from
// Loupe's Dashboard (frontend/apps/loupe-web/app/page.tsx) so Loupe,
// Governance, and Triage can compose the same dashboard system instead of
// each app re-implementing these shapes locally. Every prop here is either
// a pre-formatted string/number or a callback -- no app-specific formatting
// (money/delta/etc.) or data types live in this package, so nothing here
// couples back to Loupe's domain.

// One consistent card shape used everywhere on the dashboard/performance
// tabs -- header (icon + title), description, optional action, then
// content -- so sections read as one cohesive workspace instead of
// mismatched ad hoc cards with a floating label stacked above them.
export function SectionCard({ icon: Icon, title, description, action, className = "", children }: { icon?: ComponentType<{ size?: number }>; title: string; description?: string; action?: ReactNode; className?: string; children: ReactNode }) {
  return (
    <Card className={`section-card ${className}`}>
      <div className="card-head">
        <div><h2>{Icon && <span className="section-card-icon"><Icon size={16} /></span>}{title}</h2>{description && <div className="muted small">{description}</div>}</div>
        {action}
      </div>
      {children}
    </Card>
  );
}

// Dashboard insight primitives, built only from data the calling card already
// fetched -- no new endpoints, no invented numbers. MiniStatStrip is a row
// of small label/value pairs (KPI readouts, source-mix summary). InsightTiles
// is a row of slightly larger label/name/value cards (quick category/region
// takeaways) that read as "here's what to look at" before a longer table.
export function MiniStatStrip({ items, className = "" }: { items: { label: string; value: string }[]; className?: string }) {
  return (
    <div className={`mini-stat-strip ${className}`}>
      {items.map((it) => (
        <div className="mini-stat" key={it.label}>
          <span className="mini-stat-label">{it.label}</span>
          <span className="mini-stat-value">{it.value}</span>
        </div>
      ))}
    </div>
  );
}
export function InsightTiles({ items }: { items: { label: string; name: string; value: string }[] }) {
  return (
    <div className="insight-tiles">
      {items.map((it) => (
        <div className="insight-tile" key={it.label}>
          <span className="insight-tile-label">{it.label}</span>
          <span className="insight-tile-name">{it.name}</span>
          <span className="insight-tile-value">{it.value}</span>
        </div>
      ))}
    </div>
  );
}

// KPI tile: label + value + a colored delta badge (up/down icon, not just
// text) + an optional small contextual note -- the shadcnspace metric-tile
// composition, reused across Home/Dashboard/Performance instead of each
// tab inventing its own KPI markup. `change` is a pre-formatted string
// (e.g. from a `delta()`-style helper in the calling app); tone is derived
// from its +/- prefix, not from a raw number, so this component stays
// formatting-agnostic.
export function Stat({ label, value, change, note, icon: Icon }: { label: string; value: string; change: string; note?: string; icon?: ComponentType<{ size?: number }> }) {
  const unavailable = change === "Prior period unavailable";
  const positive = !unavailable && change.startsWith("+");
  const negative = !unavailable && change.startsWith("-");
  const tone = unavailable ? "neutral" : positive ? "up" : negative ? "down" : "neutral";
  return (
    <Card className="metric-tile">
      <div className="stat-label">{Icon && <span className="metric-tile-icon"><Icon size={13} /></span>}{label}</div>
      <div className="stat-line">
        <span className="stat-value">{value}</span>
        <span className={`metric-delta metric-delta-${tone}`}>
          {positive && <TrendingUp size={12} />}
          {negative && <TrendingDown size={12} />}
          {change}
        </span>
      </div>
      {note && <div className="metric-tile-note muted small">{note}</div>}
    </Card>
  );
}

// Dashboard-only KPI tile: label+icon, value+delta, and a thin spark/
// progress fill derived from the same change_pct/value the tile already
// displays -- a visual encoding of the number shown, not a new data source.
// `changePct` drives tone/spark only; `changeLabel` is the pre-formatted
// display string, so this component never needs its own delta formatter.
// `invertTone` flips up/down coloring for metrics where a rise is bad
// (e.g. return rate).
export function DashKpiTile({
  icon: Icon, label, value, changePct, changeLabel, invertTone = false, sparkPct, badge,
}: {
  icon: ComponentType<{ size?: number }>; label: string; value: string; changePct: number | null;
  changeLabel: string; invertTone?: boolean; sparkPct: number; badge?: ReactNode;
}) {
  const rawTone = changePct === null ? "neutral" : changePct > 0 ? "up" : changePct < 0 ? "down" : "neutral";
  const tone = invertTone && rawTone !== "neutral" ? (rawTone === "up" ? "down" : "up") : rawTone;
  return (
    <div className="dash-kpi-tile">
      <div className="dash-kpi-head"><span className="dash-kpi-icon"><Icon size={13} /></span>{label}</div>
      <div className="dash-kpi-value-row">
        <span className="dash-kpi-value">{value}</span>
        <span className={`dash-kpi-delta dash-kpi-delta-${tone}`}>{changeLabel}</span>
        {badge}
      </div>
      <div className="dash-kpi-spark-track"><span className={`dash-kpi-spark-fill dash-kpi-spark-${tone}`} style={{ width: `${Math.max(4, Math.min(100, sparkPct))}%` }} /></div>
    </div>
  );
}

// Shared up/down/neutral tone used by the mini comparison/status cards below.
export type CardTone = "up" | "down" | "neutral";

// Compact current-vs-prior comparison tile (value, delta pill, thin progress
// fill against the larger of current/prior, "Prior X" caption).
export function MiniCompareCard({ icon: Icon, label, value, prior, deltaLabel, tone, pct }: { icon: ComponentType<{ size?: number }>; label: string; value: string; prior: string; deltaLabel: string; tone: CardTone; pct: number }) {
  return (
    <div className="mini-compare-card">
      <div className="mini-card-head"><span className="mini-card-icon"><Icon size={13} /></span><span>{label}</span><span className={`mini-card-delta mini-card-delta-${tone}`}>{deltaLabel}</span></div>
      <div className="mini-card-value">{value}</div>
      <div className="mini-card-bar-track"><span className={`mini-card-bar-fill mini-card-bar-${tone}`} style={{ width: `${Math.max(4, Math.min(100, pct))}%` }} /></div>
      <div className="mini-card-sub muted small">Prior {prior}</div>
    </div>
  );
}
// Compact status tile (value, delta pill, status label) -- same shell as
// MiniCompareCard without the progress bar/prior caption, for metrics
// better expressed as a qualitative state (e.g. "Expanding"/"Compressing").
export function MiniStatusCard({ icon: Icon, label, value, deltaLabel, status, tone }: { icon: ComponentType<{ size?: number }>; label: string; value: string; deltaLabel: string; status: string; tone: CardTone }) {
  return (
    <div className="mini-compare-card">
      <div className="mini-card-head"><span className="mini-card-icon"><Icon size={13} /></span><span>{label}</span><span className={`mini-card-delta mini-card-delta-${tone}`}>{deltaLabel}</span></div>
      <div className="mini-card-value">{value}</div>
      <span className={`mini-card-status mini-card-status-${tone}`}>{status}</span>
    </div>
  );
}

// Generic compact fact pairs (label + value, optional tone + helper text) --
// for showing a handful of related raw facts side by side (e.g. an observed
// vs. expected reading, or any other small set of named values a calling app
// already has). Not tied to any one app's domain; tone reuses the shared
// up/down/neutral vocabulary and is entirely optional.
export function FactPairGrid({ items }: { items: { label: string; value: string; tone?: CardTone; helper?: string }[] }) {
  if (!items.length) return null;
  return (
    <div className="fact-pair-grid">
      {items.map((it) => (
        <div className="fact-pair" key={it.label}>
          <span className="fact-pair-label">{it.label}</span>
          <span className={`fact-pair-value${it.tone ? ` fact-pair-value-${it.tone}` : ""}`}>{it.value}</span>
          {it.helper && <span className="fact-pair-helper muted small">{it.helper}</span>}
        </div>
      ))}
    </div>
  );
}

// Generic "why this score" breakdown: a factor's name/label, its point
// contribution, and a short reason -- for any calling app that computes a
// score from named factors and wants to explain the math instead of just
// showing the final number. Not tied to any one app's scoring model; the
// calling app supplies pre-formatted label/points/reason per row.
export function ReasoningBreakdown({ items }: { items: { label: string; points: number; reason: string }[] }) {
  if (!items.length) return null;
  return (
    <div className="reasoning-breakdown">
      {items.map((it) => (
        <div className="reasoning-row" key={it.label}>
          <div className="reasoning-row-head">
            <span className="reasoning-label">{it.label}</span>
            <span className="reasoning-points">{it.points > 0 ? `+${it.points}` : it.points}</span>
          </div>
          <div className="reasoning-reason muted small">{it.reason}</div>
        </div>
      ))}
    </div>
  );
}

// Generic compact checklist for a list of short recommended/next-step
// strings -- distinct from ActionFeed, which requires a priority + icon +
// metric per row that doesn't fit a plain ordered list of suggestions.
export function RecommendationList({ title, items }: { title?: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div className="recommendation-list">
      {title && <div className="recommendation-list-title">{title}</div>}
      <ul className="recommendation-rows">
        {items.map((text, i) => (
          <li className="recommendation-row" key={i}>
            <CheckCircle2 size={14} />
            <span>{text}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// Generic related-items chip list -- a title, a row of compact pill chips,
// and an optional quiet empty-state message when there's nothing to show.
// Not tied to any one app's domain (referenced tables, linked incidents,
// tags, etc. are all just string items to the component).
export function ChipList({ title, items, tone, emptyLabel }: { title?: string; items: string[]; tone?: CardTone; emptyLabel?: string }) {
  if (!items.length && !emptyLabel) return null;
  return (
    <div className="chip-list">
      {title && <div className="chip-list-title">{title}</div>}
      {items.length ? (
        <div className="chip-row">
          {items.map((it) => (
            <span className={`chip${tone ? ` chip-${tone}` : ""}`} key={it}>{it}</span>
          ))}
        </div>
      ) : (
        <p className="muted small">{emptyLabel}</p>
      )}
    </div>
  );
}

export type FeedPriority = "high" | "medium" | "info";
export type FeedItem = { icon: ComponentType<{ size?: number }>; title: string; metric: string; priority: FeedPriority };

// Action queue as a compact activity/action feed -- a priority dot, icon,
// short action label, and a metric pill per row, instead of prose
// "Investigate / Review" sentences. Every row's data (title/metric/priority)
// is computed by the calling app; this component only renders the shape.
export function ActionFeed({ items }: { items: FeedItem[] }) {
  if (!items.length) return <p className="muted small">Not enough data yet to generate action recommendations.</p>;
  return (
    <div className="action-feed">
      {items.map((a) => (
        <div className="feed-row" key={a.title}>
          <span className={`feed-dot feed-dot-${a.priority}`} />
          <span className="feed-icon"><a.icon size={14} /></span>
          <span className="feed-title">{a.title}</span>
          <span className="feed-metric">{a.metric}</span>
        </div>
      ))}
    </div>
  );
}

// --- Loupe AI helper panel ---------------------------------------------------
// Compact, contextual "Ask Loupe" chat surface shared across Loupe Commerce,
// Governance, and Triage so the AI helper reads as one product family instead
// of each app building its own chat widget. This component is presentation
// only: it renders whatever messages/state the calling app already has and
// forwards question/ask callbacks -- it never fetches, never constructs the
// grounding payload, and never decides what the answer says. Each app owns
// its own request shape (review context, incident context, etc.) and simply
// passes pre-formatted question/answer pairs in `messages`.
export type HelperMessage = { id: string; question: string; answer: string | null };

export function AskLoupePanel({
  title = "Ask Loupe",
  status,
  messages,
  question,
  onQuestionChange,
  onAsk,
  asking,
  disabled,
  disabledMessage,
  placeholder = "Ask Loupe a question…",
  samplePrompts = [],
}: {
  title?: string;
  status?: ReactNode;
  messages: HelperMessage[];
  question: string;
  onQuestionChange: (value: string) => void;
  onAsk: (q: string) => void;
  asking: boolean;
  disabled: boolean;
  disabledMessage: string;
  placeholder?: string;
  samplePrompts?: string[];
}) {
  return (
    <div className="chat-panel chat-panel-compact">
      <div className="chat-panel-header">
        <div className="chat-panel-title"><Sparkles size={15} />{title}</div>
        {status && <div className="chat-panel-status muted small">{status}</div>}
      </div>
      <div className="chat-scroll chat-scroll-compact">
        {disabled ? (
          <div className="chat-empty">
            <Sparkles size={18} />
            <p>{disabledMessage}</p>
            {samplePrompts.length > 0 && (
              <div className="chat-chip-row" aria-disabled="true">
                {samplePrompts.map((p) => (
                  <button type="button" key={p} className="chat-chip" disabled title="Select an incident to ask this">{p}</button>
                ))}
              </div>
            )}
          </div>
        ) : messages.length === 0 ? (
          <div className="chat-empty">
            <Sparkles size={18} />
            <p>Ask a question grounded in what's on this screen right now.</p>
            {samplePrompts.length > 0 && (
              <div className="chat-chip-row">
                {samplePrompts.map((p) => (
                  <button type="button" key={p} className="chat-chip" onClick={() => onAsk(p)}>{p}</button>
                ))}
              </div>
            )}
          </div>
        ) : (
          messages.map((m) => (
            <div className="chat-message-group" key={m.id}>
              <div className="chat-bubble chat-bubble-user"><span>{m.question}</span></div>
              {m.answer === null ? (
                <div className="chat-bubble chat-bubble-assistant"><Sparkles size={14} /><div className="chat-typing"><span /><span /><span /></div></div>
              ) : (
                <div className="chat-bubble chat-bubble-assistant"><Sparkles size={14} /><span>{m.answer}</span></div>
              )}
            </div>
          ))
        )}
      </div>
      <div className="chat-composer">
        {!disabled && messages.length > 0 && samplePrompts.length > 0 && (
          <div className="chat-chip-row chat-chip-row-compact">
            {samplePrompts.map((p) => (
              <button type="button" key={p} className="chat-chip" disabled={asking} onClick={() => onAsk(p)}>{p}</button>
            ))}
          </div>
        )}
        <form
          className="chat-input-dock"
          onSubmit={(e) => {
            e.preventDefault();
            if (question.trim()) onAsk(question);
          }}
        >
          <input
            value={question}
            onChange={(e) => onQuestionChange(e.target.value)}
            placeholder={placeholder}
            aria-label={title}
            disabled={disabled}
          />
          <button type="submit" className="chat-send" disabled={disabled || asking || !question.trim()} aria-label="Send question"><ArrowRight size={15} /></button>
        </form>
      </div>
    </div>
  );
}

// --- Read-only code/SQL snippet -----------------------------------------
// Presentation-only: renders whatever code text the calling app already
// has, with a copy-to-clipboard affordance and an optional short badge
// (e.g. "Suggested -- not executed"). Never runs, validates, or interprets
// the code itself -- it's a text block, not an editor or a query client.
export function CodeBlock({ title, code, badge, actions }: { title?: string; code: string; badge?: string; actions?: ReactNode }) {
  return (
    <div className="code-block">
      <div className="code-block-head">
        <div className="code-block-head-text">
          {title && <span className="code-block-title">{title}</span>}
          {badge && <span className="code-block-badge">{badge}</span>}
        </div>
        <div className="code-block-head-actions">
          {actions}
          <button type="button" className="button ghost code-block-copy" onClick={() => navigator.clipboard.writeText(code)}>
            <Copy size={13} />Copy
          </button>
        </div>
      </div>
      <pre className="code-block-pre"><code>{code}</code></pre>
    </div>
  );
}

// --- Step-by-step debugging playbook workflow -----------------------------
// Presentation-only: renders a numbered investigation workflow from steps
// the calling app already computed (title + purpose + suggested SQL, e.g.
// from apps/data_quality_triage/sql_checks.py via the /playbook endpoint).
// Each step's SQL renders through CodeBlock with the same "Suggested -- not
// executed" badge, so nothing here ever implies a query has actually run.
export type PlaybookStepItem = { title: string; purpose: string; sql: string };

export function PlaybookWorkflow({
  steps,
  badge = "Suggested — not executed",
  onLoadStep,
}: {
  steps: PlaybookStepItem[];
  badge?: string;
  // When supplied, each step gets a "Load in sandbox" action next to Copy
  // -- the calling app owns what "loading" means (e.g. prefilling a SQL
  // sandbox textarea); this component never runs anything itself.
  onLoadStep?: (step: PlaybookStepItem) => void;
}) {
  if (!steps.length) return null;
  return (
    <ol className="playbook-workflow">
      {steps.map((step, i) => (
        <li className="playbook-step" key={step.title}>
          <div className="playbook-step-head">
            <span className="playbook-step-number">{i + 1}</span>
            <div className="playbook-step-headtext">
              <div className="playbook-step-title">{step.title}</div>
              <div className="playbook-step-purpose muted small">{step.purpose}</div>
            </div>
          </div>
          <CodeBlock
            code={step.sql}
            badge={badge}
            actions={onLoadStep && (
              <button type="button" className="button ghost code-block-load" onClick={() => onLoadStep(step)}>
                Load in sandbox
              </button>
            )}
          />
        </li>
      ))}
    </ol>
  );
}

// --- Lineage / downstream-impact chain -----------------------------------
// Presentation-only rendering of a source-table -> governed-metric ->
// downstream-asset chain. Every table/metric/asset name is supplied by the
// calling app from data it already fetched (e.g. the persisted metric
// catalog's downstream_dashboards); this component performs no lookups and
// invents nothing -- an empty `downstream` list just renders no arrow/asset
// for that metric, and an empty `metrics` list renders a quiet
// "no governed metrics" note rather than a fabricated chain segment.
export type LineageChainItem = { table: string; metrics: { name: string; downstream: string[] }[] };

export function LineageChain({ items, emptyLabel }: { items: LineageChainItem[]; emptyLabel?: string }) {
  if (!items.length) return emptyLabel ? <p className="muted small">{emptyLabel}</p> : null;
  return (
    <div className="lineage-chain">
      {items.map((item) => (
        <div className="lineage-row" key={item.table}>
          <span className="lineage-node lineage-node-table">{item.table}</span>
          {item.metrics.length === 0 ? (
            <span className="lineage-node lineage-node-empty muted small">No governed metrics on file</span>
          ) : (
            <div className="lineage-metrics">
              {item.metrics.map((m) => (
                <div className="lineage-metric-group" key={m.name}>
                  <ArrowRight size={13} className="lineage-arrow" />
                  <span className="lineage-node lineage-node-metric">{m.name}</span>
                  {m.downstream.length > 0 && (
                    <>
                      <ArrowRight size={13} className="lineage-arrow" />
                      <span className="lineage-node lineage-node-asset">{m.downstream.join(", ")}</span>
                    </>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// --- Impacted downstream assets --------------------------------------------
// Presentation-only: renders the same downstream-asset strings the calling
// app already has (e.g. incident.downstream_assets / playbook's
// affected_downstream_assets, sourced from the metric catalog's
// downstream_dashboards) as scannable grouped rows instead of one long chip
// row. This component performs no lookups and invents no assets -- it only
// classifies each existing string's *display kind* (dashboard/view/report/
// agent view) from keywords already present in the string, so the label text
// itself is shown verbatim/unchanged.
export type AssetImpactItem = { label: string; kind: "dashboard" | "view" | "report" | "agent-view" | "asset" };

function classifyDownstreamAsset(raw: string): AssetImpactItem {
  const lower = raw.toLowerCase();
  const isAgent = lower.includes("agent");
  const isDashboard = lower.includes("dashboard");
  const isReport = lower.includes("report");
  const isView = lower.includes("view");
  let kind: AssetImpactItem["kind"] = "asset";
  if (isDashboard) kind = "dashboard";
  else if (isAgent && isView) kind = "agent-view";
  else if (isReport) kind = "report";
  else if (isView) kind = "view";
  else if (isAgent) kind = "agent-view";
  return { label: raw, kind };
}

const ASSET_KIND_LABEL: Record<AssetImpactItem["kind"], string> = {
  dashboard: "Dashboard",
  view: "View",
  report: "Report",
  "agent-view": "Agent view",
  asset: "Asset",
};

export function AssetImpactList({ title = "Impacted downstream assets", items, emptyLabel }: { title?: string; items: string[]; emptyLabel?: string }) {
  if (!items.length) return emptyLabel ? <p className="muted small">{emptyLabel}</p> : null;
  const classified = items.map(classifyDownstreamAsset);
  return (
    <div className="asset-impact">
      <div className="asset-impact-title">{title}</div>
      <div className="asset-impact-list">
        {classified.map((it, i) => (
          <div className="asset-impact-row" key={`${it.label}-${i}`}>
            <span className={`asset-impact-kind asset-impact-kind-${it.kind}`}>{ASSET_KIND_LABEL[it.kind]}</span>
            <span className="asset-impact-label">{it.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// --- Definition-change risk -------------------------------------------------
// Presentation-only: renders the fixed set of definition-change-risk
// categories the calling app already computed (e.g. Governance's
// GovernanceReviewResponse.change_risk, derived deterministically in
// apps/metric_governance/remediation.py's derive_change_risk() from the
// SQL review's own findings plus the governed metric's metadata). This
// component never decides a category's status itself -- "aligned" /
// "risk" / "unknown" and the detail text are rendered exactly as given.
export type ChangeRiskCategory = { category: string; status: "aligned" | "risk" | "unknown"; detail: string };

export function ChangeRiskList({ items, emptyLabel }: { items: ChangeRiskCategory[]; emptyLabel?: string }) {
  if (!items.length) return emptyLabel ? <p className="muted small">{emptyLabel}</p> : null;
  return (
    <div className="change-risk-list">
      {items.map((it) => (
        <div className={`change-risk-row change-risk-${it.status}`} key={it.category}>
          <div className="change-risk-head">
            <span className="change-risk-category">{it.category}</span>
            <span className={`change-risk-pill change-risk-pill-${it.status}`}>{it.status}</span>
          </div>
          <div className="change-risk-detail muted small">{it.detail}</div>
        </div>
      ))}
    </div>
  );
}

// --- Governance recommendations ---------------------------------------------
// Presentation-only: renders the deterministic recommendation cards the
// calling app already computed (e.g. Governance's
// GovernanceReviewResponse.recommendations, derived in
// apps/metric_governance/remediation.py's derive_governance_recommendations()
// from the trust score, findings, change risk, and metric metadata already
// on screen). Ask Loupe may narrate *why* these matter, but this component
// -- and the deterministic function behind it -- is what puts them on
// screen; nothing here is only visible inside a chat transcript.
export type GovernanceRecommendationItem = { action: string; rationale: string; priority: "info" | "required" | "blocking" };

export function RecommendationCards({ title, items, emptyLabel }: { title?: string; items: GovernanceRecommendationItem[]; emptyLabel?: string }) {
  if (!items.length) return emptyLabel ? <p className="muted small">{emptyLabel}</p> : null;
  return (
    <div className="recommendation-cards">
      {title && <div className="recommendation-cards-title">{title}</div>}
      <div className="recommendation-cards-list">
        {items.map((it, i) => (
          <div className={`recommendation-card recommendation-card-${it.priority}`} key={`${it.action}-${i}`}>
            <div className="recommendation-card-head">
              <span className="recommendation-card-priority">{it.priority}</span>
              <span className="recommendation-card-action">{it.action}</span>
            </div>
            <div className="recommendation-card-rationale">{it.rationale}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// --- Simple selectable list --------------------------------------------------
// Generic presentation-only selector row list -- a name, an optional muted
// meta string, and a selected state -- for any calling app that needs "pick
// one of these, see its detail elsewhere on the page" (e.g. Governance's
// Catalog tab metric picker). Not tied to any one app's domain; the calling
// app owns what "selected" means and what happens on click.
export type SimpleListItem = { id: string; name: string; meta?: string };

export function SimpleList({ items, selectedId, onSelect }: { items: SimpleListItem[]; selectedId?: string | null; onSelect: (id: string) => void }) {
  if (!items.length) return null;
  return (
    <div className="simple-list">
      {items.map((it) => (
        <button
          type="button"
          key={it.id}
          className={`simple-list-row${selectedId === it.id ? " selected" : ""}`}
          onClick={() => onSelect(it.id)}
        >
          <span className="simple-list-row-name">{it.name}</span>
          {it.meta && <span className="simple-list-row-meta">{it.meta}</span>}
        </button>
      ))}
    </div>
  );
}

// --- Audit trail ----------------------------------------------------------
// Presentation-only vertical trail of named steps -- deterministic facts
// (metadata loaded, check evaluated, incident generated) and, when the
// calling app appends them from a real response it received, AI-activity
// steps (playbook generated, helper question asked) with the model that
// produced them. This component never decides what happened; it only
// renders the ordered list of steps it's given.
export type AuditTrailItem = { step: string; description: string; timestamp?: string | null; source?: string | null };

export function AuditTrailList({ items, emptyLabel }: { items: AuditTrailItem[]; emptyLabel?: string }) {
  if (!items.length) return emptyLabel ? <p className="muted small">{emptyLabel}</p> : null;
  return (
    <div className="audit-trail">
      {items.map((item, i) => (
        <div className="audit-trail-row" key={`${item.step}-${i}`}>
          <span className="audit-trail-dot" />
          <div className="audit-trail-body">
            <div className="audit-trail-head">
              <span className="audit-trail-step">{item.step.replaceAll("_", " ")}</span>
              {item.timestamp && <span className="audit-trail-time muted small">{item.timestamp}</span>}
            </div>
            <div className="audit-trail-desc muted small">{item.description}</div>
            {item.source && <div className="audit-trail-source muted small">Source: {item.source}</div>}
          </div>
        </div>
      ))}
    </div>
  );
}

// --- Debugging SQL sandbox -------------------------------------------------
// Presentation-only: a read-only SQL editor + result table. This component
// never talks to a backend, never decides whether SQL is safe, and never
// claims a query ran -- the calling app owns fetching
// POST /api/v1/triage/sql-sandbox (whose deterministic, non-AI safety
// validation lives in apps/data_quality_triage/sql_sandbox.py) and passes
// back exactly the `status`/`error`/`rows` it received. `result` is only
// ever rendered as-is: a null result renders nothing, "rejected"/"error"
// render the error text (never a row table), and only "success" renders
// rows -- so this component cannot itself misrepresent a failed run as one
// that returned data.
export type SqlSandboxResult = {
  status: "success" | "rejected" | "error";
  columns: string[];
  rows: Record<string, unknown>[];
  row_count: number;
  bytes_processed: number | null;
  error: string | null;
  row_limit: number;
};

function formatSandboxCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

export function SqlSandbox({
  sql,
  onSqlChange,
  onRun,
  running,
  result,
  onClear,
  checkTitle,
  rowLimit,
}: {
  sql: string;
  onSqlChange: (value: string) => void;
  onRun: () => void;
  running: boolean;
  result: SqlSandboxResult | null;
  onClear: () => void;
  checkTitle?: string | null;
  rowLimit: number;
}) {
  return (
    <div className="sql-sandbox">
      <div className="sql-sandbox-head">
        <div>
          <div className="sql-sandbox-title">Debugging SQL sandbox</div>
          <p className="muted small sql-sandbox-helper-copy">
            Read-only checks only. Suggested queries are not executed until you run them.
          </p>
        </div>
        {checkTitle && <span className="code-block-badge">{checkTitle}</span>}
      </div>
      <textarea
        className="sql-sandbox-editor"
        value={sql}
        onChange={(e) => onSqlChange(e.target.value)}
        placeholder="Click “Load in sandbox” on a suggested check above, or write your own read-only SELECT/WITH query…"
        spellCheck={false}
        aria-label="Debugging SQL sandbox editor"
      />
      <div className="sql-sandbox-actions">
        <button type="button" className="button primary" onClick={onRun} disabled={running || !sql.trim()}>
          {running ? "Running…" : "Run check"}
        </button>
        <button type="button" className="button ghost" onClick={onClear} disabled={running}>Clear</button>
        <span className="muted small">Capped at {rowLimit} rows · suggested SQL only, never executed automatically.</span>
      </div>
      {result && (
        <div className={`sql-sandbox-result sql-sandbox-result-${result.status}`}>
          {result.status === "rejected" && (
            <div className="sql-sandbox-status sql-sandbox-status-rejected">
              <strong>Rejected — unsafe or invalid SQL</strong>
              <p className="muted small">{result.error}</p>
            </div>
          )}
          {result.status === "error" && (
            <div className="sql-sandbox-status sql-sandbox-status-error">
              <strong>Query failed</strong>
              <p className="muted small">{result.error}</p>
            </div>
          )}
          {result.status === "success" && (
            <>
              <div className="sql-sandbox-status sql-sandbox-status-success">
                <strong>{result.row_count} row{result.row_count === 1 ? "" : "s"} returned</strong>
                <span className="muted small">
                  {" "}capped at {result.row_limit}
                  {result.bytes_processed !== null ? ` · ~${Math.max(1, Math.round(result.bytes_processed / 1_000_000))} MB scanned` : ""}
                </span>
              </div>
              {result.columns.length > 0 ? (
                <div className="table-wrap sql-sandbox-table-wrap">
                  <table className="data-table">
                    <thead><tr>{result.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
                    <tbody>
                      {result.rows.map((row, i) => (
                        <tr key={i}>{result.columns.map((c) => <td key={c}>{formatSandboxCell(row[c])}</td>)}</tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="muted small">Query ran successfully but returned no rows.</p>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
