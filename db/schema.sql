-- =====================================================================
-- Dashboard de Ocupabilidad del Equipo · esquema PostgreSQL (Supabase)
-- Se puede ejecutar varias veces: no borra datos ni duplica semillas.
-- NO lleva contraseñas: los roles se crean sin login y la clave se pone aparte (ver al final).
-- =====================================================================
create schema if not exists ocupabilidad;
set search_path = ocupabilidad;

-- ---------------------------------------------------------------------
-- 1. CONFIGURACIÓN
-- ---------------------------------------------------------------------
create table if not exists condicion (
  nombre text primary key                       -- Contratado | Trainee Pro | Trainee Pre
);

create table if not exists capacidad_condicion (
  condicion     text     not null references condicion(nombre),
  dia_semana    smallint not null check (dia_semana between 1 and 7),   -- 1=lunes ... 7=domingo
  horas         numeric(4,1) not null check (horas >= 0),
  vigente_desde date     not null default date '2000-01-01',
  primary key (condicion, dia_semana, vigente_desde)
);

create table if not exists colaborador (
  id             serial primary key,
  nombre         text not null unique,          -- como se muestra en el dashboard
  nombre_notion  text not null unique,          -- como llega de Notion
  notion_user_id text unique,                   -- opcional
  condicion      text not null references condicion(nombre),
  rol            text,
  incluir        boolean not null default true, -- false = se ignora por completo
  activo_desde   date,
  activo_hasta   date
);

create table if not exists capacidad_colaborador (   -- excepción opcional por persona
  colaborador_id int      not null references colaborador(id),
  dia_semana     smallint not null check (dia_semana between 1 and 7),
  horas          numeric(4,1) not null check (horas >= 0),
  vigente_desde  date     not null default date '2000-01-01',
  primary key (colaborador_id, dia_semana, vigente_desde)
);

create table if not exists feriado (
  fecha       date primary key,
  descripcion text
);

create table if not exists parametro (
  clave       text primary key,
  valor       text not null,
  descripcion text
);

create table if not exists estado (
  nombre           text primary key,            -- Done | En curso | Stuck | Sin empezar | Otro
  orden            smallint not null,           -- orden de abajo hacia arriba en la barra apilada
  cuenta_en_cierre boolean  not null default false
);

create table if not exists estado_equivalencia (
  texto_notion text primary key,                -- siempre en minúsculas y sin espacios sobrantes
  estado       text not null references estado(nombre)
);

-- ---------------------------------------------------------------------
-- 2. DIMENSIÓN
-- ---------------------------------------------------------------------
create table if not exists proyecto (            -- antes: dim_intervenciones.csv
  proyecto_id    text not null,                 -- id de página de Notion
  product_owner  text not null,                 -- una fila por (proyecto, PO); "Sin Product Owner" si no hay
  nombre         text not null,
  tipo_proyecto  text,
  status         text,
  sponsor        text,
  actualizado_en timestamptz not null default now(),
  primary key (proyecto_id, product_owner)
);

-- ---------------------------------------------------------------------
-- 3. HECHOS
-- ---------------------------------------------------------------------
create table if not exists foto_carga (          -- antes: snapshots + historico_horas_diarias.csv
  fecha            date not null,               -- fecha local de Lima
  momento          text not null check (momento in ('manana', 'cierre')),
  base_origen      text not null,
  actividad_id     text not null,
  persona          text not null,               -- nombre_notion tal cual llegó
  colaborador_id   int references colaborador(id),   -- null si "Sin asignar" o desconocido
  persona_display  text,
  condicion        text,                        -- la condición que tenía ese día
  clasificacion    text,
  actividad        text,
  horas_hoy        numeric(6,2) not null default 0,
  estado           text,                        -- crudo; se normaliza en la vista
  proyecto_id      text,                        -- sin FK: Tareas no trae id
  proyecto         text,
  programa         text,
  tipos_entregable text,
  tiene_entregable boolean,
  primary key (fecha, momento, base_origen, actividad_id, persona)
);
create index if not exists foto_carga_fecha_momento on foto_carga (fecha, momento);
create index if not exists foto_carga_colaborador   on foto_carga (colaborador_id);

create table if not exists estado_actual (       -- antes: estado_actual.csv (se reemplaza entera)
  base_origen      text not null,
  actividad_id     text not null,
  fecha_extraccion date,
  actividad        text,
  personas         text,
  horas_plan       numeric(10,2),
  horas_reales     numeric(10,2),
  proyecto_id      text,
  proyecto         text,
  programa         text,
  tipos_entregable text,
  tiene_entregable boolean,
  estado           text,
  primary key (base_origen, actividad_id)
);

create table if not exists corrida (             -- antes: registro_corridas.csv
  fecha              date not null,
  momento            text not null check (momento in ('manana', 'cierre')),
  ejecutado_en       timestamptz not null,
  ok                 boolean not null,
  filas_pm           int,
  filas_ruta         int,
  filas_tareas       int,
  filas_foto         int,
  horas_foto         numeric(10,2),
  filas_desconocidas int,
  horas_desconocidas numeric(10,2),
  primary key (fecha, momento)
);

-- ---------------------------------------------------------------------
-- 4. SEMILLAS (idempotentes). Equipo y feriados los carga `--sembrar` desde config.yaml.
-- ---------------------------------------------------------------------
insert into condicion (nombre) values ('Contratado'), ('Trainee Pro'), ('Trainee Pre')
  on conflict do nothing;

insert into capacidad_condicion (condicion, dia_semana, horas)
select c.condicion, d.dia, c.horas[d.dia]
from (values
  ('Contratado',  array[9,9,9,9,5,0,0]::numeric[]),
  ('Trainee Pro', array[9,9,9,9,5,0,0]::numeric[]),
  ('Trainee Pre', array[6,6,6,6,6,0,0]::numeric[])
) as c(condicion, horas)
cross join generate_series(1, 7) as d(dia)
on conflict do nothing;

insert into parametro (clave, valor, descripcion) values
  ('umbral_sobrecarga', '0.91', 'ocupabilidad mayor a este valor = SOBRECARGA'),
  ('umbral_adecuada',   '0.70', 'mayor a este valor y hasta el de sobrecarga = CARGA ADECUADA'),
  ('zona_horaria', 'America/Lima', 'zona de la fecha de cada foto'),
  ('programa_pm',  'Modelo de PM', 'programa fijo de las actividades PM')
  on conflict do nothing;

insert into estado (nombre, orden, cuenta_en_cierre) values
  ('Done', 1, true), ('En curso', 2, true), ('Stuck', 3, false),
  ('Sin empezar', 4, false), ('Otro', 5, false)
  on conflict do nothing;

insert into estado_equivalencia (texto_notion, estado) values
  ('done','Done'), ('complete','Done'), ('completed','Done'), ('completado','Done'),
  ('hecho','Done'), ('listo','Done'), ('finalizado','Done'),
  ('en curso','En curso'), ('in progress','En curso'), ('en progreso','En curso'), ('doing','En curso'),
  ('stuck','Stuck'), ('blocked','Stuck'), ('bloqueado','Stuck'), ('detenido','Stuck'),
  ('sin empezar','Sin empezar'), ('not started','Sin empezar'), ('to-do','Sin empezar'),
  ('to do','Sin empezar'), ('todo','Sin empezar'), ('por hacer','Sin empezar'),
  ('pendiente','Sin empezar'), ('backlog','Sin empezar')
  on conflict do nothing;

-- ---------------------------------------------------------------------
-- 5. VISTAS
-- ---------------------------------------------------------------------
-- Foto con el estado ya normalizado (lo desconocido cae en "Otro").
create or replace view v_foto as
select f.*,
       s.nombre as estado_canonico,
       s.orden  as estado_orden,
       s.cuenta_en_cierre
from foto_carga f
left join estado_equivalencia q on q.texto_notion = lower(btrim(f.estado))
join estado s on s.nombre = coalesce(q.estado, 'Otro');

-- Lo que cuenta: la foto de la mañana con todos los estados; la de cierre, solo los que cuentan.
create or replace view v_foto_valida as
select * from v_foto
where momento = 'manana' or cuenta_en_cierre;

-- Horas por colaborador y estado, para la barra apilada del día.
create or replace view v_horas_por_estado as
select fecha, momento, colaborador_id, persona_display,
       estado_canonico as estado, estado_orden, cuenta_en_cierre,
       sum(horas_hoy) as horas
from v_foto
group by fecha, momento, colaborador_id, persona_display,
         estado_canonico, estado_orden, cuenta_en_cierre;

-- Capacidad de cada colaborador por día con foto: feriado = 0; si hay excepción personal, manda.
create or replace view v_capacidad_dia as
select d.fecha, c.id as colaborador_id, c.nombre,
       case when fe.fecha is not null then 0
            else coalesce(
              (select cc.horas from capacidad_colaborador cc
                where cc.colaborador_id = c.id
                  and cc.dia_semana = extract(isodow from d.fecha)::int
                  and cc.vigente_desde <= d.fecha
                order by cc.vigente_desde desc limit 1),
              (select k.horas from capacidad_condicion k
                where k.condicion = c.condicion
                  and k.dia_semana = extract(isodow from d.fecha)::int
                  and k.vigente_desde <= d.fecha
                order by k.vigente_desde desc limit 1),
              0)
       end as capacidad
from (select distinct fecha from corrida) d
cross join colaborador c
left join feriado fe on fe.fecha = d.fecha
where c.incluir
  and (c.activo_desde is null or c.activo_desde <= d.fecha)
  and (c.activo_hasta is null or c.activo_hasta >= d.fecha);

-- Ocupabilidad por colaborador, día y momento, con su clasificación.
create or replace view v_ocupabilidad_diaria as
with p as (
  select max(case when clave = 'umbral_sobrecarga' then valor::numeric end) as sobre,
         max(case when clave = 'umbral_adecuada'   then valor::numeric end) as adec
  from parametro
),
m as (select fecha, momento from corrida),
h as (
  select fecha, momento, colaborador_id, sum(horas_hoy) as horas
  from v_foto_valida
  where colaborador_id is not null
  group by fecha, momento, colaborador_id
)
select m.fecha, m.momento, k.colaborador_id, k.nombre,
       coalesce(h.horas, 0) as horas,
       k.capacidad,
       case when k.capacidad > 0 then round(coalesce(h.horas, 0) / k.capacidad, 4) end as ocupabilidad,
       case when k.capacidad = 0 then null
            when coalesce(h.horas, 0) / k.capacidad > p.sobre then 'SOBRECARGA'
            when coalesce(h.horas, 0) / k.capacidad > p.adec  then 'CARGA ADECUADA'
            else 'SUBCARGA' end as clasificacion
from m
cross join p
join v_capacidad_dia k on k.fecha = m.fecha
left join h on h.fecha = m.fecha and h.momento = m.momento and h.colaborador_id = k.colaborador_id;

-- ---------------------------------------------------------------------
-- 6. ROLES Y PERMISOS (sin contraseñas)
--    cron_rw       = escribe (GitHub Actions)
--    dashboard_ro  = solo lee (Streamlit)
-- ---------------------------------------------------------------------
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'cron_rw') then
    create role cron_rw nologin;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'dashboard_ro') then
    create role dashboard_ro nologin;
  end if;
end $$;

revoke all on schema ocupabilidad from public;
do $$
declare r text;
begin
  foreach r in array array['anon', 'authenticated'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('revoke all on schema ocupabilidad from %I', r);
    end if;
  end loop;
end $$;

grant usage on schema ocupabilidad to cron_rw, dashboard_ro;
grant select, insert, update, delete on all tables in schema ocupabilidad to cron_rw;
grant usage, select on all sequences in schema ocupabilidad to cron_rw;
grant select on all tables in schema ocupabilidad to dashboard_ro;

alter default privileges in schema ocupabilidad
  grant select, insert, update, delete on tables to cron_rw;
alter default privileges in schema ocupabilidad
  grant usage, select on sequences to cron_rw;
alter default privileges in schema ocupabilidad
  grant select on tables to dashboard_ro;

-- ---------------------------------------------------------------------
-- 7. CLAVES DE LOS ROLES (EJECÚTALO APARTE, NO LO GUARDES EN EL REPO)
-- ---------------------------------------------------------------------
-- alter role cron_rw      with login password 'PON_AQUI_UNA_CLAVE_LARGA_1';
-- alter role dashboard_ro with login password 'PON_AQUI_UNA_CLAVE_LARGA_2';
