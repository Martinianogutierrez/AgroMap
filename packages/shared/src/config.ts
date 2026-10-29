import { z } from "zod";

const md = z.string().regex(/^\d{2}-\d{2}$/, "expected MM-DD");

export const WeatherWindowSchema = z
  .object({
    mode: z.enum(["crop_calendar", "fixed_md"]).default("crop_calendar"),
    start_md: md.optional(),
    end_md: md.optional(),
  })
  .strict();

export const WeatherRuleSchema = z
  .object({
    op: z.enum([
      "above_mean",
      "below_mean",
      "between",
      "top_n",
      "bottom_n",
    ]),
    tolerance_pct: z.number().min(0).max(100).default(0),
    lo: z.number().optional(),
    hi: z.number().optional(),
    n: z.number().int().positive().optional(),
  })
  .strict();

export const WeatherFilterSchema = z
  .object({
    enabled: z.boolean().default(false),
    metric: z
      .enum([
        "precip_sum_mm",
        "gdd_base10",
        "tmax_mean",
        "heat_days_gt35",
        "dry_spell_max",
      ])
      .default("precip_sum_mm"),
    window: WeatherWindowSchema.default({ mode: "crop_calendar" }),
    rule: WeatherRuleSchema.default({
      op: "above_mean",
      tolerance_pct: 0,
    }),
    min_seasons: z.number().int().min(1).max(50).default(3),
    reference: z.enum(["available", "climatology_20y"]).default("available"),
  })
  .strict();

export const SeasonsSchema = z
  .object({
    include: z.union([z.literal("all"), z.array(z.number().int())]).default("all"),
    crop: z.string().min(1).optional(),
    weather_filter: WeatherFilterSchema.optional(),
  })
  .strict();

export const ZonesSchema = z
  .object({
    k: z.union([z.literal("auto"), z.number().int().min(2).max(12)]).default("auto"),
    k_min: z.number().int().min(2).max(12).default(3),
    k_max: z.number().int().min(2).max(12).default(5),
    min_area_ha: z.number().positive().default(0.3),
    smooth_px: z.number().int().min(0).max(21).default(3),
  })
  .strict()
  .superRefine((v, ctx) => {
    if (v.k_min > v.k_max) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "k_min must be <= k_max",
        path: ["k_min"],
      });
    }
  });

export const LayersSchema = z
  .object({
    use_yield: z.boolean().default(true),
    use_ndvi_satellite: z.boolean().default(true),
    use_ndvi_drone: z.boolean().default(false),
  })
  .strict();

export const YieldSchema = z
  .object({
    column: z.string().min(1).default("VRYIELDVOL"),
    unit: z.enum(["kg/ha", "t/ha", "bu/ac"]).default("kg/ha"),
    outlier_sigma: z.number().positive().default(3),
    remove_zeros: z.boolean().default(true),
  })
  .strict();

export const ExportSchema = z
  .object({
    shapefile: z.boolean().default(true),
    gpkg: z.boolean().default(true),
    qgz: z.boolean().default(false),
    preview_png: z.boolean().default(true),
  })
  .strict();

export const AnalysisConfigSchema = z
  .object({
    field_id: z.string().uuid(),
    zones: ZonesSchema.default({}),
    layers: LayersSchema.default({}),
    seasons: SeasonsSchema.default({}),
    yield: YieldSchema.default({}),
    resolution_m: z.number().min(1).max(100).default(10),
    export: ExportSchema.default({}),
    random_seed: z.number().int().default(42),
  })
  .strict();

export type AnalysisConfig = z.infer<typeof AnalysisConfigSchema>;

export function parseAnalysisConfig(input: unknown): AnalysisConfig {
  return AnalysisConfigSchema.parse(input);
}
