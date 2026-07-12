"use client";

import { useEffect, useState } from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import {
  ChartNoAxesCombined, Database, Download, Home as HomeIcon, LayoutDashboard,
  MapPin, Megaphone, MessageSquareText, ScanSearch, Shirt, SlidersHorizontal, Sparkles, TrendingUp,
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
// Same state names apps/loupe_agent/metrics.py::STATE_ABBREV keys on.
const ALL_STATES = [
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

function inlineMd(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((p, i) => (p.startsWith("**") && p.endsWith("**") ? <strong key={i}>{p.slice(2, -2)}</strong> : <span key={i}>{p}</span>));
}

// Minimal markdown renderer covering exactly what apps/loupe_agent/chat.py's
// prompts instruct the model to produce: ## / ### headers, **bold**, "- "
// bullet lists, and pipe tables with a --- separator row. No external
// markdown dependency is added (frontend/package.json is out of scope).
function renderMarkdown(text: string) {
  const blocks = text.split(/\n\s*\n/);
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
        if (lines[0].startsWith("### ")) return <h4 key={i}>{inlineMd(lines[0].slice(4))}</h4>;
        if (lines[0].startsWith("## ")) return <h3 key={i}>{inlineMd(lines[0].slice(3))}</h3>;
        if (lines.every((l) => l.trim().startsWith("- ") || l.trim().startsWith("* "))) {
          return <ul key={i}>{lines.map((l, li) => <li key={li}>{inlineMd(l.trim().replace(/^[-*]\s+/, ""))}</li>)}</ul>;
        }
        return <p key={i}>{lines.map((l, li) => <span key={li}>{inlineMd(l)}{li < lines.length - 1 ? <br /> : null}</span>)}</p>;
      })}
    </>
  );
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
              <Bar dataKey="margin_lost_to_returns" fill="#c0362c" />
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
              <Bar dataKey="margin" fill="#2995ff" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </>
    );
  }
  const obj = data as Record<string, unknown>;
  if (Array.isArray(obj.months)) {
    return (
      <div className="chart-frame" style={{ height: 220 }}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={obj.months as Record<string, unknown>[]}>
            <CartesianGrid stroke="#e5e5e7" vertical={false} />
            <XAxis dataKey="month" tick={{ fontSize: 11 }} />
            <YAxis hide />
            <Tooltip />
            <Area type="monotone" dataKey="paid" stackId="1" stroke="#2995ff" fill="#2995ff" fillOpacity={0.5} />
            <Area type="monotone" dataKey="unpaid" stackId="1" stroke="#c7d2fe" fill="#c7d2fe" fillOpacity={0.5} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    );
  }
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

  const nav = [
    { label: "Home", icon: HomeIcon, active: activeView === "home", onClick: () => setActiveView("home") },
    { label: "Dashboard", icon: LayoutDashboard, active: activeView === "dashboard", onClick: () => setActiveView("dashboard") },
    { label: "Ask Loupe", icon: MessageSquareText, active: activeView === "ask", onClick: () => setActiveView("ask") },
    { label: "Performance", icon: ChartNoAxesCombined, active: activeView === "performance", onClick: () => setActiveView("performance") },
  ];

  const sortedCategoryRows = categoryRows ? [...categoryRows].sort((a, b) => b[sortMetric] - a[sortMetric]).slice(0, 15) : null;
  const rankedStateRows = stateRows ? [...stateRows].sort((a, b) => b.revenue - a.revenue).slice(0, 15) : null;
  const maxStateRevenue = rankedStateRows?.length ? Math.max(...rankedStateRows.map((s) => s.revenue)) : 0;

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
          </>}

          {activeView === "dashboard" && <>
            <section>
              <div className="section-title"><SlidersHorizontal size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />Filters</div>
              <Card>
                <div className="filters-row">
                  <label className="filter-field"><span className="muted small">Start date</span><input type="date" className="select" value={startDate} max={endDate} onChange={(e) => setStartDate(e.target.value)} /></label>
                  <label className="filter-field"><span className="muted small">End date</span><input type="date" className="select" value={endDate} min={startDate} onChange={(e) => setEndDate(e.target.value)} /></label>
                  <label className="filter-field"><span className="muted small">Category</span><select multiple className="select" value={selectedCategories} onChange={(e) => setSelectedCategories(Array.from(e.target.selectedOptions).map((o) => o.value))}>{ALL_CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}</select></label>
                  <label className="filter-field"><span className="muted small">State</span><select multiple className="select" value={selectedStates} onChange={(e) => setSelectedStates(Array.from(e.target.selectedOptions).map((o) => o.value))}>{ALL_STATES.map((s) => <option key={s} value={s}>{s}</option>)}</select></label>
                  {(selectedCategories.length > 0 || selectedStates.length > 0) && <button className="button" onClick={() => { setSelectedCategories([]); setSelectedStates([]) }}>Clear filters</button>}
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

            <section><div className="section-title">Revenue &amp; margin trend</div><Card><div className="chart-frame"><ResponsiveContainer width="100%" height="100%"><AreaChart data={data.trend}><defs><linearGradient id="revenueFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#2995ff" stopOpacity={.25} /><stop offset="1" stopColor="#2995ff" stopOpacity={0} /></linearGradient><linearGradient id="marginFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#8b5cf6" stopOpacity={.2} /><stop offset="1" stopColor="#8b5cf6" stopOpacity={0} /></linearGradient></defs><CartesianGrid stroke="#e5e5e7" vertical={false} /><XAxis dataKey="period" tickLine={false} axisLine={false} tick={{ fill: "#85868b", fontSize: 12 }} /><YAxis hide /><Tooltip formatter={(v) => money(Number(v))} /><Area type="monotone" name="Revenue" dataKey="revenue" stroke="#2995ff" strokeWidth={3} fill="url(#revenueFill)" /><Area type="monotone" name="Margin" dataKey="margin" stroke="#8b5cf6" strokeWidth={3} fill="url(#marginFill)" /></AreaChart></ResponsiveContainer></div></Card></section>

            <section><div className="insight-grid">
              <Card>
                <div className="card-head"><div><h2><Shirt size={16} style={{ verticalAlign: "-2px", marginRight: 6 }} />Category leaderboard</h2><div className="muted small">Top 15 by {sortMetric.replaceAll("_", " ")}</div></div>
                  <div className="actions">
                    <select className="select" value={sortMetric} onChange={(e) => setSortMetric(e.target.value as typeof sortMetric)}><option value="revenue">Revenue</option><option value="margin">Margin</option><option value="return_rate_pct">Return rate</option></select>
                    {categoryRows && <button className="button" onClick={() => downloadCsv("category_breakdown.csv", categoryRows)}><Download size={14} />CSV</button>}
                  </div>
                </div>
                {!sortedCategoryRows ? <div className="muted small">Loading category leaderboard&hellip;</div> : sortedCategoryRows.length === 0 ? <div className="muted small">No category data in this window.</div> : <div className="chart-frame" style={{ height: 340 }}><ResponsiveContainer width="100%" height="100%"><BarChart data={sortedCategoryRows} layout="vertical" margin={{ left: 110 }}><CartesianGrid stroke="#e5e5e7" horizontal={false} /><XAxis type="number" tick={{ fontSize: 11 }} tickFormatter={(v) => sortMetric === "return_rate_pct" ? `${v}%` : money(Number(v))} /><YAxis type="category" dataKey="category" width={130} tick={{ fontSize: 11 }} /><Tooltip formatter={(v) => sortMetric === "return_rate_pct" ? `${v}%` : money(Number(v))} /><Bar dataKey={sortMetric} fill="#2995ff" /></BarChart></ResponsiveContainer></div>}
              </Card>
              <Card>
                <div className="card-head"><div><h2><MapPin size={16} style={{ verticalAlign: "-2px", marginRight: 6 }} />Revenue by state</h2><div className="muted small">Top 15 states</div></div>
                  {stateRows && <button className="button" onClick={() => downloadCsv("state_breakdown.csv", stateRows)}><Download size={14} />CSV</button>}
                </div>
                {!rankedStateRows ? <div className="muted small">Loading state breakdown&hellip;</div> : rankedStateRows.length === 0 ? <div className="muted small">No state data in this window.</div> : <div className="state-bars">{rankedStateRows.map((s) => <div key={s.state} className="state-bar-row"><span className="state-bar-label">{s.state_abbrev || s.state}</span><span className="state-bar-track"><span className="state-bar-fill" style={{ width: `${maxStateRevenue ? (s.revenue / maxStateRevenue) * 100 : 0}%` }} /></span><span className="state-bar-value muted small">{money(s.revenue)}</span></div>)}</div>}
              </Card>
            </div></section>

            <section><div className="section-title"><Megaphone size={13} style={{ verticalAlign: "-2px", marginRight: 4 }} />Paid vs. organic channel mix</div><Card>
              <div className="card-head"><div><h2>Monthly order mix</h2><div className="muted small">Denominator: order_item count</div></div>{channelMonths && <button className="button" onClick={() => downloadCsv("channel_mix.csv", channelMonths)}><Download size={14} />CSV</button>}</div>
              {!channelMonths ? <div className="muted small">Loading channel mix&hellip;</div> : channelMonths.length === 0 ? <div className="muted small">No channel data in this window.</div> : <div className="chart-frame"><ResponsiveContainer width="100%" height="100%"><AreaChart data={channelMonths}><CartesianGrid stroke="#e5e5e7" vertical={false} /><XAxis dataKey="month" tick={{ fill: "#85868b", fontSize: 12 }} /><YAxis hide /><Tooltip /><Area type="monotone" name="Paid" dataKey="paid" stackId="1" stroke="#2995ff" fill="#2995ff" fillOpacity={0.5} /><Area type="monotone" name="Unpaid (organic/search)" dataKey="unpaid" stackId="1" stroke="#c7d2fe" fill="#c7d2fe" fillOpacity={0.5} /></AreaChart></ResponsiveContainer></div>}
            </Card></section>
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
                <div className="chart-frame"><ResponsiveContainer width="100%" height="100%"><AreaChart data={data.trend}><CartesianGrid stroke="#e5e5e7" vertical={false} /><XAxis dataKey="period" tickLine={false} axisLine={false} tick={{ fill: "#85868b", fontSize: 12 }} /><YAxis hide /><Tooltip formatter={(v) => money(Number(v))} /><Area type="monotone" name="Revenue" dataKey="revenue" stroke="#2995ff" strokeWidth={3} fill="none" /><Area type="monotone" name="Margin" dataKey="margin" stroke="#8b5cf6" strokeWidth={3} fill="none" /></AreaChart></ResponsiveContainer></div>
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
          </>}

          {activeView === "ask" && <section><div className="section-title">Ask Loupe</div><Card>
            <div className="card-head"><div><h2>Ask a question, get a grounded answer</h2><div className="muted small">Grounded answers with metric and source context</div></div></div>
            <div className="actions">{samplePrompts.map((p) => <button type="button" key={p} className="button" onClick={() => setQuestion(p)}>{p}</button>)}</div>
            <form className="ask-row" onSubmit={async (e) => {
              e.preventDefault(); if (!question.trim()) return; setAsking(true); setAnswer(null);
              try {
                const response = await fetch(`${API_BASE}/api/v1/loupe/ask`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question }) });
                const body = await response.json();
                if (response.ok) { setAnswer({ ...body, answer: looksUnconfigured(body.answer ?? "") ? ASSISTANT_UNAVAILABLE : body.answer }) }
                else { setAnswer({ category: "general", answer: body.detail, source_health_status: null, source_health_warning: null, raw_data: null }) }
              } catch { setAnswer({ category: "general", answer: "Loupe could not be reached.", source_health_status: null, source_health_warning: null, raw_data: null }) }
              finally { setAsking(false) }
            }}>
              <input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="Which categories drove margin growth?" aria-label="Ask Loupe" />
              <button className="button primary" disabled={asking || !question.trim()}>{asking ? "Asking…" : "Ask"}</button>
            </form>
            {answer && (answer.answer === ASSISTANT_UNAVAILABLE ? <div className="health-warning">{answer.answer}</div> : <div className="answer-block">
              <div className="actions" style={{ marginBottom: 10 }}>
                <Badge>{answer.category.replaceAll("_", " ")}</Badge>
                {answer.source_health_status && <Badge tone={answer.source_health_status === "healthy" ? "accent" : "warning"}>{answer.source_health_status}</Badge>}
              </div>
              <AskEvidence data={answer.raw_data} />
              <div className="answer"><Sparkles size={16} /><div>{renderMarkdown(answer.answer)}</div></div>
              {answer.source_health_warning && <div className="health-warning">{answer.source_health_warning}</div>}
            </div>)}
          </Card></section>}
        </>}
      </div>
    </AppShell>
  );
}

function Stat({ label, value, change }: { label: string; value: string; change: string }) {
  return <Card><div className="stat-label">{label}</div><div className="stat-line"><span className="stat-value">{value}</span><span className="delta">{change}</span></div></Card>;
}
