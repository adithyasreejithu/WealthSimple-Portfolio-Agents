import { Skeleton } from "@/components/ui/skeleton";

/**
 * Generic page loading state. A cold report build is ~7s after a pipeline run,
 * so every route shows structure while it waits.
 */
export function DashboardSkeleton({ kpis = 4, blocks = 2 }: { kpis?: number; blocks?: number }) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {Array.from({ length: kpis }).map((_, i) => (
          <Skeleton key={i} className="h-24 rounded-xl" />
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        {Array.from({ length: blocks }).map((_, i) => (
          <Skeleton key={i} className={i === 0 ? "h-80 rounded-xl lg:col-span-2" : "h-80 rounded-xl"} />
        ))}
      </div>
    </div>
  );
}
