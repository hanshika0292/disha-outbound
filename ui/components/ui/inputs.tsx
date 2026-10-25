"use client";

import {
  InputHTMLAttributes,
  TextareaHTMLAttributes,
  SelectHTMLAttributes,
  forwardRef,
} from "react";
import { cn } from "@/lib/cn";

const fieldCls =
  "w-full bg-paper border border-border-strong rounded-sm px-3 py-1.5 text-sm text-ink placeholder:text-muted/70 focus:border-terracotta focus-visible:outline-none";

export const Input = forwardRef<
  HTMLInputElement,
  InputHTMLAttributes<HTMLInputElement>
>(function Input({ className, ...props }, ref) {
  return <input ref={ref} className={cn(fieldCls, "h-9", className)} {...props} />;
});

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  TextareaHTMLAttributes<HTMLTextAreaElement>
>(function Textarea({ className, ...props }, ref) {
  return (
    <textarea
      ref={ref}
      className={cn(fieldCls, "min-h-[6rem] py-2 leading-relaxed resize-y", className)}
      {...props}
    />
  );
});

export const Select = forwardRef<
  HTMLSelectElement,
  SelectHTMLAttributes<HTMLSelectElement>
>(function Select({ className, children, ...props }, ref) {
  return (
    <select
      ref={ref}
      className={cn(fieldCls, "h-9 pr-8 appearance-none", className)}
      {...props}
    >
      {children}
    </select>
  );
});

export function Label({
  children,
  htmlFor,
  className,
}: {
  children: React.ReactNode;
  htmlFor?: string;
  className?: string;
}) {
  return (
    <label
      htmlFor={htmlFor}
      className={cn(
        "block text-[11px] uppercase tracking-wider font-medium text-muted mb-1",
        className,
      )}
    >
      {children}
    </label>
  );
}
