import { Activity, Gauge, History, ListChecks, Siren, TableProperties } from "lucide-react";
import { AppShell, Unavailable } from "@loupe/ui";
const nav=[{label:"Warehouse",icon:Gauge,active:true},{label:"Tables",icon:TableProperties},{label:"Checks",icon:ListChecks},{label:"Incidents",icon:Siren},{label:"Timeline",icon:History}];
export default function Page(){return <AppShell active="triage" brand="Triage" brandIcon={Activity} navigation={nav}><header className="page-header"><div><div className="eyebrow">RELIABILITY LAYER</div><h1>Warehouse health</h1><div className="muted">Deterministic checks across governed data sources</div></div></header><Unavailable message="The Triage API screen is a later vertical slice. No fictional incidents were substituted."/></AppShell>}
