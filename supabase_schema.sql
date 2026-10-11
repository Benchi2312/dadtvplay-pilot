-- Esquema de Supabase para el backend (WaMundo + FastAPI).
-- Todo con "if not exists" para poder correrlo varias veces sin romper nada.
-- Si ya tienes la tabla "products" del piloto anterior, no hace falta
-- volver a crearla — este script no la toca si ya existe.

-- Productos en oferta.
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
-- Columnas que podrían faltar en una tabla ya existente:
alter table products add column if not exists duration_days integer not null default 30;
alter table products add column if not exists active boolean not null default true;
alter table products add column if not exists updated_at timestamptz not null default now();

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

-- Estado de conversación, para el flujo "ofrecí un producto, espero que el
-- cliente confirme o rechace". Estados usados por el código: 'idle',
-- 'awaiting_choice', 'awaiting_confirmation', 'confirmed'.
create table if not exists conversation_state (
  phone text primary key,
  state text not null default 'idle',
  pending_product_id bigint references products(id),
  -- Candidatas ofrecidas al cliente cuando hay varias opciones
  -- (multi-plan o multi-plataforma). Se conserva hasta que elige.
  pending_product_ids bigint[],
  updated_at timestamptz not null default now()
);

-- Si la tabla ya existía (piloto anterior), agrega las columnas nuevas sin
-- romper nada:
alter table conversation_state add column if not exists pending_product_ids bigint[];
alter table conversation_state add column if not exists pending_product_id bigint references products(id);
alter table conversation_state add column if not exists updated_at timestamptz not null default now();

alter table conversation_state enable row level security;
drop policy if exists "Cualquiera puede leer/editar estado (piloto)" on conversation_state;
create policy "Cualquiera puede leer/editar estado (piloto)" on conversation_state for all using (true) with check (true);

-- Mensajes ya procesados, para no responder dos veces el mismo (el Hook
-- puede dispararse varias veces, o llegar por Hook y por el sondeo).
create table if not exists processed_messages (
  wamundo_id text primary key,
  created_at timestamptz not null default now()
);
alter table processed_messages enable row level security;

-- Si una tabla ya creada quedó con el nombre viejo de la columna:
-- alter table processed_messages rename column message_id to wamundo_id;
