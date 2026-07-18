"use client";

import { usePathname } from "next/navigation";

const TITLES: Record<string, string> = {
  "/": "Overview",
  "/portfolio": "Portfolio",
  "/stocks": "Stocks",
  "/etfs": "ETFs",
  "/income": "Income",
  "/data-quality": "Data Quality",
};

export function PageTitle() {
  const pathname = usePathname();
  if (pathname.startsWith("/holdings/")) {
    const symbol = decodeURIComponent(pathname.split("/")[2] ?? "");
    return <h1 className="text-base font-semibold">{symbol.toUpperCase()}</h1>;
  }
  const title =
    TITLES[pathname] ??
    Object.entries(TITLES).find(([href]) => href !== "/" && pathname.startsWith(href))?.[1] ??
    "Dashboard";
  return <h1 className="text-base font-semibold">{title}</h1>;
}
