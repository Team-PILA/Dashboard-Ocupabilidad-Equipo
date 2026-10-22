"""Cliente mínimo de la API de Notion: paginación, reintentos y parseo de propiedades.

Todas las bases (de cualquier teamspace) se consultan igual: por su ID.
Lo único necesario es que la integración tenga acceso a cada base.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Iterator

import requests
import yaml
from dotenv import load_dotenv

API_URL = "https://api.notion.com/v1"
RAIZ = Path(__file__).resolve().parent.parent


def cargar_config(ruta: Path | None = None) -> dict:
    with open(ruta or RAIZ / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


class NotionClient:
    def __init__(self, token: str | None = None, version: str = "2022-06-28"):
        load_dotenv(RAIZ / ".env")
        self.token = token or os.environ.get("NOTION_TOKEN")
        if not self.token:
            raise RuntimeError("Falta NOTION_TOKEN (defínelo en .env).")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.token}",
                "Notion-Version": version,
                "Content-Type": "application/json",
            }
        )

    def _post(self, url: str, payload: dict, intentos: int = 5) -> dict:
        for intento in range(intentos):
            resp = self.session.post(url, json=payload, timeout=30)
            if resp.status_code == 429:  # límite de tasa: respetar Retry-After
                time.sleep(float(resp.headers.get("Retry-After", 1)))
                continue
            if resp.status_code >= 500:
                time.sleep(2**intento)
                continue
            if resp.status_code == 404:
                raise RuntimeError(
                    "Base no encontrada (404). Verifica el ID y que la base esté "
                    "compartida con tu integración de Notion."
                )
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"Notion no respondió tras {intentos} intentos: {url}")

    def query_database(self, database_id: str, filtro: dict | None = None) -> Iterator[dict]:
        """Devuelve todas las páginas de una base, paginando de a 100."""
        payload: dict[str, Any] = {"page_size": 100}
        if filtro:
            payload["filter"] = filtro
        while True:
            data = self._post(f"{API_URL}/databases/{database_id}/query", payload)
            yield from data["results"]
            if not data.get("has_more"):
                return
            payload["start_cursor"] = data["next_cursor"]


# ---------------------------------------------------------------- parseo ---

def _texto(rich: list[dict]) -> str:
    return "".join(t.get("plain_text", "") for t in rich).strip()


def valor_propiedad(prop: dict | None) -> Any:
    """Convierte una propiedad de Notion a un valor de Python simple.

    - título / texto / select / status / url  -> str o None
    - número                                  -> float o None
    - casilla                                 -> bool
    - multi-select / persona / relación       -> list (de nombres o de IDs)
    - fecha                                   -> dict {inicio, fin} o None
    - fórmula / rollup                        -> se resuelve según su tipo interno
    """
    if not prop:
        return None
    tipo = prop.get("type")
    v = prop.get(tipo)

    if tipo in ("title", "rich_text"):
        return _texto(v) or None
    if tipo in ("select", "status"):
        return v["name"].strip() if v else None
    if tipo == "multi_select":
        return [o["name"].strip() for o in v]
    if tipo == "number":
        return v
    if tipo == "checkbox":
        return bool(v)
    if tipo in ("url", "email", "phone_number"):
        return v
    if tipo == "date":
        return {"inicio": v["start"], "fin": v.get("end")} if v else None
    if tipo == "people":
        return [p.get("name") or p["id"] for p in v]
    if tipo == "relation":
        return [r["id"] for r in v]
    if tipo == "formula":
        return valor_propiedad({"type": v["type"], v["type"]: v.get(v["type"])})
    if tipo == "rollup":
        if v["type"] == "array":
            return [valor_propiedad(x) for x in v["array"]]
        return valor_propiedad({"type": v["type"], v["type"]: v.get(v["type"])})
    if tipo in ("created_time", "last_edited_time"):
        return v
    return None


def lista(valor: Any) -> list:
    """Normaliza un valor a lista (None -> [], escalar -> [escalar])."""
    if valor is None:
        return []
    if isinstance(valor, list):
        return [x for x in valor if x not in (None, "")]
    return [valor]


def titulo_pagina(pagina: dict) -> str | None:
    """Devuelve el título de la página sin importar cómo se llame la columna título."""
    for prop in pagina["properties"].values():
        if prop["type"] == "title":
            return valor_propiedad(prop)
    return None
