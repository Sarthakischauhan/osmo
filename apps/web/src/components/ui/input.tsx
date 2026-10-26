import * as React from "react"
import { cn } from "@/lib/utils"

function Input({ className, type, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      type={type}
      data-slot="input"
      className={cn(
        "h-10 w-full min-w-0 rounded-xl border-0 bg-white/[0.06] px-4 py-1 text-base text-white transition-[background-color,box-shadow] outline-none selection:bg-white/30 selection:text-primary-foreground file:inline-flex file:h-7 file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground placeholder:text-muted-foreground disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50 md:text-sm",
        "focus-visible:bg-white/10 focus-visible:shadow-[0_0_18px_rgba(255,255,255,0.08)]",
        "aria-invalid:bg-white/[0.09]",
        className
      )}
      {...props}
    />
  )
}

export { Input }
