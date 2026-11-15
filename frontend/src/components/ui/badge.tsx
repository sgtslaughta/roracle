import * as React from "react";
import { cn } from "@/lib/utils";

/** Small pill used for metadata; `kind` maps to the prototype's tinted variants. */
export function Badge({ kind, className, ...props }: React.HTMLAttributes<HTMLSpanElement> & { kind?: "type" | "cite" | "tag" }) {
  return <span className={cn("badge-o", kind === "type" && "k-type", kind === "cite" && "k-cite", kind === "tag" && "tag", className)} {...props} />;
}
