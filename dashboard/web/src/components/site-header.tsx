import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { FreshnessBadge } from "@/components/freshness-badge";
import { PageTitle } from "@/components/page-title";
import { ThemeToggle } from "@/components/theme-toggle";
import { getReport } from "@/lib/api";

/**
 * Server component: fetches the report once for the freshness badge. Resilient
 * by design — if the API is unreachable the header still renders (sidebar, page
 * title, theme toggle), and each page's own error boundary handles the body.
 */
export async function SiteHeader() {
  let freshness: {
    databaseMtime: string;
    reportGeneratedAt: string;
    latestPriceDate: string | null;
    cashSource?: string;
  } | null = null;

  try {
    const { database_mtime, generated_at, report } = await getReport();
    const latestPriceDate = report.holdings
      .map((h) => h.last_price_date)
      .filter((d): d is string => Boolean(d))
      .sort()
      .at(-1) ?? null;
    freshness = {
      databaseMtime: database_mtime,
      reportGeneratedAt: generated_at,
      latestPriceDate,
      cashSource: report.summary.cash.source,
    };
  } catch {
    freshness = null;
  }

  return (
    <header className="sticky top-0 z-10 flex h-14 shrink-0 items-center gap-2 border-b bg-background px-4">
      <SidebarTrigger />
      <Separator orientation="vertical" className="mr-1 h-5" />
      <PageTitle />
      <div className="ml-auto flex items-center gap-2">
        {freshness ? <FreshnessBadge {...freshness} /> : null}
        <ThemeToggle />
      </div>
    </header>
  );
}
