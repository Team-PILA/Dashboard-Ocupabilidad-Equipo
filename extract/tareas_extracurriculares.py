"""Extractor de la base Tareas Extracurriculares.

Salida: una fila por (tarea, persona); con 2 personas se repiten las horas completas.
`programa` se resuelve consultando la base Programas (id -> título).

Uso:
    python -m extract.tareas_extracurriculares
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

BASE_ORIGEN = "Tareas"
REQUERIDAS = ["actividad", "personas", "horas_hoy", "horas_plan", "horas_reales",
              "proyecto", "programa", "tiene_entregable", "estado"]


def paginas_a_dataframe(
    paginas: list[dict], cols: dict, programas: dict[str, str] | None = None
) -> pd.DataFrame:
    """Convierte páginas de Tareas Extracurriculares. `programas` mapea id -> título."""
    programas = programas or {}
    paginas = [p for p in paginas if not comun.archivada(p)]
    comun.validar_columnas(paginas, cols, REQUERIDAS)
    filas = []
    for p in paginas:
        props = p["properties"]
        actividad = valor_propiedad(props.get(cols["actividad"])) or titulo_pagina(p)
        ids_prog = lista(valor_propiedad(props.get(cols["programa"])))
        nombres_prog = [programas.get(i) for i in ids_prog]
        if any(n is None for n in nombres_prog):
            faltan = [i for i, n in zip(ids_prog, nombres_prog) if n is None]
            raise ValueError(f"Programa(s) {faltan} de '{actividad}' no existen en la base Programas.")
        base = {
            "base_origen": BASE_ORIGEN,
            "actividad_id": p["id"],
            "actividad": actividad,
            "horas_hoy": comun.a_numero(valor_propiedad(props.get(cols["horas_hoy"]))) or 0.0,
            "horas_plan": comun.a_numero(valor_propiedad(props.get(cols["horas_plan"]))),
            "horas_reales": comun.a_numero(valor_propiedad(props.get(cols["horas_reales"]))),
            "proyecto_id": None,
            "proyecto": comun.unir(comun.normalizar_tipos(valor_propiedad(props.get(cols["proyecto"])))),
            "programa": comun.unir(list(dict.fromkeys(nombres_prog))),
            "tipos_entregable": None,
            "tiene_entregable": bool(valor_propiedad(props.get(cols["tiene_entregable"]))),
            "estado": valor_propiedad(props.get(cols["estado"])),
        }
        for persona in comun.personas(valor_propiedad(props.get(cols["personas"]))):
            filas.append({**base, "persona": persona})
    return pd.DataFrame(filas, columns=comun.COLUMNAS_SALIDA)


def cargar_programas(cliente: NotionClient, config: dict) -> dict[str, str]:
    db_id = config["notion"]["databases"]["programas"]
    if not db_id:
        raise RuntimeError("Falta el ID de 'programas' en config.yaml.")
    return {
        p["id"]: titulo_pagina(p)
        for p in cliente.query_database(db_id)
        if not comun.archivada(p)
    }


def extraer(cliente: NotionClient, config: dict) -> pd.DataFrame:
    db_id = config["notion"]["databases"]["tareas_extracurriculares"]
    if not db_id:
        raise RuntimeError("Falta el ID de 'tareas_extracurriculares' en config.yaml.")
    paginas = list(cliente.query_database(db_id))
    return paginas_a_dataframe(
        paginas, config["columnas"]["tareas_extracurriculares"], cargar_programas(cliente, config)
    )


def main() -> None:
    config = cargar_config()
    cliente = NotionClient(version=config["notion"]["version"])
    df = extraer(cliente, config)
    comun.resumen(df, comun.guardar(df, config, "tareas_extracurriculares"))


if __name__ == "__main__":
    main()
