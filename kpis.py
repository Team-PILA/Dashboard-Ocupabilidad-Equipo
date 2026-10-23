"""Indicadores del dashboard de ocupabilidad (funciones puras, sin Notion ni Streamlit).

Fuentes (las que genera build_master.py):
  historico  data/processed/historico_horas_diarias.csv   horas_hoy por persona/actividad/día
  estado     data/processed/estado_actual.csv             plan y reales acumulados por actividad
  registro   data/processed/registro_corridas.csv         días que tienen foto
  dim        data/processed/dim_intervenciones.csv        proyecto -> tipo de proyecto y Product Owner

Reglas de cálculo (acordadas):
- Los indicadores con rango de fechas (ocupabilidad, horas por proyecto / programa / PO,
  promedio por tipo de entregable) usan SOLO los días que tienen foto y son laborables
  (capacidad > 0: sin fines de semana ni feriados). Un día sin foto no diluye la ocupabilidad.
- Todas las sumas son horas-persona: si dos personas figuran en una actividad, cada una aporta
  las horas completas. Solo cuentan las personas del equipo; las no reconocidas
  ("Sin asignar", externos) no entran a los indicadores y salen en las alertas.
- Un proyecto con dos Product Owner reparte las horas completas a cada uno.
- Exactitud de planificación = SUM(horas_plan) / SUM(horas_reales) sobre el ESTADO ACTUAL
  (acumulado a la fecha de extracción, sin rango de fechas). Se excluyen las actividades con
  horas_plan vacías o en 0. Un valor mayor a 1 significa que se planificó más de lo ejecutado.
- Sin capacidad o sin horas reales el resultado es None ("Sin datos"), nunca 0.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from build_master import COLUMNAS_ESTADO, COLUMNAS_FOTO, COLUMNAS_REGISTRO
from capacidad import DESCONOCIDO, EN_EQUIPO, Equipo, a_fecha, dias_en_rango

PROGRAMA_PM = "Modelo de PM"
BASES_PROYECTO = ("Ruta", "PM")   # Actividades & Entregables y Actividades PM
SIN_PROYECTO = "Sin proyecto"
SIN_PO = "Sin Product Owner"
SIN_ENTREGABLE = "Sin entregable"
CON_ENTREGABLE_SIN_TIPO = "Con entregable (sin tipo)"
COLUMNAS_DIM = ["proyecto_id", "proyecto", "tipo_proyecto", "status", "sponsor", "product_owner"]

# Estados de las actividades, de abajo hacia arriba en la barra apilada del gráfico diario.
ESTADO_OTRO = "Otro"
ESTADOS_ORDEN = ["Done", "En curso", "Stuck", "Sin empezar", ESTADO_OTRO]
# Cada base escribe el estado a su manera ("Not Started", "In progress", "En curso"...).
# Se pueden ampliar desde config.yaml con la clave `estados`.
ESTADOS_EQUIV = {
    "Done": ["done", "complete", "completed", "completado", "hecho", "listo", "finalizado"],
    "En curso": ["en curso", "in progress", "en progreso", "doing"],
    "Stuck": ["stuck", "blocked", "bloqueado", "detenido"],
    "Sin empezar": ["sin empezar", "not started", "to-do", "to do", "todo", "por hacer",
                    "pendiente", "backlog"],
}


# ------------------------------------------------------------------ carga ---

def _bool(serie: pd.Series) -> pd.Series:
    return serie.fillna(False).astype(str).str.strip().str.lower().isin(["true", "1"])


def _exigir(df: pd.DataFrame, columnas: list[str], nombre: str) -> None:
    faltan = [c for c in columnas if c not in df.columns]
    if faltan:
        raise ValueError(f"{nombre}: faltan columnas {faltan}. Disponibles: {list(df.columns)}")


def preparar_historico(hist: pd.DataFrame) -> pd.DataFrame:
    _exigir(hist, COLUMNAS_FOTO, "historico")
    h = hist[COLUMNAS_FOTO].copy()
    h["fecha"] = h["fecha"].map(a_fecha)
    h["horas_hoy"] = pd.to_numeric(h["horas_hoy"], errors="coerce").fillna(0.0)
    h["tiene_entregable"] = _bool(h["tiene_entregable"])
    return h


def preparar_estado(estado: pd.DataFrame) -> pd.DataFrame:
    _exigir(estado, COLUMNAS_ESTADO, "estado_actual")
    e = estado[COLUMNAS_ESTADO].copy()
    e["horas_plan"] = pd.to_numeric(e["horas_plan"], errors="coerce")
    e["horas_reales"] = pd.to_numeric(e["horas_reales"], errors="coerce")
    e["tiene_entregable"] = _bool(e["tiene_entregable"])
    return e


def cargar_datos(raiz: Path, config: dict) -> dict[str, pd.DataFrame]:
    """Lee los 4 CSV. Los que no existen aún quedan como tablas vacías (el dashboard
    mostrará 'Sin datos' en lugar de fallar)."""
    processed = Path(raiz) / config["rutas"]["processed"]

    def leer(nombre: str, columnas: list[str]) -> pd.DataFrame:
        ruta = processed / nombre
        if not ruta.exists():
            return pd.DataFrame(columns=columnas)
        return pd.read_csv(ruta, dtype=str, encoding="utf-8-sig")

    return {
        "historico": leer("historico_horas_diarias.csv", COLUMNAS_FOTO),
        "estado": leer("estado_actual.csv", COLUMNAS_ESTADO),
        "registro": leer("registro_corridas.csv", COLUMNAS_REGISTRO),
        "dim": leer("dim_intervenciones.csv", COLUMNAS_DIM),
    }


# ---------------------------------------------------------------- utilidades ---

def _tokens(valor: object, sep: str = " | ") -> list[str]:
    """'A | B' -> ['A', 'B'] (vacío si no hay valor)."""
    if valor is None or pd.isna(valor):
        return []
    return [" ".join(t.split()) for t in str(valor).split(sep) if t.strip()]


def _mapa_tipo(dim: Optional[pd.DataFrame]) -> dict:
    if dim is None or dim.empty:
        return {}
    return dim.drop_duplicates("proyecto_id").set_index("proyecto_id")["tipo_proyecto"].to_dict()


def enriquecer_tipo(df: pd.DataFrame, dim: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Agrega tipo_proyecto (Ruta de Eficiencia / Modelo PM) vía proyecto_id.
    Las filas sin proyecto (p. ej. Tareas) quedan como 'Sin proyecto'."""
    out = df.copy()
    out["tipo_proyecto"] = out["proyecto_id"].map(_mapa_tipo(dim)).fillna(SIN_PROYECTO)
    return out


def _solo_equipo(h: pd.DataFrame) -> pd.DataFrame:
    return h[h["clasificacion"] == EN_EQUIPO]


# --------------------------------------------------------------------- días ---

def seleccionar_dias(registro: pd.DataFrame, equipo: Equipo, ini: object, fin: object) -> dict:
    """Clasifica los días del rango según el registro de corridas.

    con_foto       días con corrida exitosa
    laborables     con foto y capacidad > 0 -> los únicos que cuentan en los cálculos
    no_laborables  con foto pero sin capacidad (fin de semana o feriado): se ignoran
    sin_foto       días con capacidad > 0 en los que el script no corrió (alerta)
    """
    ini, fin = a_fecha(ini), a_fecha(fin)
    fechas: set = set()
    if registro is not None and not registro.empty and {"fecha", "ok"} <= set(registro.columns):
        fechas = {a_fecha(f) for f in registro.loc[_bool(registro["ok"]), "fecha"]}
    con_foto = sorted(d for d in fechas if ini <= d <= fin)
    con_capacidad = lambda d: equipo.horas_disponibles_equipo([d]) > 0  # noqa: E731
    return {
        "con_foto": con_foto,
        "laborables": [d for d in con_foto if con_capacidad(d)],
        "no_laborables": [d for d in con_foto if not con_capacidad(d)],
        "sin_foto": [d for d in dias_en_rango(ini, fin) if con_capacidad(d) and d not in fechas],
    }


def filtrar_historico(
    hist: pd.DataFrame,
    dias: Iterable[object],
    dim: Optional[pd.DataFrame] = None,
    personas: Optional[list[str]] = None,
    bases: Optional[list[str]] = None,
    tipos_proyecto: Optional[list[str]] = None,
) -> pd.DataFrame:
    """Histórico (ya preparado) restringido a los días y a los filtros del dashboard."""
    h = enriquecer_tipo(hist[hist["fecha"].isin({a_fecha(d) for d in dias})], dim)
    if personas is not None:
        h = h[h["persona_display"].isin(personas)]
    if bases is not None:
        h = h[h["base_origen"].isin(bases)]
    if tipos_proyecto is not None:
        h = h[h["tipo_proyecto"].isin(tipos_proyecto)]
    return h


# --------------------------------------------------------------- indicadores ---

def ocupabilidad(
    h: pd.DataFrame,
    equipo: Equipo,
    dias: Iterable[object],
    personas: Optional[list[str]] = None,
) -> dict:
    """Ocupabilidad del área y detalle por trabajador.

    `h` debe venir filtrado a `dias` (usar filtrar_historico con los días laborables).
    Cada integrante cuenta con su capacidad aunque no tenga horas (subcarga real).
    """
    dias = list(dias)
    miembros = [m for m in equipo.miembros_incluidos if personas is None or m.nombre in personas]
    por_persona = _solo_equipo(h).groupby("persona_display")["horas_hoy"].sum()
    filas = []
    for m in miembros:
        horas = float(por_persona.get(m.nombre, 0.0))
        disp = equipo.horas_disponibles_persona(m, dias)
        oc = Equipo.ocupabilidad(horas, disp)
        filas.append(
            {"persona": m.nombre, "condicion": m.condicion, "horas": horas,
             "disponibles": disp, "ocupabilidad": oc, "estado": equipo.estado_ocupabilidad(oc)}
        )
    detalle = pd.DataFrame(
        filas, columns=["persona", "condicion", "horas", "disponibles", "ocupabilidad", "estado"]
    )
    horas, disp = float(detalle["horas"].sum()), float(detalle["disponibles"].sum())
    oc = Equipo.ocupabilidad(horas, disp)
    return {
        "horas": horas,
        "disponibles": disp,
        "ocupabilidad": oc,
        "estado": equipo.estado_ocupabilidad(oc),
        "dias": len(dias),
        "detalle": detalle.sort_values("persona").reset_index(drop=True),
    }


def horas_por_proyecto(h: pd.DataFrame) -> pd.DataFrame:
    """Horas-persona por proyecto (solo Actividades & Entregables y Actividades PM)."""
    d = _solo_equipo(h)
    d = d[d["base_origen"].isin(BASES_PROYECTO)].copy()
    d["proyecto"] = d["proyecto"].fillna(SIN_PROYECTO)
    cols = ["proyecto_id", "proyecto", "tipo_proyecto", "horas"]
    if d.empty:
        return pd.DataFrame(columns=cols)
    res = (
        d.groupby(["proyecto_id", "proyecto", "tipo_proyecto"], dropna=False)["horas_hoy"]
        .sum().reset_index(name="horas")
    )
    return res.sort_values("horas", ascending=False).reset_index(drop=True)


def horas_programa(h: pd.DataFrame, programa: str = PROGRAMA_PM) -> dict:
    """Horas-persona de las filas cuyo programa incluye `programa` (tolera 'A | B')."""
    d = _solo_equipo(h)
    clave = programa.casefold()
    mascara = d["programa"].map(lambda v: clave in [t.casefold() for t in _tokens(v)]).astype(bool)
    sel = d[mascara]
    return {
        "programa": programa,
        "horas": float(sel["horas_hoy"].sum()),
        "por_base": sel.groupby("base_origen")["horas_hoy"].sum().reset_index(name="horas"),
        "por_persona": sel.groupby("persona_display")["horas_hoy"].sum()
        .reset_index(name="horas").rename(columns={"persona_display": "persona"}),
    }


def horas_por_po(h: pd.DataFrame, dim: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Horas por Product Owner y persona (sin Tareas Extracurriculares).

    El PO sale de Intervenciones por proyecto_id. Con dos PO, cada uno recibe las horas completas.
    """
    d = _solo_equipo(h)
    d = d[d["base_origen"].isin(BASES_PROYECTO)]
    po = (
        dim[["proyecto_id", "product_owner"]].drop_duplicates()
        if dim is not None and not dim.empty
        else pd.DataFrame(columns=["proyecto_id", "product_owner"])
    )
    m = d.merge(po, on="proyecto_id", how="left")
    m["product_owner"] = m["product_owner"].fillna(SIN_PO)
    res = (
        m.groupby(["product_owner", "persona_display"])["horas_hoy"].sum().reset_index()
        .rename(columns={"persona_display": "persona", "horas_hoy": "horas"})
    )
    return res.sort_values(["product_owner", "persona"]).reset_index(drop=True)


def matriz_po_persona(po: pd.DataFrame) -> pd.DataFrame:
    """Tabla PO x persona para el mapa de calor."""
    if po.empty:
        return pd.DataFrame()
    return po.pivot_table(index="product_owner", columns="persona", values="horas",
                          aggfunc="sum", fill_value=0.0)


def horas_por_tipo_entregable(h: pd.DataFrame) -> pd.DataFrame:
    """Promedio de horas por actividad, agrupado por tipo de entregable.

    Promedio = horas-persona / actividades distintas de ese tipo. Una actividad con dos tipos
    cuenta en ambos (las barras no suman el total). Sin tipo: 'Con entregable (sin tipo)' si la
    casilla está marcada, o 'Sin entregable'.
    """
    d = _solo_equipo(h)
    cols = ["tipo_entregable", "horas", "actividades", "promedio"]
    if d.empty:
        return pd.DataFrame(columns=cols)
    nombres: dict[str, str] = {}   # clave en minúsculas -> primer texto visto
    filas = []
    for r in d.itertuples(index=False):
        tipos = _tokens(r.tipos_entregable) or [
            CON_ENTREGABLE_SIN_TIPO if r.tiene_entregable else SIN_ENTREGABLE
        ]
        for t in tipos:
            filas.append((nombres.setdefault(t.casefold(), t),
                          f"{r.base_origen}|{r.actividad_id}", r.horas_hoy))
    base = pd.DataFrame(filas, columns=["tipo_entregable", "clave", "horas_hoy"])
    res = base.groupby("tipo_entregable").agg(
        horas=("horas_hoy", "sum"), actividades=("clave", "nunique")
    ).reset_index()
    res["promedio"] = res["horas"] / res["actividades"]
    return res.sort_values("promedio", ascending=False).reset_index(drop=True)[cols]


def exactitud_planificacion(
    estado: pd.DataFrame,
    bases: Optional[list[str]] = None,
    tipos_proyecto: Optional[list[str]] = None,
    personas: Optional[list[str]] = None,
    dim: Optional[pd.DataFrame] = None,
    solo_iniciadas: bool = False,
) -> dict:
    """SUM(horas_plan) / SUM(horas_reales) sobre el estado actual (acumulado, sin rango).

    Se excluyen las actividades sin plan (vacío o 0). Con solo_iniciadas=True se excluyen además
    las que aún no tienen horas reales; si no, una actividad planificada pero no iniciada sube el
    cociente. None si no hay horas reales.
    """
    e = estado
    if bases is not None:
        e = e[e["base_origen"].isin(bases)]
    if tipos_proyecto is not None:
        e = enriquecer_tipo(e, dim)
        e = e[e["tipo_proyecto"].isin(tipos_proyecto)]
    if personas is not None:
        sel = set(personas)
        e = e[e["personas"].map(lambda s: bool(sel & set(_tokens(s, "; "))))]

    con_plan = e[e["horas_plan"] > 0]
    excluidas = len(e) - len(con_plan)
    if solo_iniciadas:
        con_plan = con_plan[con_plan["horas_reales"].fillna(0) > 0]
    plan = float(con_plan["horas_plan"].sum())
    real = float(con_plan["horas_reales"].fillna(0).sum())

    d = con_plan.assign(proyecto=con_plan["proyecto"].fillna(SIN_PROYECTO),
                        horas_reales=con_plan["horas_reales"].fillna(0))
    detalle = d.groupby("proyecto").agg(
        plan=("horas_plan", "sum"), real=("horas_reales", "sum"), actividades=("actividad_id", "size")
    ).reset_index()
    detalle["exactitud"] = [p / r if r > 0 else None for p, r in zip(detalle["plan"], detalle["real"])]
    return {
        "horas_plan": plan,
        "horas_reales": real,
        "exactitud": plan / real if real > 0 else None,
        "actividades": len(con_plan),
        "excluidas_sin_plan": excluidas,
        "detalle": detalle.sort_values("plan", ascending=False).reset_index(drop=True),
    }


def exactitud_proyectos(estado: pd.DataFrame, bases: Optional[list[str]] = None, **kw) -> dict:
    """Exactitud solo de proyectos (Actividades & Entregables y Actividades PM)."""
    efectivas = [b for b in BASES_PROYECTO if bases is None or b in bases]
    return exactitud_planificacion(estado, bases=efectivas, **kw)


# ------------------------------------------------------- ocupabilidad diaria ---

def construir_tabla_estados(equivalencias: Optional[dict] = None) -> dict[str, str]:
    """texto en minúsculas -> estado canónico. `equivalencias` amplía (no reemplaza) las de fábrica."""
    tabla: dict[str, str] = {}
    fuentes = [ESTADOS_EQUIV, equivalencias or {}]
    for fuente in fuentes:
        for canonico, nombres in fuente.items():
            if canonico not in ESTADOS_ORDEN or canonico == ESTADO_OTRO:
                raise ValueError(
                    f"Estado desconocido '{canonico}' en la configuración. "
                    f"Válidos: {[e for e in ESTADOS_ORDEN if e != ESTADO_OTRO]}"
                )
            tabla[canonico.casefold()] = canonico
            for n in nombres:
                tabla[" ".join(str(n).split()).casefold()] = canonico
    return tabla


def normalizar_estado(valor: object, tabla: Optional[dict[str, str]] = None) -> str:
    """'In progress' / 'En curso' -> 'En curso', etc. Lo que no se reconoce queda como 'Otro'."""
    if valor is None or pd.isna(valor):
        return ESTADO_OTRO
    clave = " ".join(str(valor).split()).casefold()
    return (tabla or construir_tabla_estados()).get(clave, ESTADO_OTRO)


def ocupabilidad_diaria(
    h: pd.DataFrame,
    equipo: Equipo,
    dia: Optional[object],
    personas: Optional[list[str]] = None,
    equivalencias: Optional[dict] = None,
) -> dict:
    """Horas asignadas en UN día, por integrante y apiladas por estado de la actividad.

    Es el equivalente al gráfico «Status de Ocupabilidad Daily» de Notion, más la capacidad del día
    de cada persona. Todos los integrantes aparecen, también con 0 horas (para ver quién no registró).
    `h` es el histórico ya preparado; se toma solo el `dia` pedido. No filtra por día laborable:
    un sábado o un feriado tiene capacidad 0 y su ocupabilidad sale como 'Sin datos'.
    """
    cols_res = ["persona", "condicion", *ESTADOS_ORDEN, "total", "capacidad", "ocupabilidad",
                "estado_carga"]
    vacio = {
        "dia": None, "largo": pd.DataFrame(columns=["persona", "estado", "horas", "orden"]),
        "resumen": pd.DataFrame(columns=cols_res), "horas": 0.0, "capacidad": 0.0,
        "ocupabilidad": None, "estado": equipo.estado_ocupabilidad(None),
        "estados_sin_clasificar": [],
    }
    if dia is None:
        return vacio
    dia = a_fecha(dia)
    tabla = construir_tabla_estados(equivalencias)
    miembros = [m for m in equipo.miembros_incluidos if personas is None or m.nombre in personas]

    he = _solo_equipo(h)
    he = he[he["fecha"] == dia].copy()
    he["estado_norm"] = he["estado"].map(lambda e: normalizar_estado(e, tabla))
    sin_clasificar = sorted({str(e) for e, n in zip(he["estado"], he["estado_norm"])
                             if n == ESTADO_OTRO and not pd.isna(e)})
    if (he["estado"].isna() & (he["horas_hoy"] > 0)).any():
        sin_clasificar.append("(vacío)")
    por = he.groupby(["persona_display", "estado_norm"])["horas_hoy"].sum()

    filas, largo = [], []
    for m in miembros:
        horas = {e: float(por.get((m.nombre, e), 0.0)) for e in ESTADOS_ORDEN}
        total = sum(horas.values())
        capacidad = equipo.horas_dia(m.condicion, dia)
        oc = Equipo.ocupabilidad(total, capacidad)
        filas.append({"persona": m.nombre, "condicion": m.condicion, **horas, "total": total,
                      "capacidad": capacidad, "ocupabilidad": oc,
                      "estado_carga": equipo.estado_ocupabilidad(oc)})
        largo += [{"persona": m.nombre, "estado": e, "horas": v, "orden": ESTADOS_ORDEN.index(e)}
                  for e, v in horas.items() if v > 0]
    resumen = pd.DataFrame(filas, columns=cols_res).sort_values(
        ["total", "persona"], ascending=[False, True]).reset_index(drop=True)
    total, capacidad = float(resumen["total"].sum()), float(resumen["capacidad"].sum())
    oc = Equipo.ocupabilidad(total, capacidad)
    return {
        "dia": dia,
        "largo": pd.DataFrame(largo, columns=["persona", "estado", "horas", "orden"]),
        "resumen": resumen,
        "horas": total,
        "capacidad": capacidad,
        "ocupabilidad": oc,
        "estado": equipo.estado_ocupabilidad(oc),
        "estados_sin_clasificar": sin_clasificar,
    }


# ------------------------------------------------------------------ alertas ---

def alertas(h: pd.DataFrame, equipo: Equipo, dias: dict, umbral_horas_dia: float = 9.0) -> dict:
    """Calidad de datos. `h` debe cubrir todos los días con foto del rango (sin filtros).

    desconocidos          personas no reconocidas con horas (incluye "Sin asignar")
    sin_horas             integrante sin horas un día laborable con foto
    exceso_horas_dia      persona con más de `umbral_horas_dia` horas en un día
    dias_sin_foto         día laborable en que el script no corrió
    dias_no_laborables    días con foto que se ignoran (fin de semana o feriado)
    """
    desc = h[h["clasificacion"] == DESCONOCIDO]
    desconocidos = (
        desc.groupby("persona").agg(
            horas=("horas_hoy", "sum"), filas=("horas_hoy", "size"), dias=("fecha", "nunique")
        ).reset_index().sort_values("horas", ascending=False).reset_index(drop=True)
    )
    por_dia = _solo_equipo(h).groupby(["fecha", "persona_display"])["horas_hoy"].sum()
    exceso = por_dia[por_dia > umbral_horas_dia].reset_index(name="horas")
    exceso = exceso.rename(columns={"persona_display": "persona"})
    registrado = set(por_dia[por_dia > 0].index)
    sin_horas = [
        {"fecha": d, "persona": m.nombre}
        for d in dias["laborables"]
        for m in equipo.miembros_incluidos
        if (d, m.nombre) not in registrado
    ]
    return {
        "desconocidos": desconocidos,
        "sin_horas": pd.DataFrame(sin_horas, columns=["fecha", "persona"]),
        "exceso_horas_dia": exceso.reset_index(drop=True),
        "dias_sin_foto": list(dias["sin_foto"]),
        "dias_no_laborables": list(dias["no_laborables"]),
    }


# ----------------------------------------------------------------- conjunto ---

def calcular_todo(
    datos: dict[str, pd.DataFrame],
    equipo: Equipo,
    ini: object,
    fin: object,
    config: Optional[dict] = None,
    personas: Optional[list[str]] = None,
    bases: Optional[list[str]] = None,
    tipos_proyecto: Optional[list[str]] = None,
    solo_iniciadas: bool = False,
    dia: Optional[object] = None,
) -> dict:
    """Todos los indicadores del dashboard para un rango y unos filtros. Lo usa Streamlit.

    `dia` es el día del detalle diario (por defecto, la última foto del rango).

    Nota sobre los filtros: `personas` también reduce el denominador de la ocupabilidad a esas
    personas; `bases` y `tipos_proyecto` solo restringen las horas (la capacidad se mantiene).
    """
    config = config or {}
    hist = preparar_historico(datos["historico"])
    estado = preparar_estado(datos["estado"])
    dim = datos.get("dim")

    dias = seleccionar_dias(datos["registro"], equipo, ini, fin)
    h = filtrar_historico(hist, dias["laborables"], dim, personas, bases, tipos_proyecto)
    h_alertas = hist[hist["fecha"].isin(set(dias["con_foto"]))]
    filtros_estado = {"dim": dim, "tipos_proyecto": tipos_proyecto, "personas": personas,
                      "solo_iniciadas": solo_iniciadas}
    po = horas_por_po(h, dim)
    if dia is None and dias["con_foto"]:
        dia = dias["con_foto"][-1]
    h_dia = (filtrar_historico(hist, [dia], dim, personas, bases, tipos_proyecto)
             if dia is not None else hist.iloc[0:0])
    return {
        "dias": dias,
        "diaria": ocupabilidad_diaria(h_dia, equipo, dia, personas, config.get("estados")),
        "ocupabilidad": ocupabilidad(h, equipo, dias["laborables"], personas),
        "horas_por_proyecto": horas_por_proyecto(h),
        "programa_pm": horas_programa(h, config.get("programa_pm", PROGRAMA_PM)),
        "horas_por_po": po,
        "matriz_po": matriz_po_persona(po),
        "tipo_entregable": horas_por_tipo_entregable(h),
        "exactitud_global": exactitud_planificacion(estado, bases=bases, **filtros_estado),
        "exactitud_proyectos": exactitud_proyectos(estado, bases=bases, **filtros_estado),
        "alertas": alertas(h_alertas, equipo, dias, float(config.get("alerta_horas_dia", 9))),
    }
