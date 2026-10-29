import { describe, expect, it } from "vitest";
import { AnalysisConfigSchema, parseAnalysisConfig } from "../src/config";

const base = {
  field_id: "11111111-1111-1111-1111-111111111111",
};

describe("AnalysisConfigSchema", () => {
  it("accepts minimal config with defaults", () => {
    const cfg = parseAnalysisConfig(base);
    expect(cfg.zones.k).toBe("auto");
    expect(cfg.zones.k_min).toBe(3);
    expect(cfg.resolution_m).toBe(10);
    expect(cfg.layers.use_yield).toBe(true);
  });

  it("rejects unknown keys", () => {
    expect(() =>
      parseAnalysisConfig({ ...base, hack: true }),
    ).toThrow();
  });

  it("rejects k_min > k_max", () => {
    expect(() =>
      parseAnalysisConfig({
        ...base,
        zones: { k: "auto", k_min: 6, k_max: 3, min_area_ha: 0.3, smooth_px: 3 },
      }),
    ).toThrow(/k_min/);
  });

  it("accepts full weather filter", () => {
    const cfg = AnalysisConfigSchema.parse({
      ...base,
      seasons: {
        include: "all",
        crop: "soybean",
        weather_filter: {
          enabled: true,
          metric: "precip_sum_mm",
          window: { mode: "crop_calendar", start_md: "10-01", end_md: "03-31" },
          rule: { op: "above_mean", tolerance_pct: 0 },
          min_seasons: 3,
          reference: "available",
        },
      },
    });
    expect(cfg.seasons.weather_filter?.enabled).toBe(true);
  });
});
