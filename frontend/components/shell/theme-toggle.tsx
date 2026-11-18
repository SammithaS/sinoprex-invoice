"use client";

import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useSyncExternalStore } from "react";
import { cn } from "@/lib/utils";

const subscribe = () => () => {};

export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  // The theme is only known in the browser; render a neutral state on the server.
  const mounted = useSyncExternalStore(subscribe, () => true, () => false);
  const dark = mounted && resolvedTheme === "dark";

  return (
    <button
      type="button"
      role="switch"
      aria-checked={dark}
      aria-label="Dark mode"
      onClick={() => setTheme(dark ? "light" : "dark")}
      className="flex h-11 items-center"
    >
      <span className="flex h-[30px] w-[52px] items-center rounded-full bg-brand-50 p-[3px]">
        <span
          className={cn(
            "flex size-6 items-center justify-center rounded-full bg-brand-500 transition-transform",
            dark && "translate-x-[22px]",
          )}
        >
          {dark ? (
            <Moon aria-hidden className="size-3.5 text-white" strokeWidth={2.2} />
          ) : (
            <Sun aria-hidden className="size-3.5 text-white" strokeWidth={2.2} />
          )}
        </span>
      </span>
    </button>
  );
}
