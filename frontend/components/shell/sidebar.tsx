"use client";

import { ArrowLeft, ArrowRight, ChevronRight, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { Logo } from "./logo";
import { NAV, isActive } from "./nav";

type SidebarProps = {
  collapsed: boolean;
  onToggleCollapsed: () => void;
  mobileOpen: boolean;
  onCloseMobile: () => void;
};

/**
 * 248px sidebar. Desktop: collapsible to icons. Tablet (md–lg): icons only.
 * Mobile (< md): an off-canvas drawer opened from the top bar.
 */
export function Sidebar({ collapsed, onToggleCollapsed, mobileOpen, onCloseMobile }: SidebarProps) {
  return (
    <>
      {/* mobile backdrop */}
      <div
        aria-hidden
        onClick={onCloseMobile}
        className={cn(
          "fixed inset-0 z-30 bg-navy/30 backdrop-blur-[2px] transition-opacity md:hidden dark:bg-black/50",
          mobileOpen ? "opacity-100" : "pointer-events-none opacity-0",
        )}
      />
      <nav
        aria-label="Main"
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-[248px] flex-col gap-2 overflow-y-auto bg-card px-4 py-[22px] shadow-[4px_0_24px_rgba(15,22,56,0.04)] transition-[transform,width] duration-200",
          "md:sticky md:top-0 md:h-dvh md:translate-x-0",
          mobileOpen ? "translate-x-0" : "-translate-x-full",
          // tablet: always icons; desktop: follows the collapse toggle
          "md:w-[84px]",
          collapsed ? "lg:w-[84px]" : "lg:w-[248px]",
        )}
      >
        <div className="flex items-center gap-2 px-1.5 pb-3.5">
          <Link href="/dashboard" aria-label="InvoiceIQ home" onClick={onCloseMobile}>
            <span className="md:hidden lg:inline">
              <Logo compact={collapsed} />
            </span>
            <span className="hidden md:inline lg:hidden">
              <Logo compact />
            </span>
          </Link>
          <button
            type="button"
            onClick={onToggleCollapsed}
            aria-label={collapsed ? "Expand menu" : "Collapse menu"}
            className={cn(
              "ml-auto hidden size-8 items-center justify-center rounded-[9px] bg-canvas text-muted hover:text-ink lg:flex",
              collapsed && "absolute top-[78px] left-[26px]",
            )}
          >
            {collapsed ? <ArrowRight className="size-4" /> : <ArrowLeft className="size-4" />}
          </button>
          <button
            type="button"
            onClick={onCloseMobile}
            aria-label="Close menu"
            className="ml-auto flex size-11 items-center justify-center rounded-control text-muted md:hidden"
          >
            <X className="size-5" />
          </button>
        </div>

        <div className={cn(collapsed && "lg:pt-10")}>
          {NAV.map((group) => (
            <NavSection key={group.title} title={group.title} collapsed={collapsed} onNavigate={onCloseMobile}>
              {group.items}
            </NavSection>
          ))}
        </div>
      </nav>
    </>
  );
}

function NavSection({
  title,
  children,
  collapsed,
  onNavigate,
}: {
  title: string;
  children: (typeof NAV)[number]["items"];
  collapsed: boolean;
  onNavigate: () => void;
}) {
  const pathname = usePathname();
  return (
    <div className="mb-1 flex flex-col gap-0.5 border-b border-line pb-3 last:border-b-0">
      <span
        className={cn(
          "px-3 pt-2 pb-1.5 text-[11px] font-medium tracking-[0.08em] text-muted uppercase",
          "md:sr-only lg:not-sr-only",
          collapsed && "lg:sr-only",
        )}
      >
        {title}
      </span>
      {children.map(({ href, label, icon: Icon, count }) => {
        const active = isActive(pathname, href);
        return (
          <Link
            key={href}
            href={href}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            title={label}
            className={cn(
              "group flex min-h-11 items-center gap-3 rounded-control px-3 text-sm",
              active
                ? "bg-gradient-to-br from-brand-650 to-brand-700 font-medium text-white shadow-brand"
                : "text-ink/85 hover:bg-canvas hover:text-navy",
              "md:justify-center lg:justify-start",
              collapsed && "lg:justify-center",
            )}
          >
            <Icon aria-hidden className="size-[18px] shrink-0" strokeWidth={1.9} />
            <span className={cn("flex-1", "md:sr-only lg:not-sr-only", collapsed && "lg:sr-only")}>
              {label}
            </span>
            {count ? (
              <span
                className={cn(
                  "rounded-full px-2 text-xs font-semibold md:hidden lg:inline",
                  collapsed && "lg:hidden",
                  active ? "bg-white/20 text-white" : "bg-canvas text-muted",
                )}
              >
                {count}
              </span>
            ) : null}
            <ChevronRight
              aria-hidden
              className={cn("size-3.5 opacity-70 md:hidden lg:block", collapsed && "lg:hidden")}
            />
          </Link>
        );
      })}
    </div>
  );
}
