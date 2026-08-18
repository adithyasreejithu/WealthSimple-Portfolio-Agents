"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { getActions, postClassify, postRefresh } from "@/lib/api";
import { useAction } from "@/lib/use-action";
import type { Job } from "@/lib/types";

/**
 * Kick off the two standalone commands (`classify`, `pipeline`) from the
 * browser. Rendered in the site header, so it appears on every page next to
 * the freshness badge. The API runs one job at a time, so both buttons
 * disable while anything is in flight — including a job started from
 * another tab or page, which is what the initial poll picks up.
 */
export function RefreshButtons() {
  const { run, running } = useAction();
  const [active, setActive] = useState<Job | null>(null);

  useEffect(() => {
    let cancelled = false;
    getActions()
      .then((state) => !cancelled && setActive(state.active))
      .catch(() => {
        /* the badge is advisory; a failed probe should not surface an error */
      });
    return () => {
      cancelled = true;
    };
  }, [running]);

  const busy = running || active !== null;

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        variant="outline"
        size="sm"
        disabled={busy}
        onClick={() =>
          run(postClassify, {
            pending: "Running classification…",
            success: "Classification updated",
          })
        }
      >
        Run Classification
      </Button>
      <Button
        variant="outline"
        size="sm"
        disabled={busy}
        onClick={() =>
          run(postRefresh, {
            pending: "Running the data pipeline…",
            success: "Pipeline finished",
          })
        }
      >
        Run Data Pipeline
      </Button>
      {active ? (
        <span className="text-muted-foreground text-xs">
          A {active.kind} job is already running.
        </span>
      ) : null}
    </div>
  );
}
