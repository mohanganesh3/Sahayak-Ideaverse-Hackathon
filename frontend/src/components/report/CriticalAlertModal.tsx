"use client"

import { useState, useEffect } from "react"
import { motion, AnimatePresence } from "motion/react"
import { Phone, WhatsappLogo, ClipboardText } from "@phosphor-icons/react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import type { Interaction } from "@/types/sahayak"

interface CriticalAlertModalProps {
  criticalFindings: Interaction[]
}

export function CriticalAlertModal({ criticalFindings }: CriticalAlertModalProps) {
  const [open, setOpen] = useState(false)
  const [canDismiss, setCanDismiss] = useState(false)

  useEffect(() => {
    if (criticalFindings.length > 0) {
      setOpen(true)
      // Force user to read for 5 seconds before allowing dismiss
      const timer = setTimeout(() => setCanDismiss(true), 5000)
      return () => clearTimeout(timer)
    }
  }, [criticalFindings.length])

  if (criticalFindings.length === 0) return null

  return (
    <Dialog open={open} onOpenChange={(v) => canDismiss && setOpen(v)}>
      <DialogContent
        className="rounded-3xl max-w-sm mx-auto"
        role="alertdialog"
        aria-describedby="critical-alert-body"
        showCloseButton={false}
      >
        {/* Red header */}
        <div className="-mx-6 -mt-6 px-6 py-5 bg-red-600 rounded-t-3xl">
          <DialogHeader>
            <DialogTitle className="text-white text-xl font-bold flex items-center gap-2">
              ⚠️ Zaroori Jaankari
            </DialogTitle>
            <DialogDescription className="text-red-100 text-base mt-1">
              Important information about your medicines
            </DialogDescription>
          </DialogHeader>
        </div>

        <div id="critical-alert-body" className="py-2">
          <p className="text-foreground text-base leading-relaxed mb-4 font-indic">
            Aapki dawaiyon mein kuch{" "}
            <span className="font-bold text-red-700">zaroori baat</span> saamne aayi hai.
            Doctor se milne se pehle yeh dawaiyan ek saath mat lein.
          </p>

          {/* List critical findings */}
          <div className="flex flex-col gap-2 mb-5">
            {criticalFindings.map((f, i) => (
              <div key={i} className="flex items-start gap-2 p-3 rounded-xl bg-red-50 border border-red-200">
                <span className="text-red-600 font-bold text-sm flex-none">⛔</span>
                <p className="text-red-800 text-sm font-medium">{f.title}</p>
              </div>
            ))}
          </div>

          {/* Action buttons */}
          <div className="flex flex-col gap-3">
            <Button
              className="w-full min-h-[56px] rounded-2xl bg-green-600 hover:bg-green-700 text-white text-base"
              onClick={() => window.open("tel:")}
            >
              <Phone size={20} className="mr-2" />
              Doctor ko call karein
            </Button>

            <Button
              variant="outline"
              className="w-full min-h-[56px] rounded-2xl text-base border-green-600 text-green-700 hover:bg-green-50"
              onClick={() => {
                const text = `SAHAYAK Safety Alert:\n${criticalFindings.map((f) => `⛔ ${f.title}`).join("\n")}\n\nKripya doctor se mile.`
                window.open(`https://wa.me/?text=${encodeURIComponent(text)}`)
              }}
            >
              <WhatsappLogo size={20} className="mr-2" />
              Parivaar ko batayein
            </Button>
          </div>
        </div>

        {/* Dismiss after delay */}
        <AnimatePresence>
          {canDismiss && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="pt-2"
            >
              <Button
                variant="ghost"
                className="w-full min-h-[48px] text-muted-foreground"
                onClick={() => setOpen(false)}
              >
                <ClipboardText size={18} className="mr-2" />
                Samajh gaya – Doctor se baat karoonga
              </Button>
            </motion.div>
          )}
        </AnimatePresence>

        {!canDismiss && (
          <p className="text-center text-muted-foreground text-sm mt-1">
            Please read the above carefully...
          </p>
        )}
      </DialogContent>
    </Dialog>
  )
}
