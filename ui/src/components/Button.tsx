import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { Loader2, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/cn";
import { Kbd } from "./Kbd";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
type IconType = LucideIcon;

export type ButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> & {
  variant?: ButtonVariant;
  size?: "sm" | "md";
  loading?: boolean;
  icon?: IconType;
  /** Icon-only buttons must pass aria-label. */
  iconOnly?: boolean;
  kbd?: string[];
  children?: ReactNode;
  "data-testid"?: string;
};

const variants: Record<ButtonVariant, string> = {
  primary: "bg-accent text-on-accent hover:bg-accent-hover border border-transparent",
  secondary: "bg-surface-2 text-text border border-border-strong hover:bg-surface-3",
  ghost: "bg-transparent text-text border border-transparent hover:bg-surface-3",
  danger: "bg-danger-fill text-on-danger-fill border border-transparent hover:brightness-110",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", loading, icon: Icon, iconOnly, kbd, className, children, disabled, type, ...rest },
  ref,
) {
  const h = size === "sm" ? "h-8 text-small" : "h-9 text-body";
  return (
    <button
      ref={ref}
      type={type ?? "button"}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        "relative inline-flex select-none items-center justify-center gap-2 whitespace-nowrap rounded-sm font-medium",
        "transition-colors duration-fast disabled:cursor-not-allowed disabled:opacity-50",
        "max-sm:min-h-[44px]",
        h,
        iconOnly ? (size === "sm" ? "w-8" : "w-9") + " max-sm:min-w-[44px] px-0" : size === "sm" ? "px-3" : "px-4",
        variants[variant],
        className,
      )}
      {...rest}
    >
      {loading ? (
        <>
          <span className="invisible inline-flex items-center gap-2">
            {Icon && <Icon size={16} aria-hidden />}
            {!iconOnly && children}
          </span>
          <Loader2 size={16} className="absolute animate-spin" aria-hidden />
        </>
      ) : (
        <>
          {Icon && <Icon size={16} strokeWidth={1.75} aria-hidden />}
          {!iconOnly && children}
          {kbd && !iconOnly && <Kbd keys={kbd} className="ml-1 opacity-80 max-sm:hidden" />}
        </>
      )}
    </button>
  );
});

export type IconButtonProps = Omit<ButtonProps, "iconOnly" | "children" | "aria-label"> & { icon: IconType; label: string };

/** Icon-only button; `label` becomes aria-label and the tooltip (title). */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, variant = "ghost", ...rest },
  ref,
) {
  return <Button ref={ref} variant={variant} iconOnly aria-label={label} title={label} {...rest} />;
});
