"use client";

import { AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

/**
 * Shared body for every route's error.tsx. The most common failure is the API
 * not running, so the message points there and offers a retry.
 */
export function ErrorState({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <Card className="mx-auto mt-8 max-w-md border-destructive/40">
      <CardContent className="flex flex-col items-center gap-3 py-8 text-center">
        <AlertTriangle className="size-8 text-destructive" />
        <div>
          <p className="font-semibold">Couldn&apos;t load this page</p>
          <p className="text-muted-foreground mt-1 text-sm">{error.message}</p>
          <p className="text-muted-foreground mt-2 text-xs">
            Is the API running? Start it with{" "}
            <code className="rounded bg-muted px-1 py-0.5">
              uv run uvicorn main:app --port 8000 --app-dir dashboard/api
            </code>
          </p>
        </div>
        <Button onClick={reset} variant="outline" size="sm">
          Try again
        </Button>
      </CardContent>
    </Card>
  );
}
