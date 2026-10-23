"""Ocupabilidad diaria por estado (equivalente a 'Status de Ocupabilidad Daily' de Notion).

Escenario: jueves 22 de octubre de 2026 (capacidad: Contratado 9 h, Trainee Pro 9 h, Trainee Pre 6 h).
  Ariana (Contratada): Done 2.0 + In progress 3.0 + Not Started 1.5            -> 6.5 h
  Soto, Ana = Sofía (Trainee Pre): Stuck 1.0 + En curso 4.0                    -> 5.0 h
  Anghelo (Contratado): un estado que no se reconoce, 2.0                       -> 2.0 h (Otro)
  "Sin asignar" 3 h y Andrés 4 h no cuentan; Marilyn 5 h del miércoles no es de ese día.
Equipo del día: 13.5 h de 51 h de capacidad.
"""
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

import kpis
from capacidad import Equipo
from tests import test_kpis as tk

RAIZ = Path(__file__).resolve().parent.parent
with open(RAIZ / "config_capacidad.yaml", encoding="utf-8") as _f:
    EQ = Equipo(yaml.safe_load(_f))

JUE = date(2026, 10, 22)


def aprox(a, b, tol=1e-9):
    return a is not None and abs(a - b) < tol


def F(fecha, persona, horas, estado, aid="x", base="Ruta"):
    fila = tk.H(fecha, base, aid, f"Act {aid}", persona, horas, "p1", "Proyecto 1")
    fila["estado"] = estado
    return fila


def _hist():
    return kpis.preparar_historico(pd.DataFrame([
        F(tk.JUE, "Ariana Amayo", 2.0, "Done", "a1"),
        F(tk.JUE, "Ariana Amayo", 3.0, "In progress", "a2"),
        F(tk.JUE, "Ariana Amayo", 1.5, "Not Started", "a3"),
        F(tk.JUE, "Soto, Ana", 1.0, "Stuck", "s1"),
        F(tk.JUE, "Soto, Ana", 4.0, "En curso", "s2"),
        F(tk.JUE, "Anghelo Aguirre", 2.0, "Estado raro", "g1", "Tareas"),
        F(tk.JUE, "Sin asignar", 3.0, "Done", "u1"),
        F(tk.JUE, "Andres Perez", 4.0, "Done", "j1"),
        F(tk.MIE, "Marilyn Sáenz Segura", 5.0, "Done", "m1"),   # otro día
    ]))


# ---------------------------------------------------------------- estados ---

def test_normalizar_estado():
    n = kpis.normalizar_estado
    assert n("In progress") == "En curso" and n("en curso") == "En curso" and n("  EN   CURSO ") == "En curso"
    assert n("Not Started") == "Sin empezar" and n("Not started") == "Sin empezar"
    assert n("To-do") == "Sin empezar" and n("Sin empezar") == "Sin empezar"
    assert n("Done") == "Done" and n("DONE") == "Done" and n("Complete") == "Done"
    assert n("Stuck") == "Stuck" and n("Bloqueado") == "Stuck"
    assert n("Algo raro") == "Otro" and n(None) == "Otro" and n(float("nan")) == "Otro"


def test_equivalencias_de_la_configuracion_amplian_las_de_fabrica():
    tabla = kpis.construir_tabla_estados({"Stuck": ["frenado"], "Done": ["Cerrado"]})
    assert kpis.normalizar_estado("Frenado", tabla) == "Stuck"
    assert kpis.normalizar_estado("cerrado", tabla) == "Done"
    assert kpis.normalizar_estado("Not Started", tabla) == "Sin empezar"      # sigue valiendo
    try:
        kpis.construir_tabla_estados({"Inventado": ["x"]})
    except ValueError as e:
        assert "Inventado" in str(e) and "Válidos" in str(e)
    else:
        raise AssertionError("Debió fallar")


# ----------------------------------------------------------------- diaria ---

def test_ocupabilidad_diaria_por_persona_y_estado():
    r = kpis.ocupabilidad_diaria(_hist(), EQ, JUE)
    assert r["dia"] == JUE
    res = r["resumen"].set_index("persona")
    assert len(res) == 6                                       # todos, también quienes no registraron
    a = res.loc["Ariana Amayo"]
    assert (a["Done"], a["En curso"], a["Sin empezar"], a["Stuck"], a["Otro"]) == (2.0, 3.0, 1.5, 0.0, 0.0)
    assert aprox(a["total"], 6.5) and a["capacidad"] == 9
    assert aprox(a["ocupabilidad"], 6.5 / 9) and a["estado_carga"] == "CARGA ADECUADA"
    s = res.loc["Sofía Soto"]
    assert (s["Stuck"], s["En curso"]) == (1.0, 4.0) and s["capacidad"] == 6   # Trainee Pre
    assert aprox(s["ocupabilidad"], 5 / 6) and s["estado_carga"] == "CARGA ADECUADA"
    g = res.loc["Anghelo Aguirre"]
    assert g["Otro"] == 2.0 and g["estado_carga"] == "SUBCARGA"
    for ninguno in ("Gianfranco Fernandez", "Marilyn Sáenz", "Enrique Rioja"):
        assert res.loc[ninguno, "total"] == 0 and res.loc[ninguno, "estado_carga"] == "SUBCARGA"


def test_diaria_total_del_equipo_y_exclusiones():
    r = kpis.ocupabilidad_diaria(_hist(), EQ, JUE)
    assert aprox(r["horas"], 13.5)                             # sin "Sin asignar", sin Andrés, sin el miércoles
    assert aprox(r["capacidad"], 51) and aprox(r["ocupabilidad"], 13.5 / 51)
    assert r["estado"] == "SUBCARGA"
    assert r["estados_sin_clasificar"] == ["Estado raro"]
    assert list(r["resumen"]["persona"])[:3] == ["Ariana Amayo", "Sofía Soto", "Anghelo Aguirre"]  # por horas


def test_diaria_datos_largos_para_el_grafico():
    r = kpis.ocupabilidad_diaria(_hist(), EQ, JUE)
    largo = r["largo"]
    assert len(largo) == 6 and aprox(largo["horas"].sum(), 13.5)
    assert set(largo["persona"]) == {"Ariana Amayo", "Sofía Soto", "Anghelo Aguirre"}   # sin ceros
    ori = largo.set_index("estado")["orden"].to_dict()
    assert ori["Done"] < ori["En curso"] < ori["Stuck"] < ori["Sin empezar"] < ori["Otro"]


def test_diaria_en_dia_sin_capacidad_es_sin_datos():
    sabado = _hist().assign(fecha=date(2026, 10, 24))
    r = kpis.ocupabilidad_diaria(sabado, EQ, date(2026, 10, 24))
    assert r["capacidad"] == 0 and r["ocupabilidad"] is None and r["estado"] == "Sin datos"
    # las horas se muestran igual; aquí todas las filas se pasaron al sábado, incluidas las 5 h de Marilyn
    assert aprox(r["horas"], 18.5)


def test_diaria_filtro_de_personas_y_sin_dia():
    r = kpis.ocupabilidad_diaria(_hist(), EQ, JUE, personas=["Sofía Soto"])
    assert list(r["resumen"]["persona"]) == ["Sofía Soto"]
    assert aprox(r["horas"], 5) and aprox(r["capacidad"], 6)
    vacio = kpis.ocupabilidad_diaria(_hist(), EQ, None)
    assert vacio["dia"] is None and vacio["resumen"].empty and vacio["ocupabilidad"] is None


def test_diaria_estado_vacio_se_avisa():
    h = kpis.preparar_historico(pd.DataFrame([F(tk.JUE, "Ariana Amayo", 2.0, None, "a1")]))
    r = kpis.ocupabilidad_diaria(h, EQ, JUE)
    assert r["estados_sin_clasificar"] == ["(vacío)"]
    assert r["resumen"].set_index("persona").loc["Ariana Amayo", "Otro"] == 2.0


def test_diaria_en_calcular_todo():
    # escenario de test_kpis: el miércoles 21 Ariana tiene 9.5 h (b2) y Anghelo 2 h (c2)
    r = kpis.calcular_todo(tk._datos(), EQ, tk.INI, tk.FIN, dia="2026-10-21")["diaria"]
    res = r["resumen"].set_index("persona")
    assert aprox(res.loc["Ariana Amayo", "ocupabilidad"], 9.5 / 9)
    assert res.loc["Ariana Amayo", "estado_carga"] == "SOBRECARGA"
    assert aprox(res.loc["Anghelo Aguirre", "total"], 2)
    assert aprox(r["horas"], 11.5) and aprox(r["capacidad"], 51)
    # sin pedir día: la última foto del rango (sábado 24, sin capacidad)
    por_defecto = kpis.calcular_todo(tk._datos(), EQ, tk.INI, tk.FIN)["diaria"]
    assert por_defecto["dia"] == date(2026, 10, 24) and por_defecto["estado"] == "Sin datos"
    # con filtro de personas llega hasta la tabla diaria
    uno = kpis.calcular_todo(tk._datos(), EQ, tk.INI, tk.FIN, dia="2026-10-20",
                             personas=["Sofía Soto"])["diaria"]
    # el martes Sofía tiene 6 h de 6 h (100%): supera el corte de 91%
    assert aprox(uno["horas"], 6) and aprox(uno["capacidad"], 6) and uno["estado"] == "SOBRECARGA"


def test_diaria_sin_datos_no_falla():
    vacio = {
        "historico": pd.DataFrame(columns=kpis.COLUMNAS_FOTO),
        "estado": pd.DataFrame(columns=kpis.COLUMNAS_ESTADO),
        "registro": pd.DataFrame(columns=kpis.COLUMNAS_REGISTRO),
        "dim": pd.DataFrame(columns=kpis.COLUMNAS_DIM),
    }
    r = kpis.calcular_todo(vacio, EQ, tk.INI, tk.FIN)["diaria"]
    assert r["dia"] is None and r["resumen"].empty and r["estado"] == "Sin datos"
