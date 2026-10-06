import { forwardRef, useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

const control =
  "h-[var(--control-h)] w-full rounded-sm border border-border-strong bg-surface-2 px-3 text-body text-text placeholder:text-subtle " +
  "disabled:opacity-50 aria-[invalid=true]:border-danger max-sm:min-h-[44px]";

type FieldProps = { label: string; hint?: ReactNode; error?: string; hideLabel?: boolean; mono?: boolean };

function FieldShell({ id, label, hint, error, hideLabel, children }: FieldProps & { id: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <label htmlFor={id} className={cn("text-small font-medium text-text", hideLabel && "sr-only")}>
        {label}
      </label>
      {children}
      {hint && !error && (
        <div id={`${id}-hint`} className="text-caption font-normal text-subtle">
          {hint}
        </div>
      )}
      {error && (
        <div id={`${id}-error`} className="text-small text-danger">
          {error}
        </div>
      )}
    </div>
  );
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement> & FieldProps & { "data-testid"?: string }>(
  function Input({ label, hint, error, hideLabel, mono, className, id, ...rest }, ref) {
    const auto = useId();
    const fid = id ?? auto;
    return (
      <FieldShell id={fid} label={label} hint={hint} error={error} hideLabel={hideLabel}>
        <input
          ref={ref}
          id={fid}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${fid}-error` : hint ? `${fid}-hint` : undefined}
          className={cn(control, mono && "font-mono text-mono", className)}
          {...rest}
        />
      </FieldShell>
    );
  },
);

export type Option = { value: string; label: string; disabled?: boolean };

export const Select = forwardRef<
  HTMLSelectElement,
  Omit<SelectHTMLAttributes<HTMLSelectElement>, "onChange"> & FieldProps & { options: Option[]; onChange?: (v: string) => void; "data-testid"?: string }
>(function Select({ label, hint, error, hideLabel, options, onChange, className, id, ...rest }, ref) {
  const auto = useId();
  const fid = id ?? auto;
  return (
    <FieldShell id={fid} label={label} hint={hint} error={error} hideLabel={hideLabel}>
      <select
        ref={ref}
        id={fid}
        aria-invalid={error ? true : undefined}
        className={cn(control, "pr-8", className)}
        onChange={(e) => onChange?.(e.target.value)}
        {...rest}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value} disabled={o.disabled}>
            {o.label}
          </option>
        ))}
      </select>
    </FieldShell>
  );
});

export function Checkbox({
  label,
  checked,
  onChange,
  disabled,
  "data-testid": testId,
}: {
  label: ReactNode;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
  "data-testid"?: string;
}) {
  return (
    <label className="inline-flex min-h-6 cursor-pointer items-center gap-2 text-body max-sm:min-h-[44px]">
      <input
        type="checkbox"
        className="h-4 w-4 accent-[var(--accent)]"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        data-testid={testId}
      />
      <span>{label}</span>
    </label>
  );
}

/** Segmented control with radiogroup semantics. */
export function SegmentedControl<T extends string>({
  label,
  value,
  options,
  onChange,
  "data-testid": testId,
}: {
  label: string;
  value: T;
  options: { value: T; label: ReactNode; disabled?: boolean; testId?: string }[];
  onChange: (v: T) => void;
  "data-testid"?: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} data-testid={testId} className="inline-flex rounded-sm border border-border-strong bg-surface-2 p-0.5">
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={on}
            disabled={o.disabled}
            data-testid={o.testId}
            onClick={() => onChange(o.value)}
            onKeyDown={(e) => {
              if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
              e.preventDefault();
              const enabled = options.filter((x) => !x.disabled);
              const i = enabled.findIndex((x) => x.value === value);
              const next = enabled[(i + (e.key === "ArrowRight" ? 1 : enabled.length - 1)) % enabled.length];
              onChange(next.value);
            }}
            tabIndex={on ? 0 : -1}
            className={cn(
              "h-8 rounded-[5px] px-3 text-small font-medium transition-colors duration-fast disabled:opacity-50 max-sm:min-h-[40px]",
              on ? "bg-surface-1 text-text shadow-elev-1" : "text-muted hover:text-text",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
