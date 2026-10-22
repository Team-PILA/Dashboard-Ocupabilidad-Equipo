"""Extractor de la base Actividades & Entregables (Ruta).

Salida: una fila por (actividad, persona); con 2 personas se repiten las horas completas.

Uso:
    python -m extract.actividades_entregables
"""
from __future__ import annotations

import pandas as pd

from extract import comun
from extract.notion_client import (
    NotionClient,
    cargar_config,
    lista,
    titulo_pagina,
    valor_propiedad,
)

BASE_ORIGEN = "Ruta"
REQUERIDAS = ["personas", "horas_hoy", "horas_plan", "horas_reales", "proyecto",
              "tipos_entregable", "estado"]


def paginas_a_dataframe(
    paginas: list[dict], cols: dict, proyectos: dict[str, str] | None = None
) -> pd.DataFrame:
    """Convierte páginas de Actividades & Entregables. `proyectos` mapea proyecto_id -> nombre."""
    proyectos = proyectos or {}
    paginas = [p for p in paginas if not comun.archivada(p)]
    comun.validar_columnas(paginas, cols, REQUERIDAS)
    filas = []
    for p in paginas:
        props = p["properties"]
        rel = lista(valor_propiedad(props.get(cols["proyecto"])))
        proyecto_id = rel[0] if rel else None
        tipos = comun.normalizar_tipos(valor_propiedad(props.get(cols["tipos_entregable"])))
        base = {
            "base_origen": BASE_ORIGEN,
            "actividad_id": p["id"],
            "actividad": titulo_pagina(p),
            "horas_hoy": comun.a_numero(valor_propiedad(props.get(cols["horas_hoy"]))) or 0.0,
            "horas_plan": comun.a_numero(valor_propiedad(props.get(cols["horas_plan"]))),
            "horas_reales": comun.a_numero(valor_propiedad(props.get(cols["horas_reales"]))),
            "proyecto_id": proyecto_id,
            "proyecto": proyectos.get(proyecto_id),
            "programa": None,
            "tipos_entregable": comun.unir(tipos),
            "tiene_entregable": bool(tipos),
            "estado": valor_propiedad(props.get(cols["estado"])),
        }
        for persona in comun.personas(valor_propiedad(props.get(cols["personas"]))):
            filas.append({**base, "persona": persona})
    return pd.DataFrame(filas, columns=comun.COLUMNAS_SALIDA)


def extraer(cliente: NotionClient, config: dict) -> pd.DataFrame:
    db_id = config["notion"]["databases"]["actividades_entregables"]
    if not db_id:
        raise RuntimeError("Falta el ID de 'actividades_entregables' en config.yaml.")
    paginas = list(cliente.query_database(db_id))
    return paginas_a_dataframe(
        paginas, config["columnas"]["actividades_entregables"], comun.cargar_proyectos(config)
    )


def main() -> None:
    config = cargar_config()
    cliente = NotionClient(version=config["notion"]["version"])
    df = extraer(cliente, config)
    comun.resumen(df, comun.guardar(df, config, "actividades_entregables"))


if __name__ == "__main__":
    main()
