"""Compara los indicadores calculados con los CSV y con la base (Supabase, solo lectura).

    python scripts/comparar_fuentes.py                       # rango y día: de la primera a la última foto
    python scripts/comparar_fuentes.py --fecha 2026-10-27    # día del detalle diario
    python scripts/comparar_fuentes.py --desde 2026-10-23 --hasta 2026-10-28

Corre kpis.calcular_todo con cada fuente (para las fotos de mañana y de cierre) y compara las
salidas numéricas: ocupabilidad total y por persona, horas por estado del día, horas por
proyecto, programa PM y exactitud de planificación. Imprime las diferencias.
Salida 0 si coinciden; 1 si hay diferencias; 2 si no se pudo leer alguna fuente.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import pandas as pd  # noqa: E402

import kpis  # noqa: E402
import vista  # noqa: E402
from capacidad import Equipo, a_fecha  # noqa: E402

TOLERANCIA = 1e-6


def _num(v) -> float | None:
    return None if v is None or pd.isna(v) else float(v)


def aplanar(r: dict, momento: str) -> dict[str, float | None]:
    """Todas las salidas numéricas de calcular_todo, con una clave legible por valor."""
    out: dict[str, float | None] = {}
    oc = r["ocupabilidad"]
    for k in ("horas", "disponibles", "ocupabilidad", "dias"):
        out[f"[{momento}] ocupabilidad/área/{k}"] = _num(oc[k])
    for f in oc["detalle"].to_dict("records"):
        for k in ("horas", "disponibles", "ocupabilidad"):
            out[f"[{momento}] ocupabilidad/{f['persona']}/{k}"] = _num(f[k])

    d = r["diaria"]
    out[f"[{momento}] diaria/foto_disponible"] = float(bool(d["foto_disponible"]))
    for k in ("horas", "capacidad", "ocupabilidad"):
        out[f"[{momento}] diaria/área/{k}"] = _num(d[k])
    for f in d["resumen"].to_dict("records"):
        for k in (*kpis.ESTADOS_ORDEN, "total", "capacidad", "ocupabilidad"):
            out[f"[{momento}] diaria/{f['persona']}/{k}"] = _num(f[k])

    for nombre in ("exactitud_global", "exactitud_proyectos"):
        for k in ("exactitud", "horas_plan", "horas_reales", "actividades"):
            out[f"[{momento}] {nombre}/{k}"] = _num(r[nombre][k])
    for f in r["horas_por_proyecto"].to_dict("records"):
        out[f"[{momento}] proyecto/{f['proyecto']}/horas"] = _num(f["horas"])
    out[f"[{momento}] programa_pm/horas"] = _num(r["programa_pm"]["horas"])
    return out


def comparar(a: dict, b: dict, tol: float = TOLERANCIA) -> list[tuple[str, object, object]]:
    difs = []
    for k in sorted(set(a) | set(b)):
        x, y = a.get(k, "(falta)"), b.get(k, "(falta)")
        if isinstance(x, str) or isinstance(y, str) or (x is None) != (y is None):
            if x != y:
                difs.append((k, x, y))
        elif x is not None and abs(x - y) > tol:
            difs.append((k, x, y))
    return difs


def calcular(datos: dict, config: dict, ini, fin, dia) -> dict[str, float | None]:
    equipo = Equipo(config)
    plano: dict[str, float | None] = {}
    for momento in ("manana", "cierre"):
        r = kpis.calcular_todo(datos, equipo, ini, fin, config=config, dia=dia, momento=momento,
                               solo_iniciadas=True)
        plano.update(aplanar(r, momento))
    return plano


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Compara CSV vs base con kpis.calcular_todo.")
    ap.add_argument("--fecha", help="día del detalle diario (por defecto, la última foto)")
    ap.add_argument("--desde", help="inicio del rango (por defecto, la primera foto)")
    ap.add_argument("--hasta", help="fin del rango (por defecto, la última foto)")
    args = ap.parse_args(argv)

    from extract.notion_client import cargar_config

    config = cargar_config()
    try:
        csv = kpis.cargar_datos(RAIZ, config)
        import db

        conn = db.conectar(solo_lectura=True)
        try:
            datos_db = db.cargar_datos_db(conn)
            config_db = db.config_desde_db(conn, config)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 - nunca se imprime el texto crudo (puede traer la conexión)
        print(f"No se pudo leer una de las fuentes ({type(e).__name__}).", file=sys.stderr)
        return 2

    fotos = vista.fechas_con_foto(csv["registro"], momento=None)
    if not fotos:
        print("El CSV no tiene fotos: no hay nada que comparar.", file=sys.stderr)
        return 2
    ini = a_fecha(args.desde) if args.desde else fotos[0]
    fin = a_fecha(args.hasta) if args.hasta else fotos[-1]
    dia = a_fecha(args.fecha) if args.fecha else fotos[-1]

    print(f"Rango {ini} a {fin} · día del detalle {dia}")
    a = calcular(csv, config, ini, fin, dia)
    b = calcular(datos_db, config_db, ini, fin, dia)
    difs = comparar(a, b)
    print(f"Valores comparados: {len(set(a) | set(b))} · diferencias: {len(difs)}")
    if difs:
        print(pd.DataFrame(difs, columns=["valor", "csv", "base"]).to_string(index=False))
        return 1
    print("CSV y base coinciden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
