"""Acceso a PostgreSQL (Supabase): siembra de configuración, escritura de fotos y lectura.

Esquema: db/schema.sql (schema `ocupabilidad`). Roles:
  cron_rw       escribe (SUPABASE_DB_URL)
  dashboard_ro  solo lee (SUPABASE_DB_URL_RO)

Las funciones de escritura controlan la transacción: todo o nada. Los mensajes de error
nunca incluyen la cadena de conexión.
"""
from __future__ import annotations

import copy
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values

import kpis
from build_master import CIERRE, COLUMNAS_ESTADO, COLUMNAS_FOTO, COLUMNAS_REGISTRO, MANANA
from capacidad import a_fecha

S = "ocupabilidad"
LOTE = 500
ZONA_DEFECTO = "America/Lima"
TOLERANCIA_HORAS = 0.01

COLUMNAS_DB_FOTO = [
    "fecha", "momento", "base_origen", "actividad_id", "persona", "colaborador_id",
    "persona_display", "condicion", "clasificacion", "actividad", "horas_hoy", "estado",
    "proyecto_id", "proyecto", "programa", "tipos_entregable", "tiene_entregable",
]
COLUMNAS_DB_ESTADO = [
    "base_origen", "actividad_id", "fecha_extraccion", "actividad", "personas", "horas_plan",
    "horas_reales", "proyecto_id", "proyecto", "programa", "tipos_entregable",
    "tiene_entregable", "estado",
]
COLUMNAS_DB_CORRIDA = [
    "fecha", "momento", "ejecutado_en", "ok", "filas_pm", "filas_ruta", "filas_tareas",
    "filas_foto", "horas_foto", "filas_desconocidas", "horas_desconocidas",
]
COLUMNAS_DB_PROYECTO = ["proyecto_id", "product_owner", "nombre", "tipo_proyecto", "status", "sponsor"]


# ------------------------------------------------------------------ conexión ---

def conectar(solo_lectura: bool = False):
    """Abre la conexión (sin autocommit). `solo_lectura` usa el rol dashboard_ro."""
    load_dotenv(Path(__file__).parent / ".env")
    variable = "SUPABASE_DB_URL_RO" if solo_lectura else "SUPABASE_DB_URL"
    url = os.environ.get(variable)
    if not url:
        raise RuntimeError(f"Falta la variable de entorno {variable}.")
    try:
        return psycopg2.connect(url, connect_timeout=15)
    except psycopg2.Error as e:
        detalle = str(e).replace(url, "***").strip()
        raise RuntimeError(f"No se pudo conectar con {variable}: {detalle}") from None


# ---------------------------------------------------------------- limpieza ---

def _nulo(v: Any) -> bool:
    if v is None or v is pd.NA or v is pd.NaT:
        return True
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _texto(v: Any) -> Optional[str]:
    return None if _nulo(v) else str(v)


def _num(v: Any) -> Optional[float]:
    if _nulo(v):
        return None
    n = pd.to_numeric(pd.Series([v]), errors="coerce").iloc[0]
    return None if pd.isna(n) else float(n)


def _entero(v: Any) -> Optional[int]:
    n = _num(v)
    return None if n is None else int(round(n))


def _bool_o_none(v: Any) -> Optional[bool]:
    if _nulo(v):
        return None
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    s = str(v).strip().lower()
    if s in ("true", "1"):
        return True
    if s in ("false", "0"):
        return False
    return None


def _fecha(v: Any) -> Optional[date]:
    return None if _nulo(v) else a_fecha(v)


def _datetime_zona(v: Any, zona: str = ZONA_DEFECTO) -> Optional[datetime]:
    if _nulo(v):
        return None
    t = pd.Timestamp(v)
    if t.tzinfo is None:
        t = t.tz_localize(ZoneInfo(zona))
    return t.to_pydatetime()


def _hoy(config: dict, hoy: Optional[date] = None) -> date:
    if hoy is not None:
        return a_fecha(hoy)
    return datetime.now(ZoneInfo(config.get("zona_horaria") or ZONA_DEFECTO)).date()


def _insertar(cur, tabla: str, columnas: list[str], filas: list[tuple], sufijo: str = "") -> None:
    if not filas:
        return
    sql = f"insert into {S}.{tabla} ({', '.join(columnas)}) values %s {sufijo}".strip()
    execute_values(cur, sql, filas, page_size=LOTE)


def _reemplazar(cur, tabla: str, columnas: list[str], filas: list[tuple]) -> None:
    cur.execute(f"delete from {S}.{tabla}")
    _insertar(cur, tabla, columnas, filas)


def _transaccion(conn, funcion, *args):
    """Ejecuta `funcion(cur, *args)` en una sola transacción: commit al final, rollback ante todo."""
    try:
        with conn.cursor() as cur:
            resultado = funcion(cur, *args)
        conn.commit()
        return resultado
    except BaseException:
        conn.rollback()
        raise


# ------------------------------------------------------------ filas a insertar ---

def _filas_foto(foto: pd.DataFrame, fecha: date, momento: str, ids: dict[str, int]) -> list[tuple]:
    faltan = [c for c in COLUMNAS_FOTO if c not in ("momento", "fecha") and c not in foto.columns]
    if faltan:
        raise ValueError(f"foto: faltan columnas {faltan}. Disponibles: {list(foto.columns)}")
    filas = []
    for r in foto.to_dict("records"):
        display = _texto(r["persona_display"])
        horas = _num(r["horas_hoy"])
        filas.append((
            fecha, momento, r["base_origen"], r["actividad_id"], r["persona"],
            ids.get(display) if display is not None else None,
            display, _texto(r["condicion"]), _texto(r["clasificacion"]), _texto(r["actividad"]),
            0.0 if horas is None else horas, _texto(r["estado"]),
            _texto(r["proyecto_id"]), _texto(r["proyecto"]), _texto(r["programa"]),
            _texto(r["tipos_entregable"]), _bool_o_none(r["tiene_entregable"]),
        ))
    return filas


def _filas_corrida(registro: dict) -> tuple:
    return (
        _fecha(registro["fecha"]), registro["momento"], _datetime_zona(registro["ejecutado_en"]),
        bool(_bool_o_none(registro.get("ok"))),
        _entero(registro.get("filas_pm")), _entero(registro.get("filas_ruta")),
        _entero(registro.get("filas_tareas")), _entero(registro.get("filas_foto")),
        _num(registro.get("horas_foto")), _entero(registro.get("filas_desconocidas")),
        _num(registro.get("horas_desconocidas")),
    )


def _filas_estado(estado: pd.DataFrame) -> list[tuple]:
    faltan = [c for c in COLUMNAS_ESTADO if c not in estado.columns]
    if faltan:
        raise ValueError(f"estado_actual: faltan columnas {faltan}. Disponibles: {list(estado.columns)}")
    return [
        (r["base_origen"], r["actividad_id"], _fecha(r["fecha_extraccion"]), _texto(r["actividad"]),
         _texto(r["personas"]), _num(r["horas_plan"]), _num(r["horas_reales"]),
         _texto(r["proyecto_id"]), _texto(r["proyecto"]), _texto(r["programa"]),
         _texto(r["tipos_entregable"]), _bool_o_none(r["tiene_entregable"]), _texto(r["estado"]))
        for r in estado.to_dict("records")
    ]


def _filas_proyecto(dim: pd.DataFrame) -> list[tuple]:
    faltan = [c for c in kpis.COLUMNAS_DIM if c not in dim.columns]
    if faltan:
        raise ValueError(f"dim: faltan columnas {faltan}. Disponibles: {list(dim.columns)}")
    vistos: dict[tuple, tuple] = {}
    for r in dim.to_dict("records"):
        po = _texto(r["product_owner"]) or kpis.SIN_PO
        clave = (r["proyecto_id"], po)
        if clave not in vistos:     # la llave primaria es (proyecto, PO): primer match
            vistos[clave] = (r["proyecto_id"], po, r["proyecto"], _texto(r["tipo_proyecto"]),
                             _texto(r["status"]), _texto(r["sponsor"]))
    return list(vistos.values())


def _mapa_colaboradores(cur) -> dict[str, int]:
    cur.execute(f"select id, nombre from {S}.colaborador")
    return {nombre: id_ for id_, nombre in cur.fetchall()}


def _reemplazar_foto(cur, foto: pd.DataFrame, fecha: date, momento: str, ids: dict[str, int]) -> int:
    """delete + insert de una foto y verificación contra la base. Devuelve las filas insertadas."""
    filas = _filas_foto(foto, fecha, momento, ids)
    cur.execute(f"delete from {S}.foto_carga where fecha = %s and momento = %s", (fecha, momento))
    _insertar(cur, "foto_carga", COLUMNAS_DB_FOTO, filas)

    cur.execute(
        f"select count(*), coalesce(sum(horas_hoy), 0) from {S}.foto_carga "
        "where fecha = %s and momento = %s", (fecha, momento))
    n_db, horas_db = cur.fetchone()
    horas = round(sum(f[COLUMNAS_DB_FOTO.index("horas_hoy")] for f in filas), 2)
    if int(n_db) != len(filas) or abs(float(horas_db) - horas) > TOLERANCIA_HORAS:
        raise RuntimeError(
            f"Verificación fallida para {fecha} {momento}: foto recibida {len(filas)} filas / "
            f"{horas} h; en la base {n_db} filas / {float(horas_db)} h.")
    return len(filas)


_CORRIDA_SQL_SUFIJO = (
    "on conflict (fecha, momento) do update set "
    + ", ".join(f"{c} = excluded.{c}" for c in COLUMNAS_DB_CORRIDA[2:])
)


# --------------------------------------------------------------- sembrar_config ---

def _vector_vigente(filas: Iterable[tuple], hoy: date) -> tuple[Optional[date], dict[int, float]]:
    """filas (dia_semana, horas, vigente_desde) -> (fecha de la versión vigente, {dia: horas})."""
    vigentes = [f for f in filas if f[2] <= hoy]
    if not vigentes:
        return None, {}
    desde = max(f[2] for f in vigentes)
    return desde, {int(d): float(h) for d, h, v in vigentes if v == desde}


def _sembrar(cur, config: dict, hoy: date) -> dict:
    resumen: dict[str, Any] = {}

    # condicion y capacidad_condicion (versionada)
    capacidad = config.get("capacidad_por_condicion") or {}
    _insertar(cur, "condicion", ["nombre"], [(c,) for c in capacidad], "on conflict do nothing")
    versiones_nuevas = []
    for condicion, horas in capacidad.items():
        if len(horas) != 7:
            raise ValueError(f"'{condicion}' debe tener 7 valores (lunes a domingo), tiene {len(horas)}.")
        cur.execute(
            f"select dia_semana, horas, vigente_desde from {S}.capacidad_condicion where condicion = %s",
            (condicion,))
        _, actual = _vector_vigente(cur.fetchall(), hoy)
        nuevo = {i + 1: float(h) for i, h in enumerate(horas)}
        if actual != nuevo:
            versiones_nuevas.append(condicion)
            _insertar(
                cur, "capacidad_condicion", ["condicion", "dia_semana", "horas", "vigente_desde"],
                [(condicion, d, h, hoy) for d, h in nuevo.items()],
                "on conflict (condicion, dia_semana, vigente_desde) do update set horas = excluded.horas")
    resumen["condiciones"] = len(capacidad)
    resumen["capacidad_versiones_nuevas"] = versiones_nuevas

    # colaborador (upsert por nombre; nunca se borra)
    equipo = config.get("equipo") or []
    cur.execute(f"select nombre from {S}.colaborador")
    en_db = {r[0] for r in cur.fetchall()}
    en_yaml = {m["nombre"] for m in equipo}
    _insertar(
        cur, "colaborador", ["nombre", "nombre_notion", "condicion", "incluir"],
        [(m["nombre"], m.get("nombre_notion", m["nombre"]), m["condicion"], bool(m.get("incluir", True)))
         for m in equipo],
        "on conflict (nombre) do update set nombre_notion = excluded.nombre_notion, "
        "condicion = excluded.condicion, incluir = excluded.incluir")
    resumen["colaboradores"] = len(equipo)
    resumen["colaboradores_nuevos"] = sorted(en_yaml - en_db)
    resumen["colaboradores_fuera_del_yaml"] = sorted(en_db - en_yaml)

    # feriado (no pisa una descripción existente con null)
    feriados = []
    for f in config.get("feriados") or []:
        if isinstance(f, dict):
            feriados.append((a_fecha(f["fecha"]), f.get("descripcion")))
        else:
            feriados.append((a_fecha(f), None))
    _insertar(cur, "feriado", ["fecha", "descripcion"], feriados,
              "on conflict (fecha) do update set descripcion = coalesce(excluded.descripcion, feriado.descripcion)")
    resumen["feriados"] = len(feriados)

    # parametro
    umbrales = config.get("umbrales_carga") or {}
    parametros = [
        ("umbral_sobrecarga", umbrales.get("sobrecarga")),
        ("umbral_adecuada", umbrales.get("adecuada")),
        ("zona_horaria", config.get("zona_horaria")),
        ("programa_pm", config.get("programa_pm")),
    ]
    parametros = [(k, str(v)) for k, v in parametros if v is not None]
    _insertar(cur, "parametro", ["clave", "valor"], parametros,
              "on conflict (clave) do update set valor = excluded.valor")
    resumen["parametros"] = len(parametros)

    # estado y estado_equivalencia (no se pisa estado.cuenta_en_cierre)
    tabla = kpis.construir_tabla_estados(config.get("estados"))
    equivalencias = {" ".join(t.split()).lower(): e for t, e in tabla.items()}
    _insertar(
        cur, "estado", ["nombre", "orden", "cuenta_en_cierre"],
        [(e, i + 1, e in kpis.ESTADOS_VALIDOS_DEFECTO) for i, e in enumerate(kpis.ESTADOS_ORDEN)],
        "on conflict do nothing")
    _insertar(cur, "estado_equivalencia", ["texto_notion", "estado"], sorted(equivalencias.items()),
              "on conflict (texto_notion) do update set estado = excluded.estado")
    resumen["estado_equivalencias"] = len(equivalencias)
    return resumen


def sembrar_config(conn, config: dict, hoy: Optional[date] = None) -> dict:
    """Carga config.yaml en las tablas de configuración (idempotente, una transacción)."""
    return _transaccion(conn, _sembrar, config, _hoy(config, hoy))


# ------------------------------------------------------------------ guardar_foto ---

def _guardar_foto(cur, foto, registro, estado, dim) -> None:
    fecha, momento = _fecha(registro["fecha"]), registro["momento"]
    _reemplazar_foto(cur, foto, fecha, momento, _mapa_colaboradores(cur))
    _insertar(cur, "corrida", COLUMNAS_DB_CORRIDA, [_filas_corrida(registro)], _CORRIDA_SQL_SUFIJO)
    _reemplazar(cur, "estado_actual", COLUMNAS_DB_ESTADO, _filas_estado(estado))
    if dim is not None and len(dim):
        _reemplazar(cur, "proyecto",
                    [c for c in COLUMNAS_DB_PROYECTO], _filas_proyecto(dim))


def guardar_foto(conn, foto: pd.DataFrame, registro: dict, estado: pd.DataFrame,
                 dim: Optional[pd.DataFrame] = None) -> None:
    """Reemplaza la foto de (fecha, momento), la corrida, estado_actual y proyecto; todo o nada."""
    _transaccion(conn, _guardar_foto, foto, registro, estado, dim)


# ------------------------------------------------------------------ migrar_csv ---

_SNAPSHOT = re.compile(r"^snapshot_(\d{4}-\d{2}-\d{2})(_manana)?\.csv$")


def _leer_csv(ruta: Path) -> pd.DataFrame:
    return pd.read_csv(ruta, dtype=str, encoding="utf-8-sig")


def _migrar(cur, raiz: Path, config: dict) -> dict:
    rutas = config["rutas"]
    snapshots, processed = raiz / rutas["snapshots"], raiz / rutas["processed"]
    ids = _mapa_colaboradores(cur)
    resumen: dict[str, Any] = {"fotos": {}, "corridas": 0, "estado_actual": 0, "proyecto": 0}

    archivos = sorted(p for p in snapshots.glob("snapshot_*.csv") if _SNAPSHOT.match(p.name)) \
        if snapshots.exists() else []
    for ruta in archivos:
        m = _SNAPSHOT.match(ruta.name)
        fecha, momento = date.fromisoformat(m.group(1)), (MANANA if m.group(2) else CIERRE)
        n = _reemplazar_foto(cur, _leer_csv(ruta), fecha, momento, ids)
        resumen["fotos"][f"{fecha.isoformat()} {momento}"] = n

    ruta = processed / "registro_corridas.csv"
    if ruta.exists():
        reg = _leer_csv(ruta)
        if "momento" not in reg.columns:
            reg["momento"] = CIERRE
        reg["momento"] = reg["momento"].fillna(CIERRE)
        filas = [_filas_corrida(r) for r in reg.to_dict("records")]
        _insertar(cur, "corrida", COLUMNAS_DB_CORRIDA, filas, _CORRIDA_SQL_SUFIJO)
        resumen["corridas"] = len(filas)

    ruta = processed / "estado_actual.csv"
    if ruta.exists():
        filas = _filas_estado(_leer_csv(ruta))
        _reemplazar(cur, "estado_actual", COLUMNAS_DB_ESTADO, filas)
        resumen["estado_actual"] = len(filas)

    ruta = processed / "dim_intervenciones.csv"
    if ruta.exists():
        dim = _leer_csv(ruta)
        if len(dim):
            filas = _filas_proyecto(dim)
            _reemplazar(cur, "proyecto", COLUMNAS_DB_PROYECTO, filas)
            resumen["proyecto"] = len(filas)
    return resumen


def migrar_csv(conn, raiz: Path, config: dict) -> dict:
    """Carga a la base lo ya generado en CSV (idempotente, una transacción)."""
    return _transaccion(conn, _migrar, Path(raiz), config)


# -------------------------------------------------------------- cargar_datos_db ---

def _a_texto(v: Any, zona: str = ZONA_DEFECTO) -> Any:
    """Valor de la base -> texto, como lo daría pd.read_csv(dtype=str). Nulo -> NaN."""
    if _nulo(v):
        return np.nan
    if isinstance(v, (bool, np.bool_)):
        return "True" if v else "False"
    if isinstance(v, datetime):
        if v.tzinfo is not None:
            v = v.astimezone(ZoneInfo(zona))
        return v.isoformat(timespec="seconds")
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def _leer_tabla(cur, sql: str, columnas: list[str]) -> pd.DataFrame:
    cur.execute(sql)
    filas = cur.fetchall()
    if not filas:
        return pd.DataFrame(columns=columnas)
    return pd.DataFrame([[_a_texto(v) for v in f] for f in filas], columns=columnas, dtype=object)


def cargar_datos_db(conn) -> dict[str, pd.DataFrame]:
    """Misma estructura que kpis.cargar_datos (todo texto), leída de la base."""
    with conn.cursor() as cur:
        historico = _leer_tabla(
            cur, f"select {', '.join(COLUMNAS_FOTO)} from {S}.foto_carga "
                 "order by fecha, momento, base_origen, actividad_id, persona", COLUMNAS_FOTO)
        estado = _leer_tabla(
            cur, f"select {', '.join(COLUMNAS_ESTADO)} from {S}.estado_actual "
                 "order by base_origen, actividad_id", COLUMNAS_ESTADO)
        registro = _leer_tabla(
            cur, f"select {', '.join(COLUMNAS_REGISTRO)} from {S}.corrida order by fecha, momento",
            COLUMNAS_REGISTRO)
        dim = _leer_tabla(
            cur, "select proyecto_id, nombre, tipo_proyecto, status, sponsor, product_owner "
                 f"from {S}.proyecto order by proyecto_id, product_owner", kpis.COLUMNAS_DIM)
    return {"historico": historico, "estado": estado, "registro": registro, "dim": dim}


# ------------------------------------------------------------- config_desde_db ---

def config_desde_db(conn, config_base: dict, hoy: Optional[date] = None) -> dict:
    """Copia de config_base con equipo, capacidad, feriados y umbrales tomados de la base."""
    config = copy.deepcopy(config_base)
    hoy = _hoy(config_base, hoy)
    with conn.cursor() as cur:
        cur.execute(f"select nombre, nombre_notion, condicion, incluir from {S}.colaborador order by id")
        config["equipo"] = [
            {"nombre": n, "nombre_notion": nn, "condicion": c, "incluir": bool(i)}
            for n, nn, c, i in cur.fetchall()
        ]

        cur.execute(f"select condicion, dia_semana, horas, vigente_desde from {S}.capacidad_condicion")
        por_condicion: dict[str, list[tuple]] = {}
        for condicion, dia, horas, desde in cur.fetchall():
            por_condicion.setdefault(condicion, []).append((dia, horas, desde))
        capacidad = {}
        for condicion, filas in por_condicion.items():
            _, vector = _vector_vigente(filas, hoy)
            if not vector:
                continue
            faltan = [d for d in range(1, 8) if d not in vector]
            if faltan:
                raise ValueError(f"capacidad_condicion de '{condicion}': faltan los días {faltan}.")
            capacidad[condicion] = [vector[d] for d in range(1, 8)]
        config["capacidad_por_condicion"] = capacidad

        cur.execute(f"select fecha from {S}.feriado order by fecha")
        config["feriados"] = [a_fecha(f[0]).isoformat() for f in cur.fetchall()]

        cur.execute(
            f"select clave, valor from {S}.parametro "
            "where clave in ('umbral_sobrecarga', 'umbral_adecuada')")
        p = dict(cur.fetchall())
        umbrales = dict(config.get("umbrales_carga") or {})
        if "umbral_sobrecarga" in p:
            umbrales["sobrecarga"] = float(p["umbral_sobrecarga"])
        if "umbral_adecuada" in p:
            umbrales["adecuada"] = float(p["umbral_adecuada"])
        config["umbrales_carga"] = umbrales
    return config
