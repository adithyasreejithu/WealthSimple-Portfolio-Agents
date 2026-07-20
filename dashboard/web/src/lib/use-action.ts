"use client";

import { useCallback, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { ApiError, getJob } from "@/lib/api";
import type { Job } from "@/lib/types";

const POLL_INTERVAL_MS = 1200;
const POLL_TIMEOUT_MS = 15 * 60 * 1000;

/**
 * Runs one dashboard action and follows the background job it returns.
 *
 * Actions are asynchronous by design (the API never blocks a request on a
 * pipeline run), so every caller needs the same three things: a pending flag
 * for its button, polling until the job settles, and a refresh so the server
 * components pick up the new data. Keeping that here means a new action is a
 * one-liner rather than another copy of the polling loop.
 */
export function useAction() {
  const router = useRouter();
  const [running, setRunning] = useState(false);
  const cancelled = useRef(false);

  const run = useCallback(
    async (
      start: () => Promise<Job | { job: Job }>,
      { pending, success }: { pending: string; success: string },
    ): Promise<boolean> => {
      setRunning(true);
      cancelled.current = false;
      const toastId = toast.loading(pending);
      try {
        const started = await start();
        const job = "job" in started ? started.job : started;
        const settled = await pollJob(job, () => cancelled.current);
        if (settled.status === "failed") {
          toast.error("Action failed", { id: toastId, description: settled.detail ?? undefined });
          return false;
        }
        toast.success(success, { id: toastId });
        router.refresh();
        return true;
      } catch (error) {
        // A 409 means another job holds the database; that is expected
        // contention rather than a fault, so it gets a plainer message.
        const conflict = error instanceof ApiError && error.status === 409;
        toast[conflict ? "warning" : "error"](
          conflict ? "Another job is already running" : "Action failed",
          { id: toastId, description: error instanceof Error ? error.message : String(error) },
        );
        return false;
      } finally {
        setRunning(false);
      }
    },
    [router],
  );

  return { run, running };
}

async function pollJob(job: Job, isCancelled: () => boolean): Promise<Job> {
  const deadline = Date.now() + POLL_TIMEOUT_MS;
  let current = job;
  while (current.status === "queued" || current.status === "running") {
    if (isCancelled()) return current;
    if (Date.now() > deadline) {
      return { ...current, status: "failed", detail: "Timed out waiting for the job to finish." };
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
    try {
      current = await getJob(current.id);
    } catch {
      // A transient poll failure (the API restarts during a pipeline run) is
      // not itself a job failure — keep waiting until the deadline.
    }
  }
  return current;
}
