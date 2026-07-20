"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

export interface AttentionItem {
  /** Stable key so the same item is not re-announced on every navigation. */
  code: string;
  message: string;
  severity: "warning" | "info";
}

/**
 * Surfaces work the pipeline cannot do on its own — holdings the classifier
 * could not place, symbols it could not map, data it could not find — as a
 * toast that links to the page where they can be fixed.
 *
 * Announced once per browser session (sessionStorage), because these
 * conditions persist until someone acts on them and a toast on every page view
 * would train the user to dismiss them unread.
 */
export function Notifications({ items }: { items: AttentionItem[] }) {
  const router = useRouter();

  useEffect(() => {
    for (const item of items) {
      const key = `dq-notice:${item.code}:${item.message}`;
      if (sessionStorage.getItem(key)) continue;
      sessionStorage.setItem(key, "1");
      toast[item.severity](item.message, {
        action: {
          label: "Review",
          onClick: () => router.push("/data-quality"),
        },
      });
    }
  }, [items, router]);

  return null;
}
