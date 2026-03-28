"use client"

import { useRouter } from "next/navigation"
import { ArrowLeft } from "@phosphor-icons/react"
import { cn } from "@/lib/utils"
import { ProgressDots } from "./ProgressDots"

interface StepShellProps {
  step: number
  totalSteps?: number
  backHref?: string
  showBack?: boolean
  className?: string
  children: React.ReactNode
}

export function StepShell({
  step,
  totalSteps = 7,
  backHref,
  showBack = true,
  className,
  children,
}: StepShellProps) {
  const router = useRouter()

  return (
    <div className="flex flex-col min-h-svh bg-background">
      {/* Top bar */}
      <div className="flex items-center justify-between px-4 pt-4 pb-2">
        {showBack ? (
          <button
            onClick={() => (backHref ? router.push(backHref) : router.back())}
            className="flex items-center justify-center w-[60px] h-[60px] rounded-2xl hover:bg-muted transition-colors"
            aria-label="Go back"
          >
            <ArrowLeft size={28} weight="bold" className="text-foreground" />
          </button>
        ) : (
          <div className="w-[60px]" />
        )}

        <ProgressDots current={step} total={totalSteps} />

        {/* Right spacer for balance */}
        <div className="w-[60px]" />
      </div>

      {/* Page content */}
      <div className={cn("flex flex-col flex-1 px-5 pb-8", className)}>
        {children}
      </div>
    </div>
  )
}
