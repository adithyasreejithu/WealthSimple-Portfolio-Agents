"use client";

import { Button } from "@/components/ui/button";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { groupColor, groupRank } from "@/lib/derive";

export interface TickerOption {
  symbol: string;
  group?: string | null;
}

interface TickerPickerProps {
  options: TickerOption[];
  selected: string[];
  onChange: (symbols: string[]) => void;
}

/** Multi-select chips for choosing which holdings enter the correlation matrix. */
export function TickerPicker({ options, selected, onChange }: TickerPickerProps) {
  const groups = [...new Set(options.map((option) => option.group ?? "Unclassified"))].sort((a, b) => groupRank(a) - groupRank(b));
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5">
        <Button variant="outline" size="xs" onClick={() => onChange(options.map((option) => option.symbol))}>All</Button>
        {groups.map((group) => <Button key={group} variant="outline" size="xs" onClick={() => onChange(options.filter((option) => (option.group ?? "Unclassified") === group).map((option) => option.symbol))}>{group}</Button>)}
      </div>
      {groups.map((group) => (
        <div key={group} className="flex items-start gap-3">
          <span className="text-muted-foreground mt-1.5 w-24 shrink-0 text-xs">{group}</span>
          <ToggleGroup
            multiple
            value={selected.filter((symbol) => options.some((option) => option.symbol === symbol && (option.group ?? "Unclassified") === group))}
            onValueChange={(value) => {
              const groupSymbols = new Set(options.filter((option) => (option.group ?? "Unclassified") === group).map((option) => option.symbol));
              onChange([...selected.filter((symbol) => !groupSymbols.has(symbol)), ...(value as string[])]);
            }}
            className="flex-wrap justify-start"
          >
            {options.filter((option) => (option.group ?? "Unclassified") === group).map((option) => (
              <ToggleGroupItem key={option.symbol} value={option.symbol} size="sm" variant="outline" className="gap-1.5">
                {option.group ? <span className="size-1.5 rounded-full" style={{ background: groupColor(option.group) }} /> : null}
                {option.symbol}
              </ToggleGroupItem>
            ))}
          </ToggleGroup>
        </div>
      ))}
    </div>
  );
}
