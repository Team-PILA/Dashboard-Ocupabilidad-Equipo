"""Maestro y foto diaria de ocupabilidad.

Lee los CSV de los 3 extractores (data/processed/), unifica, clasifica a las personas
con capacidad.Equipo y escribe:

  data/snapshots/snapshot_AAAA-MM-DD.csv          foto de CIERRE (17:30): una fila por persona y
                                                  actividad con horas_hoy > 0 (se SOBRESCRIBE al repetir)
  data/snapshots/snapshot_AAAA-MM-DD_manana.csv   foto de la MAÑANA (8:30, Early Morning): la planificación
  data/processed/historico_horas_diarias.csv  consolidado, reconstruido desde TODOS los snapshots
  data/processed/estado_actual.csv         una fila por actividad (plan y reales acumulados)
  data/processed/registro_corridas.csv     una fila por día con foto (distingue "no corrió"
                                           de "nadie registró")

Garantías:
- Idempotente: correr dos veces el mismo día deja exactamente el mismo resultado; las horas
  viejas nunca se acumulan (se reemplaza el día completo, no se agrega al final).
- Todo o nada: se valida y calcula todo en memoria antes de escribir; si falta una fuente o
  una columna, no se escribe nada.
- Archivos viejos: si un CSV de entrada no es de la fecha de la foto, aborta (evita fotografiar
  datos de ayer como si fueran de hoy), salvo --permitir-antiguos.
- Andrés (incluir: false) se descarta por completo. Las personas no reconocidas se conservan
  marcadas como "desconocido" para mostrarlas en las alertas, pero no cuentan en la ocupabilidad.

Uso:
    python build_master.py                  # foto de hoy; momento por la hora (antes de las 12 = mañana)
    python build_master.py --momento manana # Early Morning (8:30)
    python build_master.py --momento cierre # cierre de turno (17:30)
    python build_master.py --extraer        # corre antes los 4 extractores contra Notion
    python build_master.py --fecha 2026-10-22 --permitir-antiguos

Base de datos (Supabase; requiere SUPABASE_DB_URL en .env, ver db.py):
    python build_master.py --subir          # escribe los CSV y además sube la foto a la base
    python build_master.py --solo-db        # implica --subir y NO escribe CSV (GitHub Actions)
    python build_master.py --sembrar        # carga config.yaml en las tablas de configuración y termina
    python build_master.py --migrar-csv     # carga a la base los CSV ya generados y termina
    python build_master.py --verificar      # compara filas y horas de cada snapshot contra la base;
                                            # sale con código distinto de 0 si algo no coincide
Con --subir la base se escribe ANTES que los CSV: si falla, no se escribe ningún CSV. En la
base se guardan todos los estados (el filtro de estados se aplica al leer, no al guardar).
Cualquier error de base termina con código distinto de 0; nunca se imprime la cadena de conexión.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from capacidad import DESCONOCIDO, EXCLUIDO, Equipo, a_fecha

RAIZ = Path(__file__).resolve().parent

FUENTES = {
    "PM": "actividades_pm.csv",
    "Ruta": "actividades_entregables.csv",
    "Tareas": "tareas_extracurriculares.csv",
}
EXTRACTORES = (
    "intervenciones",  # primero: PM y A&E leen dim_intervenciones.csv
    "actividades_pm",
    "actividades_entregables",
    "tareas_extracurriculares",
)

COLUMNAS_FUENTE = [
    "base_origen", "actividad_id", "actividad", "persona", "horas_hoy", "horas_plan",
    "horas_reales", "proyecto_id", "proyecto", "programa", "tipos_entregable",
    "tiene_entregable", "estado",
]
MANANA, CIERRE = "manana", "cierre"      # momentos del día en que se toma la foto
MOMENTOS = (MANANA, CIERRE)
COLUMNAS_FOTO = [
    "fecha", "base_origen", "actividad_id", "actividad", "persona", "persona_display",
    "condicion", "clasificacion", "horas_hoy", "proyecto_id", "proyecto", "programa",
    "tipos_entregable", "tiene_entregable", "estado", "momento",
]
CLAVE_FOTO = ["fecha", "momento", "base_origen", "actividad_id", "persona"]
COLUMNAS_ESTADO = [
    "fecha_extraccion", "base_origen", "actividad_id", "actividad", "personas",
    "horas_plan", "horas_reales", "proyecto_id", "proyecto", "programa",
    "tipos_entregable", "tiene_entregable", "estado",
]
COLUMNAS_REGISTRO = [
    "fecha", "momento", "ejecutado_en", "ok", "filas_pm", "filas_ruta", "filas_tareas",
    "filas_foto", "horas_foto", "filas_desconocidas", "horas_desconocidas",
]
SIN_ASIGNAR = "Sin asignar"


# ------------------------------------------------------------------ lectura ---

def cargar_fuente(ruta: Path, base: str) -> pd.DataFrame:
    """Lee un CSV de extractor y lo valida. Falla ante cualquier problema de estructura."""
    if not ruta.exists():
        raise FileNotFoundError(f"Falta {ruta}. Ejecuta los extractores primero (--extraer).")
    try:
        df = pd.read_csv(ruta, dtype=str, encoding="utf-8-sig")
    except pd.errors.EmptyDataError as e:
        raise ValueError(f"{ruta.name} está vacío (sin encabezados).") from e

    faltan = [c for c in COLUMNAS_FUENTE if c not in df.columns]
    if faltan:
        raise ValueError(
            f"{ruta.name}: faltan columnas {faltan}. Columnas disponibles: {list(df.columns)}"
        )
    df = df[COLUMNAS_FUENTE].copy()

    otras = {b for b in df["base_origen"].dropna().unique() if b != base}
    if otras:
        raise ValueError(f"{ruta.name}: base_origen inesperado {sorted(otras)} (esperaba '{base}').")

    df["persona"] = df["persona"].fillna("").str.strip().replace("", SIN_ASIGNAR)
    for c in ("horas_hoy", "horas_plan", "horas_reales"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["horas_hoy"] = df["horas_hoy"].fillna(0.0)
    df["tiene_entregable"] = (
        df["tiene_entregable"].fillna("").astype(str).str.strip().str.lower()
        .isin(["true", "1", "si", "sí", "verdadero"])
    )
    return df


def validar_frescura(rutas: list[Path], fecha: date, zona: ZoneInfo) -> None:
    """Aborta si algún CSV de entrada no se generó el día de la foto."""
    viejos = []
    for r in rutas:
        modificado = datetime.fromtimestamp(r.stat().st_mtime, tz=zona).date()
        if modificado != fecha:
            viejos.append(f"{r.name} (modificado {modificado})")
    if viejos:
        raise RuntimeError(
            f"Archivos de entrada que no son del {fecha}: {', '.join(viejos)}. "
            "Vuelve a ejecutar los extractores (--extraer) o usa --permitir-antiguos."
        )


# ------------------------------------------------------------ transformación ---

def clasificar_personas(df: pd.DataFrame, equipo: Equipo) -> pd.DataFrame:
    """Agrega persona_display, condicion y clasificacion (equipo / excluido / desconocido)."""
    cache: dict[str, tuple[str, str, object]] = {}
    for p in df["persona"].unique():
        tipo, miembro = equipo.clasificar(p)
        cache[p] = (tipo, miembro.nombre if miembro else p, miembro.condicion if miembro else None)
    out = df.copy()
    out["clasificacion"] = out["persona"].map(lambda p: cache[p][0])
    out["persona_display"] = out["persona"].map(lambda p: cache[p][1])
    out["condicion"] = out["persona"].map(lambda p: cache[p][2])
    return out


def momento_por_hora(ahora: datetime) -> str:
    """Antes de las 12:00 (Lima) es la foto de la mañana; desde las 12:00, la de cierre."""
    return MANANA if ahora.hour < 12 else CIERRE


def nombre_snapshot(fecha: date, momento: str) -> str:
    """El cierre conserva el nombre histórico (snapshot_AAAA-MM-DD.csv); la mañana lleva sufijo."""
    sufijo = "" if momento == CIERRE else f"_{momento}"
    return f"snapshot_{fecha.isoformat()}{sufijo}.csv"


def construir_foto(
    maestro: pd.DataFrame, fecha: date, equipo: Equipo, momento: str = CIERRE
) -> tuple[pd.DataFrame, list[str]]:
    """Foto del día: filas con horas_hoy > 0, sin las personas excluidas."""
    advertencias: list[str] = []
    d = clasificar_personas(maestro, equipo)
    d = d[(d["horas_hoy"] > 0) & (d["clasificacion"] != EXCLUIDO)].copy()
    d["fecha"] = fecha.isoformat()
    d["momento"] = momento
    d = d[COLUMNAS_FOTO]

    repetidas = d.duplicated(CLAVE_FOTO, keep="first")
    if repetidas.any():
        for _, r in d[repetidas].iterrows():
            advertencias.append(
                f"Fila repetida descartada: {r['base_origen']} / {r['actividad']} / {r['persona']}"
            )
        d = d[~repetidas]
    return d.sort_values(["base_origen", "persona_display", "actividad"]).reset_index(drop=True), advertencias


def construir_estado_actual(maestro: pd.DataFrame, fecha: date, equipo: Equipo) -> pd.DataFrame:
    """Una fila por actividad: horas planificadas y reales (son de la actividad, no de la persona).

    Las filas se generan por persona, así que aquí se deduplica por actividad antes de que
    nadie sume horas_plan / horas_reales.
    """
    d = clasificar_personas(maestro, equipo)
    d = d[d["clasificacion"] != EXCLUIDO]
    if d.empty:
        return pd.DataFrame(columns=COLUMNAS_ESTADO)
    g = d.groupby(["base_origen", "actividad_id"], sort=False, dropna=False)
    estado = g.agg(
        actividad=("actividad", "first"),
        personas=("persona_display", lambda s: "; ".join(dict.fromkeys(s))),
        horas_plan=("horas_plan", "first"),
        horas_reales=("horas_reales", "first"),
        proyecto_id=("proyecto_id", "first"),
        proyecto=("proyecto", "first"),
        programa=("programa", "first"),
        tipos_entregable=("tipos_entregable", "first"),
        tiene_entregable=("tiene_entregable", "max"),
        estado=("estado", "first"),
    ).reset_index()
    estado.insert(0, "fecha_extraccion", fecha.isoformat())
    return estado[COLUMNAS_ESTADO]


# ---------------------------------------------------------------- escritura ---

def escribir_atomico(df: pd.DataFrame, destino: Path) -> None:
    """Escribe a un temporal y lo mueve al destino: nunca queda un archivo a medias."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=destino.parent, suffix=".tmp")
    os.close(fd)
    try:
        df.to_csv(tmp, index=False, encoding="utf-8-sig")
        os.replace(tmp, destino)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def registrar_corrida(ruta: Path, fila: dict) -> None:
    """Agrega la corrida del día y momento; si ya existía esa pareja, la reemplaza.

    Un registro viejo sin columna `momento` se interpreta como de cierre.
    """
    previo = (
        pd.read_csv(ruta, dtype=str, encoding="utf-8-sig") if ruta.exists()
        else pd.DataFrame(columns=COLUMNAS_REGISTRO)
    )
    if "momento" not in previo.columns:
        previo["momento"] = CIERRE
    previo["momento"] = previo["momento"].fillna(CIERRE)
    previo = previo[~((previo["fecha"] == fila["fecha"]) & (previo["momento"] == fila["momento"]))]
    nuevo = pd.DataFrame([fila], columns=COLUMNAS_REGISTRO).astype(str)
    frames = [f for f in (previo.reindex(columns=COLUMNAS_REGISTRO), nuevo) if not f.empty]
    registro = pd.concat(frames, ignore_index=True).sort_values(["fecha", "momento"])
    escribir_atomico(registro, ruta)


def reconstruir_historico(dir_snapshots: Path, destino: Path) -> pd.DataFrame:
    """Consolida todos los snapshots. Se reconstruye entero cada vez: nunca se agrega al final."""
    frames = []
    for archivo in sorted(dir_snapshots.glob("snapshot_*.csv")):
        df = pd.read_csv(archivo, dtype=str, encoding="utf-8-sig")
        if "momento" not in df.columns:      # snapshots anteriores a las dos fotos diarias
            df["momento"] = MANANA if archivo.stem.endswith(f"_{MANANA}") else CIERRE
        faltan = [c for c in COLUMNAS_FOTO if c not in df.columns]
        if faltan:
            raise ValueError(f"{archivo.name}: faltan columnas {faltan}.")
        if not df.empty:
            frames.append(df[COLUMNAS_FOTO])
    historico = (
        pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNAS_FOTO)
    )
    historico["horas_hoy"] = pd.to_numeric(historico["horas_hoy"], errors="coerce")
    historico = (
        historico.drop_duplicates(CLAVE_FOTO, keep="last")
        .sort_values(["fecha", "momento", "base_origen", "persona_display", "actividad"])
        .reset_index(drop=True)
    )
    escribir_atomico(historico, destino)
    return historico


# ------------------------------------------------------------- orquestación ---

def fecha_hoy(config: dict, ahora: datetime | None = None) -> date:
    """Fecha actual en la zona horaria configurada (Lima), no en UTC."""
    zona = ZoneInfo(config.get("zona_horaria", "America/Lima"))
    return (ahora or datetime.now(zona)).astimezone(zona).date()


def generar_foto_del_dia(
    config: dict,
    fecha: object | None = None,
    ahora: datetime | None = None,
    raiz: Path = RAIZ,
    permitir_antiguos: bool = False,
    momento: str | None = None,
    subir: bool = False,
    solo_db: bool = False,
    conn=None,
) -> dict:
    """Valida, calcula en memoria y recién entonces escribe. Devuelve un resumen.

    `momento`: "manana" (Early Morning) o "cierre". Si no se indica, se decide por la hora.
    `subir`: sube la foto a la base (db.guardar_foto) ANTES de escribir los CSV; si falla, aborta
    sin escribir ningún CSV. `solo_db` implica `subir` y no escribe CSV. `conn` es inyectable
    para pruebas; si no se pasa, se abre con db.conectar() y se cierra siempre.
    """
    subir = subir or solo_db
    zona = ZoneInfo(config.get("zona_horaria", "America/Lima"))
    ahora = (ahora or datetime.now(zona)).astimezone(zona)
    momento = momento or momento_por_hora(ahora)
    if momento not in MOMENTOS:
        raise ValueError(f"momento '{momento}' no válido. Válidos: {list(MOMENTOS)}")
    dia = a_fecha(fecha) if fecha else ahora.date()
    equipo = Equipo(config)

    processed = raiz / config["rutas"]["processed"]
    snapshots = raiz / config["rutas"]["snapshots"]
    rutas = {base: processed / nombre for base, nombre in FUENTES.items()}

    # 1) Validación y cálculo (sin escribir nada todavía)
    fuentes = {base: cargar_fuente(ruta, base) for base, ruta in rutas.items()}
    if not permitir_antiguos:
        validar_frescura(list(rutas.values()), dia, zona)

    maestro = pd.concat(list(fuentes.values()), ignore_index=True)
    foto, advertencias = construir_foto(maestro, dia, equipo, momento)
    estado = construir_estado_actual(maestro, dia, equipo)
    desconocidas = foto[foto["clasificacion"] == DESCONOCIDO]
    registro = {
        "fecha": dia.isoformat(),
        "momento": momento,
        "ejecutado_en": ahora.isoformat(timespec="seconds"),
        "ok": True,
        "filas_pm": len(fuentes["PM"]),
        "filas_ruta": len(fuentes["Ruta"]),
        "filas_tareas": len(fuentes["Tareas"]),
        "filas_foto": len(foto),
        "horas_foto": round(float(foto["horas_hoy"].sum()), 2),
        "filas_desconocidas": len(desconocidas),
        "horas_desconocidas": round(float(desconocidas["horas_hoy"].sum()), 2),
    }

    # 2) Base de datos (una transacción; si falla, no se escribe ningún CSV)
    if subir:
        import db

        ruta_dim = processed / "dim_intervenciones.csv"
        dim = pd.read_csv(ruta_dim, dtype=str, encoding="utf-8-sig") if ruta_dim.exists() else None
        propia = conn is None
        conn = db.conectar() if propia else conn
        try:
            db.guardar_foto(conn, foto, registro, estado, dim)
        finally:
            if propia:
                conn.close()

    # 3) Escritura de CSV (cada archivo es idempotente; el histórico se deriva al final)
    dias_en_historico = None
    if not solo_db:
        escribir_atomico(foto, snapshots / nombre_snapshot(dia, momento))
        escribir_atomico(estado, processed / "estado_actual.csv")
        registrar_corrida(processed / "registro_corridas.csv", registro)
        historico = reconstruir_historico(snapshots, processed / "historico_horas_diarias.csv")
        dias_en_historico = historico["fecha"].nunique()

    return {
        "fecha": dia,
        "momento": momento,
        "registro": registro,
        "advertencias": advertencias,
        "dias_en_historico": dias_en_historico,
        "subido_a_db": subir,
        "csv_escritos": not solo_db,
        "personas_desconocidas": sorted(desconocidas["persona"].unique()),
    }


_SNAPSHOT = re.compile(r"^snapshot_(\d{4}-\d{2}-\d{2})(_manana)?\.csv$")


def verificar_contra_db(conn, dir_snapshots: Path) -> tuple[list[dict], bool]:
    """Compara filas y suma de horas_hoy de cada snapshot CSV con la base (por fecha y momento)."""
    with conn.cursor() as cur:
        cur.execute(
            "select fecha, momento, count(*), coalesce(sum(horas_hoy), 0) "
            "from ocupabilidad.foto_carga group by fecha, momento")
        en_db = {(f.isoformat(), m): (int(n), float(h)) for f, m, n, h in cur.fetchall()}
    filas, ok = [], True
    for ruta in sorted(dir_snapshots.glob("snapshot_*.csv")) if dir_snapshots.exists() else []:
        m = _SNAPSHOT.match(ruta.name)
        if not m:
            continue
        fecha, momento = m.group(1), (MANANA if m.group(2) else CIERRE)
        df = pd.read_csv(ruta, dtype=str, encoding="utf-8-sig")
        n_csv = len(df)
        h_csv = round(float(pd.to_numeric(df["horas_hoy"], errors="coerce").fillna(0).sum()), 2)
        n_db, h_db = en_db.get((fecha, momento), (0, 0.0))
        coincide = n_csv == n_db and abs(h_csv - h_db) <= 0.01
        ok = ok and coincide
        filas.append({"fecha": fecha, "momento": momento, "filas_csv": n_csv, "filas_db": n_db,
                      "horas_csv": h_csv, "horas_db": round(h_db, 2),
                      "resultado": "OK" if coincide else "DIFERENTE"})
    return filas, ok


def _sin_secretos(texto: str) -> str:
    for var in ("SUPABASE_DB_URL", "SUPABASE_DB_URL_RO"):
        valor = os.environ.get(var)
        if valor:
            texto = texto.replace(valor, "***")
    return texto


def _comando_db(config: dict, args) -> int:
    """--sembrar / --migrar-csv / --verificar. Devuelve el código de salida."""
    import json

    import db

    conn = None
    try:
        conn = db.conectar()
        if args.sembrar:
            resumen = db.sembrar_config(conn, config)
            print(json.dumps(resumen, indent=2, ensure_ascii=False, default=str))
        elif args.migrar_csv:
            resumen = db.migrar_csv(conn, RAIZ, config)
            print(json.dumps(resumen, indent=2, ensure_ascii=False, default=str))
        else:
            filas, ok = verificar_contra_db(conn, RAIZ / config["rutas"]["snapshots"])
            print(pd.DataFrame(filas).to_string(index=False) if filas else "No hay snapshots en CSV.")
            if not ok:
                print("Hay diferencias entre los CSV y la base.")
                return 1
        return 0
    except Exception as e:  # noqa: BLE001 - cualquier error de base -> código != 0, sin secretos
        print(f"Error de base de datos ({type(e).__name__}): {_sin_secretos(str(e))}", file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            conn.close()


def ejecutar_extractores() -> None:
    """Corre los 4 extractores (escriben sus CSV). Si uno falla, se propaga el error y no
    se genera ninguna foto: una base ausente no debe confundirse con subcarga."""
    import importlib

    for nombre in EXTRACTORES:
        importlib.import_module(f"extract.{nombre}").main()


def main() -> None:
    from extract.notion_client import cargar_config

    ap = argparse.ArgumentParser(description="Genera la foto diaria de ocupabilidad.")
    ap.add_argument("--fecha", help="AAAA-MM-DD (por defecto, hoy en hora de Lima)")
    ap.add_argument("--momento", choices=MOMENTOS,
                    help="manana (Early Morning 8:30) o cierre (17:30); por defecto, según la hora")
    ap.add_argument("--extraer", action="store_true", help="correr antes los extractores")
    ap.add_argument("--permitir-antiguos", action="store_true",
                    help="aceptar CSV de entrada de otro día")
    ap.add_argument("--subir", action="store_true", help="subir la foto a la base además de los CSV")
    ap.add_argument("--solo-db", action="store_true",
                    help="implica --subir y no escribe CSV (GitHub Actions)")
    ap.add_argument("--sembrar", action="store_true", help="cargar config.yaml en la base y terminar")
    ap.add_argument("--migrar-csv", action="store_true",
                    help="cargar a la base los CSV existentes y terminar")
    ap.add_argument("--verificar", action="store_true", help="comparar los snapshots CSV con la base")
    args = ap.parse_args()

    config = cargar_config()
    if args.sembrar or args.migrar_csv or args.verificar:
        sys.exit(_comando_db(config, args))
    zona = ZoneInfo(config.get("zona_horaria", "America/Lima"))
    ahora = datetime.now(zona)
    momento = args.momento or momento_por_hora(ahora)
    if momento == CIERRE and ahora.hour < 17 and not args.fecha:
        print(f"Aviso: son las {ahora:%H:%M}; el equipo registra horas hasta las 17:30. "
              "Puedes volver a correrlo más tarde: reemplaza la foto del día.")

    if args.extraer:
        ejecutar_extractores()

    subir = args.subir or args.solo_db
    try:
        r = generar_foto_del_dia(
            config, fecha=args.fecha, ahora=ahora, permitir_antiguos=args.permitir_antiguos,
            momento=momento, subir=subir, solo_db=args.solo_db,
        )
    except Exception as e:  # noqa: BLE001
        if not subir:
            raise
        print(f"Error ({type(e).__name__}): {_sin_secretos(str(e))}. No se escribió ningún CSV.",
              file=sys.stderr)
        sys.exit(1)
    reg = r["registro"]
    destino = "base" if args.solo_db else ("CSV y base" if subir else "CSV")
    historico = (f" ({r['dias_en_historico']} día(s) en el histórico)"
                 if r["dias_en_historico"] is not None else "")
    print(f"Foto {r['fecha']} ({r['momento']}): {reg['filas_foto']} filas, {reg['horas_foto']} h"
          f"{historico}. Guardada en: {destino}.")
    if r["personas_desconocidas"]:
        print(f"Aviso: personas no reconocidas ({reg['horas_desconocidas']} h): "
              f"{r['personas_desconocidas']}")
    for a in r["advertencias"]:
        print("Aviso:", a)


if __name__ == "__main__":
    main()
