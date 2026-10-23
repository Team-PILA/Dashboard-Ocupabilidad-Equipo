"""Lógica de presentación del dashboard (sin Streamlit ni Altair, para poder probarla).

Aquí viven los formatos, las tablas que se muestran, las opciones de los filtros y los avisos.
`app.py` solo dibuja lo que esta capa le entrega.
"""
from __future__ import annotations

import html
import math
from datetime import date, timedelta
from typing import Optional

import pandas as pd

import kpis
from capacidad import Equipo, a_fecha

# Estado de carga: color de estado (nunca va solo: siempre con icono y texto).
ICONOS = {"SOBRECARGA": "▲", "CARGA ADECUADA": "●", "SUBCARGA": "▼", "Sin datos": "–"}
COLOR_ESTADO = {
    "SUBCARGA": "#fab219",       # warning
    "CARGA ADECUADA": "#0ca30c",  # good
    "SOBRECARGA": "#d03b3b",     # critical
    "Sin datos": "#898781",      # neutro
}
SIN_DATO = "—"

# Estado de la actividad en el gráfico diario. Done y Stuck son estados "de verdad" (verde y rojo
# de estado); En curso usa el azul de la paleta y Sin empezar un gris neutro. Siempre hay leyenda.
COLOR_ESTADO_TAREA = {
    "Done": "#0ca30c",
    "En curso": "#2a78d6",
    "Stuck": "#d03b3b",
    "Sin empezar": "#c3c2b7",
    "Otro": "#898781",
}


def _vacio(x: object) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


# ----------------------------------------------------------------- formatos ---

def fmt_pct(x: Optional[float], decimales: int = 0) -> str:
    return SIN_DATO if _vacio(x) else f"{x * 100:.{decimales}f}%"


def fmt_horas(x: Optional[float]) -> str:
    return SIN_DATO if _vacio(x) else f"{x:,.1f} h"


def fmt_fecha(d: object) -> str:
    return a_fecha(d).strftime("%d/%m/%Y")


def etiqueta_estado(estado: str) -> str:
    return f"{ICONOS.get(estado, '–')} {estado}"


def html_hero(valor: str, estado: str) -> str:
    """Cifra grande de la tarjeta principal + estado con icono y texto (el color es solo un acento)."""
    color = COLOR_ESTADO.get(estado, COLOR_ESTADO["Sin datos"])
    icono = ICONOS.get(estado, "–")
    return (
        f'<div style="font-size:3.4rem;font-weight:600;line-height:1.05">{html.escape(valor)}</div>'
        f'<div style="margin-top:.3rem"><span style="color:{color};font-size:1.1rem">{icono}</span> '
        f"<b>{html.escape(estado)}</b></div>"
    )


# ------------------------------------------------------------------- tablas ---

def df_trabajadores(detalle: pd.DataFrame) -> pd.DataFrame:
    """Tabla 'Ocupabilidad por trabajador' (la versión en texto del gráfico)."""
    d = detalle.copy()
    d["_orden"] = d["ocupabilidad"].fillna(-1)
    d = d.sort_values(["_orden", "persona"], ascending=[False, True])
    return pd.DataFrame({
        "Persona": d["persona"],
        "Condición": d["condicion"],
        "Horas": d["horas"].round(2),
        "Disponibles": d["disponibles"].round(2),
        "Ocupabilidad": d["ocupabilidad"].map(fmt_pct),
        "Estado": d["estado"].map(etiqueta_estado),
    }).reset_index(drop=True)


def df_grafico_trabajadores(detalle: pd.DataFrame) -> pd.DataFrame:
    """Datos del gráfico de barras por trabajador (ocupabilidad en %, ordenada de mayor a menor)."""
    d = detalle.copy()
    d["pct"] = d["ocupabilidad"].fillna(0.0) * 100
    d["etiqueta"] = d["ocupabilidad"].map(fmt_pct)
    d = d.sort_values(["pct", "persona"], ascending=[False, True])
    return d[["persona", "condicion", "horas", "disponibles", "pct", "etiqueta", "estado"]].reset_index(drop=True)


def tabla_exactitud(detalle: pd.DataFrame) -> pd.DataFrame:
    """Tabla de exactitud por proyecto: plan, real y cociente ('—' si no hay horas reales)."""
    if detalle.empty:
        return pd.DataFrame(columns=["Proyecto", "Actividades", "Plan (h)", "Real (h)", "Exactitud"])
    d = detalle.sort_values("plan", ascending=False)
    return pd.DataFrame({
        "Proyecto": d["proyecto"],
        "Actividades": d["actividades"],
        "Plan (h)": d["plan"].round(1),
        "Real (h)": d["real"].round(1),
        "Exactitud": d["exactitud"].map(fmt_pct),
    }).reset_index(drop=True)


def alertas_para_mostrar(a: dict) -> dict[str, pd.DataFrame]:
    """Tablas de alertas con fechas legibles."""
    desc = a["desconocidos"]
    sin = a["sin_horas"]
    exc = a["exceso_horas_dia"]
    return {
        "desconocidos": pd.DataFrame({
            "Persona": desc["persona"], "Horas": desc["horas"],
            "Filas": desc["filas"], "Días": desc["dias"],
        }),
        "sin_horas": pd.DataFrame({
            "Día": sin["fecha"].map(fmt_fecha), "Persona": sin["persona"],
        }),
        "exceso_horas_dia": pd.DataFrame({
            "Día": exc["fecha"].map(fmt_fecha), "Persona": exc["persona"], "Horas": exc["horas"],
        }),
        "dias_sin_foto": pd.DataFrame({"Día": [fmt_fecha(d) for d in a["dias_sin_foto"]]}),
    }


def n_alertas(a: dict) -> int:
    return (len(a["desconocidos"]) + len(a["sin_horas"]) + len(a["exceso_horas_dia"])
            + len(a["dias_sin_foto"]))


# ------------------------------------------------------------------ filtros ---

def opciones_filtros(equipo: Equipo, dim: Optional[pd.DataFrame]) -> dict[str, list[str]]:
    tipos = []
    if dim is not None and not dim.empty and "tipo_proyecto" in dim.columns:
        tipos = sorted(dim["tipo_proyecto"].dropna().unique())
    return {
        "personas": [m.nombre for m in equipo.miembros_incluidos],
        "tipos": list(tipos) + [kpis.SIN_PROYECTO],
    }


def valor_filtro(seleccion: list[str], todas: list[str]) -> Optional[list[str]]:
    """None si están todas seleccionadas (equivale a no filtrar), si no la lista elegida."""
    return None if set(seleccion) == set(todas) else list(seleccion)


def _fechas_ok(registro: pd.DataFrame) -> list[date]:
    if registro is None or registro.empty or not {"fecha", "ok"} <= set(registro.columns):
        return []
    return sorted({a_fecha(f) for f in registro.loc[kpis._bool(registro["ok"]), "fecha"]})


def fechas_con_foto(registro: pd.DataFrame, ini: object = None, fin: object = None) -> list[date]:
    """Días con foto (opcionalmente dentro de un rango), de más antiguo a más reciente."""
    fechas = _fechas_ok(registro)
    if ini is not None:
        fechas = [d for d in fechas if d >= a_fecha(ini)]
    if fin is not None:
        fechas = [d for d in fechas if d <= a_fecha(fin)]
    return fechas


def df_diaria_totales(resumen: pd.DataFrame) -> pd.DataFrame:
    """Un renglón por integrante: total del día, capacidad y rótulo sobre la barra."""
    d = resumen.copy()
    d["etiqueta"] = d["total"].map(lambda x: f"{x:g} h")
    d["ocup_etiqueta"] = d["ocupabilidad"].map(fmt_pct)
    return d[["persona", "total", "capacidad", "etiqueta", "ocup_etiqueta", "estado_carga"]].reset_index(drop=True)


def tabla_diaria(resumen: pd.DataFrame) -> pd.DataFrame:
    """Versión en tabla del gráfico diario (la columna «Otro» solo aparece si tiene horas)."""
    estados = [e for e in COLOR_ESTADO_TAREA if e != "Otro" or (resumen["Otro"] > 0).any()]
    t = pd.DataFrame({"Persona": resumen["persona"], "Condición": resumen["condicion"]})
    for e in estados:
        t[e] = resumen[e].round(2)
    t["Total (h)"] = resumen["total"].round(2)
    t["Capacidad (h)"] = resumen["capacidad"].round(2)
    t["Ocupabilidad"] = resumen["ocupabilidad"].map(fmt_pct)
    t["Estado"] = resumen["estado_carga"].map(etiqueta_estado)
    return t.reset_index(drop=True)


def aviso_estados(sin_clasificar: list[str]) -> Optional[str]:
    if not sin_clasificar:
        return None
    return ("Estados que no se reconocieron (se muestran como «Otro»): "
            + ", ".join(f"«{e}»" for e in sin_clasificar)
            + ". Se pueden agregar en `config.yaml`, clave `estados`.")


def rango_por_defecto(registro: pd.DataFrame, hoy: date) -> tuple[date, date]:
    fechas = _fechas_ok(registro)
    return (fechas[0], fechas[-1]) if fechas else (hoy, hoy)


# ------------------------------------------------------------------- avisos ---

def ultima_corrida(registro: pd.DataFrame) -> Optional[dict]:
    fechas = _fechas_ok(registro)
    if not fechas:
        return None
    ultima = fechas[-1]
    fila = registro[registro["fecha"].map(a_fecha) == ultima].iloc[-1]
    ejecutado = fila["ejecutado_en"] if "ejecutado_en" in registro.columns else None
    return {"fecha": ultima, "ejecutado_en": None if _vacio(ejecutado) else str(ejecutado)}


def dia_laborable_anterior(equipo: Equipo, hoy: date) -> Optional[date]:
    for i in range(1, 15):
        d = hoy - timedelta(days=i)
        if equipo.horas_disponibles_equipo([d]) > 0:
            return d
    return None


def aviso_ultima_foto(ultima: Optional[dict], equipo: Equipo, hoy: date) -> Optional[str]:
    """Avisa si falta la foto del último día laborable (hoy no cuenta: la foto se toma a las 5:30 pm)."""
    if ultima is None:
        return "Todavía no hay fotos diarias. Ejecuta `python build_master.py --extraer`."
    previo = dia_laborable_anterior(equipo, hoy)
    if previo is not None and ultima["fecha"] < previo:
        return (f"Falta la foto del {fmt_fecha(previo)}. La última disponible es del "
                f"{fmt_fecha(ultima['fecha'])}.")
    return None
