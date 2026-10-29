-- Field Productivity Zones — initial schema (PROJECT_PLAN §5)

create extension if not exists postgis with schema extensions;
create extension if not exists "pgcrypto" with schema extensions;

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------

create table public.projects (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  name text not null,
  created_at timestamptz not null default now()
);

create table public.fields (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.projects(id) on delete cascade,
  name text not null,
  crop_default text,
  boundary extensions.geometry(MultiPolygon, 4326),
  created_at timestamptz not null default now()
);

create type public.dataset_kind as enum ('orthomosaic', 'satellite', 'yield', 'boundary');

create table public.datasets (
  id uuid primary key default gen_random_uuid(),
  field_id uuid not null references public.fields(id) on delete cascade,
  kind public.dataset_kind not null,
  season_year int,
  crop text,
  storage_path text not null,
  original_name text,
  size_bytes bigint,
  meta jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create type public.job_status as enum ('queued', 'running', 'succeeded', 'failed', 'canceled');

create table public.jobs (
  id uuid primary key default gen_random_uuid(),
  field_id uuid not null references public.fields(id) on delete cascade,
  owner_id uuid not null references auth.users(id),
  kind text not null default 'analysis',
  config jsonb not null,
  status public.job_status not null default 'queued',
  step text,
  progress int not null default 0,
  log text not null default '',
  error text,
  worker_id text,
  heartbeat_at timestamptz,
  retry_count int not null default 0,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz
);

create index jobs_status_created_at_idx on public.jobs (status, created_at);

create table public.job_outputs (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references public.jobs(id) on delete cascade,
  kind text not null,
  storage_path text not null,
  size_bytes bigint
);

create table public.zones (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references public.jobs(id) on delete cascade,
  zone_id int,
  zone_name text,
  area_ha numeric,
  yld_mean numeric,
  yld_cv numeric,
  ndvi_mean numeric,
  geom extensions.geometry(MultiPolygon, 4326)
);

create index zones_geom_gix on public.zones using gist (geom);
create index zones_job_id_idx on public.zones (job_id);
create index fields_project_id_idx on public.fields (project_id);
create index datasets_field_id_idx on public.datasets (field_id);

-- ---------------------------------------------------------------------------
-- Ownership helpers
-- ---------------------------------------------------------------------------

create or replace function public.is_project_owner(p_project_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from public.projects p
    where p.id = p_project_id and p.owner_id = auth.uid()
  );
$$;

create or replace function public.owns_field(p_field_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.fields f
    join public.projects p on p.id = f.project_id
    where f.id = p_field_id and p.owner_id = auth.uid()
  );
$$;

create or replace function public.owns_job(p_job_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1 from public.jobs j
    where j.id = p_job_id and j.owner_id = auth.uid()
  );
$$;

-- ---------------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------------

alter table public.projects enable row level security;
alter table public.fields enable row level security;
alter table public.datasets enable row level security;
alter table public.jobs enable row level security;
alter table public.job_outputs enable row level security;
alter table public.zones enable row level security;

create policy projects_select on public.projects
  for select using (owner_id = auth.uid());
create policy projects_insert on public.projects
  for insert with check (owner_id = auth.uid());
create policy projects_update on public.projects
  for update using (owner_id = auth.uid());
create policy projects_delete on public.projects
  for delete using (owner_id = auth.uid());

create policy fields_select on public.fields
  for select using (public.is_project_owner(project_id));
create policy fields_insert on public.fields
  for insert with check (public.is_project_owner(project_id));
create policy fields_update on public.fields
  for update using (public.is_project_owner(project_id));
create policy fields_delete on public.fields
  for delete using (public.is_project_owner(project_id));

create policy datasets_select on public.datasets
  for select using (public.owns_field(field_id));
create policy datasets_insert on public.datasets
  for insert with check (public.owns_field(field_id));
create policy datasets_update on public.datasets
  for update using (public.owns_field(field_id));
create policy datasets_delete on public.datasets
  for delete using (public.owns_field(field_id));

create policy jobs_select on public.jobs
  for select using (owner_id = auth.uid());
create policy jobs_insert on public.jobs
  for insert with check (
    owner_id = auth.uid() and public.owns_field(field_id)
  );
create policy jobs_update on public.jobs
  for update using (owner_id = auth.uid());
create policy jobs_delete on public.jobs
  for delete using (owner_id = auth.uid());

create policy job_outputs_select on public.job_outputs
  for select using (public.owns_job(job_id));
create policy job_outputs_insert on public.job_outputs
  for insert with check (public.owns_job(job_id));
create policy job_outputs_delete on public.job_outputs
  for delete using (public.owns_job(job_id));

create policy zones_select on public.zones
  for select using (public.owns_job(job_id));
create policy zones_insert on public.zones
  for insert with check (public.owns_job(job_id));
create policy zones_delete on public.zones
  for delete using (public.owns_job(job_id));

-- ---------------------------------------------------------------------------
-- Claim / reaper (service role / security definer)
-- ---------------------------------------------------------------------------

create or replace function public.claim_job(p_worker_id text)
returns public.jobs
language plpgsql
security definer
set search_path = public
as $$
declare
  claimed public.jobs;
begin
  update public.jobs j
  set
    status = 'running',
    started_at = coalesce(j.started_at, now()),
    heartbeat_at = now(),
    worker_id = p_worker_id,
    error = null
  where j.id = (
    select id from public.jobs
    where status = 'queued'
    order by created_at
    for update skip locked
    limit 1
  )
  returning * into claimed;

  return claimed;
end;
$$;

create or replace function public.reap_stale_jobs(
  max_age interval default interval '2 minutes',
  max_retries int default 2
)
returns setof public.jobs
language plpgsql
security definer
set search_path = public
as $$
begin
  return query
  update public.jobs j
  set
    status = case
      when j.retry_count >= max_retries then 'failed'::public.job_status
      else 'queued'::public.job_status
    end,
    error = case
      when j.retry_count >= max_retries then coalesce(j.error, 'stale worker heartbeat; max retries exceeded')
      else j.error
    end,
    worker_id = null,
    heartbeat_at = null,
    retry_count = j.retry_count + case when j.retry_count >= max_retries then 0 else 1 end,
    finished_at = case when j.retry_count >= max_retries then now() else null end
  where j.status = 'running'
    and j.heartbeat_at is not null
    and j.heartbeat_at < now() - max_age
  returning *;
end;
$$;

revoke all on function public.claim_job(text) from public;
revoke all on function public.reap_stale_jobs(interval, int) from public;
grant execute on function public.claim_job(text) to service_role;
grant execute on function public.reap_stale_jobs(interval, int) to service_role;

-- ---------------------------------------------------------------------------
-- Realtime
-- ---------------------------------------------------------------------------

alter publication supabase_realtime add table public.jobs;

-- ---------------------------------------------------------------------------
-- Storage buckets + policies (path: {owner_id}/...)
-- ---------------------------------------------------------------------------

insert into storage.buckets (id, name, public, file_size_limit)
values
  ('uploads', 'uploads', false, 104857600),
  ('outputs', 'outputs', false, 104857600)
on conflict (id) do nothing;

create policy uploads_select on storage.objects
  for select to authenticated
  using (
    bucket_id = 'uploads'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

create policy uploads_insert on storage.objects
  for insert to authenticated
  with check (
    bucket_id = 'uploads'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

create policy uploads_update on storage.objects
  for update to authenticated
  using (
    bucket_id = 'uploads'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

create policy uploads_delete on storage.objects
  for delete to authenticated
  using (
    bucket_id = 'uploads'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

create policy outputs_select on storage.objects
  for select to authenticated
  using (
    bucket_id = 'outputs'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

create policy outputs_insert on storage.objects
  for insert to authenticated
  with check (
    bucket_id = 'outputs'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

create policy outputs_delete on storage.objects
  for delete to authenticated
  using (
    bucket_id = 'outputs'
    and (storage.foldername(name))[1] = auth.uid()::text
  );
