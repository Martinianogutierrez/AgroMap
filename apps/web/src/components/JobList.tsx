"use client";

import { useEffect, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { summarizeConfig, type AnalysisConfig } from "@/lib/config";

type Job = {
  id: string;
  status: string;
  step: string | null;
  progress: number | null;
  error: string | null;
  created_at: string;
  config: unknown;
  finished_at: string | null;
};

export function JobList({
  fieldId,
  initialJobs,
}: {
  fieldId: string;
  initialJobs: Job[];
}) {
  const [jobs, setJobs] = useState(initialJobs);

  useEffect(() => {
    const supabase = createClient();
    const channel = supabase
      .channel(`jobs-field-${fieldId}`)
      .on(
        "postgres_changes",
        {
          event: "*",
          schema: "public",
          table: "jobs",
          filter: `field_id=eq.${fieldId}`,
        },
        (payload) => {
          setJobs((prev) => {
            if (payload.eventType === "INSERT") {
              return [payload.new as Job, ...prev];
            }
            if (payload.eventType === "UPDATE") {
              const row = payload.new as Job;
              return prev.map((j) => (j.id === row.id ? row : j));
            }
            if (payload.eventType === "DELETE") {
              const row = payload.old as Job;
              return prev.filter((j) => j.id !== row.id);
            }
            return prev;
          });
        },
      )
      .subscribe();
    return () => {
      void supabase.removeChannel(channel);
    };
  }, [fieldId]);

  async function cancel(id: string) {
    const supabase = createClient();
    await supabase.from("jobs").update({ status: "canceled" }).eq("id", id);
  }

  return (
    <div className="space-y-2">
      <h2 className="font-medium">Jobs</h2>
      <ul className="divide-y rounded-xl border bg-white">
        {jobs.length === 0 && (
          <li className="px-3 py-4 text-sm text-stone-500">No jobs.</li>
        )}
        {jobs.map((j) => {
          let summary = "";
          try {
            summary = summarizeConfig(j.config as AnalysisConfig);
          } catch {
            summary = "config";
          }
          return (
            <li key={j.id} className="space-y-1 px-3 py-3 text-sm">
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-xs">{j.id.slice(0, 8)}</span>
                <span className="rounded-full bg-stone-100 px-2 py-0.5 text-xs uppercase">
                  {j.status}
                </span>
              </div>
              <p className="text-stone-600">{summary}</p>
              {(j.status === "running" || j.status === "queued") && (
                <div className="h-2 overflow-hidden rounded bg-stone-100">
                  <div
                    className="h-full bg-emerald-600 transition-all"
                    style={{ width: `${j.progress ?? 0}%` }}
                  />
                </div>
              )}
              {j.step && (
                <p className="text-xs text-stone-500">
                  {j.step} · {j.progress ?? 0}%
                </p>
              )}
              {j.error && <p className="text-xs text-red-600">{j.error}</p>}
              {(j.status === "queued" || j.status === "running") && (
                <button
                  type="button"
                  onClick={() => cancel(j.id)}
                  className="text-xs text-red-700 underline"
                >
                  Cancel
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
