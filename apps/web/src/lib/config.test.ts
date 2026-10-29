import { describe, expect, it } from "vitest";
import { parseAnalysisConfig, summarizeConfig } from "./config";

const base = { field_id: "11111111-1111-1111-1111-111111111111" };

describe("web AnalysisConfigSchema", () => {
  it("parses defaults", () => {
    const cfg = parseAnalysisConfig(base);
    expect(cfg.zones.k_min).toBe(3);
    expect(summarizeConfig(cfg)).toMatch(/zones/);
  });

  it("rejects extra keys", () => {
    expect(() => parseAnalysisConfig({ ...base, x: 1 })).toThrow();
  });
});
