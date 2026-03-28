"use client"

import { useState } from "react"
import { motion } from "motion/react"
import { Trash, PencilSimple, CheckCircle, Warning } from "@phosphor-icons/react"
import { Progress } from "@/components/ui/progress"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import type { ExtractedDrug } from "@/types/sahayak"

interface MedicineCardProps {
  drug: ExtractedDrug
  index: number
  onRemove: () => void
  onUpdate: (updated: ExtractedDrug) => void
}

export function MedicineCard({ drug, index, onRemove, onUpdate }: MedicineCardProps) {
  const [editing, setEditing] = useState(false)
  const [editName, setEditName] = useState(drug.generic_name)

  const confidencePct = Math.round((drug.confidence ?? 0) * 100)
  const isLowConfidence = drug.ocr_needs_fallback || drug.confidence < 0.7

  function handleSave() {
    onUpdate({ ...drug, generic_name: editName })
    setEditing(false)
  }

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.95 }}
      transition={{ delay: index * 0.06, type: "spring", stiffness: 260, damping: 25 }}
      className={cn(
        "rounded-2xl border-2 p-4 bg-card",
        isLowConfidence ? "border-amber-400/60 bg-amber-50/40" : "border-border"
      )}
    >
      {isLowConfidence && (
        <div className="flex items-center gap-2 mb-3 text-amber-700">
          <Warning size={18} weight="fill" />
          <span className="text-sm font-medium font-indic">Kripya naam check karein</span>
        </div>
      )}

      {/* Brand + generic name */}
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="flex-1 min-w-0">
          {drug.brand_name && (
            <p className="font-bold text-xl text-foreground truncate">{drug.brand_name}</p>
          )}
          {editing ? (
            <div className="flex gap-2 mt-1">
              <Input
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                className="flex-1 min-h-[48px] text-base"
                onKeyDown={(e) => e.key === "Enter" && handleSave()}
                autoFocus
              />
              <button
                onClick={handleSave}
                className="flex items-center justify-center w-12 h-12 rounded-xl bg-primary text-primary-foreground"
              >
                <CheckCircle size={22} weight="fill" />
              </button>
            </div>
          ) : (
            <p className="text-muted-foreground text-base mt-0.5">{drug.generic_name}</p>
          )}
        </div>

        {/* Action buttons */}
        <div className="flex gap-2 flex-none">
          {!editing && (
            <button
              onClick={() => setEditing(true)}
              className="w-[44px] h-[44px] rounded-xl hover:bg-muted flex items-center justify-center transition-colors"
              aria-label="Edit medicine name"
            >
              <PencilSimple size={20} className="text-muted-foreground" />
            </button>
          )}
          <button
            onClick={onRemove}
            className="w-[44px] h-[44px] rounded-xl hover:bg-red-50 flex items-center justify-center transition-colors"
            aria-label="Remove medicine"
          >
            <Trash size={20} className="text-red-500" />
          </button>
        </div>
      </div>

      {/* Badges + confidence */}
      <div className="flex items-center gap-2 flex-wrap">
        {drug.dosage_form && (
          <Badge variant="secondary" className="text-xs rounded-lg">
            {drug.dosage_form}
          </Badge>
        )}
        {drug.match_type === "manual" && (
          <Badge className="text-xs rounded-lg bg-blue-100 text-blue-700 hover:bg-blue-100">
            Manual
          </Badge>
        )}
        {!drug.graph_match && drug.match_type !== "manual" && (
          <Badge variant="outline" className="text-xs rounded-lg text-amber-600 border-amber-400">
            Not in DB
          </Badge>
        )}
      </div>

      {/* Confidence bar */}
      {drug.match_type !== "manual" && (
        <div className="mt-3">
          <div className="flex justify-between text-xs text-muted-foreground mb-1">
            <span>Confidence</span>
            <span>{confidencePct}%</span>
          </div>
          <Progress
            value={confidencePct}
            className="h-2 rounded-full"
          />
        </div>
      )}
    </motion.div>
  )
}
