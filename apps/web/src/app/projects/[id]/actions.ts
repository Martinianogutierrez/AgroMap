"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";

export async function createField(formData: FormData) {
  const project_id = String(formData.get("project_id") || "");
  const name = String(formData.get("name") || "").trim();
  const crop_default = String(formData.get("crop_default") || "").trim() || null;
  if (!project_id || !name) return;
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");
  const { data, error } = await supabase
    .from("fields")
    .insert({ project_id, name, crop_default })
    .select("id")
    .single();
  if (error) throw new Error(error.message);
  revalidatePath(`/projects/${project_id}`);
  redirect(`/fields/${data.id}`);
}
