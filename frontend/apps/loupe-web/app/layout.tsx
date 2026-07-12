import type { Metadata } from "next";
export const metadata: Metadata = { title: "Loupe — Commerce Intelligence", description: "Governed commerce analytics with source-health evidence." };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) { return <html lang="en"><body>{children}</body></html>; }
