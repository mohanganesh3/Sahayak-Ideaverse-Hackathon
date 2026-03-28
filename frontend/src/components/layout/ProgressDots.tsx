"use client"

import { cn } from "@/lib/utils"

interface ProgressDotsProps {
  current: number
  total: number
}

export function ProgressDots({ current, total }: ProgressDotsProps) {
  return (
    <div className="flex items-center gap-2" role="progressbar" aria-valuenow={current} aria-valuemax={total}>
      {Array.from({ length: total }).map((_, i) => {
        const filled = i + 1 <= current
        const active = i + 1 === current
        return (
          <div
            key={i}
            className={cn(
              "rounded-full transition-all duration-300",
              active
                ? "w-6 h-3 bg-primary"
                : filled
                ? "w-3 h-3 bg-primary/40"
                : "w-3 h-3 bg-border"
            )}
          />
        )
      })}
    </div>
  )
}
