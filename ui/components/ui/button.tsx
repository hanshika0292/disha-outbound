"use client";

import { ButtonHTMLAttributes, forwardRef } from "react";
import { cn } from "@/lib/cn";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

const variantClasses: Record<Variant, string> = {
  primary:
    "bg-terracotta text-white border-transparent hover:bg-terracotta/90",
  secondary:
    "bg-paper text-ink border-border-strong hover:bg-cream",
  ghost: "bg-transparent text-muted border-transparent hover:bg-cream hover:text-ink",
  danger:
    "bg-paper text-ink border-border-strong hover:bg-red-50 hover:border-red-300 hover:text-red-700",
};

const sizeClasses: Record<Size, string> = {
  sm: "h-7 px-2.5 text-xs",
  md: "h-8 px-3 text-[13px]",
  lg: "h-9 px-4 text-sm",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    { className, variant = "secondary", size = "md", ...props },
    ref,
  ) {
    return (
      <button
        ref={ref}
        className={cn(
          "inline-flex items-center justify-center gap-1.5 rounded-md border font-medium",
          "disabled:opacity-50 disabled:cursor-not-allowed",
          variantClasses[variant],
          sizeClasses[size],
          className,
        )}
        {...props}
      />
    );
  },
);
