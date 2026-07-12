"use client";

import type { ComponentType } from "react";
import { useEffect, useRef, useState } from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import {
  ArrowRight, ChartNoAxesCombined, Database, Download, Home as HomeIcon, LayoutDashboard,
  ListChecks, MapPin, Megaphone, MessageSquareText, ScanSearch, Shirt, SlidersHorizontal, Sparkles,
  TriangleAlert, TrendingUp,
} from "lucide-react";
import { AppShell, Badge, Card, Unavailable } from "@loupe/ui";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

type MetricValue = { value: number; change_pct: number | null };
type Overview = {
  start_date: string; end_date: string; data_source: string; insight: string;
  revenue: MetricValue; gross_margin_pct: MetricValue; order_items: MetricValue; return_rate_pct: MetricValue;
  trend: { period: string; revenue: number; margin: number; items: number }[];
  source_health: { status: string; warning: string | null };
  metric_context: { certification_status: string; version: string | null; reporting_grain: string };
};
type CategoryRow = { category: string; revenue: number; margin: number; items: number; return_rate_pct: number };
type StateRow = { state: string; state_abbrev: string; revenue: number; margin: number; items: number };
type ChannelMonthRow = { month: string; paid: number; unpaid: number; total: number; paid_share_pct: number };
type LeakageRow = { category: string; returned_items: number; total_items: number; return_rate_pct: number; margin_lost_to_returns: number };
type Benchmark = { avg_margin_pct: number; avg_return_rate_pct: number };
type AskResponse = { category: string; answer: string; source_health_status: string | null; source_health_warning: string | null; raw_data: unknown };

type LoupeView = "home" | "dashboard" | "ask" | "performance";

// Same 26 categories apps/loupe_agent/metrics.py::ALL_CATEGORIES declares -- used
// only as filter option labels, never as invented data.
const ALL_CATEGORIES = [
  "Accessories", "Active", "Blazers & Jackets", "Clothing Sets", "Dresses",
  "Fashion Hoodies & Sweatshirts", "Intimates", "Jeans", "Jumpsuits & Rompers",
  "Leggings", "Maternity", "Outerwear & Coats", "Pants", "Pants & Capris",
  "Plus", "Shorts", "Skirts", "Sleep & Lounge", "Socks", "Socks & Hosiery",
  "Suits", "Suits & Sport Coats", "Sweaters", "Swim", "Tops & Tees", "Underwear",
];
// Same location names the /states endpoint groups by. Labeled "Region" in the
// UI because the warehouse's location dimension includes non-US values
// (e.g. Guangdong, England, Shanghai, Sao Paulo), not only US states.
const ALL_REGIONS = [
  "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut", "Delaware",
  "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky",
  "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi", "Missouri",
  "Montana", "Nebraska", "Nevada", "New Hampshire", "New Jersey", "New Mexico", "New York",
  "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island",
  "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
  "West Virginia", "Wisconsin", "Wyoming", "District of Columbia",
];

const samplePrompts = [
  "How is Dresses performing?",
  "Compare California, Texas, and New York.",
  "What if we cut the return rate in Swim by 5 points?",
  "Which categories are losing the most money to returns?",
  "How has paid vs organic channel mix changed?",
];

const money = (n: number) => new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 2 }).format(n);
const number = (n: number) => new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(n);
const delta = (n: number | null, suffix = "%") => (n === null ? "Prior period unavailable" : `${n >= 0 ? "+" : ""}${n.toFixed(1)}${suffix}`);
const ASSISTANT_UNAVAILABLE = "Assistant reasoning is not configured in this deployment. The governed metrics above remain live from BigQuery.";
const looksUnconfigured = (text: string) => /ANTHROPIC_API_KEY|isn't configured in this environment|isn't installed in this environment/i.test(text);
const trendWord = (n: number | null, suffix = "%") => { if (n === null) return "has no prior-period comparison available"; if (n > 0) return `rose ${n.toFixed(1)}${suffix}`; if (n < 0) return `fell ${Math.abs(n).toFixed(1)}${suffix}`; return "held flat" };
const framing = (d: Overview) => `Revenue ${trendWord(d.revenue.change_pct)}, gross margin ${trendWord(d.gross_margin_pct.change_pct, " pts")}, order item volume ${trendWord(d.order_items.change_pct)}, and the return rate ${trendWord(d.return_rate_pct.change_pct, " pts")} versus the prior period.`;
const formatDateShort = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric" });
const formatPeriod = (period: string) => { const dt = new Date(`${period}-01T00:00:00`); return Number.isNaN(dt.getTime()) ? period : dt.toLocaleDateString("en-US", { month: "short", year: "2-digit" }) };

// Ported thresholds from the original Streamlit app's return_rate_pill():
// >20% Risk, >=10% Watch, else Healthy.
function returnRatePill(rate: number | null | undefined) {
  const r = Number(rate ?? 0);
  if (r > 20) return { label: "Risk", cls: "pill-risk" };
  if (r >= 10) return { label: "Watch", cls: "pill-watch" };
  return { label: "Healthy", cls: "pill-healthy" };
}

function downloadCsv(filename: string, rows: Record<string, unknown>[]) {
  if (!rows.length) return;
  const headers = Object.keys(rows[0]);
  const csv = [headers.join(","), ...rows.map((r) => headers.map((h) => JSON.stringify(r[h] ?? "")).join(","))].join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

function useClickOutside(ref: React.RefObject<HTMLElement | null>, onOutside: () => void) {
  useEffect(() => {
    function handler(e: MouseEvent) {
      if (ref.current && e.target instanceof Node && !ref.current.contains(e.target)) onOutside();
    }
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [ref, onOutside]);
}

// Compact checklist dropdown replacing the native <select multiple> box --
// same filter state/behavior, presented as a single trigger + panel instead
// of a giant always-open list.
function MultiSelectDropdown({ label, options, selected, onChange }: { label: string; options: string[]; selected: string[]; onChange: (v: string[]) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useClickOutside(ref, () => setOpen(false));
  return (
    <div className="msd" ref={ref}>
      <span className="filter-label">{label}</span>
      <button type="button" className="msd-trigger" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span>{selected.length ? `${selected.length} selected` : `All ${label.toLowerCase()}s`}</span>
        <span className="msd-caret">{open ? "▲" : "▼"}</span>
      </button>
      {open && <div className="msd-panel">
        <div className="msd-panel-actions">
          <button type="button" className="msd-link" onClick={() => onChange([])}>Clear</button>
          <button type="button" className="msd-link" onClick={() => setOpen(false)}>Done</button>
        </div>
        <div className="msd-list">
          {options.map((o) => (
            <label key={o} className="msd-item">
              <input type="checkbox" checked={selected.includes(o)} onChange={(e) => onChange(e.target.checked ? [...selected, o] : selected.filter((x) => x !== o))} />
              <span>{o}</span>
            </label>
          ))}
        </div>
      </div>}
    </div>
  );
}

function inlineMd(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((p, i) => (p.startsWith("**") && p.endsWith("**") ? <strong key={i}>{p.slice(2, -2)}</strong> : <span key={i}>{p}</span>));
}

// Minimal markdown renderer covering exactly what apps/loupe_agent/chat.py's
// prompts instruct the model to produce: ## / ### headers, **bold**, "- "
// bullet lists, and pipe tables with a --- separator row. Standalone rule
// lines ("---", "___", "***") are dropped entirely rather than rendered as
// literal text. No external markdown dependency is added.
const HR_RE = /^\s*([-*_])\1{2,}\s*$/;
function stripRules(text: string) {
  return text.split("\n").filter((l) => !HR_RE.test(l)).join("\n");
}
function renderMarkdown(text: string) {
  const blocks = stripRules(text).split(/\n\s*\n/);
  return (
    <>
      {blocks.map((block, i) => {
        const lines = block.split("\n").filter((l) => l.trim().length);
        if (!lines.length) return null;
        if (lines.length >= 2 && lines[0].includes("|") && /^[\s|:-]+$/.test(lines[1])) {
          const parseRow = (l: string) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
          const header = parseRow(lines[0]);
          const bodyRows = lines.slice(2).map(parseRow);
          return (
            <div className="table-wrap" key={i}>
              <table className="data-table">
                <thead><tr>{header.map((h, hi) => <th key={hi}>{inlineMd(h)}</th>)}</tr></thead>
                <tbody>{bodyRows.map((r, ri) => <tr key={ri}>{r.map((c, ci) => <td key={ci}>{inlineMd(c)}</td>)}</tr>)}</tbody>
              </table>
            </div>
          );
        }
        if (lines[0].startsWith("#### ")) return <h4 key={i}>{inlineMd(lines[0].slice(5))}</h4>;
        if (lines[0].startsWith("### ")) return <h4 key={i}>{inlineMd(lines[0].slice(4))}</h4>;
        if (lines[0].startsWith("## ")) return <h4 key={i}>{inlineMd(lines[0].slice(3))}</h4>;
        if (lines.every((l) => l.trim().startsWith("- ") || l.trim().startsWith("* "))) {
          return <ul key={i}>{lines.map((l, li) => <li key={li}>{inlineMd(l.trim().replace(/^[-*]\s+/, ""))}</li>)}</ul>;
        }
        return <p key={i}>{lines.map((l, li) => <span key={li}>{inlineMd(l)}{li < lines.length - 1 ? <br /> : null}</span>)}</p>;
      })}
    </>
  );
}

// --- Insight-brief parsing -------------------------------------------------
// Groups an Ask Loupe answer's markdown headers into named sections the
// task asked for (Executive Takeaway, Baseline, Scenario/Hypothetical, Pro
// Forma Summary, Caveats, Recommendations). Matching is a case-insensitive
// keyword test against whatever headers the model actually produced (see
// apps/loupe_agent/chat.py's system prompts for the real header text this
// has to cover, e.g. "## [Category] Performance Summary" / "### Key
// Highlights:" / "### Recommendation:"). Unmatched headers fall back into a
// generic "Additional Detail" bucket rather than being dropped.
type ParsedBlock = { header: string | null; body: string };
function splitByHeaders(text: string): ParsedBlock[] {
  const lines = text.split("\n");
  const blocks: ParsedBlock[] = [];
  let header: string | null = null;
  let bodyLines: string[] = [];
  const flush = () => { blocks.push({ header, body: bodyLines.join("\n").trim() }); bodyLines = [] };
  for (const line of lines) {
    const m = line.match(/^#{2,4}\s+(.*)$/);
    if (m) { flush(); header = m[1].trim().replace(/:$/, "") } else bodyLines.push(line);
  }
  flush();
  return blocks.filter((b) => b.header || b.body.trim());
}
const SECTION_MATCHERS: { key: string; test: (h: string) => boolean }[] = [
  { key: "takeaway", test: (h) => !/pro.?forma/i.test(h) && /takeaway|performance summary|^summary$|overview/i.test(h) },
  { key: "baseline", test: (h) => /baseline|current (state|performance)/i.test(h) },
  { key: "scenario", test: (h) => /scenario|hypothetical|what.?if/i.test(h) },
  { key: "proforma", test: (h) => /pro.?forma|projected|projection|retained (revenue|margin)/i.test(h) },
  { key: "highlights", test: (h) => /highlight|key (finding|metric)/i.test(h) },
  { key: "caveats", test: (h) => /caveat|assumption|limitation|validation/i.test(h) },
  { key: "recommendations", test: (h) => /recommend|next step|action/i.test(h) },
];
function classify(header: string | null) {
  if (!header) return null;
  return SECTION_MATCHERS.find((m) => m.test(header))?.key ?? null;
}
function firstLine(s: string) { return s.split("\n").find((l) => l.trim())?.trim() ?? "" }
function buildInsight(answerText: string) {
  const blocks = splitByHeaders(stripRules(answerText));
  const buckets: Record<string, string[]> = {};
  let leadIn = "";
  blocks.forEach((b, idx) => {
    if (!b.header) { if (idx === 0) leadIn = b.body; return }
    const key = classify(b.header);
    const bucketKey = key ?? "details";
    if (!buckets[bucketKey]) buckets[bucketKey] = [];
    buckets[bucketKey].push(key ? b.body : `#### ${b.header}\n${b.body}`);
  });
  const headline = firstLine(buckets.takeaway?.[0] ?? leadIn) || firstLine(stripRules(answerText).replace(/^#{2,4}\s+.*/gm, "")) || "Grounded answer below.";
  return { headline, buckets };
}
const BODY_SECTIONS: { key: string; title: string; icon: ComponentType<{ size?: number }> }[] = [
  { key: "baseline", title: "Baseline", icon: Database },
  { key: "scenario", title: "Scenario / Hypothetical", icon: Sparkles },
  { key: "proforma", title: "Pro Forma Summary", icon: TrendingUp },
  { key: "highlights", title: "Key Highlights", icon: ChartNoAxesCombined },
  { key: "details", title: "Additional Detail", icon: MessageSquareText },
];

// Literal reporting-scope text mirrored from apps/loupe_agent/chat.py's own
// reporting_note() calls per intent (not invented -- restated for display
// since the API doesn't return a separate scope field).
function reportingScopeFor(category: string, raw: unknown): string {
  if (category === "single_category" || category === "single_state") return "One row for this entity, aggregated across all matching order items · all-time (no date filter).";
  if (category === "multi_category_comparison" || category === "multi_state_comparison") return "One row per requested entity, aggregated across all matching order items · all-time (no date filter).";
  if (category === "channel_analysis") return "One row per month per channel group (paid/unpaid) · trailing 24 months.";
  if (category === "returns_leakage") return "One row per category, ranked by margin dollars lost to returns · all-time (no date filter).";
  if (category === "scenario_simulation") {
    const lever = raw && typeof raw === "object" ? (raw as Record<string, unknown>).lever : null;
    if (lever === "channel_mix_shift") return "Baseline: one row per month per channel group · trailing 24 months.";
    if (lever === "category_price_position" || lever === "return_rate_improvement") return "Baseline: one row for the category, aggregated across all matching order items · all-time.";
    return "Baseline scope varies by scenario lever.";
  }
  return "";
}

// Renders whichever raw_data shape the matched Ask Loupe intent produced
// (see apps/loupe_agent/chat.py's handlers) -- entirely data-driven, no
// hardcoded numbers, degrades to nothing for shapes it doesn't recognize.
function AskEvidence({ data }: { data: unknown }) {
  if (data == null || typeof data !== "object") return null;
  if (Array.isArray(data)) {
    if (!data.length) return null;
    const rows = data as Record<string, number | string>[];
    if ("margin_lost_to_returns" in rows[0]) {
      const top = [...rows].sort((a, b) => Number(b.margin_lost_to_returns) - Number(a.margin_lost_to_returns)).slice(0, 10);
      return (
        <div className="chart-frame" style={{ height: 260 }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={top} layout="vertical" margin={{ left: 90 }}>
              <CartesianGrid stroke="#e5e5e7" horizontal={false} />
              <XAxis type="number" tickFormatter={(v) => money(Number(v))} tick={{ fontSize: 11 }} />
              <YAxis type="category" dataKey="category" width={110} tick={{ fontSize: 12 }} />
              <Tooltip formatter={(v) => money(Number(v))} />
              <Bar dataKey="margin_lost_to_returns" fill="#c0362c" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      );
    }
    const label = (row: Record<string, unknown>) => String(row.category ?? row.state ?? "");
    return (
      <>
        <div className="metric-grid">
          {rows.map((row, i) => {
            const pill = returnRatePill(Number(row.return_rate_pct));
            return (
              <Card key={i}>
                <div className="stat-label">{label(row)}</div>
                <div className="stat-value-sm">{money(Number(row.revenue))}</div>
                <div className="muted small">{row.return_rate_pct}% return <span className={`pill ${pill.cls}`}>{pill.label}</span></div>
              </Card>
            );
          })}
        </div>
        <div className="chart-frame" style={{ height: 220, marginTop: 16 }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={rows.map((r) => ({ ...r, label: label(r) }))}>
              <CartesianGrid stroke="#e5e5e7" vertical={false} />
              <XAxis dataKey="label" tick={{ fontSize: 11 }} />
              <YAxis hide />
              <Tooltip formatter={(v) => money(Number(v))} />
              <Bar dataKey="margin" fill="#2995ff" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </>
    );
  }
  const obj = data as Record<string, unknown>;
  if (Array.isArray(obj.months)) return <ChannelChart months={obj.months as ChannelMonthRow[]} compact />;
  if ("revenue" in obj && "return_rate_pct" in obj) {
    const pill = returnRatePill(Number(obj.return_rate_pct));
    return (
      <div className="metric-grid">
        <Card><div className="stat-label">{String(obj.category ?? obj.state ?? "Entity")}</div><div className="stat-value-sm">{money(Number(obj.revenue))}</div></Card>
        <Card><div className="stat-label">Margin</div><div className="stat-value-sm">{money(Number(obj.margin))}</div></Card>
        <Card><div className="stat-label">Return rate</div><div className="stat-value-sm">{String(obj.return_rate_pct)}% <span className={`pill ${pill.cls}`}>{pill.label}</span></div></Card>
      </div>
    );
  }
  const entries = Object.entries(obj).filter(([, v]) => typeof v !== "object");
  if (!entries.length) return null;
  return <div className="muted small evidence-kv">{entries.map(([k, v]) => <div key={k}><strong>{k.replaceAll("_", " ")}: </strong>{String(v)}</div>)}</div>;
}

// Shared paid-vs-organic renderer for both the Dashboard's channel-mix card
// and Ask Loupe's channel_analysis evidence. Falls back to a snapshot (not
// a trend line) when only one month of data is present, per requirement.
function ChannelChart({ months, compact = false }: { months: ChannelMonthRow[]; compact?: boolean }) {
  if (!months.length) return <div className="muted small">No channel data available.</div>;
  if (months.length === 1) return <ChannelSnapshot month={months[0]} />;
  const latest = months[months.length - 1];
  const prior = months[months.length - 2];
  const shift = latest.paid_share_pct - prior.paid_share_pct;
  const dir = shift > 0 ? "up" : shift < 0 ? "down" : "flat";
  return (
    <>
      <div className="chart-frame" style={compact ? { height: 220 } : undefined}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={months}>
            <CartesianGrid stroke="#e5e5e7" vertical={false} />
            <XAxis dataKey="month" tickFormatter={formatPeriod} tick={{ fill: "#85868b", fontSize: 11 }} />
            <YAxis hide />
            <Tooltip labelFormatter={(l) => formatPeriod(String(l))} />
            {!compact && <Legend verticalAlign="top" height={28} wrapperStyle={{ fontSize: 12 }} />}
            <Area type="monotone" name="Paid" dataKey="paid" stackId="1" stroke="#2995ff" fill="#2995ff" fillOpacity={0.5} />
            <Area type="monotone" name="Unpaid (organic/search)" dataKey="unpaid" stackId="1" stroke="#c7d2fe" fill="#c7d2fe" fillOpacity={0.5} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div className="chart-stats muted small">Latest paid share: {latest.paid_share_pct.toFixed(1)}% (organic {(100 - latest.paid_share_pct).toFixed(1)}%) · {dir} {Math.abs(shift).toFixed(1)} pts vs. prior month.</div>
    </>
  );
}
function ChannelSnapshot({ month }: { month: ChannelMonthRow }) {
  const paidPct = month.paid_share_pct;
  const organicPct = Math.max(0, 100 - paidPct);
  return (
    <div className="channel-snapshot">
      <div className="channel-snapshot-bar"><span className="channel-seg-paid" style={{ width: `${paidPct}%` }} /><span className="channel-seg-organic" style={{ width: `${organicPct}%` }} /></div>
      <div className="channel-snapshot-legend">
        <span><i className="dot dot-paid" />Paid {paidPct.toFixed(1)}%</span>
        <span><i className="dot dot-organic" />Organic {organicPct.toFixed(1)}%</span>
      </div>
      <p className="muted small">Only one month of data is available in this window, so this is shown as a current mix snapshot rather than a trend.</p>
    </div>
  );
}

// Compact, always-available Ask Loupe entry point for non-Ask tabs.
function QuickAsk({ subtitle, prompts, onAsk, disabled }: { subtitle: string; prompts: string[]; onAsk: (q: string) => void; disabled: boolean }) {
  return (
    <Card className="quick-ask">
      <div className="card-head"><div><h2><Sparkles size={16} style={{ verticalAlign: "-3px", marginRight: 6 }} />Ask Loupe</h2><div className="muted small">{subtitle}</div></div></div>
      <div className="actions">{prompts.map((p) => <button type="button" key={p} className="button" disabled={disabled} onClick={() => onAsk(p)}>{p}</button>)}</div>
    </Card>
  );
}

function Stat({ label, value, change }: { label: string; value: string; change: string }) {
  return <Card><div className="stat-label">{label}</div><div className="stat-line"><span className="stat-value">{value}</span><span className="delta">{change}</span></div></Card>;
}

export default function Page() {
  const [activeView, setActiveView] = useState<LoupeView>("home");

  const defaultEnd = new Date();
  const defaultStart = new Date(defaultEnd);
  defaultStart.setDate(defaultEnd.getDate() - 29);
  const [startDate, setStartDate] = useState(defaultStart.toISOString().slice(0, 10));
  const [endDate, setEndDate] = useState(defaultEnd.toISOString().slice(0, 10));
  const [selectedCategories, setSelectedCategories] = useState<string[]>([]);
  const [selectedStates, setSelectedStates] = useState<string[]>([]);
  const [sortMetric, setSortMetric] = useState<"revenue" | "margin" | "return_rate_pct">("revenue");

  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [categoryRows, setCategoryRows] = useState<CategoryRow[] | null>(null);
  const [stateRows, setStateRows] = useState<StateRow[] | null>(null);
  const [channelMonths, setChannelMonths] = useState<ChannelMonthRow[] | null>(null);
  const [leakageRows, setLeakageRows] = useState<LeakageRow[] | null>(null);
  const [benchmark, setBenchmark] = useState<Benchmark | null>(null);

  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [asking, setAsking] = useState(false);

  useEffect(() => {
    const q = new URLSearchParams({ start_date: startDate, end_date: endDate });
    selectedCategories.forEach((c) => q.append("categories", c));
    selectedStates.forEach((s) => q.append("states", s));
    fetch(`${API_BASE}/api/v1/loupe/overview?${q}`)
      .then(async (r) => { if (!r.ok) throw new Error(); return r.json() })
      .then(setData)
      .catch(() => setError("The governed warehouse could not be reached. No placeholder metrics were substituted."));
  }, [startDate, endDate, selectedCategories, selectedStates]);

  useEffect(() => {
    const catParams = new URLSearchParams({ start_date: startDate, end_date: endDate });
    selectedStates.forEach((s) => catParams.append("states", s));
    fetch(`${API_BASE}/api/v1/loupe/categories?${catParams}`).then((r) => (r.ok ? r.json() : null)).then((d) => setCategoryRows(d?.categories ?? null)).catch(() => setCategoryRows(null));

    const stateParams = new URLSearchParams({ start_date: startDate, end_date: endDate });
    selectedCategories.forEach((c) => stateParams.append("categories", c));
    fetch(`${API_BASE}/api/v1/loupe/states?${stateParams}`).then((r) => (r.ok ? r.json() : null)).then((d) => setStateRows(d?.states ?? null)).catch(() => setStateRows(null));

    const channelParams = new URLSearchParams({ start_date: startDate, end_date: endDate });
    selectedCategories.forEach((c) => channelParams.append("categories", c));
    selectedStates.forEach((s) => channelParams.append("states", s));
    fetch(`${API_BASE}/api/v1/loupe/channel-mix?${channelParams}`).then((r) => (r.ok ? r.json() : null)).then((d) => setChannelMonths(d?.months ?? null)).catch(() => setChannelMonths(null));
  }, [startDate, endDate, selectedCategories, selectedStates]);

  useEffect(() => {
    fetch(`${API_BASE}/api/v1/loupe/returns-leakage`).then((r) => (r.ok ? r.json() : null)).then((d) => setLeakageRows(d?.categories ?? null)).catch(() => setLeakageRows(null));
    fetch(`${API_BASE}/api/v1/loupe/benchmark`).then((r) => (r.ok ? r.json() : null)).then(setBenchmark).catch(() => setBenchmark(null));
  }, []);

  // Shared by the main Ask Loupe form and every QuickAsk widget -- same
  // fetch call (method/headers/body) the app has always used for
  // /api/v1/loupe/ask; only the caller (typed question vs. a quick prompt)
  // differs.
  async function askQuestion(q: string) {
    setQuestion(q);
    setActiveView("ask");
    setAsking(true); setAnswer(null);
    try {
      const response = await fetch(`${API_BASE}/api/v1/loupe/ask`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: q }) });
      const body = await response.json();
      if (response.ok) setAnswer({ ...body, answer: looksUnconfigured(body.answer ?? "") ? ASSISTANT_UNAVAILABLE : body.answer });
      else setAnswer({ category: "general", answer: body.detail, source_health_status: null, source_health_warning: null, raw_data: null });
    } catch { setAnswer({ category: "general", answer: "Loupe could not be reached.", source_health_status: null, source_health_warning: null, raw_data: null }) }
    finally { setAsking(false) }
  }

  const nav = [
    { label: "Home", icon: HomeIcon, active: activeView === "home", onClick: () => setActiveView("home") },
    { label: "Dashboard", icon: LayoutDashboard, active: activeView === "dashboard", onClick: () => setActiveView("dashboard") },
    { label: "Ask Loupe", icon: MessageSquareText, active: activeView === "ask", onClick: () => setActiveView("ask") },
    { label: "Performance", icon: ChartNoAxesCombined, active: activeView === "performance", onClick: () => setActiveView("performance") },
  ];

  const sortedCategoryRows = categoryRows ? [...categoryRows].sort((a, b) => b[sortMetric] - a[sortMetric]).slice(0, 15) : null;
  const rankedStateRows = stateRows ? [...stateRows].sort((a, b) => b.revenue - a.revenue).slice(0, 15) : null;
  const maxStateRevenue = rankedStateRows?.length ? Math.max(...rankedStateRows.map((s) => s.revenue)) : 0;
  const activeFilterCount = selectedCategories.length + selectedStates.length;

  return (
    <AppShell active="loupe" brand="Loupe" brandIcon={ScanSearch} navigation={nav}>
      <div className="dashboard-surface">
        <header className="hero-panel page-header">
          <div><div className="eyebrow">ASSISTANT LAYER</div><h1>Commerce intelligence</h1><div className="muted">Live performance from governed warehouse data</div></div>
          <div className="actions"><Badge><Database size={15} />{data?.data_source ?? "BigQuery"}</Badge></div>
        </header>

        {error ? <Unavailable message={error} /> : !data ? <div className="card skeleton" aria-label="Loading live commerce intelligence" /> : <>

          {activeView === "home" && <>
            <section><div className="metric-grid">
              <Stat label="Net revenue" value={money(data.revenue.value)} change={delta(data.revenue.change_pct)} />
              <Stat label="Gross margin" value={`${data.gross_margin_pct.value.toFixed(1)}%`} change={delta(data.gross_margin_pct.change_pct, " pts")} />
              <Stat label="Order items" value={number(data.order_items.value)} change={delta(data.order_items.change_pct)} />
              <Stat label="Return rate" value={`${data.return_rate_pct.value.toFixed(1)}%`} change={delta(data.return_rate_pct.change_pct, " pts")} />
            </div></section>
            <section><div className="insight-grid">
              <Card>
                <div className="card-head"><div><h2>See your business clearly</h2><div className="muted small">The Look &middot; E-commerce analytics</div></div><Sparkles size={18} /></div>
                <p>Loupe turns raw order, product, and traffic data into a live, queryable analytics agent, plus a full interactive dashboard, built on real e-commerce data. Every number on this page is a live BigQuery query &mdash; nothing here is sample or fabricated data.</p>
                <div className="actions">
                  <button className="button primary" onClick={() => setActiveView("ask")}><Sparkles size={15} />Ask the Agent</button>
                  <button className="button" onClick={() => setActiveView("dashboard")}><LayoutDashboard size={15} />Explore the Dashboard</button>
                </div>
              </Card>
              <Card>
                <div className="card-head"><div><h2>Source &amp; trust</h2><div className="muted small">Where these numbers come from</div></div><Badge tone={data.source_health.status === "healthy" ? "accent" : "warning"}>{data.source_health.status}</Badge></div>
                <div className="muted small"><strong>Certification: </strong>{data.metric_context.certification_status}</div>
                <div className="muted small"><strong>Version: </strong>{data.metric_context.version ?? "version unavailable"}</div>
                <div className="muted small"><strong>Reporting grain: </strong>{data.metric_context.reporting_grain}</div>
                {data.source_health.warning && <div className="health-warning">{data.source_health.warning}</div>}
              </Card>
            </div></section>
            <section><QuickAsk subtitle="What should I look at first?" disabled={asking} onAsk={askQuestion} prompts={["What should I look at first?", "Which categories are losing the most money to returns?", "How is Dresses performing?"]} /></section>
          </>}

          {activeView === "dashboard" && <>
            <section>
              <div className="section-title"><SlidersHorizontal size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />Filters</div>
              <Card>
                <div className="filters-row">
                  <div className="filter-field">
                    <span className="filter-label">Date range</span>
                    <div className="date-range-control">
                      <input type="date" className="date-input" value={startDate} max={endDate} onChange={(e) => setStartDate(e.target.value)} aria-label="Start date" />
                      <span className="date-range-sep">&ndash;</span>
                      <input type="date" className="date-input" value={endDate} min={startDate} onChange={(e) => setEndDate(e.target.value)} aria-label="End date" />
                    </div>
                  </div>
                  <MultiSelectDropdown label="Category" options={ALL_CATEGORIES} selected={selectedCategories} onChange={setSelectedCategories} />
                  <MultiSelectDropdown label="Region" options={ALL_REGIONS} selected={selectedStates} onChange={setSelectedStates} />
                </div>
                <div className="filters-summary">
                  <span className="muted small">{formatDateShort(startDate)} &ndash; {formatDateShort(endDate)} &middot; {selectedCategories.length ? `${selectedCategories.length} categories` : "All categories"} &middot; {selectedStates.length ? `${selectedStates.length} regions` : "All regions"}</span>
                  {activeFilterCount > 0 && <button type="button" className="button ghost" onClick={() => { setSelectedCategories([]); setSelectedStates([]) }}>Reset filters</button>}
                </div>
              </Card>
            </section>

            <section><div className="section-title">Overview</div><div className="metric-grid">
              <Stat label="Revenue" value={money(data.revenue.value)} change={delta(data.revenue.change_pct)} />
              <Stat label="Margin" value={money(data.revenue.value * (data.gross_margin_pct.value / 100))} change={delta(data.gross_margin_pct.change_pct, " pts")} />
              <Card>
                <div className="stat-label">Return rate</div>
                <div className="stat-line"><span className="stat-value">{data.return_rate_pct.value.toFixed(1)}%</span><span className="delta">{delta(data.return_rate_pct.change_pct, " pts")}</span></div>
                <span className={`pill ${returnRatePill(data.return_rate_pct.value).cls}`}>{returnRatePill(data.return_rate_pct.value).label}</span>
              </Card>
              <Stat label="Items sold" value={number(data.order_items.value)} change={delta(data.order_items.change_pct)} />
            </div></section>

            <section><div className="section-title">Revenue &amp; margin trend</div><Card>
              <p className="chart-caption muted small">Revenue and margin are plotted together so the profitability trend is visible alongside top-line growth, not just revenue in isolation.</p>
              <div className="chart-frame"><ResponsiveContainer width="100%" height="100%"><AreaChart data={data.trend}><defs><linearGradient id="revenueFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#2995ff" stopOpacity={.25} /><stop offset="1" stopColor="#2995ff" stopOpacity={0} /></linearGradient><linearGradient id="marginFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#8b5cf6" stopOpacity={.2} /><stop offset="1" stopColor="#8b5cf6" stopOpacity={0} /></linearGradient></defs><CartesianGrid stroke="#e5e5e7" vertical={false} /><XAxis dataKey="period" tickFormatter={formatPeriod} tickLine={false} axisLine={false} tick={{ fill: "#85868b", fontSize: 12 }} /><YAxis hide /><Tooltip formatter={(v, n) => [money(Number(v)), n]} labelFormatter={(l) => formatPeriod(String(l))} /><Legend verticalAlign="top" height={28} wrapperStyle={{ fontSize: 12 }} /><Area type="monotone" name="Revenue" dataKey="revenue" stroke="#2995ff" strokeWidth={3} fill="url(#revenueFill)" /><Area type="monotone" name="Margin" dataKey="margin" stroke="#8b5cf6" strokeWidth={3} fill="url(#marginFill)" /></AreaChart></ResponsiveContainer></div>
              {data.trend.length > 0 && (() => { const latest = data.trend[data.trend.length - 1]; const marginRate = latest.revenue ? (latest.margin / latest.revenue) * 100 : null; return <div className="chart-stats muted small">Latest period ({formatPeriod(latest.period)}): revenue {money(latest.revenue)} &middot; margin {money(latest.margin)}{marginRate !== null ? ` · margin rate ${marginRate.toFixed(1)}%` : ""}</div> })()}
            </Card></section>

            <section><div className="insight-grid">
              <Card>
                <div className="card-head"><div><h2><Shirt size={16} style={{ verticalAlign: "-2px", marginRight: 6 }} />Category leaderboard</h2><div className="muted small">Top 15 by {sortMetric.replaceAll("_", " ")}</div></div>
                  <div className="actions">
                    <select className="select" value={sortMetric} onChange={(e) => setSortMetric(e.target.value as typeof sortMetric)}><option value="revenue">Revenue</option><option value="margin">Margin</option><option value="return_rate_pct">Return rate</option></select>
                    {categoryRows && <button className="button" onClick={() => downloadCsv("category_breakdown.csv", categoryRows)}><Download size={14} />CSV</button>}
                  </div>
                </div>
                <p className="chart-caption muted small">Ranked by {sortMetric.replaceAll("_", " ")}, computed from the same order-item grain as the KPIs above.</p>
                {!sortedCategoryRows ? <div className="muted small">Loading category leaderboard&hellip;</div> : sortedCategoryRows.length === 0 ? <div className="muted small">No category data in this window.</div> : <div className="chart-frame" style={{ height: 340 }}><ResponsiveContainer width="100%" height="100%"><BarChart data={sortedCategoryRows} layout="vertical" margin={{ left: 110 }}><CartesianGrid stroke="#e5e5e7" horizontal={false} /><XAxis type="number" tick={{ fontSize: 11 }} tickFormatter={(v) => sortMetric === "return_rate_pct" ? `${v}%` : money(Number(v))} /><YAxis type="category" dataKey="category" width={130} tick={{ fontSize: 11 }} /><Tooltip formatter={(v) => sortMetric === "return_rate_pct" ? `${v}%` : money(Number(v))} /><Bar dataKey={sortMetric} fill="#2995ff" radius={[0, 4, 4, 0]} /></BarChart></ResponsiveContainer></div>}
              </Card>
              <Card>
                <div className="card-head"><div><h2><MapPin size={16} style={{ verticalAlign: "-2px", marginRight: 6 }} />Revenue by region</h2><div className="muted small">Top 15 regions</div></div>
                  {stateRows && <button className="button" onClick={() => downloadCsv("region_breakdown.csv", stateRows)}><Download size={14} />CSV</button>}
                </div>
                {!rankedStateRows ? <div className="muted small">Loading region breakdown&hellip;</div> : rankedStateRows.length === 0 ? <div className="muted small">No region data in this window.</div> : <div className="state-bars">{rankedStateRows.map((s) => <div key={s.state} className="state-bar-row"><span className="state-bar-label">{s.state_abbrev || s.state}</span><span className="state-bar-track"><span className="state-bar-fill" style={{ width: `${maxStateRevenue ? (s.revenue / maxStateRevenue) * 100 : 0}%` }} /></span><span className="state-bar-value muted small">{money(s.revenue)}</span></div>)}</div>}
              </Card>
            </div></section>

            <section><div className="section-title"><Megaphone size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />Paid vs. organic channel mix</div><Card>
              <div className="card-head"><div><h2>Monthly order mix</h2><div className="muted small">Share of order items from paid channels (Facebook, Display, Email) vs. organic/direct (Search, Organic). Denominator: order_item count, not order count.</div></div>{channelMonths && channelMonths.length > 0 && <button className="button" onClick={() => downloadCsv("channel_mix.csv", channelMonths)}><Download size={14} />CSV</button>}</div>
              {!channelMonths ? <div className="muted small">Loading channel mix&hellip;</div> : <ChannelChart months={channelMonths} />}
            </Card></section>

            <section><QuickAsk subtitle="Which category needs attention?" disabled={asking} onAsk={askQuestion} prompts={["Which category needs attention?", "Compare California, Texas, and New York.", "How has paid vs organic channel mix changed?"]} /></section>
          </>}

          {activeView === "performance" && <>
            <section><div className="section-title">Performance readout</div><Card><div className="card-head"><div><h2>vs. prior period</h2><div className="muted small">Change across the four governed metrics</div></div></div><div className="metric-grid">
              <div><div className="stat-label">Revenue</div><div className="stat-value-sm">{delta(data.revenue.change_pct)}</div></div>
              <div><div className="stat-label">Gross margin</div><div className="stat-value-sm">{delta(data.gross_margin_pct.change_pct, " pts")}</div></div>
              <div><div className="stat-label">Order items</div><div className="stat-value-sm">{delta(data.order_items.change_pct)}</div></div>
              <div><div className="stat-label">Return rate</div><div className="stat-value-sm">{delta(data.return_rate_pct.change_pct, " pts")}</div></div>
            </div></Card></section>

            <section><div className="insight-grid">
              <Card>
                <div className="card-head"><div><h2>Revenue &amp; margin performance</h2><div className="muted small">{data.metric_context.reporting_grain}</div></div><Badge tone={data.source_health.status === "healthy" ? "accent" : "warning"}>{data.source_health.status}</Badge></div>
                <div className="chart-frame"><ResponsiveContainer width="100%" height="100%"><AreaChart data={data.trend}><CartesianGrid stroke="#e5e5e7" vertical={false} /><XAxis dataKey="period" tickFormatter={formatPeriod} tickLine={false} axisLine={false} tick={{ fill: "#85868b", fontSize: 12 }} /><YAxis hide /><Tooltip formatter={(v, n) => [money(Number(v)), n]} labelFormatter={(l) => formatPeriod(String(l))} /><Legend verticalAlign="top" height={28} wrapperStyle={{ fontSize: 12 }} /><Area type="monotone" name="Revenue" dataKey="revenue" stroke="#2995ff" strokeWidth={3} fill="none" /><Area type="monotone" name="Margin" dataKey="margin" stroke="#8b5cf6" strokeWidth={3} fill="none" /></AreaChart></ResponsiveContainer></div>
                <div className="insight"><TrendingUp size={18} /><div>{data.insight}</div></div>
                <p className="muted small">{framing(data)}</p>
                {data.source_health.warning && <div className="health-warning">{data.source_health.warning}</div>}
              </Card>
              <Card>
                <div className="card-head"><div><h2>Data confidence</h2><div className="muted small">Source health and certification</div></div><Badge tone={data.source_health.status === "healthy" ? "accent" : "warning"}>{data.source_health.status}</Badge></div>
                <div className="muted small"><strong>Certification: </strong>{data.metric_context.certification_status}</div>
                <div className="muted small"><strong>Version: </strong>{data.metric_context.version ?? "version unavailable"}</div>
                <div className="muted small"><strong>Reporting grain: </strong>{data.metric_context.reporting_grain}</div>
                <div className="section-title" style={{ marginTop: 16 }}>Company benchmark</div>
                {!benchmark ? <div className="muted small">Loading benchmark&hellip;</div> : <><div className="muted small"><strong>Avg. margin: </strong>{benchmark.avg_margin_pct}%</div><div className="muted small"><strong>Avg. return rate: </strong>{benchmark.avg_return_rate_pct}% <span className={`pill ${returnRatePill(benchmark.avg_return_rate_pct).cls}`}>{returnRatePill(benchmark.avg_return_rate_pct).label}</span></div></>}
                <div className="section-title" style={{ marginTop: 16 }}>Top margin lost to returns</div>
                {!leakageRows ? <div className="muted small">Loading returns leakage&hellip;</div> : <ul className="leakage-list">{[...leakageRows].sort((a, b) => b.margin_lost_to_returns - a.margin_lost_to_returns).slice(0, 5).map((row) => <li key={row.category}><span>{row.category}</span><span className="muted small">{money(row.margin_lost_to_returns)} lost &middot; {row.return_rate_pct}% return</span></li>)}</ul>}
              </Card>
            </div></section>

            <section><QuickAsk subtitle="Where is margin leaking?" disabled={asking} onAsk={askQuestion} prompts={["Where is margin leaking?", "What if we cut the return rate in Swim by 5 points?", "Which categories are losing the most money to returns?"]} /></section>
          </>}

          {activeView === "ask" && <section><div className="section-title">Ask Loupe</div><Card>
            <div className="card-head"><div><h2>Ask a question, get a grounded answer</h2><div className="muted small">Grounded answers with metric and source context</div></div></div>
            <div className="actions">{samplePrompts.map((p) => <button type="button" key={p} className="button" onClick={() => setQuestion(p)}>{p}</button>)}</div>
            <form className="ask-row" onSubmit={(e) => { e.preventDefault(); if (question.trim()) askQuestion(question) }}>
              <input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="Which categories drove margin growth?" aria-label="Ask Loupe" />
              <button className="button primary" disabled={asking || !question.trim()}>{asking ? "Asking…" : "Ask"}</button>
            </form>

            {answer && (answer.answer === ASSISTANT_UNAVAILABLE ? <div className="health-warning">{answer.answer}</div> : (() => {
              const insight = buildInsight(answer.answer);
              const scope = reportingScopeFor(answer.category, answer.raw_data);
              return (
                <div className="insight-brief">
                  <div className="insight-brief-head">
                    <div className="actions">
                      <Badge>{answer.category.replaceAll("_", " ")}</Badge>
                      {answer.source_health_status && <Badge tone={answer.source_health_status === "healthy" ? "accent" : "warning"}>{answer.source_health_status}</Badge>}
                    </div>
                    {scope && <span className="muted small insight-scope">{scope}</span>}
                  </div>
                  <div className="insight-headline"><Sparkles size={18} /><span>{insight.headline}</span></div>
                  <AskEvidence data={answer.raw_data} />
                  <div className="insight-body">
                    {BODY_SECTIONS.filter((s) => insight.buckets[s.key]?.length).map((s) => (
                      <div className="insight-section" key={s.key}>
                        <div className="insight-section-title"><s.icon size={14} />{s.title}</div>
                        {insight.buckets[s.key].map((body, i) => <div key={i}>{renderMarkdown(body)}</div>)}
                      </div>
                    ))}
                  </div>
                  {insight.buckets.caveats?.length ? <div className="callout callout-caveat">
                    <div className="callout-title"><TriangleAlert size={15} />Caveats &amp; validation notes</div>
                    {insight.buckets.caveats.map((body, i) => <div key={i}>{renderMarkdown(body)}</div>)}
                  </div> : null}
                  {insight.buckets.recommendations?.length ? <div className="callout callout-recommend">
                    <div className="callout-title"><ArrowRight size={15} />Recommendations / next steps</div>
                    {insight.buckets.recommendations.map((body, i) => <div key={i}>{renderMarkdown(body)}</div>)}
                  </div> : null}
                  {answer.source_health_warning && <div className="health-warning">{answer.source_health_warning}</div>}
                </div>
              );
            })())}
          </Card></section>}
        </>}
      </div>
    </AppShell>
  );
}
