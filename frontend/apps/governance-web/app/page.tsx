"use client";

import { useEffect, useRef, useState } from "react";
import { CheckCircle2, Copy, GitCompare, Library, ScanText, Sparkles, ShieldCheck, TriangleAlert } from "lucide-react";
import { ActionFeed, AppShell, AskLoupePanel, Badge, Card, ChipList, ReasoningBreakdown, RecommendationList, SectionCard, Unavailable } from "@loupe/ui";
import type { FeedPriority, HelperMessage } from "@loupe/ui";

type Metric={name:string;version:string;certification_status:string;measurement_grain:string};
type TrustFactor={name:string;points:number;reason:string};
type Review={metric:Metric;review_score:number;summary:string;findings:{severity:string;category:string;message:string}[];trust_score:number;trust_band:string;scoring_version:string;trust_factors:TrustFactor[];recommended_next_steps:string[];referenced_tables:string[];source_health:string;active_incident_ids:string[];override_reason:string|null;alignment:{contract:string;expected:string;observed:string;status:string}[]};
type GovernanceView = "catalog" | "sqlReview" | "metricAlignment";

const HELPER_PROMPTS = [
  "Why did this query get this trust score?",
  "Is this safe for executive reporting?",
  "What should I fix before using this metric?",
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

export default function Page(){
  const api=process.env.NEXT_PUBLIC_API_BASE_URL??"http://localhost:8000";
  const [metrics,setMetrics]=useState<Metric[]>([]); const [metric,setMetric]=useState(""); const [sql,setSql]=useState(""); const [reviewedSql,setReviewedSql]=useState(""); const [review,setReview]=useState<Review|null>(null); const [error,setError]=useState<string|null>(null); const [running,setRunning]=useState(false);
  const [activeView,setActiveView]=useState<GovernanceView>("sqlReview");
  const [helperMessages,setHelperMessages]=useState<HelperMessage[]>([]); const [helperQuestion,setHelperQuestion]=useState(""); const [helperAsking,setHelperAsking]=useState(false);
  const nextHelperId=useRef(0);
  useEffect(()=>{fetch(`${api}/api/v1/governance/catalog`).then(async r=>{if(!r.ok)throw new Error();return r.json()}).then(data=>{setMetrics(data.metrics);setMetric(data.metrics[0]?.name??"")}).catch(()=>setError("The persisted metric catalog could not be reached. No local catalog was substituted."));},[api]);
  async function runReview(){if(!sql.trim()||!metric)return;setRunning(true);setError(null);setReview(null);setHelperMessages([]);try{const response=await fetch(`${api}/api/v1/governance/sql-review`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({sql,metric_name:metric})});if(!response.ok)throw new Error();setReview(await response.json());setReviewedSql(sql)}catch{setError("The deterministic review could not be completed. No fabricated score was shown.")}finally{setRunning(false)}}
  // Grounded solely in the review that's already on screen -- the same
  // metric/sql/score/findings/factors/steps/tables/health/incidents the
  // SectionCard below renders, sent back verbatim so the helper cannot
  // narrate a score, finding, or incident the deterministic review didn't
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
  ];
  return <AppShell active="governance" brand="Governance" brandIcon={ShieldCheck} navigation={nav}>
    <div className="dashboard-surface">
      <header className="hero-panel page-header"><div><div className="eyebrow">DEFINITION LAYER</div><h1>SQL governance review</h1><div className="muted">Validate analytical logic against governed definitions</div></div></header>
      {error&&<Unavailable message={error}/>}
      {activeView==="catalog"&&<section><div className="section-title">Persisted metric catalog</div><SectionCard icon={Library} title="Metric catalog" description="Definitions available for SQL review" action={<Badge>{metrics.length} metrics</Badge>}>{metrics.length?<div className="table-wrap"><table className="data-table"><thead><tr><th>Name</th><th>Version</th><th>Certification</th><th>Grain</th></tr></thead><tbody>{metrics.map(m=><tr key={m.name}><td>{m.name}</td><td>{m.version}</td><td>{m.certification_status}</td><td>{m.measurement_grain}</td></tr>)}</tbody></table></div>:<div className="empty-review"><Library size={24}/><strong>No catalog entries yet</strong><span className="muted small">Persisted metric definitions will appear here once available.</span></div>}</SectionCard></section>}
      {activeView==="sqlReview"&&<section><div className="section-title">Review workspace</div><div className="card-head" style={{marginBottom:16}}><div><h2>Submit for review</h2><div className="muted small">Choose a persisted metric definition, then run the deterministic review</div></div><div className="actions"><select className="select" value={metric} onChange={e=>setMetric(e.target.value)} aria-label="Metric definition">{metrics.map(m=><option key={m.name} value={m.name}>{m.name} · {m.version}</option>)}</select><button className="button primary" disabled={running||!metric||!sql.trim()} onClick={runReview}>{running?"Reviewing…":"Run review"}</button></div></div><div className="review-layout"><SectionCard icon={ScanText} title="Submitted query" description="BigQuery SQL" action={<div className="actions"><button className="button ghost" onClick={()=>setSql(EXAMPLE_SQL)}><Sparkles size={15}/>Load example SQL</button><button className="button ghost" onClick={()=>navigator.clipboard.writeText(sql)}><Copy size={15}/>Copy</button></div>}><textarea className="code-input" value={sql} onChange={e=>setSql(e.target.value)} placeholder="Paste a read-only BigQuery query for deterministic review…" aria-label="Submitted BigQuery SQL"/></SectionCard>{review?<SectionCard icon={ShieldCheck} title="Trust score" description={`Deterministic · ${review.scoring_version}`} action={<div className="score" style={{"--score":`${review.trust_score}%`} as React.CSSProperties}><span>{review.trust_score}</span></div>}>{review.findings.length>0&&<ActionFeed items={review.findings.map(f=>({icon:f.severity==="low"?CheckCircle2:TriangleAlert,title:`${f.category}: ${f.message}`,metric:f.severity,priority:findingPriority(f.severity)}))}/>}{review.trust_factors.length>0&&<ReasoningBreakdown items={review.trust_factors.map(f=>({label:f.name,points:f.points,reason:f.reason}))}/>}{review.recommended_next_steps.length>0&&<RecommendationList title="Recommended next steps" items={review.recommended_next_steps}/>}{review.override_reason&&<div className="callout callout-info"><div className="callout-title"><ShieldCheck size={14}/>Override reason</div><p>{review.override_reason}</p></div>}<ChipList title="Referenced tables" items={review.referenced_tables}/>{review.source_health&&<div className="confidence-rows"><div className="confidence-row"><span>Source health</span><Badge tone={review.source_health==="healthy"?"accent":"warning"}>{review.source_health}</Badge></div></div>}<ChipList title={review.active_incident_ids.length?`Active incidents (${review.active_incident_ids.length})`:"Active incidents"} items={review.active_incident_ids} tone="down" emptyLabel={review.source_health?"No active incidents linked.":undefined}/></SectionCard>:<Card><div className="empty-review"><ScanText size={24}/><strong>Ready for deterministic review</strong><span className="muted small">Load the example SQL or paste your own query, choose a governed metric, then run review.</span></div></Card>}</div>
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
      {activeView==="metricAlignment"&&<section><div className="section-title">Definition evidence</div>{review?<SectionCard icon={GitCompare} title="Metric alignment" description="Definition evidence and query contract" action={<Badge tone={review.trust_band==="reliable"?"accent":"warning"}>{review.trust_band.replaceAll("_"," ")}</Badge>}><div className="table-wrap"><table className="data-table"><thead><tr><th>Contract</th><th>Expected</th><th>Observed</th><th>Status</th></tr></thead><tbody>{review.alignment.map(row=><tr key={row.contract}><td>{row.contract}</td><td>{row.expected}</td><td>{row.observed}</td><td>{row.status}</td></tr>)}</tbody></table></div></SectionCard>:<Card><div className="empty-review"><GitCompare size={24}/><strong>No alignment evidence yet</strong><span className="muted small">Run a review in SQL Review to see how the query maps to the governed definition contract.</span></div></Card>}</section>}
    </div>
  </AppShell>;
}
