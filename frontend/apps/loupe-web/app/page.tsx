"use client";

import type { ComponentType, ReactNode } from "react";
import { useEffect, useRef, useState } from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import {
  ArrowRight, ChartNoAxesCombined, Database, DollarSign, Download, Eye, Home as HomeIcon, LayoutDashboard,
  Lightbulb, ListChecks, MapPin, Megaphone, MessageSquareText, PackageSearch, Percent, RotateCcw, ScanSearch,
  ShieldCheck, ShoppingBag, SlidersHorizontal, Sparkles, Target, TriangleAlert, TrendingDown, TrendingUp, Trophy,
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
type LeakageRow = { category: string; revenue?: number; margin?: number; returned_items: number; total_items: number; return_rate_pct: number; margin_lost_to_returns: number };
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

// The 6 supported prompts -- these map to intents apps/loupe_agent/chat.py
// actually routes (single_category, multi_state_comparison, scenario_simulation,
// returns_leakage, channel_analysis). "What categories need attention?" now
// routes to returns_leakage too (see chat.py's _ROUTER_SYSTEM). No
// unsupported/vague prompts that still fail routing.
const samplePrompts = [
  "Which categories are losing the most money to returns?",
  "How is Swim performing?",
  "Compare California, Texas, and New York.",
  "How has paid vs organic channel mix changed?",
  "What if we cut the return rate in Swim by 5 points?",
  "What categories need attention?",
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
// "Category" -> "categories", "Region" -> "regions" (naive -y/-ies handling
// is all two labels ever need here).
function pluralizeLower(label: string) {
  const lower = label.toLowerCase();
  return lower.endsWith("y") ? `${lower.slice(0, -1)}ies` : `${lower}s`;
}
function MultiSelectDropdown({ label, options, selected, onChange }: { label: string; options: string[]; selected: string[]; onChange: (v: string[]) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useClickOutside(ref, () => setOpen(false));
  return (
    <div className="msd" ref={ref}>
      <span className="filter-label">{label}</span>
      <button type="button" className="msd-trigger" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <span>{selected.length ? `${selected.length} selected` : `All ${pluralizeLower(label)}`}</span>
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
// bullet lists, "> " blockquote-style asides, and pipe tables with a ---
// separator row. Standalone rule lines ("---", "___", "***") are dropped
// entirely rather than rendered as literal text, "> " prefixes are stripped
// (the aside still renders, just without the raw marker), and narrative
// sentences that restate the reporting grain/date window are dropped --
// the app already shows that once via reportingScopeFor(), and chat.py's
// _GROUNDING_FOOTER instructs the model to restate it inline too, which
// otherwise shows the same scope information twice. No external markdown
// dependency is added.
const HR_RE = /^\s*([-*_])\1{2,}\s*$/;
const SCOPE_RESTATEMENT_RE = /reporting grain|date window/i;
function cleanNarrativeLine(line: string) {
  // Leave headers, list items, and table rows untouched -- only prose lines
  // get sentence-level scope stripping.
  if (/^\s*(#{2,4}\s|[-*]\s|\|)/.test(line)) return line;
  if (!SCOPE_RESTATEMENT_RE.test(line)) return line;
  const sentences = line.split(/(?<=[.!?])\s+/).filter((s) => !SCOPE_RESTATEMENT_RE.test(s));
  return sentences.join(" ").trim();
}
function cleanMarkdown(text: string) {
  return text
    .split("\n")
    .filter((l) => !HR_RE.test(l))
    .map((l) => l.replace(/^>\s?/, ""))
    .map(cleanNarrativeLine)
    .join("\n");
}
function renderMarkdown(text: string) {
  const blocks = cleanMarkdown(text).split(/\n\s*\n/);
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
  { key: "takeaway", test: (h) => !/pro.?forma/i.test(h) && /takeaway|performance summary|^summary$|overview|where to focus|bottom line/i.test(h) },
  { key: "baseline", test: (h) => /baseline|current (state|performance)/i.test(h) },
  { key: "scenario", test: (h) => /scenario|hypothetical|what.?if/i.test(h) },
  { key: "proforma", test: (h) => /pro.?forma|projected|projection|retained (revenue|margin)/i.test(h) },
  // Returns-leakage diagnostic headers (see apps/loupe_agent/chat.py's
  // _LEAKAGE_SYSTEM): "Priority 1 -- Highest Financial Impact", "Priority 2
  // -- High Return Volume", "Watchlist -- Rate Anomalies", "Key Distinction".
  { key: "priority1", test: (h) => /priority\s*1|highest financial impact|financial impact/i.test(h) },
  { key: "priority2", test: (h) => /priority\s*2|return volume|operational/i.test(h) },
  { key: "watchlist", test: (h) => /watchlist|rate anomal/i.test(h) },
  { key: "distinction", test: (h) => /key distinction/i.test(h) },
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
  const blocks = splitByHeaders(cleanMarkdown(answerText));
  const buckets: Record<string, string[]> = {};
  let leadIn = "";
  blocks.forEach((b, idx) => {
    if (!b.header) { if (idx === 0) leadIn = b.body; return }
    const key = classify(b.header);
    const bucketKey = key ?? "details";
    if (!buckets[bucketKey]) buckets[bucketKey] = [];
    buckets[bucketKey].push(key ? b.body : `#### ${b.header}\n${b.body}`);
  });
  const headline = firstLine(buckets.takeaway?.[0] ?? leadIn) || firstLine(cleanMarkdown(answerText).replace(/^#{2,4}\s+.*/gm, "")) || "Grounded answer below.";
  return { headline, buckets };
}
const BODY_SECTIONS: { key: string; title: string; icon: ComponentType<{ size?: number }>; tone?: "financial" | "operational" }[] = [
  { key: "baseline", title: "Baseline", icon: Database },
  { key: "scenario", title: "Scenario / Hypothetical", icon: Sparkles },
  { key: "proforma", title: "Pro Forma Summary", icon: TrendingUp },
  { key: "priority1", title: "Priority 1 · Highest Financial Impact", icon: DollarSign, tone: "financial" },
  { key: "priority2", title: "Priority 2 · High Return Volume", icon: PackageSearch, tone: "operational" },
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
  const shares = months.map((m) => m.paid_share_pct);
  const flat = months.length > 1 && Math.max(...shares) - Math.min(...shares) < 0.5;
  if (months.length === 1 || flat) return <ChannelSnapshot month={months[months.length - 1]} />;
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

// Compact, always-available Ask Loupe entry point for non-Ask tabs -- styled
// as a small version of the same chat composer used on the Ask Loupe tab
// (soft dock + pill chips) rather than a plain card with a button row.
// Clicking a chip routes to Ask Loupe and submits immediately via the same
// askQuestion() the main composer uses.
function QuickAsk({ subtitle, prompts, onAsk, disabled }: { subtitle: string; prompts: string[]; onAsk: (q: string) => void; disabled: boolean }) {
  return (
    <Card className="quick-ask">
      <div className="quick-ask-head"><Sparkles size={15} /><span>Ask Loupe</span><span className="muted small quick-ask-subtitle">{subtitle}</span></div>
      <div className="quick-ask-dock">
        <div className="quick-ask-chips">{prompts.map((p) => <button type="button" key={p} className="chat-chip" disabled={disabled} onClick={() => onAsk(p)}>{p}</button>)}</div>
      </div>
    </Card>
  );
}

// --- returns_leakage deterministic diagnostic ------------------------------
// The Claude narrative for returns_leakage is free-form prose (see
// apps/loupe_agent/chat.py's _LEAKAGE_SYSTEM), and no amount of markdown/
// bullet styling turns prose into a product UI. So for this category the
// primary view is computed directly from response.raw_data -- the same
// ranked leakage table BigQuery returned -- with zero text generated by the
// model. The full narrative is still available, collapsed, at the bottom.
function leakageRows(raw: unknown): LeakageRow[] {
  if (!Array.isArray(raw) || !raw.length) return [];
  const first = raw[0] as Record<string, unknown>;
  if (!("margin_lost_to_returns" in first) || !("category" in first)) return [];
  return raw as LeakageRow[];
}
function byMarginDesc(rows: LeakageRow[]) { return [...rows].sort((a, b) => b.margin_lost_to_returns - a.margin_lost_to_returns) }
function byVolumeDesc(rows: LeakageRow[]) { return [...rows].sort((a, b) => b.returned_items - a.returned_items) }
function byRateDesc(rows: LeakageRow[]) { return [...rows].sort((a, b) => b.return_rate_pct - a.return_rate_pct) }

// One ranked row: category name + up to 4 metric/value pairs, laid out as a
// compact card instead of a paragraph.
function LeakageRowCard({ row, metrics, tone, tag }: { row: LeakageRow; metrics: { label: string; value: string }[]; tone?: "financial" | "operational" | "amber"; tag?: string }) {
  return (
    <div className={`leakage-row${tone ? ` leakage-row-${tone}` : ""}`}>
      <div className="leakage-row-cat">{row.category}{tag && <span className="leakage-row-tag">{tag}</span>}</div>
      <div className="leakage-row-metrics">
        {metrics.map((m) => <span className="leakage-row-metric" key={m.label}><b>{m.value}</b><i>{m.label}</i></span>)}
      </div>
    </div>
  );
}

// Two-sentence, entirely computed executive summary: top financial priority,
// then the combined dollar impact of the top 3.
function LeakageSummary({ rows }: { rows: LeakageRow[] }) {
  const top3 = byMarginDesc(rows).slice(0, 3);
  const top = top3[0];
  const combined = top3.reduce((sum, r) => sum + r.margin_lost_to_returns, 0);
  return (
    <div className="leakage-summary">
      <p><strong>{top.category}</strong> is the top financial priority, losing {money(top.margin_lost_to_returns)} in margin to returns at a {top.return_rate_pct.toFixed(1)}% return rate.</p>
      <p>Together, {top3.map((r) => r.category).join(", ")} account for {money(combined)} in lost margin.</p>
    </div>
  );
}

function PriorityFinancialModule({ rows }: { rows: LeakageRow[] }) {
  const top = byMarginDesc(rows).slice(0, 5);
  return (
    <div className="insight-section insight-section-financial">
      <div className="insight-section-title"><DollarSign size={14} />Priority 1 · Financial impact</div>
      <div className="leakage-row-list">
        {top.map((r) => (
          <LeakageRowCard key={r.category} row={r} tone="financial" metrics={[
            { label: "margin lost", value: money(r.margin_lost_to_returns) },
            { label: "return rate", value: `${r.return_rate_pct.toFixed(1)}%` },
            { label: "returned items", value: number(r.returned_items) },
          ]} />
        ))}
      </div>
    </div>
  );
}

function PriorityOperationalModule({ rows }: { rows: LeakageRow[] }) {
  const top = byVolumeDesc(rows).slice(0, 5);
  return (
    <div className="insight-section insight-section-operational">
      <div className="insight-section-title"><PackageSearch size={14} />Priority 2 · Operational volume</div>
      <div className="leakage-row-list">
        {top.map((r) => (
          <LeakageRowCard key={r.category} row={r} tone="operational" metrics={[
            { label: "returned items", value: number(r.returned_items) },
            { label: "return rate", value: `${r.return_rate_pct.toFixed(1)}%` },
            { label: "margin lost", value: money(r.margin_lost_to_returns) },
          ]} />
        ))}
      </div>
    </div>
  );
}

// Top 3 by return rate, tagged "Small volume" whenever a category's total
// item count is below the dataset average -- flags early-warning anomalies
// distinct from the high-dollar categories above.
function WatchlistModule({ rows }: { rows: LeakageRow[] }) {
  const top = byRateDesc(rows).slice(0, 3);
  const avgItems = rows.reduce((sum, r) => sum + r.total_items, 0) / rows.length;
  return (
    <div className="callout callout-watchlist callout-compact">
      <div className="callout-title"><Eye size={15} />Watchlist · Rate anomalies</div>
      <div className="leakage-row-list">
        {top.map((r) => (
          <LeakageRowCard key={r.category} row={r} tone="amber" tag={r.total_items < avgItems ? "Small volume" : undefined} metrics={[
            { label: "return rate", value: `${r.return_rate_pct.toFixed(1)}%` },
            { label: "returned", value: number(r.returned_items) },
            { label: "total items", value: number(r.total_items) },
            { label: "margin lost", value: money(r.margin_lost_to_returns) },
          ]} />
        ))}
      </div>
    </div>
  );
}

// Fixed, non-generated explanation of the two signal types -- no model
// wording is used here at all, only the real top category/numbers per side.
function KeyDistinctionModule({ rows }: { rows: LeakageRow[] }) {
  const topFinancial = byMarginDesc(rows)[0];
  const topOperational = byVolumeDesc(rows)[0];
  return (
    <div className="callout callout-info">
      <div className="callout-title"><Lightbulb size={15} />Key distinction</div>
      <div className="distinction-columns">
        <div className="distinction-col distinction-col-financial">
          <div className="distinction-col-title"><DollarSign size={13} />Financial signal</div>
          <p className="muted small">Absolute margin lost shows where dollars are leaking.</p>
          <div className="distinction-col-value">{topFinancial.category}</div>
          <div className="muted small">{money(topFinancial.margin_lost_to_returns)} lost</div>
        </div>
        <div className="distinction-col distinction-col-operational">
          <div className="distinction-col-title"><PackageSearch size={13} />Operational signal</div>
          <p className="muted small">Return volume/rate shows where process or customer experience may be broken.</p>
          <div className="distinction-col-value">{topOperational.category}</div>
          <div className="muted small">{topOperational.return_rate_pct.toFixed(1)}% return rate · {number(topOperational.returned_items)} returned items</div>
        </div>
      </div>
    </div>
  );
}

// Deterministic 3-action plan: (1) the top financial-impact category, (2)
// whichever category appears in both the top-3 financial and top-3 volume
// lists (skipped if none), (3) the highest-rate category among below-average
// item volume. Every category/number is read from raw_data only.
function leakageActions(rows: LeakageRow[]): { category: string; reason: string; step: string }[] {
  const top = byMarginDesc(rows);
  const top3Financial = new Set(top.slice(0, 3).map((r) => r.category));
  const top3Volume = new Set(byVolumeDesc(rows).slice(0, 3).map((r) => r.category));
  const avgItems = rows.reduce((sum, r) => sum + r.total_items, 0) / rows.length;
  const smallHighRate = byRateDesc(rows).find((r) => r.total_items < avgItems);
  const actions: { category: string; reason: string; step: string }[] = [
    { category: top[0].category, reason: `Highest absolute margin lost to returns (${money(top[0].margin_lost_to_returns)}).`, step: "Investigate return reasons, sizing/fit accuracy, and product quality first." },
  ];
  const overlap = top.find((r) => r.category !== top[0].category && top3Financial.has(r.category) && top3Volume.has(r.category));
  if (overlap) actions.push({ category: overlap.category, reason: "Appears in both the top-3 financial-impact and top-3 return-volume categories.", step: "Prioritize -- this is simultaneously a revenue and an operational problem." });
  if (smallHighRate && smallHighRate.category !== top[0].category && smallHighRate.category !== overlap?.category) {
    actions.push({ category: smallHighRate.category, reason: `Highest return rate (${smallHighRate.return_rate_pct.toFixed(1)}%) among below-average-volume categories.`, step: "Monitor as an early-warning quality/listing issue before it scales." });
  }
  return actions;
}
function RecommendationModule({ rows }: { rows: LeakageRow[] }) {
  const actions = leakageActions(rows);
  return (
    <div className="callout callout-recommend">
      <div className="callout-title"><Target size={15} />Recommendation · Action plan</div>
      <ol className="leakage-action-list">
        {actions.map((a, i) => (
          <li className="leakage-action-row" key={a.category}>
            <span className="leakage-action-index">{i + 1}</span>
            <span className="leakage-action-cat">{a.category}</span>
            <span className="leakage-action-reason">{a.reason}</span>
            <span className="leakage-action-step">{a.step}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

// Full deterministic diagnostic: summary, Priority 1/2 rows, Watchlist,
// Key Distinction, Recommendation -- all computed from raw_data. The raw
// chart is dropped here (rows already show the ranking; a chart on top of
// five ranked-row modules just re-crowds the exact report feel this is
// fixing). The model's own narrative is kept, but demoted to a collapsed,
// muted "View model narrative" details block at the very bottom.
function LeakageDiagnostic({ response, rows }: { response: AskResponse; rows: LeakageRow[] }) {
  const scope = reportingScopeFor(response.category, response.raw_data);
  return (
    <div className="insight-summary-card">
      <div className="insight-brief-head">
        <div className="actions">
          <Badge>returns leakage</Badge>
          {response.source_health_status && <Badge tone={response.source_health_status === "healthy" ? "accent" : "warning"}>{response.source_health_status}</Badge>}
        </div>
        {scope && <span className="muted small insight-scope">{scope}</span>}
      </div>
      <LeakageSummary rows={rows} />
      <div className="insight-body">
        <PriorityFinancialModule rows={rows} />
        <PriorityOperationalModule rows={rows} />
      </div>
      <WatchlistModule rows={rows} />
      <KeyDistinctionModule rows={rows} />
      <RecommendationModule rows={rows} />
      {response.source_health_warning && <div className="health-warning">{response.source_health_warning}</div>}
      <details className="model-narrative">
        <summary>View model narrative</summary>
        <div className="model-narrative-body muted small">{renderMarkdown(response.answer)}</div>
      </details>
    </div>
  );
}

// Ask Loupe answer hierarchy: the short headline lives in the chat bubble
// (see the "ask" view below); this renders everything after it. For
// returns_leakage with array raw_data, the deterministic LeakageDiagnostic
// above is the entire body. Every other category keeps the original
// markdown-section renderer -- badges + reporting scope, the evidence
// row/chart, then Baseline / Scenario / Pro Forma / Highlights / Additional
// Detail sections, then Watchlist / Key Distinction / Validation Notes /
// Recommendation callouts.
function AskInsightBody({ response, insight }: { response: AskResponse; insight: { headline: string; buckets: Record<string, string[]> } }) {
  const rows = leakageRows(response.raw_data);
  if (response.category === "returns_leakage" && rows.length > 0) {
    return <LeakageDiagnostic response={response} rows={rows} />;
  }
  const scope = reportingScopeFor(response.category, response.raw_data);
  return (
    <div className="insight-summary-card">
      <div className="insight-brief-head">
        <div className="actions">
          <Badge>{response.category.replaceAll("_", " ")}</Badge>
          {response.source_health_status && <Badge tone={response.source_health_status === "healthy" ? "accent" : "warning"}>{response.source_health_status}</Badge>}
        </div>
        {scope && <span className="muted small insight-scope">{scope}</span>}
      </div>
      <AskEvidence data={response.raw_data} />
      <div className="insight-body">
        {BODY_SECTIONS.filter((s) => insight.buckets[s.key]?.length).map((s) => (
          <div className={`insight-section${s.tone ? ` insight-section-${s.tone}` : ""}`} key={s.key}>
            <div className="insight-section-title"><s.icon size={14} />{s.title}</div>
            {insight.buckets[s.key].map((body, i) => <div key={i}>{renderMarkdown(body)}</div>)}
          </div>
        ))}
      </div>
      {insight.buckets.watchlist?.length ? <div className="callout callout-watchlist">
        <div className="callout-title"><Eye size={15} />Watchlist</div>
        {insight.buckets.watchlist.map((body, i) => <div key={i}>{renderMarkdown(body)}</div>)}
      </div> : null}
      {insight.buckets.distinction?.length ? <div className="callout callout-info">
        <div className="callout-title"><Lightbulb size={15} />Key distinction</div>
        {insight.buckets.distinction.map((body, i) => <div key={i}>{renderMarkdown(body)}</div>)}
      </div> : null}
      {insight.buckets.caveats?.length ? <div className="callout callout-caveat">
        <div className="callout-title"><TriangleAlert size={15} />Validation notes</div>
        {insight.buckets.caveats.map((body, i) => <div key={i}>{renderMarkdown(body)}</div>)}
      </div> : null}
      {insight.buckets.recommendations?.length ? <div className="callout callout-recommend">
        <div className="callout-title"><Target size={15} />Recommendation</div>
        {insight.buckets.recommendations.map((body, i) => <div key={i}>{renderMarkdown(body)}</div>)}
      </div> : null}
      {response.source_health_warning && <div className="health-warning">{response.source_health_warning}</div>}
    </div>
  );
}

// One consistent card shape used everywhere on the dashboard/performance
// tabs -- header (icon + title), description, optional action, then
// content -- so sections read as one cohesive workspace instead of
// mismatched ad hoc cards with a floating label stacked above them.
function SectionCard({ icon: Icon, title, description, action, className = "", children }: { icon?: ComponentType<{ size?: number }>; title: string; description?: string; action?: ReactNode; className?: string; children: ReactNode }) {
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

// Dashboard-only: Category Leaderboard as a dense table/list hybrid (rank,
// category + mini bar for the active sort metric, revenue, margin, return
// rate, items) instead of a bare chart -- reads as a ranked business card.
function CategoryLeaderboardRows({ rows, sortMetric }: { rows: CategoryRow[]; sortMetric: "revenue" | "margin" | "return_rate_pct" }) {
  if (!rows.length) return null;
  const max = Math.max(...rows.map((r) => Number(r[sortMetric])), 1);
  return (
    <div className="leaderboard-table">
      <div className="leaderboard-head">
        <span /><span>Category</span><span>Revenue</span><span>Margin</span><span>Return rate</span><span>Items</span>
      </div>
      {rows.map((r, i) => (
        <div className="leaderboard-row" key={r.category}>
          <span className="leaderboard-rank">{i + 1}</span>
          <span className="leaderboard-cat">
            <span className="leaderboard-cat-name">{r.category}</span>
            <span className="leaderboard-bar-track"><span className="leaderboard-bar-fill" style={{ width: `${(Number(r[sortMetric]) / max) * 100}%` }} /></span>
          </span>
          <span className="leaderboard-cell">{money(r.revenue)}</span>
          <span className="leaderboard-cell">{money(r.margin)}</span>
          <span className="leaderboard-cell">{r.return_rate_pct.toFixed(1)}%</span>
          <span className="leaderboard-cell">{number(r.items)}</span>
        </div>
      ))}
    </div>
  );
}

// Dashboard-only: top 3 returns-leakage categories by absolute margin lost,
// read straight from the /returns-leakage endpoint's raw rows (same shape
// as Ask Loupe's diagnostic) -- reuses the .leakage-row-* card styling so
// the two surfaces look like one design system without sharing any code.
function ReturnsLeakageSnapshot({ rows }: { rows: LeakageRow[] }) {
  const top = [...rows].sort((a, b) => b.margin_lost_to_returns - a.margin_lost_to_returns).slice(0, 3);
  if (!top.length) return null;
  const lead = top[0];
  return (
    <>
      <p className="leakage-summary-line"><strong>{lead.category}</strong> leads at {money(lead.margin_lost_to_returns)} lost to returns ({lead.return_rate_pct.toFixed(1)}% return rate, {number(lead.returned_items)} returned items).</p>
      <div className="leakage-row-list">
        {top.map((r) => (
          <div className="leakage-row leakage-row-financial" key={r.category}>
            <span className="leakage-row-cat">{r.category}</span>
            <span className="leakage-row-metrics">
              <span className="leakage-row-metric"><b>{money(r.margin_lost_to_returns)}</b><i>margin lost</i></span>
              <span className="leakage-row-metric"><b>{r.return_rate_pct.toFixed(1)}%</b><i>return rate</i></span>
              <span className="leakage-row-metric"><b>{number(r.returned_items)}</b><i>returned</i></span>
            </span>
          </div>
        ))}
      </div>
    </>
  );
}

// KPI tile: label + value + a colored delta badge (up/down icon, not just
// text) + an optional small contextual note -- the shadcnspace metric-tile
// composition, reused across Home/Dashboard/Performance instead of each
// tab inventing its own KPI markup.
// `icon` is optional and additive-only: Home/Performance keep calling Stat
// without it (identical rendering to before), while Dashboard passes a
// semantic lucide icon for the shadcnspace-style metric tile header.
function Stat({ label, value, change, note, icon: Icon }: { label: string; value: string; change: string; note?: string; icon?: ComponentType<{ size?: number }> }) {
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
  const [messages, setMessages] = useState<{ id: string; question: string; response: AskResponse | null }[]>([]);
  const [asking, setAsking] = useState(false);
  const nextMessageId = useRef(0);

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
  // differs. Each call appends a new turn to the chat transcript instead of
  // replacing a single answer slot, so Ask Loupe reads as a conversation.
  async function askQuestion(q: string) {
    const id = String(nextMessageId.current++);
    setQuestion("");
    setActiveView("ask");
    setAsking(true);
    setMessages((prev) => [...prev, { id, question: q, response: null }]);
    try {
      const response = await fetch(`${API_BASE}/api/v1/loupe/ask`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: q }) });
      const body = await response.json();
      const result: AskResponse = response.ok
        ? { ...body, answer: looksUnconfigured(body.answer ?? "") ? ASSISTANT_UNAVAILABLE : body.answer }
        : { category: "general", answer: body.detail, source_health_status: null, source_health_warning: null, raw_data: null };
      setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, response: result } : m)));
    } catch {
      setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, response: { category: "general", answer: "Loupe could not be reached.", source_health_status: null, source_health_warning: null, raw_data: null } } : m)));
    } finally {
      setAsking(false);
    }
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
              <Stat label="Net revenue" value={money(data.revenue.value)} change={delta(data.revenue.change_pct)} note="vs. prior period" />
              <Stat label="Gross margin" value={`${data.gross_margin_pct.value.toFixed(1)}%`} change={delta(data.gross_margin_pct.change_pct, " pts")} note="vs. prior period" />
              <Stat label="Order items" value={number(data.order_items.value)} change={delta(data.order_items.change_pct)} note="vs. prior period" />
              <Stat label="Return rate" value={`${data.return_rate_pct.value.toFixed(1)}%`} change={delta(data.return_rate_pct.change_pct, " pts")} note={returnRatePill(data.return_rate_pct.value).label} />
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
            <section><QuickAsk subtitle="Grounded answers about revenue, returns, and regions." disabled={asking} onAsk={askQuestion} prompts={["Which categories are losing the most money to returns?", "How is Swim performing?", "Compare California, Texas, and New York."]} /></section>
          </>}

          {activeView === "dashboard" && <div className="dash-surface">
            <section><div className="metric-grid">
              <Stat icon={DollarSign} label="Revenue" value={money(data.revenue.value)} change={delta(data.revenue.change_pct)} note="vs. prior period" />
              <Stat icon={Percent} label="Margin" value={money(data.revenue.value * (data.gross_margin_pct.value / 100))} change={delta(data.gross_margin_pct.change_pct, " pts")} note="vs. prior period" />
              <Card className="metric-tile">
                <div className="stat-label"><span className="metric-tile-icon"><RotateCcw size={13} /></span>Return rate</div>
                <div className="stat-line">
                  <span className="stat-value">{data.return_rate_pct.value.toFixed(1)}%</span>
                  <span className="delta">{delta(data.return_rate_pct.change_pct, " pts")}</span>
                  <span className={`pill ${returnRatePill(data.return_rate_pct.value).cls}`}>{returnRatePill(data.return_rate_pct.value).label}</span>
                </div>
              </Card>
              <Stat icon={ShoppingBag} label="Items sold" value={number(data.order_items.value)} change={delta(data.order_items.change_pct)} note="vs. prior period" />
            </div></section>

            <section>
              <div className="dash-toolbar">
                <SlidersHorizontal size={15} className="dash-toolbar-icon" />
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
                <div className="dash-toolbar-spacer" />
                {activeFilterCount > 0 && <Badge tone="accent">{activeFilterCount} filters active</Badge>}
                {activeFilterCount > 0 && <button type="button" className="button reset-filters-btn" onClick={() => { setSelectedCategories([]); setSelectedStates([]) }}><RotateCcw size={13} />Reset</button>}
              </div>
            </section>

            <section><div className="dash-primary-grid">
              <SectionCard icon={TrendingUp} title="Revenue & margin trend" description="Revenue and margin are plotted together so the profitability trend is visible alongside top-line growth, not just revenue in isolation.">
                {data.trend.length > 0 && (() => { const latest = data.trend[data.trend.length - 1]; const marginRate = latest.revenue ? (latest.margin / latest.revenue) * 100 : null; return (
                  <div className="chart-highlight-row">
                    <span><strong>{money(latest.revenue)}</strong> revenue</span>
                    <span><strong>{money(latest.margin)}</strong> margin</span>
                    {marginRate !== null && <span><strong>{marginRate.toFixed(1)}%</strong> margin rate</span>}
                    <span className="muted small">latest period ({formatPeriod(latest.period)})</span>
                  </div>
                ) })()}
                <div className="chart-frame chart-frame-compact"><ResponsiveContainer width="100%" height="100%"><AreaChart data={data.trend}><defs><linearGradient id="revenueFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#2995ff" stopOpacity={.25} /><stop offset="1" stopColor="#2995ff" stopOpacity={0} /></linearGradient><linearGradient id="marginFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#8b5cf6" stopOpacity={.2} /><stop offset="1" stopColor="#8b5cf6" stopOpacity={0} /></linearGradient></defs><CartesianGrid stroke="#e5e5e7" vertical={false} /><XAxis dataKey="period" tickFormatter={formatPeriod} tickLine={false} axisLine={false} tick={{ fill: "#85868b", fontSize: 12 }} /><YAxis hide /><Tooltip formatter={(v, n) => [money(Number(v)), n]} labelFormatter={(l) => formatPeriod(String(l))} /><Legend verticalAlign="top" height={24} wrapperStyle={{ fontSize: 12 }} /><Area type="monotone" name="Revenue" dataKey="revenue" stroke="#2995ff" strokeWidth={3} fill="url(#revenueFill)" /><Area type="monotone" name="Margin" dataKey="margin" stroke="#8b5cf6" strokeWidth={3} fill="url(#marginFill)" /></AreaChart></ResponsiveContainer></div>
                <div className="chart-footer-line muted small">Revenue is {delta(data.revenue.change_pct)} and margin rate is {delta(data.gross_margin_pct.change_pct, " pts")} vs. the prior period.</div>
              </SectionCard>

              <div className="dash-side-stack">
                <SectionCard icon={TrendingDown} title="Returns leakage snapshot" description="Top categories losing margin to returns.">
                  {!leakageRows ? <div className="muted small">Loading leakage data&hellip;</div> : leakageRows.length === 0 ? <div className="muted small">No leakage data available.</div> : <ReturnsLeakageSnapshot rows={leakageRows} />}
                </SectionCard>
                <SectionCard icon={ShieldCheck} title="Data confidence" description="Live source status for this tab.">
                  <div className="confidence-rows">
                    <div className="confidence-row"><span className="muted small">Source</span><Badge tone={data.source_health.status === "healthy" ? "accent" : "warning"}>{data.source_health.status}</Badge></div>
                    <div className="confidence-row"><span className="muted small">Certification</span><span>{data.metric_context.certification_status}</span></div>
                    <div className="confidence-row"><span className="muted small">Reporting grain</span><span>{data.metric_context.reporting_grain}</span></div>
                    <div className="confidence-row"><span className="muted small">Active filters</span><span>{activeFilterCount > 0 ? `${activeFilterCount} applied` : "None"}</span></div>
                  </div>
                  {data.source_health.warning && <div className="health-warning">{data.source_health.warning}</div>}
                </SectionCard>
                <QuickAsk subtitle="Get a grounded recommendation." disabled={asking} onAsk={askQuestion} prompts={["What categories need attention?", "Which categories are losing the most money to returns?", "How has paid vs organic channel mix changed?"]} />
              </div>
            </div></section>

            <section><div className="dash-two-col">
              <SectionCard
                icon={Trophy}
                title="Category leaderboard"
                description={`Top 8 by ${sortMetric.replaceAll("_", " ")}, computed from the same order-item grain as the KPIs above.`}
                action={<div className="actions">
                  <select className="select" value={sortMetric} onChange={(e) => setSortMetric(e.target.value as typeof sortMetric)}><option value="revenue">Revenue</option><option value="margin">Margin</option><option value="return_rate_pct">Return rate</option></select>
                  {categoryRows && <button className="button" onClick={() => downloadCsv("category_breakdown.csv", categoryRows)}><Download size={14} />CSV</button>}
                </div>}
              >
                {!sortedCategoryRows ? <div className="muted small">Loading category leaderboard&hellip;</div> : sortedCategoryRows.length === 0 ? <div className="muted small">No category data in this window.</div> : <CategoryLeaderboardRows rows={sortedCategoryRows.slice(0, 8)} sortMetric={sortMetric} />}
              </SectionCard>
              <SectionCard
                icon={MapPin}
                title="Revenue by region"
                description="Top 15 regions by revenue, share of the total shown region-to-region."
                action={stateRows && <button className="button" onClick={() => downloadCsv("region_breakdown.csv", stateRows)}><Download size={14} />CSV</button>}
              >
                {!rankedStateRows ? <div className="muted small">Loading region breakdown&hellip;</div> : rankedStateRows.length === 0 ? <div className="muted small">No region data in this window.</div> : (() => {
                  const totalRevenue = rankedStateRows.reduce((sum, s) => sum + s.revenue, 0);
                  return <div className="state-bars">{rankedStateRows.map((s) => (
                    <div key={s.state} className="state-bar-row">
                      <span className="state-bar-label">{s.state_abbrev || s.state}</span>
                      <span className="state-bar-track"><span className="state-bar-fill" style={{ width: `${maxStateRevenue ? (s.revenue / maxStateRevenue) * 100 : 0}%` }} /></span>
                      <span className="state-bar-value muted small">{money(s.revenue)}</span>
                      <span className="state-bar-share muted small">{totalRevenue ? `${((s.revenue / totalRevenue) * 100).toFixed(0)}%` : ""}</span>
                    </div>
                  ))}</div>;
                })()}
              </SectionCard>
            </div></section>

            <section><SectionCard
              icon={Megaphone}
              title="Paid vs. organic channel mix"
              description="Share of order items from paid channels (Facebook, Display, Email) vs. organic/direct (Search, Organic)."
              action={channelMonths && channelMonths.length > 0 && <button className="button" onClick={() => downloadCsv("channel_mix.csv", channelMonths)}><Download size={14} />CSV</button>}
            >
              {!channelMonths ? <div className="muted small">Loading channel mix&hellip;</div> : <>
                <ChannelChart months={channelMonths} compact />
                <div className="chart-footer-line muted small">Denominator: {number(channelMonths[channelMonths.length - 1].total)} order items in the latest period.</div>
              </>}
            </SectionCard></section>
          </div>}

          {activeView === "performance" && <>
            <section><SectionCard title="Performance readout" description="Change across the four governed metrics vs. the prior period.">
              <div className="metric-grid">
                <Stat label="Revenue" value={money(data.revenue.value)} change={delta(data.revenue.change_pct)} note="vs. prior period" />
                <Stat label="Gross margin" value={`${data.gross_margin_pct.value.toFixed(1)}%`} change={delta(data.gross_margin_pct.change_pct, " pts")} note="vs. prior period" />
                <Stat label="Order items" value={number(data.order_items.value)} change={delta(data.order_items.change_pct)} note="vs. prior period" />
                <Stat label="Return rate" value={`${data.return_rate_pct.value.toFixed(1)}%`} change={delta(data.return_rate_pct.change_pct, " pts")} note={returnRatePill(data.return_rate_pct.value).label} />
              </div>
            </SectionCard></section>

            <section><div className="insight-grid">
              <SectionCard
                title="Revenue & margin performance"
                description={data.metric_context.reporting_grain}
                action={<Badge tone={data.source_health.status === "healthy" ? "accent" : "warning"}>{data.source_health.status}</Badge>}
              >
                <div className="chart-frame chart-frame-compact"><ResponsiveContainer width="100%" height="100%"><AreaChart data={data.trend}><CartesianGrid stroke="#e5e5e7" vertical={false} /><XAxis dataKey="period" tickFormatter={formatPeriod} tickLine={false} axisLine={false} tick={{ fill: "#85868b", fontSize: 12 }} /><YAxis hide /><Tooltip formatter={(v, n) => [money(Number(v)), n]} labelFormatter={(l) => formatPeriod(String(l))} /><Legend verticalAlign="top" height={24} wrapperStyle={{ fontSize: 12 }} /><Area type="monotone" name="Revenue" dataKey="revenue" stroke="#2995ff" strokeWidth={3} fill="none" /><Area type="monotone" name="Margin" dataKey="margin" stroke="#8b5cf6" strokeWidth={3} fill="none" /></AreaChart></ResponsiveContainer></div>
                <div className="insight"><TrendingUp size={18} /><div>{data.insight}</div></div>
                <p className="muted small">{framing(data)}</p>
                {data.source_health.warning && <div className="health-warning">{data.source_health.warning}</div>}
              </SectionCard>
              <SectionCard
                title="Data confidence"
                description="Source health and certification"
                action={<Badge tone={data.source_health.status === "healthy" ? "accent" : "warning"}>{data.source_health.status}</Badge>}
              >
                <div className="muted small"><strong>Certification: </strong>{data.metric_context.certification_status}</div>
                <div className="muted small"><strong>Version: </strong>{data.metric_context.version ?? "version unavailable"}</div>
                <div className="muted small"><strong>Reporting grain: </strong>{data.metric_context.reporting_grain}</div>
                <div className="subsection-title">Company benchmark</div>
                {!benchmark ? <div className="muted small">Loading benchmark&hellip;</div> : <><div className="muted small"><strong>Avg. margin: </strong>{benchmark.avg_margin_pct}%</div><div className="muted small"><strong>Avg. return rate: </strong>{benchmark.avg_return_rate_pct}% <span className={`pill ${returnRatePill(benchmark.avg_return_rate_pct).cls}`}>{returnRatePill(benchmark.avg_return_rate_pct).label}</span></div></>}
                <div className="subsection-title">Top margin lost to returns</div>
                {!leakageRows ? <div className="muted small">Loading returns leakage&hellip;</div> : <ul className="leakage-list">{[...leakageRows].sort((a, b) => b.margin_lost_to_returns - a.margin_lost_to_returns).slice(0, 5).map((row) => <li key={row.category}><span>{row.category}</span><span className="muted small">{money(row.margin_lost_to_returns)} lost &middot; {row.return_rate_pct}% return</span></li>)}</ul>}
              </SectionCard>
            </div></section>

            <section><QuickAsk subtitle="Ask about margin, returns, or scenario impact." disabled={asking} onAsk={askQuestion} prompts={["What if we cut the return rate in Swim by 5 points?", "Which categories are losing the most money to returns?", "How is Swim performing?"]} /></section>
          </>}

          {activeView === "ask" && <section className="chat-panel-wrap"><div className="chat-panel">
            <div className="chat-panel-header">
              <div className="chat-panel-title"><Sparkles size={16} />Ask Loupe</div>
              <div className="chat-panel-status muted small">
                {data.source_health.status === "healthy" ? "Grounded in live BigQuery data" : `Source health: ${data.source_health.status}`}
              </div>
            </div>

            <div className="chat-scroll">
              {messages.length === 0 ? (
                <div className="chat-empty">
                  <Sparkles size={22} />
                  <p>Ask about revenue, categories, regions, channel mix, or scenario impact &mdash; grounded in live BigQuery data.</p>
                  <div className="chat-chip-row">
                    {samplePrompts.map((p) => <button type="button" key={p} className="chat-chip" onClick={() => askQuestion(p)}>{p}</button>)}
                  </div>
                </div>
              ) : messages.map((m) => {
                const resp = m.response;
                return (
                  <div className="chat-message-group" key={m.id}>
                    <div className="chat-bubble chat-bubble-user"><span>{m.question}</span></div>
                    {!resp ? (
                      <div className="chat-bubble chat-bubble-assistant"><Sparkles size={15} /><div className="chat-typing"><span /><span /><span /></div></div>
                    ) : resp.answer === ASSISTANT_UNAVAILABLE ? (
                      <div className="chat-bubble chat-bubble-assistant"><Sparkles size={15} /><span>{resp.answer}</span></div>
                    ) : (() => {
                      const insight = buildInsight(resp.answer);
                      return (
                        <>
                          <div className="chat-bubble chat-bubble-assistant"><Sparkles size={15} /><span>{insight.headline}</span></div>
                          <AskInsightBody response={resp} insight={insight} />
                        </>
                      );
                    })()}
                  </div>
                );
              })}
            </div>

            <div className="chat-composer">
              {messages.length > 0 && <div className="chat-chip-row chat-chip-row-compact">
                {samplePrompts.map((p) => <button type="button" key={p} className="chat-chip" disabled={asking} onClick={() => askQuestion(p)}>{p}</button>)}
              </div>}
              <form className="chat-input-dock" onSubmit={(e) => { e.preventDefault(); if (question.trim()) askQuestion(question) }}>
                <input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="Ask about revenue, categories, regions, or a scenario…" aria-label="Ask Loupe" />
                <button type="submit" className="chat-send" disabled={asking || !question.trim()} aria-label="Send question"><ArrowRight size={16} /></button>
              </form>
            </div>
          </div></section>}
        </>}
      </div>
    </AppShell>
  );
}
