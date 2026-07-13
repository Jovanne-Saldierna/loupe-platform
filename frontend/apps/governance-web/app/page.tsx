"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, Copy, GitCompare, Library, ScanText, ShieldCheck, TriangleAlert } from "lucide-react";
import { ActionFeed, AppShell, Badge, Card, ReasoningBreakdown, RecommendationList, SectionCard, Unavailable } from "@loupe/ui";
import type { FeedPriority } from "@loupe/ui";

type Metric={name:string;version:string;certification_status:string;measurement_grain:string};
type TrustFactor={name:string;points:number;reason:string};
type Review={metric:Metric;review_score:number;summary:string;findings:{severity:string;category:string;message:string}[];trust_score:number;trust_band:string;scoring_version:string;trust_factors:TrustFactor[];recommended_next_steps:string[];source_health:string;override_reason:string|null;alignment:{contract:string;expected:string;observed:string;status:string}[]};
type GovernanceView = "catalog" | "sqlReview" | "metricAlignment";

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
  const [metrics,setMetrics]=useState<Metric[]>([]); const [metric,setMetric]=useState(""); const [sql,setSql]=useState(""); const [review,setReview]=useState<Review|null>(null); const [error,setError]=useState<string|null>(null); const [running,setRunning]=useState(false);
  const [activeView,setActiveView]=useState<GovernanceView>("sqlReview");
  useEffect(()=>{fetch(`${api}/api/v1/governance/catalog`).then(async r=>{if(!r.ok)throw new Error();return r.json()}).then(data=>{setMetrics(data.metrics);setMetric(data.metrics[0]?.name??"")}).catch(()=>setError("The persisted metric catalog could not be reached. No local catalog was substituted."));},[api]);
  async function runReview(){if(!sql.trim()||!metric)return;setRunning(true);setError(null);setReview(null);try{const response=await fetch(`${api}/api/v1/governance/sql-review`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({sql,metric_name:metric})});if(!response.ok)throw new Error();setReview(await response.json())}catch{setError("The deterministic review could not be completed. No fabricated score was shown.")}finally{setRunning(false)}}
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
      {activeView==="sqlReview"&&<section><div className="section-title">Review workspace</div><div className="card-head" style={{marginBottom:16}}><div><h2>Submit for review</h2><div className="muted small">Choose a persisted metric definition, then run the deterministic review</div></div><div className="actions"><select className="select" value={metric} onChange={e=>setMetric(e.target.value)} aria-label="Metric definition">{metrics.map(m=><option key={m.name} value={m.name}>{m.name} · {m.version}</option>)}</select><button className="button primary" disabled={running||!metric||!sql.trim()} onClick={runReview}>{running?"Reviewing…":"Run review"}</button></div></div><div className="review-layout"><SectionCard icon={ScanText} title="Submitted query" description="BigQuery SQL" action={<button className="button ghost" onClick={()=>navigator.clipboard.writeText(sql)}><Copy size={15}/>Copy</button>}><textarea className="code-input" value={sql} onChange={e=>setSql(e.target.value)} placeholder="Paste a read-only BigQuery query for deterministic review…" aria-label="Submitted BigQuery SQL"/></SectionCard>{review?<SectionCard icon={ShieldCheck} title="Trust score" description={`Deterministic · ${review.scoring_version}`} action={<div className="score" style={{"--score":`${review.trust_score}%`} as React.CSSProperties}><span>{review.trust_score}</span></div>}>{review.findings.length>0&&<ActionFeed items={review.findings.map(f=>({icon:f.severity==="low"?CheckCircle2:TriangleAlert,title:`${f.category}: ${f.message}`,metric:f.severity,priority:findingPriority(f.severity)}))}/>}{review.trust_factors.length>0&&<ReasoningBreakdown items={review.trust_factors.map(f=>({label:f.name,points:f.points,reason:f.reason}))}/>}{review.recommended_next_steps.length>0&&<RecommendationList title="Recommended next steps" items={review.recommended_next_steps}/>}{review.override_reason&&<div className="callout callout-info"><div className="callout-title"><ShieldCheck size={14}/>Override reason</div><p>{review.override_reason}</p></div>}</SectionCard>:<Card><div className="empty-review"><ScanText size={24}/><strong>Ready for deterministic review</strong><span className="muted small">Paste SQL and choose a persisted metric definition.</span></div></Card>}</div></section>}
      {activeView==="metricAlignment"&&<section><div className="section-title">Definition evidence</div>{review?<SectionCard icon={GitCompare} title="Metric alignment" description="Definition evidence and query contract" action={<Badge tone={review.trust_band==="reliable"?"accent":"warning"}>{review.trust_band.replaceAll("_"," ")}</Badge>}><div className="table-wrap"><table className="data-table"><thead><tr><th>Contract</th><th>Expected</th><th>Observed</th><th>Status</th></tr></thead><tbody>{review.alignment.map(row=><tr key={row.contract}><td>{row.contract}</td><td>{row.expected}</td><td>{row.observed}</td><td>{row.status}</td></tr>)}</tbody></table></div></SectionCard>:<Card><div className="empty-review"><GitCompare size={24}/><strong>No alignment evidence yet</strong><span className="muted small">Run a review in SQL Review to see how the query maps to the governed definition contract.</span></div></Card>}</section>}
    </div>
  </AppShell>;
}
