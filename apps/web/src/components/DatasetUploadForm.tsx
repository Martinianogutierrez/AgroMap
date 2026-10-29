"use client";

import { FormEvent, useState } from "react";
import { createClient } from "@/lib/supabase/client";

const KIND_EXT: Record<string, string[]> = {
  yield: [".csv", ".shp", ".zip", ".gpkg", ".geojson"],
  orthomosaic: [".tif", ".tiff"],
  boundary: [".geojson", ".json", ".gpkg", ".zip", ".shp"],
  satellite: [".tif", ".tiff"],
};

function extOf(name: string) {
  const n = name.toLowerCase();
  if (n.endsWith(".shp.zip")) return ".shp.zip";
  const i = n.lastIndexOf(".");
  return i >= 0 ? n.slice(i) : "";
}

export function DatasetUploadForm({
  fieldId,
  ownerId,
}: {
  fieldId: string;
  ownerId: string;
}) {
  const [progress, setProgress] = useState(0);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    setMsg(null);
    const fd = new FormData(e.currentTarget);
    const file = fd.get("file") as File | null;
    const kind = String(fd.get("kind") || "yield");
    if (!file) {
      setErr("Pick a file");
      setBusy(false);
      return;
    }
    const ext = extOf(file.name);
    const allowed = KIND_EXT[kind] || [];
    if (!allowed.some((a) => ext === a || file.name.toLowerCase().endsWith(a))) {
      setErr(`Bad extension for ${kind}: ${ext}`);
      setBusy(false);
      return;
    }
    const maxBytes = kind === "orthomosaic" ? 100_000_000 : 50_000_000;
    if (file.size > maxBytes) {
      setErr("File too large");
      setBusy(false);
      return;
    }

    const supabase = createClient();
    const path = `${ownerId}/${fieldId}/${crypto.randomUUID()}_${file.name}`;
    setProgress(10);
    const { error: upErr } = await supabase.storage.from("uploads").upload(path, file, {
      upsert: false,
    });
    if (upErr) {
      setErr(upErr.message);
      setBusy(false);
      return;
    }
    setProgress(70);
    const season_year = fd.get("season_year") ? Number(fd.get("season_year")) : null;
    const crop = String(fd.get("crop") || "") || null;
    const yield_column = String(fd.get("yield_column") || "") || null;
    const { data: ds, error: rowErr } = await supabase
      .from("datasets")
      .insert({
        field_id: fieldId,
        kind,
        season_year,
        crop,
        storage_path: path,
        original_name: file.name,
        size_bytes: file.size,
        meta: yield_column ? { yield_column } : {},
      })
      .select("id")
      .single();
    if (rowErr || !ds) {
      setErr(rowErr?.message ?? "dataset insert failed");
      setBusy(false);
      return;
    }
    setProgress(85);
    // queue worker inspect so suggested_mapping lands in meta
    const {
      data: { user },
    } = await supabase.auth.getUser();
    if (user) {
      await supabase.from("jobs").insert({
        field_id: fieldId,
        owner_id: user.id,
        kind: "ingest_dataset",
        config: { dataset_id: ds.id, field_id: fieldId },
        status: "queued",
      });
    }
    setProgress(100);
    setMsg(
      `Uploaded ${file.name}. Ingest job queued — refresh Data tab after worker runs to confirm column mapping.`,
    );
    setBusy(false);
    e.currentTarget.reset();
  }

  return (
    <form onSubmit={onSubmit} className="space-y-3 rounded-xl border bg-white p-4">
      <h2 className="font-medium">Upload dataset</h2>
      <div className="grid gap-2 text-sm sm:grid-cols-2">
        <label>
          Kind
          <select name="kind" defaultValue="yield" className="mt-1 w-full rounded border px-2 py-1">
            <option value="yield">yield</option>
            <option value="boundary">boundary</option>
            <option value="orthomosaic">orthomosaic</option>
            <option value="satellite">satellite</option>
          </select>
        </label>
        <label>
          Season year
          <input name="season_year" type="number" className="mt-1 w-full rounded border px-2 py-1" />
        </label>
        <label>
          Crop
          <input name="crop" className="mt-1 w-full rounded border px-2 py-1" />
        </label>
        <label>
          Yield column (optional override)
          <input
            name="yield_column"
            defaultValue="VRYIELDVOL"
            className="mt-1 w-full rounded border px-2 py-1"
          />
        </label>
        <label className="sm:col-span-2">
          File
          <input name="file" type="file" required className="mt-1 block w-full text-sm" />
        </label>
      </div>
      {busy && (
        <div className="h-2 overflow-hidden rounded bg-stone-100">
          <div className="h-full bg-emerald-600" style={{ width: `${progress}%` }} />
        </div>
      )}
      <button
        type="submit"
        disabled={busy}
        className="rounded-md bg-stone-800 px-4 py-2 text-sm text-white disabled:opacity-60"
      >
        {busy ? "Uploading…" : "Upload"}
      </button>
      {msg && <p className="text-sm text-emerald-700">{msg}</p>}
      {err && <p className="text-sm text-red-600">{err}</p>}
    </form>
  );
}
