"use client";

import { useEffect, useRef, useState } from "react";
import { CheckCircle2, Copy, FileText, GitBranch, GitCompare, Library, ListChecks, ScanText, Sparkles, ShieldCheck, TriangleAlert } from "lucide-react";
import {
  ActionFeed, AppShell, AskLoupePanel, AssetImpactList, Badge, Card, ChangeRiskList, ChipList, CodeBlock,
  CompletenessChecklist, FactPairGrid, ReasoningBreakdown, RecommendationCards, RecommendationList, SectionCard,
  SimpleList, Unavailable,
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

// Per the product-depth pass: these should read as questions a reviewer
// actually asks before approving a metric for reporting, not generic
// "explain the score" prompts.
const HELPER_PROMPTS = [
  "Is this safe for executive reporting?",
  "What changed from the governed definition?",
  "What downstream assets are affected?",
  "What should I fix before approval?",
];

// Steward Summary's own suggested prompts -- documentation/stakeholder
// framing, distinct from SQL Review's score-focused prompts above, per the
// steward-output pass. Shares the same Ask Loupe backend endpoint and
// grounding contract; only the suggestions shown differ.
const STEWARD_HELPER_PROMPTS = [
  "Draft a governance summary for this metric.",
  "What should I tell stakeholders?",
  "What needs to happen before approval?",
  "What documentation is missing?",
];

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

// Formats the "copyable governance brief" block from fields the app already
// has on screen -- the same metric-card and (when present) review fields
// rendered elsewhere on this page, arranged into the example structure
// (Metric/Owner/Status/.../Recommended next step). This is pure string
// formatting, never a new judgment: it picks no score, no recommendation,
// no risk that wasn't already deterministically computed by the backend.
function buildGovernanceBrief(metric: Metric, review: Review | null): string {
  const reviewMatches = review !== null && review.metric.name === metric.name;
  const lines: string[] = [
    `Metric: ${metric.name}`,
    `Owner: ${metric.owner || "Unassigned"}`,
    `Status: ${metric.certification_status.replaceAll("_"," ")}`,
    `Certified definition: ${metric.formula || "No formula on file."}`,
    `Approved sources: ${metric.approved_source_tables.length?metric.approved_source_tables.join(", "):"None on file"}`,
    `Downstream assets: ${metric.downstream_dashboards.length?metric.downstream_dashboards.join("; "):"None on file"}`,
  ];
  if (reviewMatches && review) {
    lines.push(`Current review outcome: Trust score ${review.trust_score}/100 (${review.trust_band.replaceAll("_"," ")}) -- ${review.summary}`);
    const risks = review.change_risk.filter(c=>c.status==="risk").map(c=>`${c.category}: ${c.detail}`);
    const incidentNote = review.active_incident_ids.length?`Active incidents: ${review.active_incident_ids.join(", ")}.`:null;
    const riskLines = [...risks, ...(incidentNote?[incidentNote]:[])];
    lines.push(`Risks: ${riskLines.length?riskLines.join(" | "):"None identified in this review."}`);
    const topRec = review.recommendations[0];
    lines.push(`Recommended next step: ${topRec?`${topRec.action} -- ${topRec.rationale}`:"No recommendation generated yet."}`);
  } else {
    const incidentNote = metric.active_incident_ids.length?`Active incidents: ${metric.active_incident_ids.join(", ")}.`:null;
    lines.push("Current review outcome: No SQL review run yet for this metric.");
    lines.push(`Risks: ${incidentNote||"No SQL review run yet -- run one in SQL Review to surface change risk."}`);
    lines.push("Recommended next step: Run a SQL review in SQL Review to get a deterministic governance decision.");
  }
  return lines.join("\n");
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
            <FactPairGrid items={[
              {label:"Owner",value:selectedCatalogMetric.owner||"Unassigned"},
              {label:"Grain",value:selectedCatalogMetric.measurement_grain},
              {label:"Freshness expectation",value:selectedCatalogMetric.freshness_expectation||"Undeclared"},
              {label:"Version",value:selectedCatalogMetric.version},
            ]}/>
            <ChipList title="Approved source tables" items={selectedCatalogMetric.approved_source_tables} emptyLabel="No approved source tables on file."/>
            <AssetImpactList title="Downstream dashboards &amp; reports" items={selectedCatalogMetric.downstream_dashboards} emptyLabel="No downstream dashboards or reports on file."/>
            <ChipList title={selectedCatalogMetric.active_incident_ids.length?`Known risks / open incidents (${selectedCatalogMetric.active_incident_ids.length})`:"Known risks / open incidents"} items={selectedCatalogMetric.active_incident_ids} tone="down" emptyLabel="No open incidents on this metric's source tables."/>
            {selectedCatalogMetric.source_health&&<div className="confidence-rows"><div className="confidence-row"><span>Source health</span><Badge tone={selectedCatalogMetric.source_health==="healthy"?"accent":"warning"}>{selectedCatalogMetric.source_health}</Badge></div></div>}
          </SectionCard>:<Card><div className="empty-review"><Library size={24}/><strong>No metric selected</strong><span className="muted small">Select a metric on the left to see its full detail.</span></div></Card>}
        </div>
      </section>}

      {activeView==="sqlReview"&&<section><div className="section-title">Review workspace</div><div className="card-head" style={{marginBottom:16}}><div><h2>Submit for review</h2><div className="muted small">Choose a persisted metric definition, then run the deterministic review</div></div><div className="actions"><select className="select" value={metric} onChange={e=>setMetric(e.target.value)} aria-label="Metric definition">{metrics.map(m=><option key={m.name} value={m.name}>{m.name} · {m.version}</option>)}</select><button className="button primary" disabled={running||!metric||!sql.trim()} onClick={runReview}>{running?"Reviewing…":"Run review"}</button></div></div><div className="review-layout"><SectionCard icon={ScanText} title="Submitted query" description="BigQuery SQL" action={<div className="actions"><button className="button ghost" onClick={()=>setSql(EXAMPLE_SQL)}><Sparkles size={15}/>Load example SQL</button><button className="button ghost" onClick={()=>navigator.clipboard.writeText(sql)}><Copy size={15}/>Copy</button></div>}><textarea className="code-input" value={sql} onChange={e=>setSql(e.target.value)} placeholder="Paste a read-only BigQuery query for deterministic review…" aria-label="Submitted BigQuery SQL"/></SectionCard>{review?<SectionCard icon={ShieldCheck} title="Trust score" description={`Deterministic · ${review.scoring_version}`} action={<div className="score" style={{"--score":`${review.trust_score}%`} as React.CSSProperties}><span>{review.trust_score}</span></div>}>{review.findings.length>0&&<ActionFeed items={review.findings.map(f=>({icon:f.severity==="low"?CheckCircle2:TriangleAlert,title:`${f.category}: ${f.message}`,metric:f.severity,priority:findingPriority(f.severity)}))}/>}{review.trust_factors.length>0&&<ReasoningBreakdown items={review.trust_factors.map(f=>({label:f.name,points:f.points,reason:f.reason}))}/>}{review.recommended_next_steps.length>0&&<RecommendationList title="Recommended next steps" items={review.recommended_next_steps}/>}{review.override_reason&&<div className="callout callout-info"><div className="callout-title"><ShieldCheck size={14}/>Override reason</div><p>{review.override_reason}</p></div>}<ChipList title="Referenced tables" items={review.referenced_tables}/>{review.source_health&&<div className="confidence-rows"><div className="confidence-row"><span>Source health</span><Badge tone={review.source_health==="healthy"?"accent":"warning"}>{review.source_health}</Badge></div></div>}<ChipList title={review.active_incident_ids.length?`Active incidents (${review.active_incident_ids.length})`:"Active incidents"} items={review.active_incident_ids} tone="down" emptyLabel={review.source_health?"No active incidents linked.":undefined}/><p className="muted small">See Definition Diff, Impact, and Recommendations for the full trust picture.</p></SectionCard>:<Card><div className="empty-review"><ScanText size={24}/><strong>Ready for deterministic review</strong><span className="muted small">Load the example SQL or paste your own query, choose a governed metric, then run review.</span></div></Card>}</div>
        <div className="section-title">Loupe AI helper</div>
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

      {activeView==="metricAlignment"&&<section><div className="section-title">Definition evidence</div>{review?<SectionCard icon={GitCompare} title="Metric alignment" description="Definition evidence and query contract" action={<Badge tone={review.trust_band==="high_trust"?"accent":review.trust_band==="do_not_rely"?"warning":"neutral"}>{review.trust_band.replaceAll("_"," ")}</Badge>}><FactPairGrid items={[{label:"Review score",value:`${review.review_score}/100`},{label:"Trust score",value:`${review.trust_score}/100`,tone:review.trust_band==="do_not_rely"?"down":review.trust_band==="high_trust"?"up":undefined},{label:"Trust band",value:review.trust_band.replaceAll("_"," ")}]}/><div className="table-wrap"><table className="data-table"><thead><tr><th>Contract</th><th>Expected</th><th>Observed</th><th>Status</th></tr></thead><tbody>{review.alignment.map(row=><tr key={row.contract}><td>{row.contract}</td><td>{row.expected}</td><td>{row.observed}</td><td>{row.status}</td></tr>)}</tbody></table></div></SectionCard>:<Card><div className="empty-review"><GitCompare size={24}/><strong>No alignment evidence yet</strong><span className="muted small">Run a review in SQL Review to see how the query maps to the governed definition contract.</span></div></Card>}</section>}

      {activeView==="definitionDiff"&&<section><div className="section-title">Definition change risk</div>
        {review?<>
          <SectionCard icon={TriangleAlert} title="Current vs. proposed logic" description={`${review.metric.name} · certified formula vs. submitted SQL`}>
            <div className="definition-diff-columns">
              <CodeBlock title="Current governed formula" code={review.metric.formula||"No formula on file."} badge={review.metric.certification_status.replaceAll("_"," ")}/>
              <CodeBlock title="Submitted SQL (proposed logic)" code={reviewedSql||"No SQL submitted yet."} badge="From SQL Review"/>
            </div>
          </SectionCard>
          <SectionCard icon={TriangleAlert} title="Definition-change risk categories" description="Derived from the deterministic SQL review and metric metadata -- not a formal diff model">
            <ChangeRiskList items={review.change_risk} emptyLabel="No change-risk categories available yet."/>
          </SectionCard>
        </>:<Card><div className="empty-review"><TriangleAlert size={24}/><strong>No review yet</strong><span className="muted small">Run a review in SQL Review to see definition-change risk.</span></div></Card>}
      </section>}

      {activeView==="impact"&&<section><div className="section-title">Downstream impact</div>
        {review?<SectionCard icon={GitBranch} title="What breaks if this metric is wrong" description={`${review.metric.name} · source tables → downstream assets`} action={<Badge tone={review.source_health==="healthy"?"accent":"warning"}>{review.source_health}</Badge>}>
          <ChipList title="Source tables in this query" items={review.referenced_tables} emptyLabel="No source tables detected in the submitted SQL."/>
          <AssetImpactList items={review.downstream_assets} emptyLabel="No downstream dashboards or reports on file for this metric."/>
          <ChipList title={review.active_incident_ids.length?`Active incidents (${review.active_incident_ids.length})`:"Active incidents"} items={review.active_incident_ids} tone="down" emptyLabel="No active incidents on this metric's source tables."/>
        </SectionCard>:<Card><div className="empty-review"><GitBranch size={24}/><strong>No review yet</strong><span className="muted small">Run a review in SQL Review to see downstream impact.</span></div></Card>}
      </section>}

      {activeView==="recommendations"&&<section><div className="section-title">Governance recommendations</div>
        {review?<SectionCard icon={ListChecks} title="What to do next" description={`${review.metric.name} · derived from the deterministic review`}>
          <RecommendationCards items={review.recommendations} emptyLabel="No recommendations generated yet."/>
        </SectionCard>:<Card><div className="empty-review"><ListChecks size={24}/><strong>No review yet</strong><span className="muted small">Run a review in SQL Review to see governance recommendations.</span></div></Card>}
      </section>}

      {activeView==="stewardSummary"&&<section><div className="section-title">Steward summary</div>
        {selectedCatalogMetric?<>
          <SectionCard icon={FileText} title={`${selectedCatalogMetric.name} · Metric card`} description="What this metric means and where it's used" action={<Badge tone={certBadgeTone(selectedCatalogMetric.certification_status)}>{selectedCatalogMetric.certification_status.replaceAll("_"," ")}</Badge>}>
            <p>{selectedCatalogMetric.description||"No business definition on file."}</p>
            <FactPairGrid items={[
              {label:"Owner",value:selectedCatalogMetric.owner||"Unassigned"},
              {label:"Grain",value:selectedCatalogMetric.measurement_grain},
              {label:"Freshness expectation",value:selectedCatalogMetric.freshness_expectation||"Undeclared"},
              {label:"Version",value:selectedCatalogMetric.version},
            ]}/>
            <ChipList title="Approved source tables" items={selectedCatalogMetric.approved_source_tables} emptyLabel="No approved source tables on file."/>
            <AssetImpactList title="Downstream dashboards &amp; reports" items={selectedCatalogMetric.downstream_dashboards} emptyLabel="No downstream dashboards or reports on file."/>
            <ChipList title={selectedCatalogMetric.active_incident_ids.length?`Active incident exposure (${selectedCatalogMetric.active_incident_ids.length})`:"Active incident exposure"} items={selectedCatalogMetric.active_incident_ids} tone="down" emptyLabel="No active incidents on this metric's source tables."/>
            {selectedCatalogMetric.source_health&&<div className="confidence-rows"><div className="confidence-row"><span>Current trust posture · source health</span><Badge tone={selectedCatalogMetric.source_health==="healthy"?"accent":"warning"}>{selectedCatalogMetric.source_health}</Badge></div></div>}
          </SectionCard>

          <SectionCard icon={ListChecks} title="Governance completeness" description="Deterministic checklist -- not affected by Ask Loupe">
            <CompletenessChecklist items={selectedCatalogMetric.completeness} score={selectedCatalogMetric.completeness_score}/>
          </SectionCard>

          {review&&review.metric.name===selectedCatalogMetric.name?<SectionCard icon={ShieldCheck} title="Governance decision summary" description={`Deterministic · ${review.scoring_version}`} action={<Badge tone={review.trust_band==="high_trust"?"accent":review.trust_band==="do_not_rely"?"warning":"neutral"}>{review.trust_band.replaceAll("_"," ")}</Badge>}>
            <FactPairGrid items={[{label:"Trust score",value:`${review.trust_score}/100`},{label:"Review score",value:`${review.review_score}/100`}]}/>
            <p>{review.summary}</p>
            {review.trust_factors.length>0&&<ReasoningBreakdown items={review.trust_factors.map(f=>({label:f.name,points:f.points,reason:f.reason}))}/>}
            <ChangeRiskList items={review.change_risk} emptyLabel="No change-risk categories available yet."/>
            <RecommendationCards title="Recommended decision" items={review.recommendations} emptyLabel="No recommendations generated yet."/>
          </SectionCard>:<Card><div className="empty-review"><ShieldCheck size={22}/><strong>No review run yet for this metric</strong><span className="muted small">Run a review in SQL Review to see the full governance decision summary in the brief below.</span></div></Card>}

          <SectionCard icon={Copy} title="Copyable governance brief" description="Paste into a ticket, PRD, Slack update, or metric registry note">
            <CodeBlock title="Governance brief" code={buildGovernanceBrief(selectedCatalogMetric, review)} badge={review&&review.metric.name===selectedCatalogMetric.name?"Includes latest review":"Metric card only"}/>
          </SectionCard>

          <div className="section-title">Loupe AI helper</div>
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
            samplePrompts={STEWARD_HELPER_PROMPTS}
          />
        </>:<Card><div className="empty-review"><FileText size={24}/><strong>No metric selected</strong><span className="muted small">Select a metric in Catalog to see its steward summary.</span></div></Card>}
      </section>}
    </div>
  </AppShell>;
}
