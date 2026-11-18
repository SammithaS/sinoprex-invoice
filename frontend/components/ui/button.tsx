import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

export const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-control text-sm font-semibold whitespace-nowrap transition-[filter,background-color] disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary:
          "bg-gradient-to-br from-brand-650 to-brand-700 text-white shadow-brand hover:brightness-110",
        soft: "bg-brand-50 text-brand-700 hover:bg-brand-100 dark:text-brand-500",
        danger: "bg-rejected-bg text-rejected hover:brightness-95",
        ghost: "bg-card text-ink shadow-card hover:bg-canvas",
        icon: "size-11 bg-transparent text-muted hover:bg-canvas [&_svg]:size-5",
      },
      size: {
        md: "h-11 px-5",
        sm: "h-9 px-3.5 text-[13px]",
        icon: "size-11 p-0",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

type ButtonProps = ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & { asChild?: boolean };

export function Button({ className, variant, size, asChild, ...props }: ButtonProps) {
  const Comp = asChild ? Slot : "button";
  return <Comp className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}
