import { Clock } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { fmtDate, fmtDateTime } from "@/lib/format";

interface FreshnessBadgeProps {
  databaseMtime: string;
  reportGeneratedAt: string;
  latestPriceDate: string | null;
  cashSource?: string;
}

/**
 * The mock's "Refresh Date {Source: Date x3}" — three provenance dates so it's
 * clear how current each layer is: when the database file last changed, when
 * this report was computed, and the most recent market close in it.
 */
export function FreshnessBadge({
  databaseMtime,
  reportGeneratedAt,
  latestPriceDate,
  cashSource,
}: FreshnessBadgeProps) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Badge variant="outline" className="gap-1.5 font-normal">
            <Clock className="size-3.5" />
            <span className="hidden sm:inline">Data as of </span>
            {fmtDate(latestPriceDate)}
          </Badge>
        }
      />
      <TooltipContent align="end" className="text-xs">
        <div className="grid gap-1">
          <Row label="Prices through" value={fmtDate(latestPriceDate)} />
          <Row label="Database updated" value={fmtDateTime(databaseMtime)} />
          <Row label="Report computed" value={fmtDateTime(reportGeneratedAt)} />
          {cashSource ? <Row label="Cash source" value={cashSource} /> : null}
        </div>
      </TooltipContent>
    </Tooltip>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{value}</span>
    </div>
  );
}
