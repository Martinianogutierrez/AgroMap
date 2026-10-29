"use client";

import { FormEvent, useState } from "react";
import { createClient } from "@/lib/supabase/client";

export function BoundaryForm({
  fieldId,
  hasBoundary,
}: {
  fieldId: string;
  hasBoundary: boolean;
}) {
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setErr(null);
    setOk(null);
    const text = String(new FormData(e.currentTarget).get("geojson") || "");
    let gj: unknown;
    try {
      gj = JSON.parse(text);
    } catch {
      setErr("Invalid JSON");
      return;
    }
    const supabase = createClient();
    // store as GeoJSON geometry; PostGIS accepts GeoJSON via RPC in full build.
    // MVP: keep JSON in a datasets row + try update if column accepts.
    const { error } = await supabase
      .from("fields")
      .update({ boundary: gj as never })
      .eq("id", fieldId);
    if (error) {
      // fallback: save as boundary dataset meta for worker ingest_boundary
      const {
        data: { user },
      } = await supabase.auth.getUser();
      if (!user) {
        setErr(error.message);
        return;
      }
      const path = `${user.id}/${fieldId}/boundary_${Date.now()}.geojson`;
      const blob = new Blob([text], { type: "application/geo+json" });
      await supabase.storage.from("uploads").upload(path, blob, { upsert: true });
      await supabase.from("datasets").insert({
        field_id: fieldId,
        kind: "boundary",
        storage_path: path,
        original_name: "boundary.geojson",
        size_bytes: blob.size,
        meta: { geojson: gj },
      });
      await supabase.from("jobs").insert({
        field_id: fieldId,
        owner_id: user.id,
        kind: "ingest_boundary",
        config: { field_id: fieldId, storage_path: path },
        status: "queued",
      });
      setOk("Boundary queued for worker ingest (geometry column update failed in browser).");
      return;
    }
    setOk("Boundary saved.");
  }

  return (
    <form onSubmit={onSubmit} className="space-y-2 rounded-xl border bg-white p-4">
      <h2 className="font-medium">Field boundary (GeoJSON)</h2>
      <p className="text-xs text-stone-500">
        {hasBoundary ? "Boundary present." : "No boundary yet."} Paste a Polygon/MultiPolygon Feature or geometry.
      </p>
      <textarea
        name="geojson"
        required
        rows={6}
        className="w-full rounded border border-stone-300 p-2 font-mono text-xs"
        placeholder='{"type":"Polygon","coordinates":[...]}'
      />
      <button type="submit" className="rounded-md bg-stone-800 px-3 py-1.5 text-sm text-white">
        Save boundary
      </button>
      {ok && <p className="text-sm text-emerald-700">{ok}</p>}
      {err && <p className="text-sm text-red-600">{err}</p>}
    </form>
  );
}
