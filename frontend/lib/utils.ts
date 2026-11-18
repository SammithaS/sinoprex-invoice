import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const inr = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 2,
  minimumFractionDigits: 0,
});

/** ₹1,47,500 — Indian digit grouping. Accepts the decimal strings FastAPI returns. */
export function formatINR(value: string | number): string {
  return inr.format(typeof value === "string" ? Number(value) : value);
}

export function formatQty(value: string | number): string {
  return new Intl.NumberFormat("en-IN", { maximumFractionDigits: 3 }).format(Number(value));
}

export function formatDate(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}
