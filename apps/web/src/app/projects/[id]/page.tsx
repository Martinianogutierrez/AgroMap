import { createClient } from "@/lib/supabase/server";
import { notFound, redirect } from "next/navigation";
import { createField } from "./actions";

export default async function ProjectPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: project } = await supabase
    .from("projects")
    .select("id, name")
    .eq("id", id)
    .maybeSingle();
  if (!project) notFound();

  const { data: fields } = await supabase
    .from("fields")
    .select("id, name, crop_default, created_at")
    .eq("project_id", id)
    .order("created_at", { ascending: false });

  return (
    <div className="space-y-8">
      <div>
        <a href="/dashboard" className="text-sm text-stone-500 hover:underline">
          ← Projects
        </a>
        <h1 className="mt-2 text-2xl font-semibold">{project.name}</h1>
      </div>

      <form action={createField} className="flex flex-wrap gap-2">
        <input type="hidden" name="project_id" value={id} />
        <input
          name="name"
          required
          placeholder="Field name"
          className="min-w-[12rem] flex-1 rounded-md border border-stone-300 px-3 py-2"
        />
        <input
          name="crop_default"
          placeholder="Crop (optional)"
          className="w-40 rounded-md border border-stone-300 px-3 py-2"
        />
        <button
          type="submit"
          className="rounded-md bg-emerald-700 px-4 py-2 text-white hover:bg-emerald-800"
        >
          Add field
        </button>
      </form>

      <ul className="divide-y divide-stone-200 rounded-xl border border-stone-200 bg-white">
        {(fields ?? []).length === 0 && (
          <li className="px-4 py-6 text-sm text-stone-500">No fields yet.</li>
        )}
        {(fields ?? []).map((f) => (
          <li key={f.id}>
            <a
              href={`/fields/${f.id}`}
              className="flex items-center justify-between px-4 py-3 hover:bg-stone-50"
            >
              <span className="font-medium">{f.name}</span>
              <span className="text-xs text-stone-500">{f.crop_default ?? "—"}</span>
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
