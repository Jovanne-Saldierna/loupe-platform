"use client";

import { useEffect, useRef, useState } from "react";
import { CheckCircle2, Copy, FileText, GitBranch, GitCompare, Library, ListChecks, ScanText, Sparkles, ShieldCheck, TriangleAlert } from "lucide-react";
import {
  ActionFeed, AppShell, AskLoupePanel, AssetImpactList, Badge, Card, ChangeRiskList, ChipList, CodeBlock,
  CompletenessChecklist, FactPairGrid, GrainSummary, ReasoningBreakdown, RecommendationCards, RecommendationList,
  SectionCard, SimpleList, Unavailable,
} from "@loupe/ui";
import type { ChangeRiskCategory, CompletenessCheckItem, FeedPriority, GovernanceRecommendationItem, HelperMessage } from "@loupe/ui";

type Metric={
  name:string;version:string;certification_status:string;measurement_grain:string;
  owner:string|null;description:string|null;formula:string|null;approved_source_tables:string[];
  freshness_expectation:string|null;downstream_dashboards:string[];required_filters:string[];
  last_reviewed_at:string|null;source_health:string|null;active_incident_ids:string[];
  completeness:CompletenessCheckItem[];completeness_score:number;
};
type TrustFactor={name:string;points:number;reason:string};
type Review={
  metric:Metric;review_score:number;summary:string;findings:{severity:string;category:string;message:string}[];
  trust_score:number;trust_band:string;scoring_version:string;trust_factors:TrustFactor[];
  recommended_next_steps:string[];referenced_tables:string[];source_health:string;active_incident_ids:string[];
  override_reason:string|null;alignment:{contract:string;expected:string;observed:string;status:string}[];
  downstream_assets:string[];change_risk:ChangeRiskCategory[];recommendations:GovernanceRecommendationItem[];
};
type GovernanceView = "catalog" | "sqlReview" | "metricAlignment" | "definitionDiff" | "impact" | "recommendations" | "stewardSummary";

// Per the product-hardening pass: every suggested prompt should read as a
// governance decision a reviewer or steward actually needs answered before
// approving a metric for reporting -- not a generic "explain the score"
// prompt. Shared by both the SQL Review and Steward Summary Ask Loupe
// panels, which already share the same helper state/backend contract, so
// there is one consistent set of governance questions across the app
// instead of two overlapping lists.
const HELPER_PROMPTS = [
  "Is this safe for executive reporting?",
  "What changed from the governed definition?",
  "What downstream assets are affected?",
  "What should I fix before approval?",
  "Draft a governance summary for stakeholders.",
];

// --- Grain readability -------------------------------------------------
// Governance's persisted MetricDefinition.measurement_grain strings follow a
// "<short grain> -- <technical notes>" pattern (see
// shared/metric_catalog.py; api/services/governance_review.py already
// splits on the same " -- " separator to build ContractAlignment's grain
// label). The short label is genuinely useful on its own, but a business/BI
// reader also needs the two sentences a data engineer would say out loud:
// what one row represents, and why that grain choice matters operationally.
// This is a frontend-only display concern -- it changes no scoring, no
// alignment logic, no persisted data. The five entries below are written
// directly from each metric's real catalog description/measurement_grain
// text (shared/metric_catalog.py), not invented; unrecognised metrics fall
// back to a generic paraphrase of their own short grain label.
const GRAIN_MEANING: Record<string, { meaning: string; whyItMatters: string }> = {
  revenue: {
    meaning: "Each row represents one order item, not one order or session. Its sale price adds directly to revenue.",
    whyItMatters: "Keeps revenue additive across any grouping (day, category, state, whole window) and prevents double counting.",
  },
  margin: {
    meaning: "Each row represents one order item paired with its product cost, not one order or session.",
    whyItMatters: "Keeps gross margin dollars additive across any grouping the same way revenue is, so the two stay comparable.",
  },
  return_rate: {
    meaning: "Both the numerator (returned items) and denominator (all items) are counted at the same order-item grain, in the same filter scope.",
    whyItMatters: "Mixing grains between numerator and denominator (e.g. orders vs. items) would silently distort the rate.",
  },
  margin_leakage: {
    meaning: "Margin lost is summed from individual returned order items, then grouped by category or product for presentation.",
    whyItMatters: "Keeps the ranking based on real dollars lost, not a rate that could hide a small-dollar, high-percentage return.",
  },
  channel_mix: {
    meaning: "Each order item is attributed to the traffic source of the user who placed it -- one row per line item, not one per order.",
    whyItMatters: "The underlying `order_count` column reads as order-grain; it isn't. Treating it as such would overstate channel share.",
  },
};

function describeGrain(metricName: string, rawGrain: string): { short: string; meaning: string; whyItMatters: string; technicalDetail?: string } {
  const [short, ...rest] = rawGrain.split(" -- ");
  const technicalDetail = rest.join(" -- ").trim() || undefined;
  const known = GRAIN_MEANING[metricName];
  if (known) return { short: short.trim(), ...known, technicalDetail };
  return {
    short: short.trim(),
    meaning: `Each row represents one ${short.trim().replaceAll("_", " ")}.`,
    whyItMatters: "Declaring the grain keeps every team aggregating this metric at the same level instead of silently mixing grains.",
    technicalDetail,
  };
}

// --- Governance completeness explanations -------------------------------
// The seven checks below are a fixed, known set defined deterministically in
// apps/metric_governance/remediation.py's derive_governance_completeness()
// and never change at runtime. This lookup supplies plain-language
// "meaning / why it matters / what good looks like" copy per check label --
// display-only text describing what each requirement means in general, kept
// separate from the check's own pass/fail `detail`, which stays this
// specific metric's deterministic evidence.
const COMPLETENESS_EXPLANATIONS: Record<string, { meaning: string; whyItMatters: string; goodState: string }> = {
  "Has owner": {
    meaning: "A named person or team is accountable for this metric's definition.",
    whyItMatters: "Without an owner, nobody is on the hook to review changes, resolve incidents, or answer questions about the definition.",
    goodState: "Owner is on file and reachable for review or escalation.",
  },
  "Has certified definition": {
    meaning: "The definition has been formally reviewed and certified, not just proposed or pending validation.",
    whyItMatters: "Uncertified definitions haven't been checked against the certification bar (name, formula, grain, sources, filters, owner, freshness) -- treat their numbers with caution.",
    goodState: "Certification status is \"certified\".",
  },
  "Has declared grain": {
    meaning: "Metric defines the level of measurement -- what one row represents.",
    whyItMatters: "Prevents teams from mixing order-level, item-level, and session-level calculations.",
    goodState: "Grain is documented and matches the submitted SQL.",
  },
  "Has approved source tables": {
    meaning: "The specific tables this metric is allowed to read from are documented.",
    whyItMatters: "Queries that pull from unapproved tables can silently diverge from the governed definition.",
    goodState: "At least one approved source table is on file.",
  },
  "Has freshness/SLA expectation": {
    meaning: "How current this metric's underlying data is expected to be.",
    whyItMatters: "Without a freshness expectation, stale data can flow into reporting with no one noticing.",
    goodState: "A freshness expectation is declared, not \"undeclared\".",
  },
  "Has downstream usage documented": {
    meaning: "Which dashboards, reports, or agent views consume this metric.",
    whyItMatters: "Without this, a definition change can break downstream assets with no way to know who to warn first.",
    goodState: "At least one downstream dashboard or report is on file.",
  },
  "No active incident blocking trust": {
    meaning: "None of this metric's source tables currently have an open data-quality incident.",
    whyItMatters: "An active incident on a source table means this metric's numbers may already be wrong.",
    goodState: "Zero active incidents on this metric's source tables.",
  },
};

function enrichCompleteness(items: CompletenessCheckItem[]): CompletenessCheckItem[] {
  return items.map((it) => ({ ...it, ...COMPLETENESS_EXPLANATIONS[it.label] }));
}

// --- Definition-change risk explanations ---------------------------------
// The five categories below are the fixed set derived deterministically in
// apps/metric_governance/remediation.py's derive_change_risk() and never
// change at runtime. This lookup supplies general "what this drift type
// means / why it matters to business trust" copy per category -- separate
// from the category's own `detail`, which stays this specific review's
// deterministic finding.
const CHANGE_RISK_EXPLANATIONS: Record<string, { meaning: string; whyItMatters: string }> = {
  "Calculation drift": {
    meaning: "The submitted SQL's projection or join logic doesn't match how the certified formula computes this metric.",
    whyItMatters: "Even a small calculation difference can move the reported number without anyone changing the certified definition.",
  },
  "Source table mismatch": {
    meaning: "The query reads from a table that isn't on this metric's approved source-table list.",
    whyItMatters: "Unapproved tables can have different grain, filters, or freshness than the governed source -- the number may not mean the same thing.",
  },
  "Grain mismatch": {
    meaning: "The query's aggregation level doesn't match the metric's declared grain.",
    whyItMatters: "Mixing grains (e.g. order-level vs. item-level) silently over- or under-counts the result.",
  },
  "Filter/status mismatch": {
    meaning: "The query is missing a required filter (e.g. an order-status condition) the certified definition depends on.",
    whyItMatters: "Dropping a required filter can pull in rows the certified definition deliberately excludes.",
  },
  "Freshness/SLA mismatch": {
    meaning: "This metric's source tables are not currently meeting their declared freshness/SLA expectation.",
    whyItMatters: "Stale source data means the number may be technically correct but operationally out of date.",
  },
};

function enrichChangeRisk(items: ChangeRiskCategory[]): ChangeRiskCategory[] {
  return items.map((it) => ({ ...it, ...CHANGE_RISK_EXPLANATIONS[it.category] }));
}

// --- Recommendation operational detail ------------------------------------
// Adds suggested-owner / next-step / blocks-approval copy to each
// deterministic recommendation, derived entirely from fields already on
// screen (the recommendation's own action/priority, and the metric's owner)
// -- never a new fact the backend didn't already surface. Priority itself
// already carries an implicit meaning (info/required/blocking); this only
// makes that meaning explicit and operational for a data steward.
function enrichRecommendation(item: GovernanceRecommendationItem, metric: Metric): GovernanceRecommendationItem {
  const actionImpliesOwnerWork = /owner/i.test(item.action);
  const suggestedOwner = actionImpliesOwnerWork
    ? "Steward review required -- this action is to assign one."
    : metric.owner || "Owner missing -- steward review required.";
  const nextStep = item.priority === "blocking"
    ? "Resolve before this metric can be approved for reporting."
    : item.priority === "required"
    ? "Address before certifying or promoting this definition."
    : "Track as a documentation or process follow-up.";
  const blocksApproval = item.priority === "blocking" ? "Yes -- blocks certification/approval until resolved." : undefined;
  return { ...item, suggestedOwner, nextStep, blocksApproval };
}

// A realistic, reviewable starter query against the same governed ecommerce
// tables/columns the deterministic review already knows about (order_items,
// products) -- so a first-time user has something real to run instead of a
// blank textarea. Loading it never runs the review itself; it only fills the
// textarea, same as pasting the SQL by hand.
const EXAMPLE_SQL = `SELECT
  p.category,
  SUM(oi.sale_price) AS revenue,
  SUM(oi.sale_price - p.cost) AS gross_margin,
  SAFE_DIVIDE(SUM(oi.sale_price - p.cost), SUM(oi.sale_price)) AS gross_margin_pct
FROM \`bigquery-public-data.thelook_ecommerce.order_items\` AS oi
JOIN \`bigquery-public-data.thelook_ecommerce.products\` AS p
  ON oi.product_id = p.id
WHERE oi.status = 'Complete'
GROUP BY p.category
ORDER BY gross_margin DESC;`;

// Governance-specific mapping from a finding's severity to the shared
// ActionFeed's priority dot -- mirrors the original binary icon rule below
// (severity==="low" is the only distinguished case; anything else was
// already rendered as a warning) rather than inventing a new severity
// scale that isn't actually present in the review model.
function findingPriority(severity: string): FeedPriority {
  return severity === "low" ? "info" : "high";
}

function certBadgeTone(status: string): "accent" | "neutral" | "warning" {
  return status === "certified" ? "accent" : "warning";
}

// A trust band already carries an implicit governance verdict
// (shared/models.py's _TRUST_BAND_VALUES: high_trust / review_required /
// do_not_rely). This makes that verdict explicit as a one-line decision
// sentence for the brief -- a pure label of an already-computed field, not
// a new judgment call.
function decisionForTrustBand(band: string): string {
  if (band === "high_trust") return "Approve for executive reporting.";
  if (band === "do_not_rely") return "Do not rely on this metric for executive reporting until the risks below are resolved.";
  return "Needs review before executive reporting.";
}

// Formats the "copyable governance brief" block from fields the app already
// has on screen -- the same metric-card and (when present) review fields
// rendered elsewhere on this page, arranged into clearly labeled sections
// (Metric/Owner/Certification/Grain/.../Decision/Recommended next step) with
// blank lines between them so it pastes cleanly into a ticket, PRD, Slack
// update, or metric registry note instead of reading as one dense block.
// This is pure string formatting, never a new judgment: it picks no score,
// no recommendation, no risk that wasn't already deterministically computed
// by the backend.
function buildGovernanceBrief(metric: Metric, review: Review | null): string {
  const reviewMatches = review !== null && review.metric.name === metric.name;
  const grain = describeGrain(metric.name, metric.measurement_grain);
  const sections: string[] = [
    [
      `Metric: ${metric.name}`,
      `Owner: ${metric.owner || "Unassigned"}`,
      `Certification: ${metric.certification_status.replaceAll("_"," ")}`,
      `Grain: ${grain.short} -- ${grain.meaning}`,
    ].join("\n"),
    [
      `Approved sources: ${metric.approved_source_tables.length?metric.approved_source_tables.join(", "):"None on file"}`,
      `Downstream assets: ${metric.downstream_dashboards.length?metric.downstream_dashboards.join("; "):"None on file"}`,
    ].join("\n"),
  ];
  if (reviewMatches && review) {
    sections.push(`Current review outcome: Trust score ${review.trust_score}/100 (${review.trust_band.replaceAll("_"," ")}) -- ${review.summary}`);
    const risks = review.change_risk.filter(c=>c.status==="risk").map(c=>`- ${c.category}: ${c.detail}`);
    const incidentNote = review.active_incident_ids.length?`- Active incidents: ${review.active_incident_ids.join(", ")}.`:null;
    const riskLines = [...risks, ...(incidentNote?[incidentNote]:[])];
    sections.push(`Risks:\n${riskLines.length?riskLines.join("\n"):"None identified in this review."}`);
    sections.push(`Decision: ${decisionForTrustBand(review.trust_band)}`);
    const topRec = review.recommendations[0];
    sections.push(`Recommended next step: ${topRec?`${topRec.action} -- ${topRec.rationale}`:"No recommendation generated yet."}`);
  } else {
    const incidentNote = metric.active_incident_ids.length?`Active incidents: ${metric.active_incident_ids.join(", ")}.`:null;
    sections.push("Current review outcome: No SQL review run yet for this metric.");
    sections.push(`Risks:\n${incidentNote||"No SQL review run yet -- run one in SQL Review to surface change risk."}`);
    sections.push("Decision: No SQL review run yet -- run one before making a governance decision.");
    sections.push("Recommended next step: Run a SQL review in SQL Review to get a deterministic governance decision.");
  }
  return sections.join("\n\n");
}

export default function Page(){
  const api=process.env.NEXT_PUBLIC_API_BASE_URL??"http://localhost:8000";
  const [metrics,setMetrics]=useState<Metric[]>([]); const [metric,setMetric]=useState(""); const [sql,setSql]=useState(""); const [reviewedSql,setReviewedSql]=useState(""); const [review,setReview]=useState<Review|null>(null); const [error,setError]=useState<string|null>(null); const [running,setRunning]=useState(false);
  const [activeView,setActiveView]=useState<GovernanceView>("catalog");
  const [helperMessages,setHelperMessages]=useState<HelperMessage[]>([]); const [helperQuestion,setHelperQuestion]=useState(""); const [helperAsking,setHelperAsking]=useState(false);
  const nextHelperId=useRef(0);
  useEffect(()=>{fetch(`${api}/api/v1/governance/catalog`).then(async r=>{if(!r.ok)throw new Error();return r.json()}).then(data=>{setMetrics(data.metrics);setMetric(data.metrics[0]?.name??"")}).catch(()=>setError("The persisted metric catalog could not be reached. No local catalog was substituted."));},[api]);
  const selectedCatalogMetric = metrics.find(m=>m.name===metric) ?? null;
  async function runReview(){if(!sql.trim()||!metric)return;setRunning(true);setError(null);setReview(null);setHelperMessages([]);try{const response=await fetch(`${api}/api/v1/governance/sql-review`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({sql,metric_name:metric})});if(!response.ok)throw new Error();setReview(await response.json());setReviewedSql(sql)}catch{setError("The deterministic review could not be completed. No fabricated score was shown.")}finally{setRunning(false)}}
  // Grounded solely in the review that's already on screen -- the same
  // metric/sql/score/findings/factors/steps/tables/health/incidents/
  // downstream-assets/change-risk/recommendations already rendered across
  // the tabs, sent back verbatim so the helper cannot narrate a score,
  // finding, incident, or recommendation the deterministic review didn't
  // already produce (see api/services/governance_helper.py).
  async function askHelper(q:string){
    if(!review)return;
    const id=String(nextHelperId.current++);
    setHelperQuestion("");setHelperAsking(true);
    setHelperMessages(prev=>[...prev,{id,question:q,answer:null}]);
    try{
      const response=await fetch(`${api}/api/v1/governance/helper`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
        question:q,metric:review.metric,sql:reviewedSql,review_score:review.review_score,summary:review.summary,
        findings:review.findings,trust_score:review.trust_score,trust_band:review.trust_band,trust_factors:review.trust_factors,
        recommended_next_steps:review.recommended_next_steps,referenced_tables:review.referenced_tables,
        source_health:review.source_health,active_incident_ids:review.active_incident_ids,override_reason:review.override_reason,
        downstream_assets:review.downstream_assets,change_risk:review.change_risk,recommendations:review.recommendations,
        completeness:review.metric.completeness,
      })});
      const body=await response.json();
      const answer=response.ok?body.answer:body.detail??"Loupe could not produce a grounded answer right now.";
      setHelperMessages(prev=>prev.map(m=>m.id===id?{...m,answer}:m));
    }catch{
      setHelperMessages(prev=>prev.map(m=>m.id===id?{...m,answer:"Loupe could not be reached."}:m));
    }finally{
      setHelperAsking(false);
    }
  }
  const nav = [
    {label:"Catalog",icon:Library,active:activeView==="catalog",onClick:()=>setActiveView("catalog")},
    {label:"SQL Review",icon:ScanText,active:activeView==="sqlReview",onClick:()=>setActiveView("sqlReview")},
    {label:"Metric Alignment",icon:GitCompare,active:activeView==="metricAlignment",onClick:()=>setActiveView("metricAlignment")},
    {label:"Definition Diff",icon:TriangleAlert,active:activeView==="definitionDiff",onClick:()=>setActiveView("definitionDiff")},
    {label:"Impact",icon:GitBranch,active:activeView==="impact",onClick:()=>setActiveView("impact")},
    {label:"Recommendations",icon:ListChecks,active:activeView==="recommendations",onClick:()=>setActiveView("recommendations")},
    {label:"Steward Summary",icon:FileText,active:activeView==="stewardSummary",onClick:()=>setActiveView("stewardSummary")},
  ];
  return <AppShell active="governance" brand="Governance" brandIcon={ShieldCheck} navigation={nav}>
    <div className="dashboard-surface">
      <header className="hero-panel page-header"><div><div className="eyebrow">DEFINITION LAYER</div><h1>Metric trust &amp; governance</h1><div className="muted">Catalog, review, and trace impact for every governed metric</div></div></header>
      {error&&<Unavailable message={error}/>}

      {activeView==="catalog"&&<section><div className="section-title">Persisted metric catalog</div>
        <div className="two-col-layout">
          <SectionCard icon={Library} title="Governed metrics" description="Select a metric to see its full catalog detail" action={<Badge>{metrics.length} metrics</Badge>}>
            {metrics.length?<SimpleList items={metrics.map(m=>({id:m.name,name:m.name,meta:`${m.certification_status.replaceAll("_"," ")} · v${m.version}`}))} selectedId={metric} onSelect={setMetric}/>:<div className="empty-review"><Library size={24}/><strong>No catalog entries yet</strong><span className="muted small">Persisted metric definitions will appear here once available.</span></div>}
          </SectionCard>
          {selectedCatalogMetric?<SectionCard icon={Library} title={selectedCatalogMetric.name} description={selectedCatalogMetric.description||"No business definition on file."} action={<Badge tone={certBadgeTone(selectedCatalogMetric.certification_status)}>{selectedCatalogMetric.certification_status.replaceAll("_"," ")}</Badge>}>
            {/* Compact metadata grid -- owner, short grain, freshness/SLA,
                version, source health -- so the card is scannable in one
                glance instead of the raw grain paragraph dominating it. */}
            <FactPairGrid items={[
              {label:"Owner",value:selectedCatalogMetric.owner||"Unassigned"},
              {label:"Grain",value:describeGrain(selectedCatalogMetric.name, selectedCatalogMetric.measurement_grain).short},
              {label:"Freshness / SLA",value:selectedCatalogMetric.freshness_expectation||"Undeclared"},
              {label:"Version",value:selectedCatalogMetric.version},
              ...(selectedCatalogMetric.source_health?[{label:"Source health",value:selectedCatalogMetric.source_health,tone:(selectedCatalogMetric.source_health==="healthy"?"up":"down") as "up"|"down"}]:[]),
            ]}/>
            <GrainSummary {...describeGrain(selectedCatalogMetric.name, selectedCatalogMetric.measurement_grain)} grain={describeGrain(selectedCatalogMetric.name, selectedCatalogMetric.measurement_grain).short}/>
            <ChipList title="Approved source tables" items={selectedCatalogMetric.approved_source_tables} emptyLabel="No approved source tables on file."/>
            <AssetImpactList title="Downstream dashboards &amp; reports" items={selectedCatalogMetric.downstream_dashboards} emptyLabel="No downstream dashboards or reports on file."/>
            <ChipList title={selectedCatalogMetric.active_incident_ids.length?`Known risks / open incidents (${selectedCatalogMetric.active_incident_ids.length})`:"Known risks / open incidents"} items={selectedCatalogMetric.active_incident_ids} tone="down" emptyLabel="No open incidents on this metric's source tables."/>
          </SectionCard>:<Card><div className="empty-review"><Library size={24}/><strong>No metric selected</strong><span className="muted small">Select a metric on the left to see its full detail.</span></div></Card>}
        </div>
      </section>}

      {activeView==="sqlReview"&&<section><div className="section-title">Review workspace</div><div className="card-head" style={{marginBottom:16}}><div><h2>Submit for review</h2><div className="muted small">Choose a persisted metric definition, then run the deterministic review</div></div><div className="actions"><select className="select" value={metric} onChange={e=>setMetric(e.target.value)} aria-label="Metric definition">{metrics.map(m=><option key={m.name} value={m.name}>{m.name} · {m.version}</option>)}</select><button className="button primary" disabled={running||!metric||!sql.trim()} onClick={runReview}>{running?"Reviewing…":"Run review"}</button></div></div><div className="review-layout"><SectionCard icon={ScanText} title="Submitted query" description="BigQuery SQL" action={<div className="actions"><button className="button ghost" onClick={()=>setSql(EXAMPLE_SQL)}><Sparkles size={15}/>Load example SQL</button><button className="button ghost" onClick={()=>navigator.clipboard.writeText(sql)}><Copy size={15}/>Copy</button></div>}><textarea className="code-input" value={sql} onChange={e=>setSql(e.target.value)} placeholder="Paste a read-only BigQuery query for deterministic review…" aria-label="Submitted BigQuery SQL"/></SectionCard>{review?<SectionCard icon={ShieldCheck} title="Trust score" description={`Deterministic · ${review.scoring_version}`} action={<div className="score" style={{"--score":`${review.trust_score}%`} as React.CSSProperties}><span>{review.trust_score}</span></div>}>{review.findings.length>0&&<div className="action-feed-wrap"><ActionFeed items={review.findings.map(f=>({icon:f.severity==="low"?CheckCircle2:TriangleAlert,title:`${f.category}: ${f.message}`,metric:f.severity,priority:findingPriority(f.severity)}))}/></div>}{review.trust_factors.length>0&&<><div className="section-subtitle">Score contributions</div><p className="muted small">Deterministic checks contributing to this trust score.</p><ReasoningBreakdown items={review.trust_factors.map(f=>({label:f.name,points:f.points,reason:f.reason}))}/></>}{review.recommended_next_steps.length>0&&<RecommendationList title="Recommended next steps" items={review.recommended_next_steps}/>}{review.override_reason&&<div className="callout callout-info"><div className="callout-title"><ShieldCheck size={14}/>Override reason</div><p>{review.override_reason}</p></div>}<ChipList title="Referenced tables" items={review.referenced_tables}/>{review.source_health&&<div className="confidence-rows"><div className="confidence-row"><span>Source health</span><Badge tone={review.source_health==="healthy"?"accent":"warning"}>{review.source_health}</Badge></div></div>}<ChipList title={review.active_incident_ids.length?`Active incidents (${review.active_incident_ids.length})`:"Active incidents"} items={review.active_incident_ids} tone="down" emptyLabel={review.source_health?"No active incidents linked.":undefined}/><p className="muted small">See Definition Diff, Impact, and Recommendations for the full trust picture.</p></SectionCard>:<Card><div className="empty-review"><ScanText size={24}/><strong>Ready for deterministic review</strong><span className="muted small">Load the example SQL or paste your own query, choose a governed metric, then run review.</span></div></Card>}</div>
        <div className="section-title ask-loupe-gap">Loupe AI helper</div>
        <AskLoupePanel
          title="Ask Loupe"
          status={review?`Grounded in this review · trust score ${review.trust_score}`:"Waiting on a review"}
          messages={helperMessages}
          question={helperQuestion}
          onQuestionChange={setHelperQuestion}
          onAsk={askHelper}
          asking={helperAsking}
          disabled={!review}
          disabledMessage="Run a review first, then ask Loupe what the score means."
          placeholder="Ask about this review's score, findings, or safety for reporting…"
          samplePrompts={HELPER_PROMPTS}
        />
      </section>}

      {activeView==="metricAlignment"&&<section><div className="section-title">Definition evidence</div>{review?<SectionCard icon={GitCompare} title="Metric alignment" description="Definition evidence and query contract" action={<Badge tone={review.trust_band==="high_trust"?"accent":review.trust_band==="do_not_rely"?"warning":"neutral"}>{review.trust_band.replaceAll("_"," ")}</Badge>}>
        <div className="alignment-stack">
          <div className="callout callout-info callout-compact">
            <div className="callout-title"><GitCompare size={14}/>What this table compares</div>
            <p>Each row compares what the certified metric definition contracts for (Expected) against what the submitted SQL actually does (Observed). Aligned means the query matches the governed contract on that point; Review means it diverges and a reviewer should confirm the divergence is intentional before this metric is trusted for reporting.</p>
          </div>
          <FactPairGrid items={[{label:"Review score",value:`${review.review_score}/100`},{label:"Trust score",value:`${review.trust_score}/100`,tone:review.trust_band==="do_not_rely"?"down":review.trust_band==="high_trust"?"up":undefined},{label:"Trust band",value:review.trust_band.replaceAll("_"," ")}]}/>
          <div className="table-wrap alignment-table-wrap"><table className="data-table"><thead><tr><th>Contract</th><th>Expected</th><th>Observed</th><th>Status</th></tr></thead><tbody>{review.alignment.map(row=><tr key={row.contract}><td>{row.contract}</td><td>{row.expected}</td><td>{row.observed}</td><td><Badge tone={row.status==="Aligned"?"accent":"warning"}>{row.status}</Badge></td></tr>)}</tbody></table></div>
          <p className="muted small">If a row shows Review, check Definition Diff for the underlying drift category, then Recommendations for what to do next.</p>
        </div>
      </SectionCard>:<Card><div className="empty-review"><GitCompare size={24}/><strong>No alignment evidence yet</strong><span className="muted small">Run a review in SQL Review to see how the query maps to the governed definition contract.</span></div></Card>}</section>}

      {activeView==="definitionDiff"&&<section><div className="section-title">Definition change risk</div>
        {review?<>
          <SectionCard icon={TriangleAlert} title="Current vs. proposed logic" description={`${review.metric.name} · certified formula vs. submitted SQL`}>
            <div className="definition-diff-columns">
              <CodeBlock title="Current governed formula" code={review.metric.formula||"No formula on file."} badge={review.metric.certification_status.replaceAll("_"," ")}/>
              <CodeBlock title="Submitted SQL (proposed logic)" code={reviewedSql||"No SQL submitted yet."} badge="From SQL Review"/>
            </div>
          </SectionCard>
          <SectionCard icon={TriangleAlert} title="Definition-change risk categories" description="Derived from the deterministic SQL review and metric metadata -- not a formal diff model">
            <ChangeRiskList items={enrichChangeRisk(review.change_risk)} emptyLabel="No change-risk categories available yet."/>
          </SectionCard>
        </>:<Card><div className="empty-review"><TriangleAlert size={24}/><strong>No review yet</strong><span className="muted small">Run a review in SQL Review to see definition-change risk.</span></div></Card>}
      </section>}

      {activeView==="impact"&&<section><div className="section-title">Downstream impact</div>
        {review?<SectionCard icon={GitBranch} title="What breaks if this metric is wrong" description={`${review.metric.name} · source tables → downstream assets`} action={<Badge tone={review.source_health==="healthy"?"accent":"warning"}>{review.source_health}</Badge>}>
          <p className="muted small">
            This metric reads from the source tables below. Their current health is {review.source_health}
            {review.active_incident_ids.length?` with ${review.active_incident_ids.length} active incident${review.active_incident_ids.length===1?"":"s"}`:" with no active incidents"}.
            {review.source_health==="healthy"&&!review.active_incident_ids.length?" If that changes, every downstream asset listed below inherits the risk.":" Every downstream asset listed below is exposed to that risk until it's resolved."}
          </p>
          <ChipList title="Source tables in this query" items={review.referenced_tables} emptyLabel="No source tables detected in the submitted SQL."/>
          <AssetImpactList items={review.downstream_assets} emptyLabel="No downstream dashboards or reports on file for this metric."/>
          <ChipList title={review.active_incident_ids.length?`Active incidents (${review.active_incident_ids.length})`:"Active incidents"} items={review.active_incident_ids} tone="down" emptyLabel="No active incidents on this metric's source tables."/>
        </SectionCard>:<Card><div className="empty-review"><GitBranch size={24}/><strong>No review yet</strong><span className="muted small">Run a review in SQL Review to see downstream impact.</span></div></Card>}
      </section>}

      {activeView==="recommendations"&&<section><div className="section-title">Governance recommendations</div>
        {review?<SectionCard icon={ListChecks} title="What to do next" description={`${review.metric.name} · derived from the deterministic review`}>
          <RecommendationCards items={review.recommendations.map(r=>enrichRecommendation(r, review.metric))} emptyLabel="No recommendations generated yet."/>
        </SectionCard>:<Card><div className="empty-review"><ListChecks size={24}/><strong>No review yet</strong><span className="muted small">Run a review in SQL Review to see governance recommendations.</span></div></Card>}
      </section>}

      {activeView==="stewardSummary"&&<section><div className="section-title">Steward summary</div>
        {selectedCatalogMetric?<>
          <SectionCard icon={FileText} title={`${selectedCatalogMetric.name} · Metric card`} description="What this metric means and where it's used" action={<Badge tone={certBadgeTone(selectedCatalogMetric.certification_status)}>{selectedCatalogMetric.certification_status.replaceAll("_"," ")}</Badge>}>
            <p>{selectedCatalogMetric.description||"No business definition on file."}</p>
            <FactPairGrid items={[
              {label:"Owner",value:selectedCatalogMetric.owner||"Unassigned"},
              {label:"Grain",value:describeGrain(selectedCatalogMetric.name, selectedCatalogMetric.measurement_grain).short},
              {label:"Freshness / SLA",value:selectedCatalogMetric.freshness_expectation||"Undeclared"},
              {label:"Version",value:selectedCatalogMetric.version},
              ...(selectedCatalogMetric.source_health?[{label:"Source health",value:selectedCatalogMetric.source_health,tone:(selectedCatalogMetric.source_health==="healthy"?"up":"down") as "up"|"down"}]:[]),
            ]}/>
            <GrainSummary {...describeGrain(selectedCatalogMetric.name, selectedCatalogMetric.measurement_grain)} grain={describeGrain(selectedCatalogMetric.name, selectedCatalogMetric.measurement_grain).short}/>
            <ChipList title="Approved source tables" items={selectedCatalogMetric.approved_source_tables} emptyLabel="No approved source tables on file."/>
            <AssetImpactList title="Downstream dashboards &amp; reports" items={selectedCatalogMetric.downstream_dashboards} emptyLabel="No downstream dashboards or reports on file."/>
            <ChipList title={selectedCatalogMetric.active_incident_ids.length?`Active incident exposure (${selectedCatalogMetric.active_incident_ids.length})`:"Active incident exposure"} items={selectedCatalogMetric.active_incident_ids} tone="down" emptyLabel="No active incidents on this metric's source tables."/>
          </SectionCard>

          <SectionCard icon={ListChecks} title="Governance completeness" description="Deterministic checklist -- not affected by Ask Loupe">
            <p className="muted small">Each check below explains what it verifies, why it matters operationally, and what a passing state looks like -- not just pass/fail.</p>
            <CompletenessChecklist items={enrichCompleteness(selectedCatalogMetric.completeness)} score={selectedCatalogMetric.completeness_score}/>
          </SectionCard>

          {review&&review.metric.name===selectedCatalogMetric.name?<SectionCard icon={ShieldCheck} title="Governance decision summary" description={`Deterministic · ${review.scoring_version}`} action={<Badge tone={review.trust_band==="high_trust"?"accent":review.trust_band==="do_not_rely"?"warning":"neutral"}>{review.trust_band.replaceAll("_"," ")}</Badge>}>
            <FactPairGrid items={[{label:"Trust score",value:`${review.trust_score}/100`},{label:"Review score",value:`${review.review_score}/100`}]}/>
            <p>{review.summary}</p>
            {review.trust_factors.length>0&&<><div className="section-subtitle">Score contributions</div><p className="muted small">Deterministic checks contributing to this trust score.</p><ReasoningBreakdown items={review.trust_factors.map(f=>({label:f.name,points:f.points,reason:f.reason}))}/></>}
            <ChangeRiskList items={enrichChangeRisk(review.change_risk)} emptyLabel="No change-risk categories available yet."/>
            <RecommendationCards title="Recommended decision" items={review.recommendations.map(r=>enrichRecommendation(r, review.metric))} emptyLabel="No recommendations generated yet."/>
          </SectionCard>:<Card><div className="empty-review"><ShieldCheck size={22}/><strong>No review run yet for this metric</strong><span className="muted small">Run a review in SQL Review to see the full governance decision summary in the brief below.</span></div></Card>}

          <SectionCard icon={Copy} title="Copyable governance brief" description="Paste into a ticket, PRD, Slack update, or metric registry note">
            <CodeBlock title="Governance brief" code={buildGovernanceBrief(selectedCatalogMetric, review)} badge={review&&review.metric.name===selectedCatalogMetric.name?"Includes latest review":"Metric card only"} className="code-block-wrap"/>
          </SectionCard>

          <div className="section-title ask-loupe-gap">Loupe AI helper</div>
          <AskLoupePanel
            title="Ask Loupe"
            status={review&&review.metric.name===selectedCatalogMetric.name?`Grounded in this review · trust score ${review.trust_score}`:"Run a review to ground the helper"}
            messages={helperMessages}
            question={helperQuestion}
            onQuestionChange={setHelperQuestion}
            onAsk={askHelper}
            asking={helperAsking}
            disabled={!review||review.metric.name!==selectedCatalogMetric.name}
            disabledMessage="Run a review for this metric in SQL Review, then ask Loupe to draft a summary or explain what's missing."
            placeholder="Ask Loupe to draft a summary or explain what's needed before approval…"
            samplePrompts={HELPER_PROMPTS}
          />
        </>:<Card><div className="empty-review"><FileText size={24}/><strong>No metric selected</strong><span className="muted small">Select a metric in Catalog to see its steward summary.</span></div></Card>}
      </section>}
    </div>
  </AppShell>;
}
