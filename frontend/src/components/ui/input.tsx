import * as React from "react";
import { cn } from "@/lib/utils";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input ref={ref} className={cn("h-8 rounded-lg border border-border bg-card px-2.5 text-[13px] outline-none focus-visible:border-ring", className)} {...props} />
  ),
);
Input.displayName = "Input";
