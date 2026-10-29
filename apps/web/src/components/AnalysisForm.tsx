"use client";

import { FormEvent, useState } from "react";
import { parseAnalysisConfig, summarizeConfig } from "@/lib/config";
import { createClient } from "@/lib/supabase/client";

export function AnalysisForm({
  fieldId,
  cropDefault,
}: {
  fieldId: string;
  cropDefault: string | null;
}) {
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setOk(null);
    const fd = new FormData(e.currentTarget);
    const kMode = String(fd.get("k_mode"));
    const weatherOn = fd.get("weather_enabled") === "on";
    const raw = {
      field_id: fieldId,
      zones: {
        k: kMode === "auto" ? "auto" : Number(fd.get("k_fixed")),
        k_min: Number(fd.get("k_min")),
        k_max: Number(fd.get("k_max")),
        min_area_ha: Number(fd.get("min_area_ha")),
        smooth_px: Number(fd.get("smooth_px")),
      },
      layers: {
        use_yield: fd.get("use_yield") === "on",
        use_ndvi_satellite: fd.get("use_ndvi_satellite") === "on",
        use_ndvi_drone: false,
      },
      seasons: {
        include: "all" as const,
        crop: String(fd.get("crop") || "") || undefined,
        weather_filter: {
          enabled: weatherOn,
          metric: String(fd.get("metric") || "precip_sum_mm"),
          window: { mode: "crop_calendar" as const },
          rule: {
            op: String(fd.get("rule_op") || "above_mean"),
            tolerance_pct: Number(fd.get("tolerance_pct") || 0),
          },
          min_seasons: Number(fd.get("min_seasons") || 3),
          reference: "available" as const,
        },
      },
      yield: {
        column: String(fd.get("yield_column") || "VRYIELDVOL"),
        unit: String(fd.get("yield_unit") || "kg/ha"),
        outlier_sigma: Number(fd.get("outlier_sigma") || 3),
        remove_zeros: true,
      },
      resolution_m: Number(fd.get("resolution_m") || 10),
      export: {
        shapefile: true,
        gpkg: true,
        qgz: false,
        preview_png: true,
      },
    };

    try {
      const cfg = parseAnalysisConfig(raw);
      const supabase = createClient();
      const {
        data: { user },
      } = await supabase.auth.getUser();
      if (!user) throw new Error("Not signed in");
      const { data, error: insErr } = await supabase
        .from("jobs")
        .insert({
          field_id: fieldId,
          owner_id: user.id,
          kind: "analysis",
          config: cfg,
          status: "queued",
        })
        .select("id")
        .single();
      if (insErr) throw new Error(insErr.message);
      setOk(`Queued ${data.id.slice(0, 8)} — ${summarizeConfig(cfg)}`);
      e.currentTarget.reset();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invalid config");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-4 rounded-xl border bg-white p-4">
      <h2 className="font-medium">New analysis</h2>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm">
          Crop
          <input
            name="crop"
            defaultValue={cropDefault ?? "soybean"}
            className="mt-1 w-full rounded border px-2 py-1"
          />
        </label>
        <label className="text-sm">
          Resolution (m)
          <input
            name="resolution_m"
            type="number"
            defaultValue={10}
            min={1}
            max={100}
            className="mt-1 w-full rounded border px-2 py-1"
          />
        </label>
        <label className="text-sm">
          Zone k
          <select name="k_mode" defaultValue="auto" className="mt-1 w-full rounded border px-2 py-1">
            <option value="auto">auto</option>
            <option value="fixed">fixed</option>
          </select>
        </label>
        <label className="text-sm">
          Fixed k
          <input name="k_fixed" type="number" defaultValue={3} min={2} max={12} className="mt-1 w-full rounded border px-2 py-1" />
        </label>
        <label className="text-sm">
          k_min
          <input name="k_min" type="number" defaultValue={3} className="mt-1 w-full rounded border px-2 py-1" />
        </label>
        <label className="text-sm">
          k_max
          <input name="k_max" type="number" defaultValue={5} className="mt-1 w-full rounded border px-2 py-1" />
        </label>
        <label className="text-sm">
          min area (ha)
          <input name="min_area_ha" type="number" step="0.01" defaultValue={0.3} className="mt-1 w-full rounded border px-2 py-1" />
        </label>
        <label className="text-sm">
          smooth px
          <input name="smooth_px" type="number" defaultValue={3} className="mt-1 w-full rounded border px-2 py-1" />
        </label>
        <label className="text-sm">
          Yield column
          <input name="yield_column" defaultValue="VRYIELDVOL" className="mt-1 w-full rounded border px-2 py-1" />
        </label>
        <label className="text-sm">
          Unit
          <select name="yield_unit" defaultValue="kg/ha" className="mt-1 w-full rounded border px-2 py-1">
            <option value="kg/ha">kg/ha</option>
            <option value="t/ha">t/ha</option>
            <option value="bu/ac">bu/ac</option>
          </select>
        </label>
        <label className="text-sm">
          Outlier σ
          <input name="outlier_sigma" type="number" defaultValue={3} className="mt-1 w-full rounded border px-2 py-1" />
        </label>
      </div>

      <fieldset className="space-y-2 rounded border p-3 text-sm">
        <legend className="px-1 font-medium">Layers</legend>
        <label className="flex items-center gap-2">
          <input name="use_yield" type="checkbox" defaultChecked /> Yield
        </label>
        <label className="flex items-center gap-2">
          <input name="use_ndvi_satellite" type="checkbox" defaultChecked /> Sentinel-2 NDVI
        </label>
      </fieldset>

      <fieldset className="space-y-2 rounded border p-3 text-sm">
        <legend className="px-1 font-medium">Weather filter</legend>
        <label className="flex items-center gap-2">
          <input name="weather_enabled" type="checkbox" /> Enable
        </label>
        <label>
          Metric
          <select name="metric" defaultValue="precip_sum_mm" className="mt-1 w-full rounded border px-2 py-1">
            <option value="precip_sum_mm">precip_sum_mm</option>
            <option value="gdd_base10">gdd_base10</option>
            <option value="tmax_mean">tmax_mean</option>
            <option value="heat_days_gt35">heat_days_gt35</option>
            <option value="dry_spell_max">dry_spell_max</option>
          </select>
        </label>
        <label>
          Rule
          <select name="rule_op" defaultValue="above_mean" className="mt-1 w-full rounded border px-2 py-1">
            <option value="above_mean">above_mean</option>
            <option value="below_mean">below_mean</option>
            <option value="top_n">top_n</option>
            <option value="bottom_n">bottom_n</option>
          </select>
        </label>
        <label>
          tolerance %
          <input name="tolerance_pct" type="number" defaultValue={0} className="mt-1 w-full rounded border px-2 py-1" />
        </label>
        <label>
          min seasons
          <input name="min_seasons" type="number" defaultValue={3} className="mt-1 w-full rounded border px-2 py-1" />
        </label>
      </fieldset>

      <button
        type="submit"
        disabled={busy}
        className="rounded-md bg-emerald-700 px-4 py-2 text-white hover:bg-emerald-800 disabled:opacity-60"
      >
        {busy ? "Queueing…" : "Queue analysis"}
      </button>
      {ok && <p className="text-sm text-emerald-700">{ok}</p>}
      {error && <p className="text-sm text-red-600">{error}</p>}
    </form>
  );
}
