"use client";

import { Button } from "@/components/ui/button";
import { postClassify } from "@/lib/api";
import { useAction } from "@/lib/use-action";

/**
 * Runs the automatic classifier from a button click (e.g., in the unclassified
 * holdings card). Unlike RefreshButtons, this doesn't poll for concurrent jobs
 * elsewhere — it just triggers the classify action and shows loading/result
 * states for this specific button.
 */
export function ClassifyActionButton() {
  const { run, running } = useAction();

  return (
    <Button
      variant="outline"
      size="sm"
      disabled={running}
      onClick={() =>
        run(postClassify, {
          pending: "Classifying…",
          success: "Classification updated",
        })
      }
    >
      {running ? "Classifying…" : "Classify"}
    </Button>
  );
}
