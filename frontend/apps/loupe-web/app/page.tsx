"use client";

import { useEffect, useState } from "react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { CalendarRange, ChartNoAxesCombined, Database, LayoutDashboard, MessageSquareText, PackageSearch, ScanSearch, SlidersHorizontal, Sparkles, TrendingUp, Users } from "lucide-react";
import { AppShell, Badge, Card, Unavailable } from "@loupe/ui";

type Overview = {
  start_date:string; end_date:string; data_source:string; insight:string;
  revenue:{value:number;change_pct:number|null}; gross_margin_pct:{value:number;change_pct:number|null};
  order_items:{value:number;change_pct:number|null}; return_rate_pct:{value:number;change_pct:number|null};
  trend:{period:string;revenue:number;margin:number;items:number}[];
  source_health:{status:string;warning:string|null};
  metric_context:{certification_status:string;version:string|null;reporting_grain:string};
};

const nav = [
  {label:"Overview",icon:LayoutDashboard,active:true},{label:"Ask Loupe",icon:MessageSquareText},
  {label:"Performance",icon:ChartNoAxesCombined},{label:"Customers",icon:Users},
  {label:"Products",icon:PackageSearch},{label:"Scenarios",icon:SlidersHorizontal},
];
const money=(n:number)=>new Intl.NumberFormat("en-US",{style:"currency",currency:"USD",notation:"compact",maximumFractionDigits:2}).format(n);
const number=(n:number)=>new Intl.NumberFormat("en-US",{maximumFractionDigits:0}).format(n);
const delta=(n:number|null,suffix="%")=>n===null?"Prior period unavailable":`${n>=0?"+":""}${n.toFixed(1)}${suffix}`;

export default function Page(){
  const [data,setData]=useState<Overview|null>(null); const [error,setError]=useState<string|null>(null);
  useEffect(()=>{const end=new Date();const start=new Date(end);start.setDate(end.getDate()-29);const q=new URLSearchParams({start_date:start.toISOString().slice(0,10),end_date:end.toISOString().slice(0,10)});fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL??"http://localhost:8000"}/api/v1/loupe/overview?${q}`).then(async r=>{if(!r.ok)throw new Error();return r.json()}).then(setData).catch(()=>setError("The governed warehouse could not be reached. No placeholder metrics were substituted."));},[]);
  return <AppShell active="loupe" brand="Loupe" brandIcon={ScanSearch} navigation={nav}>
    <header className="page-header"><div><div className="eyebrow">ASSISTANT LAYER</div><h1>Commerce intelligence</h1><div className="muted">Live performance from governed warehouse data</div></div><div className="actions"><Badge><Database size={15}/>{data?.data_source??"BigQuery"}</Badge><button className="button"><CalendarRange size={15}/>Last 30 days</button></div></header>
    {error?<Unavailable message={error}/>:!data?<div className="card skeleton" aria-label="Loading live commerce intelligence"/>:<>
      <div className="stats"><Stat label="Net revenue" value={money(data.revenue.value)} change={delta(data.revenue.change_pct)}/><Stat label="Gross margin" value={`${data.gross_margin_pct.value.toFixed(1)}%`} change={delta(data.gross_margin_pct.change_pct," pts")}/><Stat label="Order items" value={number(data.order_items.value)} change={delta(data.order_items.change_pct)}/><Stat label="Return rate" value={`${data.return_rate_pct.value.toFixed(1)}%`} change={delta(data.return_rate_pct.change_pct," pts")}/></div>
      <div className="main-grid"><Card><div className="card-head"><div><h2>Revenue performance</h2><div className="muted small">Net revenue · {data.metric_context.reporting_grain}</div></div><Badge tone={data.source_health.status==="healthy"?"accent":"warning"}>{data.source_health.status}</Badge></div><div className="chart-frame"><ResponsiveContainer width="100%" height="100%"><AreaChart data={data.trend}><defs><linearGradient id="revenueFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#2995ff" stopOpacity={.25}/><stop offset="1" stopColor="#2995ff" stopOpacity={0}/></linearGradient></defs><CartesianGrid stroke="#e5e5e7" vertical={false}/><XAxis dataKey="period" tickLine={false} axisLine={false} tick={{fill:"#85868b",fontSize:12}}/><YAxis hide/><Tooltip formatter={(v)=>money(Number(v))}/><Area type="monotone" dataKey="revenue" stroke="#2995ff" strokeWidth={3} fill="url(#revenueFill)"/></AreaChart></ResponsiveContainer></div></Card><Card><div className="card-head"><h2>Loupe insight</h2><Sparkles size={18}/></div><div className="insight"><TrendingUp size={18}/><div>{data.insight}</div></div>{data.source_health.warning&&<div className="health-warning">{data.source_health.warning}</div>}<div className="evidence"><span className="muted small">{data.metric_context.certification_status} · {data.metric_context.version??"version unavailable"}</span><button className="button">Evidence</button></div></Card></div>
    </>}
  </AppShell>;
}
function Stat({label,value,change}:{label:string;value:string;change:string}){return <Card><div className="stat-label">{label}</div><div className="stat-line"><span className="stat-value">{value}</span><span className="delta">{change}</span></div></Card>}
