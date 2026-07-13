"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Activity, AlertTriangle, BookOpen, CheckCircle2, CircleDot, Gauge, GitBranch, ListChecks, RefreshCw, Siren, TableProperties } from "lucide-react";
import { AppShell, AskLoupePanel, AssetImpactList, AuditTrailList, Badge, ChipList, FactPairGrid, LineageChain, MiniStatStrip, PlaybookWorkflow, RecommendationList, SectionCard, SqlSandbox, Stat, Unavailable } from "@loupe/ui";
import type { AuditTrailItem, HelperMessage, LineageChainItem, PlaybookStepItem, SqlSandboxResult } from "@loupe/ui";

type TableHealth={table_id:string;status:"healthy"|"degraded"|"critical"|"unknown";freshness_minutes:number|null;active_incident_count:number};
type IncidentAuditEntry={step:string;description:string;timestamp:string|null;source:string|null};
type Incident={incident_id:string;table_id:string;check_type:string;severity:string;status:string;created_at:string;observed_value:number|null;expected_value:number|null;affected_metrics:string[];owner:string|null;next_allowed_statuses:string[];governed_metric_names:string[];downstream_assets:string[];audit_trail:IncidentAuditEntry[]};
type LineageMetric={name:string;downstream_dashboards:string[]};
type TriageLineage={table_id:string;governed_metrics:LineageMetric[]};
type Warehouse={generated_at:string;dataset:string;monitored_tables:number;healthy_tables:number;degraded_tables:number;critical_tables:number;open_incidents:number;freshness_minutes:number|null;tables:TableHealth[];incidents:Incident[];lineage:TriageLineage[]};
type TriageView = "warehouse" | "sourceHealth" | "incidentQueue" | "playbook" | "lineage" | "auditTrail";
type SqlCheck={title:string;purpose:string;sql:string};
type Playbook={likely_root_cause:string;impact_summary:string;affected_downstream_assets:string[];affected_governed_metrics:string[];debugging_steps:string[];sql_checks:SqlCheck[];owner_recommendation:string;next_action:string;model:string|null};

const HELPER_PROMPTS = [
  "What happened here?",
  "Is this a data issue or a real business issue?",
  "What should I check next?",
  "Which metrics are affected?",
];

export default function Page(){
  const api=process.env.NEXT_PUBLIC_API_BASE_URL??"http://localhost:8000";
  const [data,setData]=useState<Warehouse|null>(null);const [error,setError]=useState<string|null>(null);const [loading,setLoading]=useState(true);const [selected,setSelected]=useState<Incident|null>(null);const [notes,setNotes]=useState("");const [transitioning,setTransitioning]=useState(false);
  const [activeView,setActiveView]=useState<TriageView>("warehouse");
  const [helperMessages,setHelperMessages]=useState<HelperMessage[]>([]); const [helperQuestion,setHelperQuestion]=useState(""); const [helperAsking,setHelperAsking]=useState(false);
  const nextHelperId=useRef(0);
  const [playbook,setPlaybook]=useState<Playbook|null>(null); const [playbookLoading,setPlaybookLoading]=useState(false); const [playbookError,setPlaybookError]=useState<string|null>(null);
  const [sandboxSql,setSandboxSql]=useState(""); const [sandboxCheckTitle,setSandboxCheckTitle]=useState<string|null>(null); const [sandboxResult,setSandboxResult]=useState<SqlSandboxResult|null>(null); const [sandboxRunning,setSandboxRunning]=useState(false);
  // Client-appended audit entries -- ONLY pushed after a real, successful
  // response comes back (never speculatively). Keyed by incident so a stale
  // entry never appears to describe a different incident's activity.
  const [extraAudit,setExtraAudit]=useState<Record<string,AuditTrailItem[]>>({});
  const load=useCallback(()=>{setLoading(true);setError(null);fetch(`${api}/api/v1/triage/warehouse`).then(async r=>{if(!r.ok)throw new Error();return r.json()}).then((result:Warehouse)=>{setData(result);setSelected(current=>current?result.incidents.find(i=>i.incident_id===current.incident_id)??null:result.incidents[0]??null)}).catch(()=>setError("Persisted warehouse health could not be reached. No fictional incidents were substituted.")).finally(()=>setLoading(false));},[api]);
  useEffect(load,[load]);
  // A new incident selection means prior helper answers no longer describe
  // what's on screen -- clear the transcript rather than leaving a stale
  // answer attached to a different incident's context.
  useEffect(()=>{setHelperMessages([]);setPlaybook(null);setPlaybookError(null);setSandboxSql("");setSandboxCheckTitle(null);setSandboxResult(null);},[selected?.incident_id]);
  const healthyPct=data?.monitored_tables?Math.round(data.healthy_tables/data.monitored_tables*100):0;
  const lineageItems:LineageChainItem[]=useMemo(()=>(data?.lineage??[]).map(entry=>({
    table:entry.table_id,
    metrics:entry.governed_metrics.map(m=>({name:m.name,downstream:m.downstream_dashboards})),
  })),[data?.lineage]);
  const combinedAudit:AuditTrailItem[]=useMemo(()=>{
    if(!selected)return [];
    const deterministic:AuditTrailItem[]=selected.audit_trail.map(entry=>({step:entry.step,description:entry.description,timestamp:entry.timestamp,source:entry.source}));
    return [...deterministic,...(extraAudit[selected.incident_id]??[])];
  },[selected,extraAudit]);
  async function transition(target:string){if(!selected)return;if(target==="resolved"&&!notes.trim()){setError("Resolution notes are required before resolving an incident.");return}setTransitioning(true);setError(null);try{const response=await fetch(`${api}/api/v1/triage/incidents/${encodeURIComponent(selected.incident_id)}/transition`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({target_status:target,expected_current_status:selected.status,resolution_notes:target==="resolved"?notes:null})});if(!response.ok)throw new Error();setNotes("");await load()}catch{setError("The incident changed or the transition could not be committed. Refresh and retry.")}finally{setTransitioning(false)}}
  // Grounded solely in the selected incident that's already on screen -- the
  // same id/table/check/severity/status/observed/expected/affected metrics
  // the timeline and fact grid above render, sent back verbatim so the
  // helper cannot narrate a root cause or affected metric the deterministic
  // incident record didn't already contain (see api/services/triage_helper.py).
  async function askHelper(q:string){
    if(!selected)return;
    const id=String(nextHelperId.current++);
    setHelperQuestion("");setHelperAsking(true);
    setHelperMessages(prev=>[...prev,{id,question:q,answer:null}]);
    try{
      const activeIncidentCount=data?.tables.find(t=>t.table_id===selected.table_id)?.active_incident_count??null;
      const response=await fetch(`${api}/api/v1/triage/helper`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
        question:q,incident_id:selected.incident_id,table_id:selected.table_id,check_type:selected.check_type,
        severity:selected.severity,status:selected.status,created_at:selected.created_at,
        observed_value:selected.observed_value,expected_value:selected.expected_value,
        affected_metrics:selected.affected_metrics,governed_metric_names:selected.governed_metric_names,
        active_incident_count:activeIncidentCount,owner:selected.owner,
      })});
      const body=await response.json();
      const answer=response.ok?body.answer:body.detail??"Loupe could not produce a grounded answer right now.";
      setHelperMessages(prev=>prev.map(m=>m.id===id?{...m,answer}:m));
      if(response.ok){
        // Real, successful response only -- append using the actual model
        // reported by the backend (null when Claude isn't configured) and
        // the actual question asked, never a fabricated placeholder.
        appendAudit(selected.incident_id,{
          step:"helper_question_asked",
          description:`Helper asked: "${q}"${body.model?"":" (no model configured -- fallback response)"}`,
          timestamp:new Date().toISOString(),
          source:body.model?`model: ${body.model}`:null,
        });
      }
    }catch{
      setHelperMessages(prev=>prev.map(m=>m.id===id?{...m,answer:"Loupe could not be reached."}:m));
    }finally{
      setHelperAsking(false);
    }
  }
  function appendAudit(incidentId:string,entry:AuditTrailItem){
    setExtraAudit(prev=>({...prev,[incidentId]:[...(prev[incidentId]??[]),entry]}));
  }
  // Grounded only in the selected incident + the lineage/downstream metadata
  // already rendered on screen -- this never re-queries the warehouse and
  // never lets the AI decide whether the incident is real (see
  // api/services/triage_playbook.py). Deterministic fields (SQL checks,
  // debugging steps, owner recommendation) are computed server-side without
  // any model call; only likely_root_cause/impact_summary/next_action are
  // AI-narrated, and only from the facts sent below.
  async function generatePlaybook(){
    if(!selected)return;
    setPlaybookLoading(true);setPlaybookError(null);
    try{
      const activeIncidentCount=data?.tables.find(t=>t.table_id===selected.table_id)?.active_incident_count??null;
      const sourceHealth=data?.tables.find(t=>t.table_id===selected.table_id)?.status??null;
      const response=await fetch(`${api}/api/v1/triage/playbook`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
        incident_id:selected.incident_id,table_id:selected.table_id,check_type:selected.check_type,
        severity:selected.severity,status:selected.status,created_at:selected.created_at,
        observed_value:selected.observed_value,expected_value:selected.expected_value,
        affected_metrics:selected.affected_metrics,governed_metric_names:selected.governed_metric_names,
        downstream_assets:selected.downstream_assets,active_incident_count:activeIncidentCount,
        source_health:sourceHealth,owner:selected.owner,
      })});
      const body=await response.json();
      if(!response.ok)throw new Error(body.detail??"Loupe could not produce a grounded playbook right now.");
      setPlaybook(body as Playbook);
      appendAudit(selected.incident_id,{
        step:"ai_playbook_generated",
        description:body.model?"Triage playbook generated from the incident's deterministic context.":"Triage playbook generated (deterministic fields only -- Claude isn't configured, so root cause/impact/next action use honest fallback text).",
        timestamp:new Date().toISOString(),
        source:body.model?`model: ${body.model}`:null,
      });
    }catch(err){
      setPlaybookError(err instanceof Error?err.message:"Loupe could not produce a grounded playbook right now.");
    }finally{
      setPlaybookLoading(false);
    }
  }
  // Prefills the sandbox textarea from a suggested check -- never runs
  // anything itself. Logged to the audit trail immediately since "loaded"
  // is a real, already-completed client action, unlike "executed" which
  // only gets logged once a run actually completes (see runSandbox below).
  function loadIntoSandbox(step:PlaybookStepItem){
    setSandboxSql(step.sql);
    setSandboxCheckTitle(step.title);
    setSandboxResult(null);
    if(selected){
      appendAudit(selected.incident_id,{
        step:"debugging_sql_loaded",
        description:`Loaded "${step.title}" into the debugging SQL sandbox.`,
        timestamp:new Date().toISOString(),
        source:"triage_sql_sandbox",
      });
    }
  }
  function clearSandbox(){
    setSandboxSql("");
    setSandboxCheckTitle(null);
    setSandboxResult(null);
  }
  // Runs whatever SQL is currently in the sandbox textarea (loaded from a
  // suggested check, or hand-edited) through the read-only backend
  // sandbox. Safety is decided entirely server-side, deterministically
  // (see api/services/triage_sql_sandbox.py) -- this function never
  // second-guesses that decision, it only renders whatever status/error/
  // rows the backend actually returned. The audit entry's wording always
  // matches the real outcome: only a "success" response says rows were
  // returned; "rejected"/"error" responses -- or a network failure --
  // describe the failure honestly instead of claiming results exist.
  async function runSandbox(){
    if(!selected||!sandboxSql.trim())return;
    setSandboxRunning(true);
    try{
      const response=await fetch(`${api}/api/v1/triage/sql-sandbox`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
        incident_id:selected.incident_id,sql:sandboxSql,check_title:sandboxCheckTitle,
      })});
      const body=await response.json();
      if(!response.ok)throw new Error(body.detail??"Loupe could not run this check right now.");
      const result=body as SqlSandboxResult;
      setSandboxResult(result);
      const title=sandboxCheckTitle?`"${sandboxCheckTitle}"`:"the sandbox query";
      if(result.status==="success"){
        appendAudit(selected.incident_id,{step:"debugging_sql_executed",description:`Ran ${title} — ${result.row_count} row${result.row_count===1?"":"s"} returned.`,timestamp:new Date().toISOString(),source:"triage_sql_sandbox"});
      }else if(result.status==="rejected"){
        appendAudit(selected.incident_id,{step:"debugging_sql_rejected",description:`Rejected ${title}: ${result.error??"unsafe or invalid SQL"}`,timestamp:new Date().toISOString(),source:"triage_sql_sandbox"});
      }else{
        appendAudit(selected.incident_id,{step:"debugging_sql_executed",description:`Execution failed for ${title}: ${result.error??"unknown error"} -- no results were returned.`,timestamp:new Date().toISOString(),source:"triage_sql_sandbox"});
      }
    }catch(err){
      const message=err instanceof Error?err.message:"Loupe could not run this check right now.";
      setSandboxResult({status:"error",columns:[],rows:[],row_count:0,bytes_processed:null,error:message,row_limit:25});
      appendAudit(selected.incident_id,{step:"debugging_sql_executed",description:`Execution failed: ${message} -- no results were returned.`,timestamp:new Date().toISOString(),source:"triage_sql_sandbox"});
    }finally{
      setSandboxRunning(false);
    }
  }
  const nav = [
    {label:"Warehouse",icon:Gauge,active:activeView==="warehouse",onClick:()=>setActiveView("warehouse")},
    {label:"Source Health",icon:TableProperties,active:activeView==="sourceHealth",onClick:()=>setActiveView("sourceHealth")},
    {label:"Incident Queue",icon:Siren,active:activeView==="incidentQueue",onClick:()=>setActiveView("incidentQueue")},
    {label:"Playbook",icon:BookOpen,active:activeView==="playbook",onClick:()=>setActiveView("playbook")},
    {label:"Lineage",icon:GitBranch,active:activeView==="lineage",onClick:()=>setActiveView("lineage")},
    {label:"Audit Trail",icon:ListChecks,active:activeView==="auditTrail",onClick:()=>setActiveView("auditTrail")},
  ];
  // Shared across every tab: whichever incident was last selected in Source
  // Health or Incident Queue is exactly what Playbook/Lineage/Audit Trail
  // render for -- a single `selected` state, no per-tab copies, so picking
  // the seeded incident anywhere immediately unlocks the other tabs.
  const selectedSubtitle = selected?`${selected.incident_id} · ${selected.table_id} · ${selected.check_type.replaceAll("_"," ")}`:"No incident selected";
  return <AppShell active="triage" brand="Triage" brandIcon={Activity} navigation={nav}>
    <div className="dashboard-surface">
      <header className="hero-panel page-header"><div><div className="eyebrow">RELIABILITY LAYER</div><h1>Warehouse health</h1><div className="muted">Persisted incidents across governed data sources</div></div><div className="actions"><Badge>{data?`${data.monitored_tables} sources monitored`:"Live persistence"}</Badge><button className="button" onClick={load} disabled={loading}><RefreshCw size={15}/>{loading?"Refreshing…":"Refresh"}</button></div></header>
      {error&&<Unavailable message={error}/>} {loading&&!data?<div className="card skeleton" aria-label="Loading warehouse health"/>:data&&<>
        {activeView==="warehouse"&&<section><div className="section-title">Key metrics</div><div className="metric-grid"><Stat label="Healthy tables" value={String(data.healthy_tables)} change={`${healthyPct}%`}/><Stat label="Open incidents" value={String(data.open_incidents)} change={`${data.critical_tables} critical`}/><Stat label="Sources healthy" value={`${healthyPct}%`} change={`${data.monitored_tables} governed`}/><Stat label="Max freshness" value={formatFreshness(data.freshness_minutes)} change="metadata"/></div></section>}

        {activeView==="sourceHealth"&&<section><div className="section-title">Governed source health</div><div className="triage-layout"><SectionCard icon={TableProperties} title="Governed source health" description="Current persisted incident state" action={<Badge>Live</Badge>}><div className="health-bars">{data.tables.map(table=><button key={table.table_id} className="health-row" onClick={()=>setSelected(data.incidents.find(i=>i.table_id===table.table_id)??null)}><span className="health-name-wrap"><span className="health-name">{table.table_id}</span><span className="health-count">{table.active_incident_count} active incident{table.active_incident_count===1?"":"s"}</span></span><span className="health-track"><span className={`health-fill health-${table.status}`} style={{width:table.status==="healthy"?"100%":table.status==="degraded"?"62%":table.status==="critical"?"35%":"18%"}}/></span><span className={`status-dot status-${table.status}`}>{table.status}</span></button>)}</div></SectionCard><SectionCard icon={Siren} title="Incident timeline" description={selected?`${selected.table_id} · ${selected.check_type}`:"No active incident selected"} action={selected&&<Badge tone="warning">{selected.severity}</Badge>}>{selected?<><div className="timeline"><TimelineStep icon={CircleDot} label="Detected" detail={new Date(selected.created_at).toLocaleString()}/><TimelineStep icon={CheckCircle2} label={selected.status} detail={selected.owner?`Owner: ${selected.owner}`:"Owner unassigned"}/>{selected.affected_metrics.length>0&&<TimelineStep icon={AlertTriangle} label="Affected metrics" detail={selected.affected_metrics.join(", ")}/>}</div><FactPairGrid items={observedExpectedFacts(selected)}/><ChipList title="Affected governed metrics" items={selected.governed_metric_names} tone="down" emptyLabel="No governed metrics linked."/><div className="incident-actions">{selected.next_allowed_statuses.map(status=><button className="button" key={status} disabled={transitioning} onClick={()=>transition(status)}>{status.replaceAll("_"," ")}</button>)}</div>{selected.next_allowed_statuses.includes("resolved")&&<textarea className="notes" value={notes} onChange={e=>setNotes(e.target.value)} placeholder="Required resolution notes" aria-label="Resolution notes"/>}</>:<div className="empty-review"><CheckCircle2 size={24}/><strong>No active incidents</strong><span className="muted small">Governed sources currently have no persisted active incidents.</span></div>}</SectionCard></div>
        <div className="section-title">Loupe AI helper</div>
        <AskLoupePanel
          title="Ask Loupe"
          status={selected?`Grounded in incident ${selected.incident_id} · ${selected.severity}`:"Waiting on a selection"}
          messages={helperMessages}
          question={helperQuestion}
          onQuestionChange={setHelperQuestion}
          onAsk={askHelper}
          asking={helperAsking}
          disabled={!selected}
          disabledMessage="Select an incident or source health row, then ask Loupe what changed."
          placeholder="Ask about this incident's cause, affected metrics, or next steps…"
          samplePrompts={HELPER_PROMPTS}
          resizable
        />
        </section>}

        {activeView==="incidentQueue"&&<section><div className="section-title">Incident queue</div><SectionCard icon={AlertTriangle} title="Active incident queue" description="Prioritized by deterministic severity rules" action={<Badge tone={data.incidents.length?"warning":"accent"}>{data.incidents.length} active</Badge>}><MiniStatStrip items={severityStrip(data.incidents)}/>{data.incidents.length?<div className="table-wrap"><table className="data-table incident-table"><thead><tr><th>Severity</th><th>Incident</th><th>Affected metrics</th><th>Age</th><th>Status</th></tr></thead><tbody>{data.incidents.map(incident=><tr key={incident.incident_id} onClick={()=>setSelected(incident)} className={selected?.incident_id===incident.incident_id?"selected":""}><td><span className={`severity severity-${incident.severity}`}>{incident.severity}</span></td><td><strong>{incident.table_id}</strong><div className="muted small">{incident.check_type.replaceAll("_"," ")}</div></td><td>{incident.affected_metrics.join(", ")||"None mapped"}</td><td>{formatAge(incident.created_at)}</td><td>{incident.status}</td></tr>)}</tbody></table></div>:<div className="queue-empty"><CheckCircle2 size={18}/>No active incidents</div>}</SectionCard>
        <SectionCard icon={CircleDot} title="Selected incident facts" description={selectedSubtitle}>
          {selected?<><FactPairGrid items={observedExpectedFacts(selected)}/><ChipList title="Affected governed metrics" items={selected.governed_metric_names} tone="down" emptyLabel="No governed metrics linked."/><AssetImpactList items={selected.downstream_assets} emptyLabel="No downstream assets on file."/></>:<div className="empty-review"><CircleDot size={22}/><strong>No incident selected</strong><span className="muted small">Select a row above to see its facts, or open Source Health.</span></div>}
        </SectionCard>
        </section>}

        {activeView==="playbook"&&<section><div className="section-title">AI-generated triage playbook</div>
        <SectionCard icon={BookOpen} title="Triage playbook" description={selectedSubtitle} action={selected&&<button className="button" onClick={generatePlaybook} disabled={playbookLoading}>{playbookLoading?"Generating…":playbook?"Regenerate":"Generate playbook"}</button>}>
          {!selected?<div className="empty-review"><BookOpen size={24}/><strong>No incident selected</strong><span className="muted small">Select an incident in Source Health or Incident Queue to generate a grounded triage playbook.</span></div>
          :playbookError?<Unavailable message={playbookError}/>
          :!playbook?<div className="empty-review"><BookOpen size={24}/><strong>No playbook generated yet</strong><span className="muted small">Generate a playbook grounded only in this incident's persisted fields -- nothing is fabricated.</span></div>
          :<div className="playbook-body">
            <FactPairGrid items={[{label:"Likely root cause",value:playbook.likely_root_cause},{label:"Impact summary",value:playbook.impact_summary},{label:"Owner recommendation",value:playbook.owner_recommendation},{label:"Next action",value:playbook.next_action}]}/>
            <AssetImpactList items={playbook.affected_downstream_assets} emptyLabel="No downstream assets on file for this table."/>
            <ChipList title="Affected governed metrics" items={playbook.affected_governed_metrics} tone="down" emptyLabel="No governed metrics linked."/>
            <RecommendationList title="Investigation checklist" items={playbook.debugging_steps}/>
            <div className="section-subtitle">Debugging workflow <span className="muted small">-- run these SQL checks in order (suggested — not executed automatically)</span></div>
            <PlaybookWorkflow steps={playbook.sql_checks} onLoadStep={loadIntoSandbox}/>
            {!playbook.model&&<p className="muted small">Claude isn&apos;t configured in this environment, so root cause / impact / next action above use an honest fallback rather than an AI narration.</p>}
          </div>}
        </SectionCard>
        <SectionCard icon={BookOpen} title="Debugging SQL sandbox" description={selected?`Runs against ${selected.table_id} · read-only`:"Select an incident to run debugging SQL"}>
          {!selected?<div className="empty-review"><BookOpen size={22}/><strong>No incident selected</strong><span className="muted small">Select an incident, then load a suggested check above or write your own read-only query.</span></div>
          :<SqlSandbox
            sql={sandboxSql}
            onSqlChange={setSandboxSql}
            onRun={runSandbox}
            running={sandboxRunning}
            result={sandboxResult}
            onClear={clearSandbox}
            checkTitle={sandboxCheckTitle}
            rowLimit={sandboxResult?.row_limit??25}
          />}
        </SectionCard>
        </section>}

        {activeView==="lineage"&&<section><div className="section-title">Lineage &amp; downstream impact</div>
        <SectionCard icon={GitBranch} title="Source → governed metric → downstream asset" description="Persisted catalog lineage for governed sources">
          <LineageChain items={lineageItems} emptyLabel="No governed lineage on file for the currently monitored sources."/>
        </SectionCard>
        </section>}

        {activeView==="auditTrail"&&<section><div className="section-title">Audit trail</div>
        <SectionCard icon={ListChecks} title="Incident activity" description={selected?`${selected.incident_id} · deterministic + AI-triggered steps`:"Select an incident to see its audit trail"}>
          {selected?<AuditTrailList items={combinedAudit} emptyLabel="No audit entries recorded for this incident yet."/>:<div className="empty-review"><ListChecks size={24}/><strong>No incident selected</strong><span className="muted small">Select an incident in Source Health or Incident Queue to see what metadata was loaded, which check ran, and when the incident was generated.</span></div>}
        </SectionCard>
        </section>}
      </>}
    </div>
  </AppShell>;
}
function TimelineStep({icon:Icon,label,detail}:{icon:typeof CircleDot;label:string;detail:string}){return <div className="timeline-step"><Icon size={17}/><div><strong>{label}</strong><div className="muted small">{detail}</div></div></div>}
function formatFreshness(value:number|null){if(value===null)return "Unknown";if(value<60)return `${Math.round(value)}m`;return `${Math.round(value/60)}h`}
function formatAge(created:string){const minutes=Math.max(0,Math.floor((Date.now()-new Date(created).getTime())/60000));return minutes<60?`${minutes}m`:`${Math.floor(minutes/60)}h ${minutes%60}m`}
// Deterministic, no invented precision: whole numbers print as-is, anything
// else rounds to 2 decimal places purely for display.
function formatMetricValue(value:number){return Number.isInteger(value)?String(value):value.toFixed(2)}
// Observed/expected/difference for the selected incident's detail panel --
// only the facts the persisted incident actually has. Difference is a plain
// arithmetic derivation (observed - expected), not an assumption about
// whether a rise is good or bad for a given check, so no tone is applied.
function observedExpectedFacts(incident:Incident):{label:string;value:string}[]{
  const facts:{label:string;value:string}[]=[];
  if(incident.observed_value!==null)facts.push({label:"Observed",value:formatMetricValue(incident.observed_value)});
  if(incident.expected_value!==null)facts.push({label:"Expected",value:formatMetricValue(incident.expected_value)});
  if(incident.observed_value!==null&&incident.expected_value!==null){
    const diff=incident.observed_value-incident.expected_value;
    facts.push({label:"Difference",value:`${diff>0?"+":""}${formatMetricValue(diff)}`});
  }
  return facts;
}
// Queue-level severity summary, bucketed from the persisted `severity`
// values already on each incident (high/medium seen in practice; anything
// else falls into "Other" rather than assuming a fixed vocabulary).
function severityStrip(incidents:Incident[]):{label:string;value:string}[]{
  const critical=incidents.filter(i=>i.severity==="critical").length;
  const warning=incidents.filter(i=>i.severity==="high"||i.severity==="medium"||i.severity==="warning").length;
  const other=incidents.length-critical-warning;
  return [
    {label:"Critical",value:String(critical)},
    {label:"Warning",value:String(warning)},
    {label:"Other",value:String(other)},
    {label:"Total open",value:String(incidents.length)},
  ];
}
