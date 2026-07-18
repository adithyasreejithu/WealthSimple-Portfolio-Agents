import type { ReactNode } from "react";

import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface KpiCardProps {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  subClassName?: string;
  icon?: ReactNode;
}

export function KpiCard({ label, value, sub, subClassName, icon }: KpiCardProps) {
  return (
    <Card className="gap-0 py-4">
      <CardContent className="px-4">
        <div className="flex items-center justify-between">
          <p className="text-muted-foreground text-sm">{label}</p>
          {icon ? <span className="text-muted-foreground">{icon}</span> : null}
        </div>
        <p className="mt-1 text-2xl font-semibold tabular-nums tracking-tight">{value}</p>
        {sub ? <p className={cn("mt-0.5 text-sm tabular-nums", subClassName)}>{sub}</p> : null}
      </CardContent>
    </Card>
  );
}
