"use client";

import { useId } from "react";
import type { AppField } from "@/lib/api/apps";

export const inputClass =
  "h-11 w-full rounded-control border border-line bg-card px-3 text-sm font-normal text-ink placeholder:text-muted";

/** Form value for a field: lists of emails are edited as text, everything else as is. */
export function toFormValue(field: AppField, value: unknown): unknown {
  const v = value === undefined || value === null ? field.default : value;
  if (field.kind === "emails") return Array.isArray(v) ? v.join(", ") : (v ?? "");
  if (field.kind === "multiselect") return Array.isArray(v) ? v : [];
  if (field.kind === "boolean") return Boolean(v);
  return v === undefined || v === null ? "" : String(v);
}

export function FieldInput({
  field,
  value,
  onChange,
}: {
  field: AppField;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const id = useId();
  const help = field.help ? (
    <span id={`${id}-help`} className="text-xs font-normal text-muted">
      {field.help}
    </span>
  ) : null;
  const label = (
    <>
      {field.label}
      {field.required && <span className="text-rejected"> *</span>}
    </>
  );

  if (field.kind === "boolean") {
    return (
      <label className="flex items-start gap-2.5 text-sm text-navy">
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
          className="mt-0.5 size-4 accent-brand-700"
        />
        <span className="flex flex-col gap-0.5">
          <span className="font-medium">{label}</span>
          {help}
        </span>
      </label>
    );
  }

  if (field.kind === "multiselect") {
    const selected = new Set(Array.isArray(value) ? (value as string[]) : []);
    return (
      <fieldset className="flex flex-col gap-1.5">
        <legend className="mb-1.5 text-[13px] font-medium text-navy">{label}</legend>
        {field.options.map((o) => (
          <label key={o.value} className="flex items-center gap-2.5 text-sm text-ink">
            <input
              type="checkbox"
              checked={selected.has(o.value)}
              onChange={(e) => {
                const next = new Set(selected);
                if (e.target.checked) next.add(o.value);
                else next.delete(o.value);
                onChange(field.options.map((x) => x.value).filter((x) => next.has(x)));
              }}
              className="size-4 accent-brand-700"
            />
            {o.label}
          </label>
        ))}
        {help}
      </fieldset>
    );
  }

  const common = {
    id,
    value: String(value ?? ""),
    "aria-describedby": field.help ? `${id}-help` : undefined,
    onChange: (e: { target: { value: string } }) => onChange(e.target.value),
  };

  return (
    <label htmlFor={id} className="flex flex-col gap-1.5 text-[13px] font-medium text-navy">
      <span>{label}</span>
      {field.kind === "select" ? (
        <select {...common} className={inputClass}>
          {!field.required && <option value="">—</option>}
          {field.options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      ) : field.kind === "textarea" ? (
        <textarea {...common} rows={3} className={`${inputClass} h-auto py-2.5`} />
      ) : (
        <input
          {...common}
          type={
            field.kind === "number"
              ? "number"
              : field.kind === "email"
                ? "email"
                : field.kind === "url"
                  ? "url"
                  : field.kind === "time"
                    ? "time"
                    : "text"
          }
          min={field.minimum ?? undefined}
          max={field.maximum ?? undefined}
          placeholder={field.kind === "emails" ? "name@company.com, other@company.com" : undefined}
          className={inputClass}
        />
      )}
      {help}
    </label>
  );
}
