import { createClient } from "@/lib/supabase/server";
import { notFound, redirect } from "next/navigation";
import { AnalysisForm } from "@/components/AnalysisForm";
import { DatasetUploadForm } from "@/components/DatasetUploadForm";
import { JobList } from "@/components/JobList";
import { BoundaryForm } from "@/components/BoundaryForm";
import { ResultsMap } from "@/components/ResultsMap";

export default async function FieldPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ tab?: string }>;
}) {
  const { id } = await params;
  const { tab = "data" } = await searchParams;
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: field } = await supabase
    .from("fields")
    .select("id, name, crop_default, project_id, boundary")
    .eq("id", id)
    .maybeSingle();
  if (!field) notFound();

  const { data: datasets } = await supabase
    .from("datasets")
    .select("id, kind, season_year, crop, original_name, created_at, meta")
    .eq("field_id", id)
    .order("created_at", { ascending: false });

  const { data: jobs } = await supabase
    .from("jobs")
    .select("id, status, step, progress, error, created_at, config, finished_at")
    .eq("field_id", id)
    .order("created_at", { ascending: false });

  const latestDone = (jobs ?? []).find((j) => j.status === "succeeded");
  let zones: Array<{
    zone_id: number | null;
    zone_name: string | null;
    area_ha: number | null;
    yld_mean: number | null;
    geom: unknown;
  }> = [];
  let outputs: Array<{ kind: string; storage_path: string }> = [];
  if (latestDone) {
    const z = await supabase
      .from("zones")
      .select("zone_id, zone_name, area_ha, yld_mean, geom")
      .eq("job_id", latestDone.id);
    zones = z.data ?? [];
    const o = await supabase
      .from("job_outputs")
      .select("kind, storage_path")
      .eq("job_id", latestDone.id);
    outputs = o.data ?? [];
  }

  const tabs = [
    { id: "data", label: "Data" },
    { id: "analyses", label: "Analyses" },
    { id: "results", label: "Results" },
  ] as const;

  return (
    <div className="space-y-6">
      <div>
        <a
          href={`/projects/${field.project_id}`}
          className="text-sm text-stone-500 hover:underline"
        >
          ← Project
        </a>
        <h1 className="mt-2 text-2xl font-semibold">{field.name}</h1>
        <p className="text-sm text-stone-600">{field.crop_default ?? "No default crop"}</p>
      </div>

      <div className="flex gap-2 border-b border-stone-200">
        {tabs.map((t) => (
          <a
            key={t.id}
            href={`/fields/${id}?tab=${t.id}`}
            className={`-mb-px border-b-2 px-3 py-2 text-sm ${
              tab === t.id
                ? "border-emerald-700 font-medium text-emerald-800"
                : "border-transparent text-stone-600 hover:text-stone-900"
            }`}
          >
            {t.label}
          </a>
        ))}
      </div>

      {tab === "data" && (
        <div className="space-y-6">
          <BoundaryForm fieldId={id} hasBoundary={Boolean(field.boundary)} />
          <DatasetUploadForm fieldId={id} ownerId={user.id} />
          <section>
            <h2 className="mb-2 font-medium">Datasets</h2>
            <ul className="divide-y rounded-lg border bg-white text-sm">
              {(datasets ?? []).length === 0 && (
                <li className="px-3 py-4 text-stone-500">None yet.</li>
              )}
              {(datasets ?? []).map((d) => (
                <li key={d.id} className="flex justify-between px-3 py-2">
                  <span>
                    {d.kind} · {d.original_name ?? d.id.slice(0, 8)}
                  </span>
                  <span className="text-stone-500">
                    {d.season_year ?? "—"} {d.crop ?? ""}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        </div>
      )}

      {tab === "analyses" && (
        <div className="space-y-6">
          <AnalysisForm fieldId={id} cropDefault={field.crop_default} />
          <JobList fieldId={id} initialJobs={jobs ?? []} />
        </div>
      )}

      {tab === "results" && (
        <div className="space-y-4">
          {!latestDone && (
            <p className="text-sm text-stone-500">No succeeded jobs yet.</p>
          )}
          {latestDone && (
            <>
              <p className="text-sm text-stone-600">
                Job {latestDone.id.slice(0, 8)} · {outputs.length} outputs
              </p>
              <ul className="text-sm">
                {outputs.map((o) => (
                  <li key={o.kind}>
                    <code>{o.kind}</code>: {o.storage_path}
                  </li>
                ))}
              </ul>
              <ResultsMap
                boundary={field.boundary}
                zones={zones.map((z) => ({
                  id: z.zone_id ?? 0,
                  name: z.zone_name ?? "",
                  areaHa: Number(z.area_ha ?? 0),
                  yldMean: z.yld_mean != null ? Number(z.yld_mean) : null,
                  geom: z.geom,
                }))}
              />
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b">
                    <th className="py-1">Zone</th>
                    <th>Area ha</th>
                    <th>Yield mean</th>
                  </tr>
                </thead>
                <tbody>
                  {zones.map((z) => (
                    <tr key={String(z.zone_id)} className="border-b border-stone-100">
                      <td className="py-1">{z.zone_name}</td>
                      <td>{z.area_ha != null ? Number(z.area_ha).toFixed(2) : "—"}</td>
                      <td>{z.yld_mean != null ? Number(z.yld_mean).toFixed(0) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
      )}
    </div>
  );
}
