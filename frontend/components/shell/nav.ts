import {
  Blocks,
  BookOpen,
  Building2,
  ClipboardCheck,
  FilePen,
  LayoutDashboard,
  MessageSquare,
  NotebookTabs,
  Plus,
  Settings,
  UserCheck,
  type LucideIcon,
} from "lucide-react";

export type NavItem = { href: string; label: string; icon: LucideIcon; count?: number };
export type NavGroup = { title: string; items: NavItem[] };

export const NAV: NavGroup[] = [
  {
    title: "Workflow",
    items: [
      { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
      { href: "/", label: "New run", icon: Plus },
      { href: "/review", label: "Review queue", icon: ClipboardCheck },
      { href: "/approvals", label: "Approvals", icon: UserCheck },
      { href: "/erp", label: "ERP ledger", icon: NotebookTabs },
    ],
  },
  {
    title: "Setup",
    items: [
      { href: "/procedure", label: "Procedure", icon: FilePen },
      { href: "/knowledge", label: "Knowledge base", icon: BookOpen },
      { href: "/reference", label: "Vendors & POs", icon: Building2 },
      { href: "/apps", label: "Apps", icon: Blocks },
    ],
  },
  {
    title: "Help",
    items: [
      { href: "/assistant", label: "Ask AP", icon: MessageSquare },
      { href: "/settings", label: "Settings", icon: Settings },
    ],
  },
];

export function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}
