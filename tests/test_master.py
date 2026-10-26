import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

import build_master as bm

RAIZ = Path(__file__).resolve().parent.parent
LIMA = ZoneInfo("America/Lima")
DIA1 = date(2026, 10, 22)
DIA2 = date(2026, 10, 23)


def _config() -> dict:
    with open(RAIZ / "config_capacidad.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["zona_horaria"] = "America/Lima"
    cfg["rutas"] = {"processed": "data/processed", "snapshots": "data/snapshots"}
    return cfg


def fila(base, aid, act, persona, hoy=0.0, plan=None, real=None, pid="p1", proy="Proy 1",
         prog=None, tipos=None, tiene=False, estado="En curso"):
    return {
        "base_origen": base, "actividad_id": aid, "actividad": act, "persona": persona,
        "horas_hoy": hoy, "horas_plan": plan, "horas_reales": real, "proyecto_id": pid,
        "proyecto": proy, "programa": prog, "tipos_entregable": tipos,
        "tiene_entregable": tiene, "estado": estado,
    }


def _escribir(raiz: Path, filas_por_base: dict, dia: date = DIA1) -> None:
    """Escribe los CSV de entrada con fecha de modificación = mediodía de Lima de `dia`."""
    carpeta = raiz / "data" / "processed"
    carpeta.mkdir(parents=True, exist_ok=True)
    ts = datetime(dia.year, dia.month, dia.day, 12, 0, tzinfo=LIMA).timestamp()
    for base, nombre in bm.FUENTES.items():
        df = pd.DataFrame(filas_por_base.get(base, []), columns=bm.COLUMNAS_FUENTE)
        ruta = carpeta / nombre
        df.to_csv(ruta, index=False, encoding="utf-8-sig")
        os.utime(ruta, (ts, ts))


def _ahora(dia: date, hora: int = 17, minuto: int = 30) -> datetime:
    return datetime(dia.year, dia.month, dia.day, hora, minuto, tzinfo=LIMA)


def _datos_dia1():
    return {
        "PM": [
            # dos personas en la misma actividad: ambas con las horas completas
            fila("PM", "a1", "Entrevistas", "Ariana Amayo", 3, plan=5, real=2, prog="Modelo de PM"),
            fila("PM", "a1", "Entrevistas", "Rioja, Enrique", 3, plan=5, real=2, prog="Modelo de PM"),
            fila("PM", "a2", "Sin horas hoy", "Ariana Amayo", 0, plan=1, real=0),
        ],
        "Ruta": [
            fila("Ruta", "b1", "Plan de acción", "Soto, Ana", 2, plan=10, real=4, tipos="Planes de Acción"),
            fila("Ruta", "b2", "Solo jefe", "Andres Perez", 4, plan=3, real=3),
            fila("Ruta", "b3", "Sin responsable", "Sin asignar", 1, plan=2, real=1),
        ],
        "Tareas": [
            fila("Tareas", "c1", "Tarea X", "Anghelo Aguirre", 1, plan=3, real=1, pid=None,
                 proy="Otros", prog="Extracurricular"),
            fila("Tareas", "c2", "Tarea externa", "Persona Externa", 2),
        ],
    }


def _correr(raiz, dia=DIA1, **kw):
    return bm.generar_foto_del_dia(_config(), fecha=dia, ahora=_ahora(dia), raiz=raiz, **kw)


def _leer(raiz, *partes):
    return pd.read_csv(raiz.joinpath(*partes), dtype=str, encoding="utf-8-sig")


# ------------------------------------------------------------------- foto ---

def test_foto_filtra_clasifica_y_descarta_excluidos():
    with tempfile.TemporaryDirectory() as t:
        raiz = Path(t)
        _escribir(raiz, _datos_dia1())
        r = _correr(raiz)
        foto = _leer(raiz, "data", "snapshots", "snapshot_2026-10-22.csv")

        assert set(foto["actividad"]) == {
            "Entrevistas", "Plan de acción", "Sin responsable", "Tarea X", "Tarea externa",
        }
        assert "Solo jefe" not in set(foto["actividad"])        # Andrés descartado
        assert "Sin horas hoy" not in set(foto["actividad"])    # horas_hoy = 0
        soto = foto[foto["persona"] == "Soto, Ana"].iloc[0]
        assert soto["persona_display"] == "Sofía Soto" and soto["condicion"] == "Trainee Pre"
        assert soto["clasificacion"] == "equipo"
        assert set(foto[foto["clasificacion"] == "desconocido"]["persona"]) == {
            "Sin asignar", "Persona Externa",
        }
        # dos personas -> las horas completas a cada una
        entrev = foto[foto["actividad"] == "Entrevistas"]
        assert sorted(entrev["horas_hoy"].astype(float)) == [3.0, 3.0]
        assert r["registro"]["filas_foto"] == 6
        assert r["registro"]["horas_foto"] == 12.0          # 3+3+2+1+1+2
        assert r["registro"]["filas_desconocidas"] == 2
        assert r["registro"]["horas_desconocidas"] == 3.0   # 1 + 2


def test_estado_actual_una_fila_por_actividad_sin_duplicar_horas():
    with tempfile.TemporaryDirectory() as t:
        raiz = Path(t)
        _escribir(raiz, _datos_dia1())
        _correr(raiz)
        est = _leer(raiz, "data", "processed", "estado_actual.csv")
        assert len(est) == len(est.drop_duplicates(["base_origen", "actividad_id"]))
        a1 = est[est["actividad_id"] == "a1"].iloc[0]
        assert float(a1["horas_plan"]) == 5.0               # no 10, aunque hay 2 personas
        assert set(a1["personas"].split("; ")) == {"Ariana Amayo", "Enrique Rioja"}
        assert "b2" not in set(est["actividad_id"])         # actividad solo de Andrés
        assert "a2" in set(est["actividad_id"])             # sin horas hoy, pero sí existe


# ------------------------------------------------------------ idempotencia ---

def test_idempotente_mismo_dia_dos_veces():
    with tempfile.TemporaryDirectory() as t:
        raiz = Path(t)
        _escribir(raiz, _datos_dia1())
        _correr(raiz)
        antes = {n: _leer(raiz, "data", *n.split("/")) for n in (
            "snapshots/snapshot_2026-10-22.csv",
            "processed/historico_horas_diarias.csv",
            "processed/estado_actual.csv",
        )}
        _correr(raiz)
        for n, df in antes.items():
            pd.testing.assert_frame_equal(df, _leer(raiz, "data", *n.split("/")))
        reg = _leer(raiz, "data", "processed", "registro_corridas.csv")
        assert len(reg) == 1


def test_rerun_reemplaza_horas_no_las_acumula():
    with tempfile.TemporaryDirectory() as t:
        raiz = Path(t)
        _escribir(raiz, _datos_dia1())
        _correr(raiz)
        # alguien corrige sus horas y se vuelve a correr el mismo día
        datos = _datos_dia1()
        datos["Tareas"][0]["horas_hoy"] = 0.5
        datos["PM"] = [f for f in datos["PM"] if f["persona"] != "Rioja, Enrique"]
        _escribir(raiz, datos)
        _correr(raiz)
        hist = _leer(raiz, "data", "processed", "historico_horas_diarias.csv")
        hist["horas_hoy"] = hist["horas_hoy"].astype(float)
        assert hist[hist["actividad"] == "Tarea X"]["horas_hoy"].iloc[0] == 0.5
        assert "Rioja, Enrique" not in set(hist["persona"])
        assert hist["fecha"].nunique() == 1


def test_dos_dias_se_acumulan_en_el_historico_y_conservan_snapshots():
    with tempfile.TemporaryDirectory() as t:
        raiz = Path(t)
        _escribir(raiz, _datos_dia1(), DIA1)
        _correr(raiz, DIA1)
        dia2 = {"Tareas": [fila("Tareas", "c1", "Tarea X", "Anghelo Aguirre", 2, pid=None)]}
        _escribir(raiz, dia2, DIA2)
        r = _correr(raiz, DIA2)
        hist = _leer(raiz, "data", "processed", "historico_horas_diarias.csv")
        assert sorted(hist["fecha"].unique()) == ["2026-10-22", "2026-10-23"]
        assert r["dias_en_historico"] == 2
        assert (raiz / "data/snapshots/snapshot_2026-10-22.csv").exists()
        reg = _leer(raiz, "data", "processed", "registro_corridas.csv")
        assert list(reg["fecha"]) == ["2026-10-22", "2026-10-23"]


def test_dia_sin_horas_deja_snapshot_vacio_y_cuenta_como_corrida():
    with tempfile.TemporaryDirectory() as t:
        raiz = Path(t)
        _escribir(raiz, _datos_dia1(), DIA1)
        _correr(raiz, DIA1)
        vacio = {"Tareas": [fila("Tareas", "c1", "Tarea X", "Anghelo Aguirre", 0)]}
        _escribir(raiz, vacio, DIA2)
        _correr(raiz, DIA2)
        snap = _leer(raiz, "data", "snapshots", "snapshot_2026-10-23.csv")
        assert snap.empty and list(snap.columns) == bm.COLUMNAS_FOTO
        reg = _leer(raiz, "data", "processed", "registro_corridas.csv")
        assert reg[reg["fecha"] == "2026-10-23"].iloc[0]["filas_foto"] == "0"
        hist = _leer(raiz, "data", "processed", "historico_horas_diarias.csv")
        assert set(hist["fecha"]) == {"2026-10-22"}         # el día vacío no inventa filas


# ----------------------------------------------------------- todo o nada ---

def test_falta_una_fuente_no_escribe_nada():
    with tempfile.TemporaryDirectory() as t:
        raiz = Path(t)
        _escribir(raiz, _datos_dia1())
        (raiz / "data/processed" / bm.FUENTES["Tareas"]).unlink()
        try:
            _correr(raiz)
        except FileNotFoundError as e:
            assert "tareas_extracurriculares.csv" in str(e)
        else:
            raise AssertionError("Debió fallar")
        assert not (raiz / "data" / "snapshots").exists()
        assert not (raiz / "data/processed/registro_corridas.csv").exists()


def test_columna_faltante_lista_las_disponibles():
    with tempfile.TemporaryDirectory() as t:
        raiz = Path(t)
        _escribir(raiz, _datos_dia1())
        ruta = raiz / "data/processed" / bm.FUENTES["PM"]
        df = pd.read_csv(ruta).drop(columns=["horas_hoy"])
        df.to_csv(ruta, index=False)
        try:
            _correr(raiz)
        except ValueError as e:
            assert "horas_hoy" in str(e) and "Columnas disponibles" in str(e)
        else:
            raise AssertionError("Debió fallar")
        assert not (raiz / "data" / "snapshots").exists()


def test_base_origen_inesperado_falla():
    with tempfile.TemporaryDirectory() as t:
        raiz = Path(t)
        datos = _datos_dia1()
        datos["PM"][0]["base_origen"] = "Ruta"
        _escribir(raiz, datos)
        try:
            _correr(raiz)
        except ValueError as e:
            assert "base_origen" in str(e)
        else:
            raise AssertionError("Debió fallar")


def test_archivos_de_ayer_se_rechazan_salvo_permiso():
    with tempfile.TemporaryDirectory() as t:
        raiz = Path(t)
        _escribir(raiz, _datos_dia1(), DIA1)                # archivos con fecha del 22
        try:
            _correr(raiz, DIA2)                             # foto del 23
        except RuntimeError as e:
            assert "no son del 2026-10-23" in str(e)
        else:
            raise AssertionError("Debió fallar")
        assert not (raiz / "data" / "snapshots").exists()
        _correr(raiz, DIA2, permitir_antiguos=True)
        assert (raiz / "data/snapshots/snapshot_2026-10-23.csv").exists()


# ---------------------------------------------------------------- varios ---

def test_fecha_hoy_usa_hora_de_lima_no_utc():
    # 02:00 UTC del 23 son las 21:00 del 22 en Lima
    utc = datetime(2026, 10, 23, 2, 0, tzinfo=ZoneInfo("UTC"))
    assert bm.fecha_hoy({"zona_horaria": "America/Lima"}, utc) == date(2026, 10, 22)
    utc2 = datetime(2026, 10, 22, 22, 30, tzinfo=ZoneInfo("UTC"))  # 17:30 Lima
    assert bm.fecha_hoy({"zona_horaria": "America/Lima"}, utc2) == date(2026, 10, 22)


def test_fila_repetida_se_descarta_con_aviso():
    with tempfile.TemporaryDirectory() as t:
        raiz = Path(t)
        datos = _datos_dia1()
        datos["Tareas"].append(dict(datos["Tareas"][0]))    # misma clave dos veces
        _escribir(raiz, datos)
        r = _correr(raiz)
        assert any("repetida" in a for a in r["advertencias"])
        foto = _leer(raiz, "data", "snapshots", "snapshot_2026-10-22.csv")
        assert len(foto[foto["actividad"] == "Tarea X"]) == 1


def test_horas_vacias_o_negativas_no_entran_a_la_foto():
    with tempfile.TemporaryDirectory() as t:
        raiz = Path(t)
        datos = _datos_dia1()
        datos["Tareas"].append(fila("Tareas", "c9", "Vacía", "Anghelo Aguirre", None))
        datos["Tareas"].append(fila("Tareas", "c8", "Negativa", "Anghelo Aguirre", -2))
        _escribir(raiz, datos)
        _correr(raiz)
        foto = _leer(raiz, "data", "snapshots", "snapshot_2026-10-22.csv")
        assert not ({"Vacía", "Negativa"} & set(foto["actividad"]))


# ------------------------------------------------------- dos fotos por día ---

def _ahora_a(dia, hora, minuto=30):
    return datetime(dia.year, dia.month, dia.day, hora, minuto, tzinfo=LIMA)


def test_momento_por_hora_y_nombres_de_snapshot():
    assert bm.momento_por_hora(_ahora_a(DIA1, 8)) == "manana"
    assert bm.momento_por_hora(_ahora_a(DIA1, 11, 59)) == "manana"
    assert bm.momento_por_hora(_ahora_a(DIA1, 12, 0)) == "cierre"
    assert bm.nombre_snapshot(DIA1, "cierre") == "snapshot_2026-10-22.csv"       # nombre histórico
    assert bm.nombre_snapshot(DIA1, "manana") == "snapshot_2026-10-22_manana.csv"


def test_foto_de_la_manana_y_de_cierre_conviven_y_no_se_pisan():
    with tempfile.TemporaryDirectory() as t:
        raiz = Path(t)
        # 8:30: Tarea X todavía "Sin empezar", 1 h planificada
        manana = {"Tareas": [fila("Tareas", "c1", "Tarea X", "Anghelo Aguirre", 1, estado="Sin empezar")]}
        _escribir(raiz, manana)
        r1 = bm.generar_foto_del_dia(_config(), fecha=DIA1, ahora=_ahora_a(DIA1, 8), raiz=raiz)
        assert r1["momento"] == "manana"
        # 17:30: ya está "Done" y registró 3 h
        cierre = {"Tareas": [fila("Tareas", "c1", "Tarea X", "Anghelo Aguirre", 3, estado="Done")]}
        _escribir(raiz, cierre)
        r2 = bm.generar_foto_del_dia(_config(), fecha=DIA1, ahora=_ahora_a(DIA1, 17), raiz=raiz)
        assert r2["momento"] == "cierre"
        snaps = raiz / "data" / "snapshots"
        assert (snaps / "snapshot_2026-10-22_manana.csv").exists() and (snaps / "snapshot_2026-10-22.csv").exists()
        hist = _leer(raiz, "data", "processed", "historico_horas_diarias.csv").set_index("momento")
        assert len(hist) == 2
        assert hist.loc["manana", "estado"] == "Sin empezar" and float(hist.loc["manana", "horas_hoy"]) == 1.0
        assert hist.loc["cierre", "estado"] == "Done" and float(hist.loc["cierre", "horas_hoy"]) == 3.0
        reg = _leer(raiz, "data", "processed", "registro_corridas.csv")
        assert sorted(reg["momento"]) == ["cierre", "manana"] and len(reg) == 2
        assert r2["dias_en_historico"] == 1                       # un solo día, dos fotos


def test_repetir_un_momento_solo_reemplaza_ese_momento():
    with tempfile.TemporaryDirectory() as t:
        raiz = Path(t)
        _escribir(raiz, _datos_dia1())
        bm.generar_foto_del_dia(_config(), fecha=DIA1, ahora=_ahora_a(DIA1, 8), raiz=raiz)
        bm.generar_foto_del_dia(_config(), fecha=DIA1, ahora=_ahora_a(DIA1, 17), raiz=raiz)
        antes = _leer(raiz, "data", "processed", "historico_horas_diarias.csv")
        bm.generar_foto_del_dia(_config(), fecha=DIA1, ahora=_ahora_a(DIA1, 9), raiz=raiz)   # mañana otra vez
        despues = _leer(raiz, "data", "processed", "historico_horas_diarias.csv")
        pd.testing.assert_frame_equal(antes, despues)             # mismo resultado, sin duplicados
        assert len(_leer(raiz, "data", "processed", "registro_corridas.csv")) == 2


def test_momento_explicito_gana_a_la_hora_y_uno_invalido_falla():
    with tempfile.TemporaryDirectory() as t:
        raiz = Path(t)
        _escribir(raiz, _datos_dia1())
        r = bm.generar_foto_del_dia(_config(), fecha=DIA1, ahora=_ahora_a(DIA1, 17), raiz=raiz,
                                    momento="manana")
        assert r["momento"] == "manana"
        try:
            bm.generar_foto_del_dia(_config(), fecha=DIA1, ahora=_ahora_a(DIA1, 17), raiz=raiz,
                                    momento="tarde")
        except ValueError as e:
            assert "tarde" in str(e)
        else:
            raise AssertionError("Debió fallar")


def test_snapshots_y_registro_antiguos_sin_momento_se_leen_como_cierre():
    with tempfile.TemporaryDirectory() as t:
        raiz = Path(t)
        snaps = raiz / "data" / "snapshots"
        snaps.mkdir(parents=True)
        viejo = pd.DataFrame([{c: "x" for c in bm.COLUMNAS_FOTO if c != "momento"}])
        viejo["fecha"], viejo["horas_hoy"] = "2026-10-20", "2"
        viejo.to_csv(snaps / "snapshot_2026-10-20.csv", index=False)
        viejo.to_csv(snaps / "snapshot_2026-10-21_manana.csv", index=False)      # sin columna, por nombre
        h = bm.reconstruir_historico(snaps, raiz / "h.csv")
        assert sorted(h["momento"]) == ["cierre", "manana"]       # por columna ausente: nombre del archivo
        # registro viejo (sin columna momento) + una corrida nueva
        ruta = raiz / "reg.csv"
        pd.DataFrame({"fecha": ["2026-10-20"], "ejecutado_en": ["x"], "ok": ["True"]}).to_csv(ruta, index=False)
        bm.registrar_corrida(ruta, {"fecha": "2026-10-20", "momento": "manana", "ejecutado_en": "y", "ok": True})
        reg = pd.read_csv(ruta, dtype=str)
        assert sorted(reg["momento"]) == ["cierre", "manana"]
