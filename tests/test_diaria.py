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


# --------------------------------------------------- solo estados Done y En curso ---

def test_filtrar_estados_validos_deja_done_y_en_curso():
    h = _hist()
    f = kpis.filtrar_estados_validos(h)
    assert set(f["estado"]) == {"Done", "In progress", "En curso"}          # sin Not Started, Stuck ni raros
    assert len(f) == 6                                                      # a1,a2,s2,u1,j1,m1
    assert len(kpis.filtrar_estados_validos(h, [])) == len(h)               # lista vacía = sin filtro
    solo_done = kpis.filtrar_estados_validos(h, ["Done"])
    assert set(solo_done["estado"]) == {"Done"}
    try:
        kpis.filtrar_estados_validos(h, ["Inventado"])
    except ValueError as e:
        assert "Inventado" in str(e)
    else:
        raise AssertionError("Debió fallar")


def test_calcular_todo_solo_cuenta_estados_validos_pero_avisa_los_raros():
    from tests import test_kpis as k
    datos = k._datos()
    hist = pd.concat([datos["historico"], pd.DataFrame([
        F(k.JUE, "Ariana Amayo", 2.0, "Not Started", "n1"),
        F(k.JUE, "Ariana Amayo", 1.0, "Estado raro", "n2"),
    ]).assign(fecha=k.JUE)], ignore_index=True)
    datos = {**datos, "historico": hist}
    con = kpis.calcular_todo(datos, EQ, k.INI, k.FIN, dia=k.JUE)
    sin = kpis.calcular_todo(datos, EQ, k.INI, k.FIN, dia=k.JUE,
                             config={"estados_validos": []})
    ar = lambda r: r["diaria"]["resumen"].set_index("persona").loc["Ariana Amayo", "total"]  # noqa: E731
    assert aprox(ar(sin) - ar(con), 3.0)                  # Not Started 2 + raro 1 quedan fuera
    assert con["estados_validos"] == ["Done", "En curso"]
    assert con["diaria"]["estados_sin_clasificar"] == ["Estado raro"]       # igual se avisa


# ------------------------------------------- filtros vacíos o sin coincidencias ---

def test_calcular_todo_no_falla_con_ningun_filtro_vacio():
    import itertools
    fallos = []
    for p, b, t, s in itertools.product([None, [], ["Sofía Soto"]], [None, [], ["Tareas"], ["PM"]],
                                        [None, [], ["Modelo PM"], ["Sin proyecto"]], [True, False]):
        try:
            kpis.calcular_todo(tk._datos(), EQ, tk.INI, tk.FIN, personas=p, bases=b,
                               tipos_proyecto=t, solo_iniciadas=s)
        except Exception as e:  # noqa: BLE001
            fallos.append((p, b, t, s, repr(e)))
    assert not fallos, fallos[:3]


# ---------------------------------------------------- exactitud y rango de fechas ---

def _exac(ini, fin, **kw):
    r = kpis.calcular_todo(tk._datos(), EQ, ini, fin, **kw)
    return r["exactitud_global"], r["exactitud_proyectos"], r


def test_exactitud_sin_rango_activo_usa_todo_el_estado_actual():
    g, p, r = _exac(tk.LUN, tk.LUN)               # rango de un día, pero el rango NO está activo
    assert aprox(g["horas_plan"], 29) and aprox(g["horas_reales"], 13) and r["rango_activo"] is False


def test_exactitud_con_rango_activo_solo_actividades_con_horas_en_el_rango():
    # lunes: solo PM a1 (plan 5, real 2)
    g, p, r = _exac(tk.LUN, tk.LUN, rango_activo=True)
    assert aprox(g["horas_plan"], 5) and aprox(g["horas_reales"], 2) and g["actividades"] == 1
    assert aprox(p["horas_plan"], 5) and r["rango_activo"] is True
    # martes: Ruta b1 (10/4) + Tareas c1 (3/1); proyectos solo b1
    g, p, _ = _exac(tk.MAR, tk.MAR, rango_activo=True)
    assert aprox(g["horas_plan"], 13) and aprox(g["horas_reales"], 5)
    assert aprox(p["horas_plan"], 10) and aprox(p["exactitud"], 2.5)
    # toda la semana: a1, b1, b2, c1 (b3 solo tiene horas de "Sin asignar"; a2 y c2 no tienen horas/estado)
    g, _, _ = _exac(tk.INI, tk.FIN, rango_activo=True)
    assert aprox(g["horas_plan"], 23) and aprox(g["horas_reales"], 12) and aprox(g["exactitud"], 23 / 12)


def test_exactitud_con_rango_activo_sin_fotos_es_sin_datos():
    g, p, _ = _exac("2026-10-05", "2026-10-09", rango_activo=True)
    assert g["exactitud"] is None and g["actividades"] == 0 and g["detalle"].empty
    assert p["exactitud"] is None


def test_texto_y_aviso_del_rango():
    import vista
    assert vista.texto_periodo(False, "2026-10-19", "2026-10-24") == "Todo el histórico: 19/10/2026 – 24/10/2026"
    assert vista.texto_periodo(True, "2026-10-20", "2026-10-21") == "Rango elegido: 20/10/2026 – 21/10/2026"
    reg = tk._registro()
    assert vista.aviso_rango_sin_fotos(reg, "2026-10-19", "2026-10-20") is None
    msg = vista.aviso_rango_sin_fotos(reg, "2026-10-05", "2026-10-09")
    assert "05/10/2026" in msg and "19/10/2026" in msg and "24/10/2026" in msg


# --------------------------------------------- Early Morning vs Cierre de turno ---

def _datos_dos_fotos():
    """Jueves 22: a las 8:30 planificó 6 h (Done 1, En curso 1, Sin empezar 3, Stuck 1);
    al cierre quedó con 5 h (Done 3, En curso 2) y las filas de mañana NO deben contarse dos veces."""
    base = tk._datos()
    manana = pd.DataFrame([
        F(tk.JUE, "Ariana Amayo", 1.0, "Done", "m1"), F(tk.JUE, "Ariana Amayo", 1.0, "In progress", "m2"),
        F(tk.JUE, "Ariana Amayo", 3.0, "Not Started", "m3"), F(tk.JUE, "Ariana Amayo", 1.0, "Stuck", "m4"),
    ]).assign(momento="manana")
    cierre = pd.DataFrame([
        F(tk.JUE, "Ariana Amayo", 3.0, "Done", "m1"), F(tk.JUE, "Ariana Amayo", 2.0, "In progress", "m2"),
    ]).assign(momento="cierre")
    hist = pd.concat([base["historico"], manana, cierre], ignore_index=True)
    reg = pd.concat([base["registro"].assign(momento="cierre"),
                     pd.DataFrame({"fecha": [tk.JUE], "ok": [True], "momento": ["manana"]})],
                    ignore_index=True)
    return {**base, "historico": hist, "registro": reg}


def test_early_morning_muestra_todos_los_estados():
    r = kpis.calcular_todo(_datos_dos_fotos(), EQ, tk.INI, tk.FIN, dia=tk.JUE, momento="manana")
    d = r["diaria"]
    a = d["resumen"].set_index("persona").loc["Ariana Amayo"]
    assert (a["Done"], a["En curso"], a["Sin empezar"], a["Stuck"]) == (1.0, 1.0, 3.0, 1.0)
    assert aprox(a["total"], 6.0) and d["momento"] == "manana" and d["foto_disponible"] is True
    assert r["estados_validos"] == []                        # sin filtro de estados


def test_cierre_de_turno_solo_done_y_en_curso_y_no_mezcla_la_manana():
    r = kpis.calcular_todo(_datos_dos_fotos(), EQ, tk.INI, tk.FIN, dia=tk.JUE, momento="cierre")
    a = r["diaria"]["resumen"].set_index("persona").loc["Ariana Amayo"]
    assert (a["Done"], a["En curso"], a["Sin empezar"], a["Stuck"]) == (3.0, 2.0, 0.0, 0.0)
    assert aprox(a["total"], 5.0) and r["estados_validos"] == ["Done", "En curso"]


def test_los_indicadores_por_rango_ignoran_las_fotos_de_la_manana():
    con = kpis.calcular_todo(_datos_dos_fotos(), EQ, tk.INI, tk.FIN)
    # el histórico base más solo las filas de cierre del jueves (5 h de Ariana); las 6 h de la mañana no entran
    base = kpis.calcular_todo(tk._datos(), EQ, tk.INI, tk.FIN)
    ar = lambda r: r["ocupabilidad"]["detalle"].set_index("persona").loc["Ariana Amayo", "horas"]  # noqa: E731
    assert aprox(ar(con) - ar(base), 5.0)
    assert [str(d) for d in con["dias"]["con_foto"]] == [str(d) for d in base["dias"]["con_foto"]]


def test_foto_disponible_por_momento():
    datos = _datos_dos_fotos()
    sin_manana = {**datos, "registro": datos["registro"][datos["registro"]["momento"] == "cierre"]}
    d = kpis.calcular_todo(sin_manana, EQ, tk.INI, tk.FIN, dia=tk.JUE, momento="manana")["diaria"]
    assert d["foto_disponible"] is False             # el aviso sale del registro de corridas
    assert kpis.calcular_todo(sin_manana, EQ, tk.INI, tk.FIN, dia=tk.JUE)["diaria"]["foto_disponible"] is True
    try:
        kpis.calcular_todo(datos, EQ, tk.INI, tk.FIN, momento="tarde")
    except ValueError as e:
        assert "tarde" in str(e)
    else:
        raise AssertionError("Debió fallar")


def test_estados_validos_de_la_manana_se_pueden_configurar():
    r = kpis.calcular_todo(_datos_dos_fotos(), EQ, tk.INI, tk.FIN, dia=tk.JUE, momento="manana",
                           config={"estados_validos_manana": ["Done", "En curso"]})
    a = r["diaria"]["resumen"].set_index("persona").loc["Ariana Amayo"]
    assert aprox(a["total"], 2.0) and r["estados_validos"] == ["Done", "En curso"]
