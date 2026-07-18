import type { ReactNode } from "react";

import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

interface StatTileProps {
  label: string;
  value: ReactNode;
  hint?: string;
  valueClassName?: string;
}

/** Compact labelled figure with an optional definition tooltip. */
export function StatTile({ label, value, hint, valueClassName }: StatTileProps) {
  const labelNode = hint ? (
    <Tooltip>
      <TooltipTrigger
        render={<span className="cursor-help text-muted-foreground text-sm underline decoration-dotted underline-offset-2" />}
      >
        {label}
      </TooltipTrigger>
      <TooltipContent className="max-w-56 text-xs">{hint}</TooltipContent>
    </Tooltip>
  ) : (
    <span className="text-muted-foreground text-sm">{label}</span>
  );

  return (
    <div className="rounded-lg border p-3">
      {labelNode}
      <p className={cn("mt-1 text-xl font-semibold tabular-nums tracking-tight", valueClassName)}>
        {value}
      </p>
    </div>
  );
}
