"""Pruebas de los extractores de actividades con páginas simuladas (sin API)."""
import pytest

from extract import actividades_entregables as ae
from extract import actividades_pm as pm
from extract import tareas_extracurriculares as tx
from extract.comun import COLUMNAS_SALIDA, SIN_ASIGNAR

COLS_PM = {
    "actividad": "Actividad", "personas": "PM / Team Member", "horas_hoy": "HH Asignadas Hoy",
    "horas_plan": "HH Plan", "horas_reales": "HH Acumuladas", "proyecto": "Proyecto",
    "tipos_entregable": "Entregable", "estado": "Estado",
}
COLS_AE = {
    "personas": "Team Member", "horas_hoy": "HH Asignadas Hoy", "horas_plan": "Horas Asignadas PI",
    "horas_reales": "Horas Reales PI", "proyecto": "Proyecto",
    "tipos_entregable": "Entregable (Descripción)", "estado": "Estado",
}
COLS_TX = {
    "actividad": "Actividad-Tarea", "personas": "Asignado a", "horas_hoy": "Horas Asignadas Hoy",
    "horas_plan": "Horas Asignadas Totales", "horas_reales": "Horas Reales Totales",
    "proyecto": "Proyecto/Servicio", "programa": "Programas",
    "tiene_entregable": "¿Tiene Entregable?", "estado": "Estado",
}
PROYECTOS = {"proy-1": "Proyecto Uno"}
PROGRAMA_PM = "Modelo de PM"


# ---- constructores de propiedades de Notion ----
def title(t): return {"type": "title", "title": [{"plain_text": t}]}
def num(n): return {"type": "number", "number": n}
def people(*n): return {"type": "people", "people": [{"id": f"u{i}", "name": x} for i, x in enumerate(n)]}
def rel(*i): return {"type": "relation", "relation": [{"id": x} for x in i]}
def multi(*n): return {"type": "multi_select", "multi_select": [{"name": x} for x in n]}
def sel(n): return {"type": "select", "select": {"name": n} if n else None}
def status(n): return {"type": "status", "status": {"name": n}}
def check(b): return {"type": "checkbox", "checkbox": b}


def pagina(id_, **props):
    return {"id": id_, "archived": False, "properties": props}


def pm_pagina(id_="a1", **extra):
    props = {
        "Actividad": title("Kickoff"), "PM / Team Member": people("Rioja, Enrique"),
        "HH Asignadas Hoy": num(2), "HH Plan": num(10), "HH Acumuladas": num(4),
        "Proyecto": rel("proy-1"), "Entregable": multi("Acta"), "Estado": status("En curso"),
    }
    props.update(extra)
    return pagina(id_, **props)


def test_dos_personas_repiten_horas_completas():
    df = pm.paginas_a_dataframe(
        [pm_pagina(**{"PM / Team Member": people("Rioja, Enrique", "Ariana Amayo")})],
        COLS_PM, PROYECTOS, PROGRAMA_PM,
    )
    assert list(df.columns) == COLUMNAS_SALIDA
    assert df["persona"].tolist() == ["Rioja, Enrique", "Ariana Amayo"]
    assert df["horas_hoy"].tolist() == [2, 2]
    assert df["horas_plan"].tolist() == [10, 10]


def test_cero_personas_sin_asignar():
    df = pm.paginas_a_dataframe([pm_pagina(**{"PM / Team Member": people()})], COLS_PM, PROYECTOS, PROGRAMA_PM)
    assert df["persona"].tolist() == [SIN_ASIGNAR]


def test_horas_vacias():
    df = pm.paginas_a_dataframe(
        [pm_pagina(**{"HH Asignadas Hoy": num(None), "HH Plan": num(None), "HH Acumuladas": num(None)})],
        COLS_PM, PROYECTOS, PROGRAMA_PM,
    )
    assert df.loc[0, "horas_hoy"] == 0
    assert df["horas_plan"].isna().all() and df["horas_reales"].isna().all()


def test_tipos_duplicados_y_proyecto_pm():
    df = pm.paginas_a_dataframe(
        [pm_pagina(Entregable=multi("Acta", " acta ", "Informe  final", "Informe final"))],
        COLS_PM, PROYECTOS, PROGRAMA_PM,
    )
    assert df.loc[0, "tipos_entregable"] == "Acta | Informe final"
    assert bool(df.loc[0, "tiene_entregable"]) is True
    assert df.loc[0, "proyecto_id"] == "proy-1"
    assert df.loc[0, "proyecto"] == "Proyecto Uno"
    assert df.loc[0, "programa"] == PROGRAMA_PM
    assert df.loc[0, "base_origen"] == "PM"


def test_pm_sin_tipos():
    df = pm.paginas_a_dataframe([pm_pagina(Entregable=multi())], COLS_PM, PROYECTOS, PROGRAMA_PM)
    assert df.loc[0, "tipos_entregable"] is None
    assert bool(df.loc[0, "tiene_entregable"]) is False


def test_archivadas_se_ignoran():
    arch = pm_pagina("a2")
    arch["archived"] = True
    df = pm.paginas_a_dataframe([pm_pagina(), arch], COLS_PM, PROYECTOS, PROGRAMA_PM)
    assert df["actividad_id"].tolist() == ["a1"]


def test_columna_inexistente_lista_disponibles():
    pag = pm_pagina()
    del pag["properties"]["HH Plan"]
    with pytest.raises(ValueError) as e:
        pm.paginas_a_dataframe([pag], COLS_PM, PROYECTOS, PROGRAMA_PM)
    assert "HH Plan" in str(e.value) and "Actividad" in str(e.value)


def test_ae_usa_titulo_y_select():
    p = pagina(
        "e1", Nombre=title("Diagnóstico"), **{
            "Team Member": people("Andres Perez"), "HH Asignadas Hoy": num(3),
            "Horas Asignadas PI": num(8), "Horas Reales PI": num(None),
            "Proyecto": rel("proy-1"), "Entregable (Descripción)": sel("Informe"),
            "Estado": status("Pendiente"),
        },
    )
    df = ae.paginas_a_dataframe([p], COLS_AE, PROYECTOS)
    r = df.iloc[0]
    assert (r["base_origen"], r["actividad"], r["proyecto"]) == ("Ruta", "Diagnóstico", "Proyecto Uno")
    assert r["tipos_entregable"] == "Informe" and bool(r["tiene_entregable"]) is True
    assert r["programa"] is None and df["horas_reales"].isna().all()


def test_ae_sin_tipo():
    p = pagina(
        "e2", Nombre=title("X"), **{
            "Team Member": people(), "HH Asignadas Hoy": num(None), "Horas Asignadas PI": num(1),
            "Horas Reales PI": num(1), "Proyecto": rel(), "Entregable (Descripción)": sel(None),
            "Estado": status("Pendiente"),
        },
    )
    r = ae.paginas_a_dataframe([p], COLS_AE, PROYECTOS).iloc[0]
    assert r["persona"] == SIN_ASIGNAR and bool(r["tiene_entregable"]) is False
    assert r["proyecto_id"] is None and r["horas_hoy"] == 0


def test_tareas_resuelve_programas():
    p = pagina(
        "t1", **{
            "Actividad-Tarea": title("Charla"), "Asignado a": people("Soto, Ana"),
            "Horas Asignadas Hoy": num(1.5), "Horas Asignadas Totales": num(6),
            "Horas Reales Totales": num(2), "Proyecto/Servicio": multi("Interno", "Formal"),
            "Programas": rel("pg-1", "pg-2", "pg-1"), "¿Tiene Entregable?": check(True),
            "Estado": status("En curso"),
        },
    )
    df = tx.paginas_a_dataframe([p], COLS_TX, {"pg-1": "Programa A", "pg-2": "Programa B"})
    r = df.iloc[0]
    assert r["programa"] == "Programa A | Programa B"
    assert (r["base_origen"], r["proyecto"], r["persona"]) == ("Tareas", "Interno | Formal", "Soto, Ana")
    assert r["proyecto_id"] is None and r["tipos_entregable"] is None
    assert bool(r["tiene_entregable"]) is True


def test_tareas_programa_desconocido_falla():
    p = pagina(
        "t2", **{
            "Actividad-Tarea": title("Charla"), "Asignado a": people("A"),
            "Horas Asignadas Hoy": num(1), "Horas Asignadas Totales": num(1),
            "Horas Reales Totales": num(1), "Proyecto/Servicio": sel(None),
            "Programas": rel("pg-x"), "¿Tiene Entregable?": check(False), "Estado": status("x"),
        },
    )
    with pytest.raises(ValueError):
        tx.paginas_a_dataframe([p], COLS_TX, {})
