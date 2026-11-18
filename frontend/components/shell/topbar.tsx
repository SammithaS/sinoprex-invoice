"use client";

import { Bell, Mail, Menu, Search } from "lucide-react";
import { ThemeToggle } from "./theme-toggle";

export function Topbar({ onOpenMenu }: { onOpenMenu: () => void }) {
  return (
    <header className="sticky top-0 z-20 flex flex-wrap items-center gap-2 bg-card px-4 py-3 shadow-[0_4px_20px_rgba(15,22,56,0.03)] sm:gap-3.5 sm:px-8 sm:py-[18px]">
      <button
        type="button"
        onClick={onOpenMenu}
        aria-label="Open menu"
        className="flex size-11 items-center justify-center rounded-control text-muted hover:bg-canvas md:hidden"
      >
        <Menu className="size-5" />
      </button>

      <label className="order-last flex h-11 w-full items-center gap-2.5 rounded-control bg-canvas px-4 text-muted sm:order-none sm:w-auto sm:flex-1">
        <Search aria-hidden className="size-4" />
        <span className="sr-only">Search</span>
        <input
          type="search"
          placeholder="Search invoices, vendors, POs"
          className="min-w-0 flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-muted"
        />
      </label>

      <div className="ml-auto flex items-center gap-1 sm:gap-2">
        <button
          type="button"
          aria-label="Notifications"
          className="relative flex size-11 items-center justify-center rounded-control text-muted hover:bg-canvas"
        >
          <Bell aria-hidden className="size-5" strokeWidth={1.9} />
          <span className="absolute top-2.5 right-[11px] size-2 rounded-full border-2 border-card bg-brand-500" />
        </button>
        <button
          type="button"
          aria-label="Messages"
          className="hidden size-11 items-center justify-center rounded-control text-muted hover:bg-canvas sm:flex"
        >
          <Mail aria-hidden className="size-5" strokeWidth={1.9} />
        </button>
        <div className="flex items-center gap-2.5 pl-1">
          <span
            aria-hidden
            className="flex size-[42px] items-center justify-center rounded-full bg-gradient-to-br from-brand-300 to-brand-500 text-[15px] font-semibold text-white"
          >
            PS
          </span>
          <div className="hidden flex-col leading-tight lg:flex">
            <span className="text-sm font-semibold text-navy">Priya Shah</span>
            <span className="text-xs text-muted">AP Reviewer</span>
          </div>
        </div>
        <ThemeToggle />
      </div>
    </header>
  );
}
