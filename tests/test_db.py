"""Pruebas de db.py con una conexión simulada (sin base de datos real)."""
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

import db
import kpis
from build_master import COLUMNAS_ESTADO, COLUMNAS_FOTO, COLUMNAS_REGISTRO
from capacidad import Equipo

RAIZ = Path(__file__).resolve().parent.parent
URL = "postgresql://cron_rw:secreto-123@db.ejemplo.supabase.co:5432/postgres"
with open(RAIZ / "config.yaml", encoding="utf-8") as _f:
    CONFIG = yaml.safe_load(_f)


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._resultado = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.log.append(("execute", sql, params))
        self._resultado = self.conn.responder(sql, params)

    def fetchall(self):
        return list(self._resultado)

    def fetchone(self):
        return self._resultado[0] if self._resultado else None


class FakeConn:
    """Registra sentencias, parámetros, commits y rollbacks."""

    def __init__(self, respuestas=None, falla_en=None):
        self.log = []
        self.respuestas = respuestas or {}     # fragmento de sql -> filas (o callable)
        self.falla_en = falla_en               # fragmento de sql que lanza error
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.log.append(("commit",))
        self.commits += 1

    def rollback(self):
        self.log.append(("rollback",))
        self.rollbacks += 1

    def responder(self, sql, params):
        if self.falla_en and self.falla_en in sql:
            raise RuntimeError("falla simulada")
        for fragmento, filas in self.respuestas.items():
            if fragmento in sql:
                return filas(params) if callable(filas) else filas
        return []

    def sentencias(self):
        return [e[1] for e in self.log if e[0] in ("execute", "values")]


@pytest.fixture(autouse=True)
def fake_execute_values(monkeypatch):
    def _ev(cur, sql, filas, page_size=100):
        if self_falla(cur, sql):
            raise RuntimeError("falla simulada")
        cur.conn.log.append(("values", sql, list(filas), page_size))
    def self_falla(cur, sql):
        return cur.conn.falla_en is not None and cur.conn.falla_en in sql
    monkeypatch.setattr(db, "execute_values", _ev)


def foto_demo():
    return pd.DataFrame([
        ["2026-10-28", "PM", "a1", "Act 1", "Ariana Amayo", "Ariana Amayo", "Contratado", "equipo",
         "4.0", "p1", "Proy", "Modelo de PM", np.nan, "False", "Done", "manana"],
        ["2026-10-28", "Tareas", "t1", "Tarea", "Sin asignar", "Sin asignar", np.nan, "desconocido",
         "2.5", np.nan, np.nan, pd.NA, np.nan, pd.NA, "In progress", "manana"],
    ], columns=COLUMNAS_FOTO)


def estado_demo():
    return pd.DataFrame([["2026-10-28", "PM", "a1", "Act 1", "Ariana Amayo", "1.0", np.nan, "p1",
                          "Proy", "Modelo de PM", np.nan, "False", "Done"]], columns=COLUMNAS_ESTADO)


REGISTRO = {"fecha": "2026-10-28", "momento": "manana", "ejecutado_en": "2026-10-28T08:00:00-05:00",
            "ok": True, "filas_pm": 1, "filas_ruta": 0, "filas_tareas": 1, "filas_foto": 2,
            "horas_foto": 6.5, "filas_desconocidas": 1, "horas_desconocidas": 2.5}

RESP_OK = {
    "from ocupabilidad.colaborador": [(1, "Ariana Amayo"), (2, "Enrique Rioja")],
    "select count(*)": [(2, Decimal("6.50"))],
}


# ------------------------------------------------------------------ limpieza ---

def test_limpieza_nulos():
    for v in (None, np.nan, pd.NA, pd.NaT, float("nan")):
        assert db._texto(v) is None
        assert db._num(v) is None
        assert db._bool_o_none(v) is None
        assert db._fecha(v) is None
        assert db._datetime_zona(v) is None
    assert db._texto("x") == "x"
    assert db._num("4.5") == 4.5
    assert db._num("abc") is None
    assert db._entero("41") == 41


def test_limpieza_booleanos_y_fechas():
    assert db._bool_o_none("True") is True
    assert db._bool_o_none("False") is False
    assert db._bool_o_none("") is None
    assert db._bool_o_none(np.bool_(True)) is True
    assert db._fecha("2026-10-28") == date(2026, 10, 28)
    assert db._fecha(pd.Timestamp("2026-10-28 10:00")) == date(2026, 10, 28)
    dt = db._datetime_zona("2026-10-28T08:00:00-05:00")
    assert dt.utcoffset().total_seconds() == -5 * 3600
    naive = db._datetime_zona("2026-10-28T08:00:00")
    assert naive.tzinfo is not None and naive.utcoffset().total_seconds() == -5 * 3600


# --------------------------------------------------------------- guardar_foto ---

def test_guardar_foto_delete_antes_de_insert_y_un_solo_commit():
    conn = FakeConn(RESP_OK)
    db.guardar_foto(conn, foto_demo(), REGISTRO, estado_demo(), None)
    sql = conn.sentencias()
    i_del = next(i for i, s in enumerate(sql) if s.startswith("delete from ocupabilidad.foto_carga"))
    i_ins = next(i for i, s in enumerate(sql) if s.startswith("insert into ocupabilidad.foto_carga"))
    assert i_del < i_ins
    assert conn.commits == 1 and conn.rollbacks == 0
    assert conn.log[-1] == ("commit",)                       # el commit es lo último
    assert any(s.startswith("insert into ocupabilidad.corrida") for s in sql)
    assert any(s.startswith("delete from ocupabilidad.estado_actual") for s in sql)
    assert not any("ocupabilidad.proyecto" in s for s in sql)    # dim=None no toca proyecto


def test_guardar_foto_reemplaza_proyecto_si_hay_dim():
    dim = pd.DataFrame([["p1", "Proy", "Modelo PM", "In progress", np.nan, "PO1"],
                        ["p1", "Proy", "Modelo PM", "In progress", np.nan, "PO2"],
                        ["p1", "Proy", "Modelo PM", "In progress", np.nan, "PO1"]],
                       columns=kpis.COLUMNAS_DIM)
    conn = FakeConn(RESP_OK)
    db.guardar_foto(conn, foto_demo(), REGISTRO, estado_demo(), dim)
    sql = conn.sentencias()
    assert any(s.startswith("delete from ocupabilidad.proyecto") for s in sql)
    filas = next(e[2] for e in conn.log if e[0] == "values" and "ocupabilidad.proyecto" in e[1])
    assert [f[:2] for f in filas] == [("p1", "PO1"), ("p1", "PO2")]
    assert conn.commits == 1


def test_guardar_foto_rollback_si_falla_insert():
    conn = FakeConn(RESP_OK, falla_en="insert into ocupabilidad.foto_carga")
    with pytest.raises(RuntimeError, match="falla simulada"):
        db.guardar_foto(conn, foto_demo(), REGISTRO, estado_demo())
    assert conn.commits == 0 and conn.rollbacks == 1


@pytest.mark.parametrize("devuelto", [(1, Decimal("6.50")), (2, Decimal("6.00"))])
def test_guardar_foto_rollback_si_verificacion_no_coincide(devuelto):
    conn = FakeConn({**RESP_OK, "select count(*)": [devuelto]})
    with pytest.raises(RuntimeError, match="Verificación fallida"):
        db.guardar_foto(conn, foto_demo(), REGISTRO, estado_demo())
    assert conn.commits == 0 and conn.rollbacks == 1


def test_colaborador_id_por_persona_display():
    conn = FakeConn(RESP_OK)
    db.guardar_foto(conn, foto_demo(), REGISTRO, estado_demo())
    filas = next(e[2] for e in conn.log if e[0] == "values" and "ocupabilidad.foto_carga" in e[1])
    cols = db.COLUMNAS_DB_FOTO
    por_actividad = {f[cols.index("actividad_id")]: f for f in filas}
    assert por_actividad["a1"][cols.index("colaborador_id")] == 1
    assert por_actividad["t1"][cols.index("colaborador_id")] is None       # "Sin asignar"
    assert por_actividad["t1"][cols.index("tiene_entregable")] is None     # pd.NA -> None
    assert por_actividad["a1"][cols.index("tiene_entregable")] is False
    assert por_actividad["a1"][cols.index("tipos_entregable")] is None     # NaN -> None
    assert por_actividad["a1"][cols.index("fecha")] == date(2026, 10, 28)
    assert por_actividad["a1"][cols.index("horas_hoy")] == 4.0


# ------------------------------------------------------------ cargar_datos_db ---

def respuestas_lectura():
    ahora = datetime(2026, 10, 28, 13, 0, tzinfo=timezone.utc)   # 08:00 en Lima
    return {
        "from ocupabilidad.foto_carga": [
            (date(2026, 10, 28), "PM", "a1", "Act 1", "Ariana Amayo", "Ariana Amayo", "Contratado",
             "equipo", Decimal("4.00"), "p1", "Proy", "Modelo de PM", None, False, "In progress", "manana"),
            (date(2026, 10, 28), "Tareas", "t1", "Tarea", "Sin asignar", "Sin asignar", None,
             "desconocido", Decimal("2.50"), None, None, None, None, None, "Done", "manana"),
        ],
        "from ocupabilidad.estado_actual": [
            (date(2026, 10, 28), "PM", "a1", "Act 1", "Ariana Amayo", Decimal("1.00"), None, "p1",
             "Proy", "Modelo de PM", None, True, "Done")],
        "from ocupabilidad.corrida": [
            (date(2026, 10, 28), "manana", ahora, True, 1, 0, 1, 2, Decimal("6.50"), 1, Decimal("2.50"))],
        "from ocupabilidad.proyecto": [("p1", "Proy", "Modelo PM", "In progress", None, "PO1")],
    }


def test_cargar_datos_db_misma_forma_que_csv(tmp_path):
    datos = db.cargar_datos_db(FakeConn(respuestas_lectura()))
    esperado = {"historico": kpis.COLUMNAS_DIM and COLUMNAS_FOTO, "estado": COLUMNAS_ESTADO,
                "registro": COLUMNAS_REGISTRO, "dim": kpis.COLUMNAS_DIM}
    assert set(datos) == set(esperado)
    for clave, cols in esperado.items():
        assert list(datos[clave].columns) == cols

    # Mismo tipo que pd.read_csv(dtype=str): texto o NaN, nunca números/bool/fechas
    for df in datos.values():
        for v in df.to_numpy().ravel():
            assert isinstance(v, str) or (isinstance(v, float) and np.isnan(v)), repr(v)
    h = datos["historico"]
    assert h.loc[0, "fecha"] == "2026-10-28" and h.loc[0, "tiene_entregable"] == "False"
    assert h.loc[0, "horas_hoy"] == "4.00"
    assert pd.isna(h.loc[1, "tiene_entregable"])
    assert datos["registro"].loc[0, "ejecutado_en"] == "2026-10-28T08:00:00-05:00"
    assert datos["registro"].loc[0, "ok"] == "True"

    # Igual que un CSV leído con kpis.cargar_datos
    (tmp_path / "p").mkdir()
    for nombre, clave in [("historico_horas_diarias", "historico"), ("estado_actual", "estado"),
                          ("registro_corridas", "registro"), ("dim_intervenciones", "dim")]:
        datos[clave].to_csv(tmp_path / "p" / f"{nombre}.csv", index=False, encoding="utf-8-sig")
    desde_csv = kpis.cargar_datos(tmp_path, {"rutas": {"processed": "p"}})
    for clave in datos:
        assert list(desde_csv[clave].columns) == list(datos[clave].columns)
        assert desde_csv[clave].shape == datos[clave].shape

    # Pasa por las funciones de preparación sin error
    assert len(kpis.preparar_historico(datos["historico"])) == 2
    assert len(kpis.preparar_estado(datos["estado"])) == 1
    assert len(kpis.registro_ok(datos["registro"], "manana")) == 1


def test_cargar_datos_db_tablas_vacias():
    datos = db.cargar_datos_db(FakeConn())
    assert list(datos["historico"].columns) == COLUMNAS_FOTO
    assert list(datos["dim"].columns) == kpis.COLUMNAS_DIM
    assert all(df.empty for df in datos.values())
    kpis.preparar_historico(datos["historico"])
    assert kpis.registro_ok(datos["registro"]).empty


# ------------------------------------------------------------- config_desde_db ---

def test_config_desde_db_construye_equipo_y_respeta_lunes_a_domingo():
    cap = []
    for cond, horas in {"Contratado": [9, 9, 9, 9, 5, 0, 0], "Trainee Pre": [6, 6, 6, 6, 6, 0, 0]}.items():
        cap += [(cond, d + 1, Decimal(str(h)), date(2000, 1, 1)) for d, h in enumerate(horas)]
    # Versión nueva de Contratado desde el 1-oct: el viernes baja a 4 (no mezclar con la anterior)
    cap += [("Contratado", d + 1, Decimal(str(h)), date(2026, 10, 1))
            for d, h in enumerate([8, 8, 8, 8, 4, 0, 0])]
    conn = FakeConn({
        "from ocupabilidad.colaborador": [("Ana", "Soto, Ana", "Trainee Pre", True),
                                          ("Luis", "Luis X", "Contratado", False)],
        "from ocupabilidad.capacidad_condicion": cap,
        "from ocupabilidad.feriado": [(date(2026, 11, 1),), (date(2026, 12, 8),)],
        "from ocupabilidad.parametro": [("umbral_sobrecarga", "0.95"), ("umbral_adecuada", "0.60")],
    })
    cfg = db.config_desde_db(conn, CONFIG, hoy=date(2026, 10, 28))
    assert cfg["capacidad_por_condicion"]["Contratado"] == [8, 8, 8, 8, 4, 0, 0]
    assert cfg["capacidad_por_condicion"]["Trainee Pre"] == [6, 6, 6, 6, 6, 0, 0]
    assert cfg["feriados"] == ["2026-11-01", "2026-12-08"]
    assert cfg["umbrales_carga"] == {"sobrecarga": 0.95, "adecuada": 0.60}
    assert cfg["equipo"][1] == {"nombre": "Luis", "nombre_notion": "Luis X",
                                "condicion": "Contratado", "incluir": False}
    assert cfg["zona_horaria"] == CONFIG["zona_horaria"] and "rutas" in cfg   # resto conservado
    assert CONFIG["equipo"][0]["nombre"] == "Gianfranco Fernandez"           # base intacta

    eq = Equipo(cfg)
    assert [m.nombre for m in eq.miembros_incluidos] == ["Ana"]
    assert eq.horas_dia("Contratado", date(2026, 10, 26)) == 8      # lunes
    assert eq.horas_dia("Contratado", date(2026, 10, 30)) == 4      # viernes
    assert eq.horas_dia("Trainee Pre", date(2026, 10, 31)) == 0     # sábado
    assert eq.horas_dia("Trainee Pre", date(2026, 11, 1)) == 0      # feriado
    assert eq.umbral_sobrecarga == 0.95


def test_config_desde_db_vector_incompleto_falla():
    cap = [("Contratado", d, Decimal("9"), date(2000, 1, 1)) for d in range(1, 6)]
    conn = FakeConn({"from ocupabilidad.capacidad_condicion": cap})
    with pytest.raises(ValueError, match="faltan los días"):
        db.config_desde_db(conn, CONFIG, hoy=date(2026, 10, 28))


# -------------------------------------------------------------- sembrar_config ---

def test_sembrar_config_idempotente_y_versiona():
    base = [(d + 1, Decimal(str(h)), date(2000, 1, 1))     # (dia_semana, horas, vigente_desde)
            for d, h in enumerate([9, 9, 9, 9, 5, 0, 0])]
    cap = {"capacidad_por_condicion": {"Contratado": [9, 9, 9, 9, 5, 0, 0],
                                       "Trainee Pre": [6, 6, 6, 6, 6, 0, 0]},
           "equipo": [{"nombre": "Ana", "nombre_notion": "Soto, Ana", "condicion": "Trainee Pre"}],
           "feriados": ["2026-11-01"], "umbrales_carga": {"sobrecarga": 0.91, "adecuada": 0.7},
           "zona_horaria": "America/Lima", "programa_pm": "Modelo de PM"}
    conn = FakeConn({
        "from ocupabilidad.capacidad_condicion": lambda p: base if p[0] == "Contratado" else [],
        "select nombre from ocupabilidad.colaborador": [("Ana",), ("Viejo",)],
    })
    r = db.sembrar_config(conn, cap, hoy=date(2026, 10, 28))
    assert r["capacidad_versiones_nuevas"] == ["Trainee Pre"]      # Contratado igual: no hace nada
    assert r["colaboradores_fuera_del_yaml"] == ["Viejo"]
    assert not any(s.startswith("delete") for s in conn.sentencias())
    filas = next(e[2] for e in conn.log if e[0] == "values" and "capacidad_condicion" in e[1])
    assert len(filas) == 7 and all(f[3] == date(2026, 10, 28) for f in filas)
    assert conn.commits == 1

    # Si cambia el vector, inserta versión nueva sin tocar las anteriores
    cap["capacidad_por_condicion"]["Contratado"] = [8, 8, 8, 8, 4, 0, 0]
    conn = FakeConn({"from ocupabilidad.capacidad_condicion": base})
    r = db.sembrar_config(conn, cap, hoy=date(2026, 10, 28))
    assert "Contratado" in r["capacidad_versiones_nuevas"]
    assert not any(s.startswith("delete") or s.startswith("update") for s in conn.sentencias())


def test_sembrar_config_estados_minusculas_y_rollback():
    cfg = {**CONFIG, "estados": {"Done": ["  Cerrado   OK "]}}
    conn = FakeConn()
    db.sembrar_config(conn, cfg, hoy=date(2026, 10, 28))
    eq = next(e[2] for e in conn.log if e[0] == "values" and "estado_equivalencia" in e[1])
    assert ("cerrado ok", "Done") in eq and ("in progress", "En curso") in eq
    est = next(e[1] for e in conn.log if e[0] == "values" and "ocupabilidad.estado " in e[1])
    assert "do nothing" in est and "cuenta_en_cierre =" not in est        # no pisa cuenta_en_cierre

    conn = FakeConn(falla_en="insert into ocupabilidad.feriado")
    with pytest.raises(RuntimeError):
        db.sembrar_config(conn, CONFIG, hoy=date(2026, 10, 28))
    assert conn.commits == 0 and conn.rollbacks == 1


# ------------------------------------------------------------------ migrar_csv ---

def test_migrar_csv_usa_nombres_de_archivo(tmp_path):
    (tmp_path / "snaps").mkdir()
    (tmp_path / "proc").mkdir()
    f = foto_demo()
    f.drop(columns="momento").assign(fecha="2026-10-27").to_csv(
        tmp_path / "snaps" / "snapshot_2026-10-27.csv", index=False, encoding="utf-8-sig")
    f.assign(fecha="2026-10-28").to_csv(
        tmp_path / "snaps" / "snapshot_2026-10-28_manana.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([{k: v for k, v in REGISTRO.items() if k != "momento"}]).to_csv(
        tmp_path / "proc" / "registro_corridas.csv", index=False, encoding="utf-8-sig")
    conn = FakeConn(RESP_OK)
    r = db.migrar_csv(conn, tmp_path, {"rutas": {"snapshots": "snaps", "processed": "proc"}})
    assert r["fotos"] == {"2026-10-27 cierre": 2, "2026-10-28 manana": 2}
    assert r["corridas"] == 1
    corrida = next(e[2] for e in conn.log if e[0] == "values" and "ocupabilidad.corrida" in e[1])
    assert corrida[0][1] == "cierre"                       # sin columna momento = cierre
    assert conn.commits == 1 and conn.rollbacks == 0


# ---------------------------------------------------------------- secretos ---

def test_errores_no_contienen_cadena_de_conexion(monkeypatch):
    monkeypatch.setenv("SUPABASE_DB_URL", URL)
    monkeypatch.setattr(db, "load_dotenv", lambda *a, **k: None)

    def falla(url, **kw):
        raise db.psycopg2.OperationalError(f"no se pudo abrir {url}")
    monkeypatch.setattr(db.psycopg2, "connect", falla)
    with pytest.raises(RuntimeError) as e:
        db.conectar()
    assert URL not in str(e.value) and "secreto-123" not in str(e.value)
    assert e.value.__cause__ is None and e.value.__suppress_context__

    monkeypatch.delenv("SUPABASE_DB_URL_RO", raising=False)
    with pytest.raises(RuntimeError, match="SUPABASE_DB_URL_RO") as e:
        db.conectar(solo_lectura=True)
    assert URL not in str(e.value)

    monkeypatch.setenv("SUPABASE_DB_URL_RO", URL)
    monkeypatch.setattr(db.psycopg2, "connect", lambda url, **kw: pytest.fail("usó la URL equivocada")
                        if url != URL else "conexion")
    assert db.conectar(solo_lectura=True) == "conexion"


def test_conectar_timeout(monkeypatch):
    monkeypatch.setenv("SUPABASE_DB_URL", URL)
    monkeypatch.setattr(db, "load_dotenv", lambda *a, **k: None)
    visto = {}
    monkeypatch.setattr(db.psycopg2, "connect", lambda url, **kw: visto.update(kw) or "c")
    db.conectar()
    assert visto == {"connect_timeout": 15}


def test_mensajes_de_verificacion_no_filtran_url():
    conn = FakeConn({**RESP_OK, "select count(*)": [(0, Decimal("0"))]})
    with pytest.raises(RuntimeError) as e:
        db.guardar_foto(conn, foto_demo(), REGISTRO, estado_demo())
    assert "postgresql://" not in str(e.value)
