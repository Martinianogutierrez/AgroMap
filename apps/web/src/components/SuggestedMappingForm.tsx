"use client";

import { FormEvent, useState } from "react";
import { createClient } from "@/lib/supabase/client";

type Mapping = {
  yield?: string | null;
  lat?: string | null;
  lon?: string | null;
};

export function SuggestedMappingForm({
  datasetId,
  columns,
  suggested,
  confirmed,
}: {
  datasetId: string;
  columns: string[];
  suggested: Mapping;
  confirmed?: Mapping | null;
}) {
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setMsg(null);
    setErr(null);
    const fd = new FormData(e.currentTarget);
    const mapping = {
      yield: String(fd.get("yield") || "") || null,
      lat: String(fd.get("lat") || "") || null,
      lon: String(fd.get("lon") || "") || null,
    };
    const supabase = createClient();
    const { data: row } = await supabase
      .from("datasets")
      .select("meta")
      .eq("id", datasetId)
      .single();
    const meta = { ...(row?.meta as object | null), confirmed_mapping: mapping, yield_column: mapping.yield };
    const { error } = await supabase
      .from("datasets")
      .update({ meta })
      .eq("id", datasetId);
    if (error) setErr(error.message);
    else setMsg("Mapping saved. Analysis will use these columns.");
  }

  const opts = ["", ...columns];
  const cur = confirmed ?? suggested;

  return (
    <form onSubmit={onSubmit} className="mt-2 space-y-2 rounded border border-amber-200 bg-amber-50 p-2 text-xs">
      <p className="font-medium text-amber-900">Confirm column mapping</p>
      <div className="grid gap-2 sm:grid-cols-3">
        {(["yield", "lat", "lon"] as const).map((k) => (
          <label key={k}>
            {k}
            <select
              name={k}
              defaultValue={cur[k] ?? ""}
              className="mt-0.5 w-full rounded border px-1 py-0.5"
            >
              {opts.map((c) => (
                <option key={c || "_"} value={c}>
                  {c || "—"}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
      <button type="submit" className="rounded bg-amber-800 px-2 py-1 text-white">
        Confirm
      </button>
      {msg && <p className="text-emerald-700">{msg}</p>}
      {err && <p className="text-red-600">{err}</p>}
    </form>
  );
}
