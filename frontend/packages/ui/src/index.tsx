import type { ComponentType, ReactNode } from "react";
import { Activity, ShieldCheck, Sparkles } from "lucide-react";
import "./styles.css";
import "./themes.css";

export function PlatformLinks({ active }: { active: "loupe" | "governance" | "triage" }) {
  const links = [
    { id: "loupe", label: "Loupe", icon: Sparkles, href: process.env.NEXT_PUBLIC_LOUPE_URL ?? "http://localhost:3000" },
    { id: "governance", label: "Governance", icon: ShieldCheck, href: process.env.NEXT_PUBLIC_GOVERNANCE_URL ?? "http://localhost:3001" },
    { id: "triage", label: "Triage", icon: Activity, href: process.env.NEXT_PUBLIC_TRIAGE_URL ?? "http://localhost:3002" },
  ] as const;
  return <nav className="platform-links" aria-label="Loupe platform applications">{links.map(({ id, label, icon: Icon, href }) => <a key={id} className={`platform-link ${active === id ? "active" : ""}`} href={href}><Icon size={15} />{label}</a>)}</nav>;
}

export function AppShell({
  active,
  brand,
  brandIcon: BrandIcon,
  navigation,
  children,
}: {
  active: "loupe" | "governance" | "triage";
  brand: string;
  brandIcon: ComponentType<{ size?: number }>;
  navigation: { label: string; icon: ComponentType<{ size?: number }>; active?: boolean }[];
  children: ReactNode;
}) {
  return <div className={`product product-${active}`}><div className="app-shell"><aside className="sidebar"><div className="brand"><BrandIcon size={17} />{brand}</div><nav className="app-nav" aria-label={`${brand} sections`}>{navigation.map(({ label, icon: Icon, active: selected }) => <button type="button" className={`nav-item ${selected ? "active" : ""}`} aria-current={selected ? "page" : undefined} aria-disabled={!selected} disabled={!selected} key={label}><Icon size={17} />{label}</button>)}</nav></aside><main className="workspace">{children}</main></div></div>;
}

export function Badge({ children, tone = "accent" }: { children: ReactNode; tone?: "accent" | "neutral" | "warning" }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`card ${className}`}>{children}</section>;
}

export function Unavailable({ message }: { message: string }) {
  return <Card className="unavailable"><strong>Live data unavailable</strong><p>{message}</p></Card>;
}
