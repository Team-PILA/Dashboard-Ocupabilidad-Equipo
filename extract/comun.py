"""Utilidades compartidas por los extractores de actividades (PM, A&E, Tareas).

Salida común: una fila por (actividad, persona) con las horas completas.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from extract.notion_client import RAIZ, lista

SIN_ASIGNAR = "Sin asignar"
SEPARADOR = " | "
COLUMNAS_SALIDA = [
    "base_origen",
    "actividad_id",
    "actividad",
    "persona",
    "horas_hoy",
    "horas_plan",
    "horas_reales",
    "proyecto_id",
    "proyecto",
    "programa",
    "tipos_entregable",
    "tiene_entregable",
    "estado",
]


def archivada(pagina: dict) -> bool:
    return bool(pagina.get("archived") or pagina.get("in_trash"))


def validar_columnas(paginas: list[dict], cols: dict, requeridas: Iterable[str]) -> None:
    """Falla si una columna configurada no existe en la base (nunca ceros en silencio)."""
    if not paginas:
        return
    disponibles = set()
    for p in paginas:
        disponibles.update(p["properties"])
    faltan = [(k, cols.get(k)) for k in requeridas if cols.get(k) not in disponibles]
    if faltan:
        detalle = ", ".join(f"{k}='{n}'" for k, n in faltan)
        raise ValueError(
            f"Columnas no encontradas en la base: {detalle}. "
            f"Columnas disponibles: {sorted(disponibles)}"
        )


def a_numero(valor: Any) -> float | None:
    """Número de una propiedad (número, fórmula o rollup). Vacío -> None."""
    if isinstance(valor, list):
        nums = [a_numero(v) for v in valor]
        nums = [n for n in nums if n is not None]
        return float(sum(nums)) if nums else None
    if valor is None or valor == "":
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def personas(valor: Any) -> list[str]:
    """Nombres tal como los devuelve Notion (sin normalizar), sin repetidos."""
    vistos: dict[str, None] = {}
    for n in lista(valor):
        n = str(n).strip()
        if n:
            vistos.setdefault(n, None)
    return list(vistos) or [SIN_ASIGNAR]


def normalizar_tipos(valor: Any) -> list[str]:
    """strip + espacios colapsados + sin duplicados (ignorando mayúsculas)."""
    vistos: dict[str, str] = {}
    for t in lista(valor):
        t = " ".join(str(t).split())
        if t:
            vistos.setdefault(t.casefold(), t)
    return list(vistos.values())


def unir(valores: list[str]) -> str | None:
    return SEPARADOR.join(valores) or None


def guardar(df: pd.DataFrame, config: dict, nombre: str) -> Path:
    salida = Path(RAIZ / config["rutas"]["processed"])
    salida.mkdir(parents=True, exist_ok=True)
    destino = salida / f"{nombre}.csv"
    df.to_csv(destino, index=False, encoding="utf-8-sig")
    return destino


def cargar_proyectos(config: dict) -> dict[str, str]:
    """proyecto_id -> proyecto desde dim_intervenciones.csv (primer match)."""
    ruta = Path(RAIZ / config["rutas"]["processed"]) / "dim_intervenciones.csv"
    if not ruta.exists():
        raise RuntimeError(
            f"No existe {ruta}. Ejecuta primero: python -m extract.intervenciones"
        )
    dim = pd.read_csv(ruta, encoding="utf-8-sig")
    return dict(dim.drop_duplicates("proyecto_id", keep="first")[["proyecto_id", "proyecto"]].values)


def resumen(df: pd.DataFrame, destino: Path) -> None:
    sin = int((df["persona"] == SIN_ASIGNAR).sum())
    print(
        f"{len(df)} filas, {df['actividad_id'].nunique()} actividades, "
        f"horas_hoy={df['horas_hoy'].sum():g}, sin asignar={sin} -> {destino}"
    )
