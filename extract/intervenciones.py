"""Extractor de la base Intervenciones (dimensión de proyectos).

Salida: una fila por (proyecto, Product Owner). Si un proyecto tiene dos PO,
genera dos filas con el mismo proyecto_id; así las horas se pueden asignar
completas a cada PO (regla acordada).

Uso:
    python -m extract.intervenciones
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from extract.notion_client import (
    NotionClient,
    RAIZ,
    cargar_config,
    lista,
    titulo_pagina,
    valor_propiedad,
)

SIN_PO = "Sin Product Owner"
COLUMNAS_SALIDA = [
    "proyecto_id",
    "proyecto",
    "tipo_proyecto",
    "status",
    "sponsor",
    "product_owner",
]


def _limpiar(nombres: list[str]) -> list[str]:
    """strip + elimina vacíos y duplicados conservando el orden."""
    vistos: dict[str, None] = {}
    for n in nombres:
        n = " ".join(str(n).split())
        if n:
            vistos.setdefault(n, None)
    return list(vistos)


def paginas_a_dataframe(paginas: list[dict], cols: dict) -> pd.DataFrame:
    """Convierte páginas de Notion (Intervenciones) en el DataFrame de dimensión."""
    filas = []
    for p in paginas:
        if p.get("archived") or p.get("in_trash"):
            continue
        props = p["properties"]
        proyecto = valor_propiedad(props.get(cols["proyecto"])) or titulo_pagina(p)
        pos = _limpiar(lista(valor_propiedad(props.get(cols["product_owner"])))) or [SIN_PO]
        sponsors = _limpiar(lista(valor_propiedad(props.get(cols["sponsor"]))))
        base = {
            "proyecto_id": p["id"],
            "proyecto": proyecto,
            "tipo_proyecto": valor_propiedad(props.get(cols["tipo_proyecto"])),
            "status": valor_propiedad(props.get(cols["status"])),
            "sponsor": "; ".join(sponsors) or None,
        }
        for po in pos:
            filas.append({**base, "product_owner": po})
    return pd.DataFrame(filas, columns=COLUMNAS_SALIDA)


def extraer(cliente: NotionClient, config: dict) -> pd.DataFrame:
    db_id = config["notion"]["databases"]["intervenciones"]
    if not db_id:
        raise RuntimeError("Falta el ID de 'intervenciones' en config.yaml.")
    paginas = list(cliente.query_database(db_id))
    return paginas_a_dataframe(paginas, config["columnas"]["intervenciones"])


def main() -> None:
    config = cargar_config()
    cliente = NotionClient(version=config["notion"]["version"])
    df = extraer(cliente, config)

    salida = Path(RAIZ / config["rutas"]["processed"])
    salida.mkdir(parents=True, exist_ok=True)
    destino = salida / "dim_intervenciones.csv"
    df.to_csv(destino, index=False, encoding="utf-8-sig")

    print(f"{df['proyecto_id'].nunique()} proyectos, {len(df)} filas proyecto×PO -> {destino}")
    sin_po = df[df["product_owner"] == SIN_PO]["proyecto"].tolist()
    if sin_po:
        print(f"Aviso: proyectos sin Product Owner: {sin_po}")


if __name__ == "__main__":
    main()
