"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { postOverride } from "@/lib/api";
import { useAction } from "@/lib/use-action";

/**
 * Assign a group to a holding the classifier could not place. Submitting
 * writes an approved manual override to the classifier's reference YAML and
 * re-runs classification, which is exactly what editing the file by hand and
 * running `portfolio-classify` + `classification-sync` would do.
 */
export function ClassifyDialog({
  ticker,
  groups,
  currentGroup,
}: {
  ticker: string;
  groups: string[];
  currentGroup?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const [group, setGroup] = useState("");
  const [rationale, setRationale] = useState("");
  const { run, running } = useAction();

  async function submit() {
    const ok = await run(
      () => postOverride({ ticker, primary_group: group, rationale }),
      {
        pending: `Classifying ${ticker}…`,
        success: `${ticker} assigned to ${group}`,
      },
    );
    if (ok) {
      setOpen(false);
      setRationale("");
    }
  }

  const complete = group !== "" && rationale.trim() !== "";

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="outline" size="sm" />}>Classify</DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Classify {ticker}</DialogTitle>
          <DialogDescription>
            Pins {ticker} to a group as an approved manual override, then re-runs
            classification. {currentGroup ? `Currently ${currentGroup}.` : null}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1.5">
            <label className="text-sm font-medium" htmlFor={`group-${ticker}`}>
              Group
            </label>
            <Select value={group} onValueChange={(v) => setGroup(v ?? "")}>
              <SelectTrigger id={`group-${ticker}`} className="w-full">
                <SelectValue placeholder="Choose a group" />
              </SelectTrigger>
              <SelectContent>
                {groups.map((name) => (
                  <SelectItem key={name} value={name}>
                    {name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <label className="text-sm font-medium" htmlFor={`rationale-${ticker}`}>
              Rationale
            </label>
            <Input
              id={`rationale-${ticker}`}
              value={rationale}
              onChange={(e) => setRationale(e.target.value)}
              placeholder="Why does this holding belong there?"
            />
            <p className="text-muted-foreground text-xs">
              Stored with the override so the reason survives in the reference file.
            </p>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => setOpen(false)} disabled={running}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={!complete || running}>
            {running ? "Saving…" : "Save and re-classify"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
