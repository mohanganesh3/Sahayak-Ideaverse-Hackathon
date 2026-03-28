import type { ExtractedDrug, PrescriberSource } from "../types/sahayak"

export function buildSourceImageKey(
  type: "allopathic" | "ayurvedic",
  imageIndex: number,
  imageUri?: string,
): string {
  return imageUri ? `${type}:${imageUri}` : `${type}:${imageIndex}`
}

function slugPart(value: string): string {
  const slug = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40)
  return slug || "medicine"
}

export function getMedicineDisplayName(drug: ExtractedDrug): string {
  return (drug.brand_name || drug.generic_name || "Unknown medicine").trim()
}

function buildMedicineId(
  drug: ExtractedDrug,
  entryOrigin: ExtractedDrug["entry_origin"],
  sourceImageKey: string,
  ordinal: number,
): string {
  const preferred = getMedicineDisplayName(drug)
  return `${sourceImageKey}:${entryOrigin}:${slugPart(preferred)}:${ordinal}`
}

export function withMedicineIdentity(
  drug: ExtractedDrug,
  options: {
    entryOrigin: ExtractedDrug["entry_origin"]
    type: "allopathic" | "ayurvedic"
    imageIndex: number
    imageUri?: string
    ordinal?: number
  },
): ExtractedDrug {
  const sourceImageKey = buildSourceImageKey(options.type, options.imageIndex, options.imageUri)
  return {
    ...drug,
    medicine_id:
      drug.medicine_id ??
      buildMedicineId(drug, options.entryOrigin, sourceImageKey, options.ordinal ?? 0),
    entry_origin: drug.entry_origin ?? options.entryOrigin,
    source_image_key: drug.source_image_key ?? sourceImageKey,
    image_uri: options.imageUri ?? drug.image_uri,
    medicine_type: options.type ?? drug.medicine_type,
  }
}

export function withMedicineIdentityList(
  drugs: ExtractedDrug[],
  options: {
    entryOrigin: ExtractedDrug["entry_origin"]
    type: "allopathic" | "ayurvedic"
    imageIndex: number
    imageUri?: string
  },
): ExtractedDrug[] {
  return drugs.map((drug, index) =>
    withMedicineIdentity(drug, { ...options, ordinal: index }),
  )
}

export function getMedicinePrescriberSource(
  drug: ExtractedDrug,
  prescriberMap: Record<string, PrescriberSource>,
): PrescriberSource | undefined {
  if (drug.medicine_id && prescriberMap[drug.medicine_id]) {
    return prescriberMap[drug.medicine_id]
  }
  return undefined
}

function mergePrescriberSource(
  current: PrescriberSource | undefined,
  incoming: PrescriberSource,
): PrescriberSource {
  const rank: Record<PrescriberSource, number> = {
    doctor: 1,
    medical_shop: 2,
    self: 3,
  }
  if (!current) return incoming
  return rank[incoming] >= rank[current] ? incoming : current
}

export function buildPrescriberInfoByName(
  medicines: ExtractedDrug[],
  prescriberMap: Record<string, PrescriberSource>,
): Record<string, PrescriberSource> {
  const aggregated = new Map<string, PrescriberSource>()
  for (const medicine of medicines) {
    const source = getMedicinePrescriberSource(medicine, prescriberMap)
    if (!source) continue
    const label = (medicine.generic_name || medicine.brand_name || getMedicineDisplayName(medicine)).trim()
    aggregated.set(label, mergePrescriberSource(aggregated.get(label), source))
  }
  return Object.fromEntries(aggregated)
}

export function getPrescriberSummaryRows(
  medicines: ExtractedDrug[],
  prescriberMap: Record<string, PrescriberSource>,
): Array<{ key: string; name: string; source: PrescriberSource; manual: boolean }> {
  return medicines.flatMap((medicine) => {
    const source = getMedicinePrescriberSource(medicine, prescriberMap)
    if (!source) return []
    return [
      {
        key: medicine.medicine_id ?? getMedicineDisplayName(medicine),
        name: getMedicineDisplayName(medicine),
        source,
        manual: medicine.entry_origin === "manual",
      },
    ]
  })
}
