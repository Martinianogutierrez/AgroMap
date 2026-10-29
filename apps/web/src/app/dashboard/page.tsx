import { createClient } from "@/lib/supabase/server";
import { redirect } from "next/navigation";
import { createProject } from "./actions";

export default async function DashboardPage() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: projects } = await supabase
    .from("projects")
    .select("id, name, created_at")
    .order("created_at", { ascending: false });

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold">Projects</h1>
        <p className="text-sm text-stone-600">{user.email}</p>
      </div>

      <form action={createProject} className="flex gap-2">
        <input
          name="name"
          required
          placeholder="New project name"
          className="flex-1 rounded-md border border-stone-300 px-3 py-2"
        />
        <button
          type="submit"
          className="rounded-md bg-emerald-700 px-4 py-2 text-white hover:bg-emerald-800"
        >
          Create
        </button>
      </form>

      <ul className="divide-y divide-stone-200 rounded-xl border border-stone-200 bg-white">
        {(projects ?? []).length === 0 && (
          <li className="px-4 py-6 text-sm text-stone-500">No projects yet.</li>
        )}
        {(projects ?? []).map((p) => (
          <li key={p.id}>
            <a
              href={`/projects/${p.id}`}
              className="flex items-center justify-between px-4 py-3 hover:bg-stone-50"
            >
              <span className="font-medium">{p.name}</span>
              <span className="text-xs text-stone-500">
                {new Date(p.created_at).toLocaleDateString()}
              </span>
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
