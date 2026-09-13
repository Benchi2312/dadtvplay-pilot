-- Si ya tienes la tabla "products" del piloto anterior, no hace falta
-- volver a crearla — este script no la toca si ya existe.
create table if not exists products (
  id bigint generated always as identity primary key,
  platform text not null,
  plan_name text not null,
  price numeric not null,
  stock integer not null default 0,
  duration_days integer not null default 30,
  active boolean not null default true,
  updated_at timestamptz not null default now()
);

alter table products enable row level security;
drop policy if exists "Cualquiera puede leer productos" on products;
create policy "Cualquiera puede leer productos" on products for select using (true);
drop policy if exists "Cualquiera puede editar productos (piloto)" on products;
create policy "Cualquiera puede editar productos (piloto)" on products for all using (true) with check (true);

-- Bitácora de conversación.
create table if not exists conversation_log (
  id bigint generated always as identity primary key,
  customer_phone text not null,
  incoming_message text not null,
  intent text,
  reply_message text,
  created_at timestamptz not null default now()
);

alter table conversation_log enable row level security;
drop policy if exists "Cualquiera puede leer bitacora" on conversation_log;
create policy "Cualquiera puede leer bitacora" on conversation_log for all using (true) with check (true);

-- NUEVO: estado de conversación, para el flujo "ofrecí un producto, espero
-- que el cliente confirme o rechace". Sin esto, un "sí confirmo" después
-- de una oferta se trata como mensaje genérico sin contexto.
create table if not exists conversation_state (
  phone text primary key,
  state text not null default 'idle',
  pending_product_id bigint references products(id),
  updated_at timestamptz not null default now()
);

alter table conversation_state enable row level security;
drop policy if exists "Cualquiera puede leer/editar estado (piloto)" on conversation_state;
create policy "Cualquiera puede leer/editar estado (piloto)" on conversation_state for all using (true) with check (true);