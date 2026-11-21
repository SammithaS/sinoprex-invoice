import {
  BellRing,
  Blocks,
  CalendarClock,
  Hash,
  Inbox,
  Mail,
  MessageSquareShare,
  Send,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

const ICONS: Record<string, LucideIcon> = {
  "bell-ring": BellRing,
  "calendar-clock": CalendarClock,
  inbox: Inbox,
  mail: Mail,
  "message-square-share": MessageSquareShare,
  send: Send,
  slack: Hash,
  workflow: Workflow,
};

export function AppIcon({ name, className }: { name: string; className?: string }) {
  const Icon = ICONS[name] ?? Blocks;
  return (
    <span
      className={cn(
        "grid size-11 shrink-0 place-items-center rounded-control bg-brand-50 text-brand-800",
        className,
      )}
    >
      <Icon aria-hidden className="size-5" />
    </span>
  );
}
