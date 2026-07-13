import type { ComponentType, ReactNode } from "react";
import { Activity, CheckCircle2, ShieldCheck, Sparkles, TrendingDown, TrendingUp } from "lucide-react";
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
