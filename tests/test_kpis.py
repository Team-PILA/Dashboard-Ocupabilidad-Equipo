"""Pruebas de kpis.py con un escenario pequeño cuyas cifras se calcularon a mano.

Semana del lunes 19 al domingo 25 de octubre de 2026 (sin feriados):
  fotos: lun 19, mar 20, mié 21, jue 22 y sáb 24. NO hay foto el viernes 23.
  -> días laborables con foto: 19, 20, 21, 22 (el sábado se ignora: capacidad 0)
Capacidad por día laborable lun-jue: Contratado 9, Trainee Pro 9, Trainee Pre 6
  -> equipo (4 Contratados + 1 Pro + 1 Pre) = 51 h por día -> 204 h en los 4 días
"""
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

import kpis
from capacidad import Equipo

RAIZ = Path(__file__).resolve().parent.parent
with open(RAIZ / "config_capacidad.yaml", encoding="utf-8") as _f:
    EQ = Equipo(yaml.safe_load(_f))

INI, FIN = "2026-10-19", "2026-10-25"
LUN, MAR, MIE, JUE, SAB = (f"2026-10-{d}" for d in (19, 20, 21, 22, 24))


def aprox(a, b, tol=1e-9):
    return a is not None and abs(a - b) < tol


def H(fecha, base, aid, act, persona, horas, pid=None, proy=None, prog=None, tipos=None, tiene=False):
    clas, m = EQ.clasificar(persona)
    return {
        "fecha": fecha, "base_origen": base, "actividad_id": aid, "actividad": act,
        "persona": persona, "persona_display": m.nombre if m else persona,
        "condicion": m.condicion if m else None, "clasificacion": clas, "horas_hoy": horas,
        "proyecto_id": pid, "proyecto": proy, "programa": prog, "tipos_entregable": tipos,
        "tiene_entregable": tiene, "estado": "En curso",
    }


def _historico():
    return pd.DataFrame([
        H(LUN, "PM", "a1", "Entrevistas", "Ariana Amayo", 3, "p1", "Flujo", "Modelo de PM"),
        H(LUN, "PM", "a1", "Entrevistas", "Rioja, Enrique", 3, "p1", "Flujo", "Modelo de PM"),
        H(MAR, "Ruta", "b1", "Plan", "Soto, Ana", 6, "p2", "F1: Aviation", None, "Planes de Acción"),
        H(MAR, "Tareas", "c1", "Tarea X", "Anghelo Aguirre", 4, None, "Otros", "Extracurricular", None, True),
        H(MIE, "Tareas", "c2", "Cap MPM", "Anghelo Aguirre", 2, None, "Otros",
          "Modelo de PM | Extracurricular"),
        H(MIE, "Ruta", "b2", "Entregable", "Ariana Amayo", 9.5, "p2", "F1: Aviation", None,
          "Planes de Acción | Informe"),
        H(JUE, "Ruta", "b3", "Sin resp", "Sin asignar", 1, "p2", "F1: Aviation"),
        H(SAB, "Tareas", "c1", "Tarea X", "Anghelo Aguirre", 5, None, "Otros", "Extracurricular", None, True),
    ])


def _registro():
    return pd.DataFrame({"fecha": [LUN, MAR, MIE, JUE, SAB], "ok": [True] * 5})


def _dim():
    return pd.DataFrame([
        {"proyecto_id": "p1", "proyecto": "Flujo", "tipo_proyecto": "Modelo PM",
         "status": "In progress", "sponsor": "S", "product_owner": "Leticia Cajas"},
        {"proyecto_id": "p2", "proyecto": "F1: Aviation", "tipo_proyecto": "Ruta de Eficiencia",
         "status": "Done", "sponsor": "S", "product_owner": "Javier Villaseca"},
        {"proyecto_id": "p2", "proyecto": "F1: Aviation", "tipo_proyecto": "Ruta de Eficiencia",
         "status": "Done", "sponsor": "S", "product_owner": "Lucio Benavente"},
    ])


def _estado():
    def e(base, aid, plan, real, pid, proy, personas):
        return {
            "fecha_extraccion": "2026-10-23", "base_origen": base, "actividad_id": aid,
            "actividad": aid, "personas": personas, "horas_plan": plan, "horas_reales": real,
            "proyecto_id": pid, "proyecto": proy, "programa": None, "tipos_entregable": None,
            "tiene_entregable": False, "estado": "En curso",
        }
    return pd.DataFrame([
        e("PM", "a1", 5, 2, "p1", "Flujo", "Ariana Amayo; Enrique Rioja"),
        e("PM", "a2", 0, 0, "p1", "Flujo", "Ariana Amayo"),      # plan 0 -> excluida
        e("Ruta", "b1", 10, 4, "p2", "F1: Aviation", "Sofía Soto"),
        e("Ruta", "b2", 5, 5, "p2", "F1: Aviation", "Ariana Amayo"),
        e("Ruta", "b3", 2, 1, "p2", "F1: Aviation", "Sin asignar"),
        e("Ruta", "b4", 4, 0, "p2", "F1: Aviation", "Sin asignar"),   # no iniciada
        e("Tareas", "c1", 3, 1, None, "Otros", "Anghelo Aguirre"),
    ])


def _datos():
    return {"historico": _historico(), "estado": _estado(), "registro": _registro(), "dim": _dim()}


def _h(**filtros):
    hist = kpis.preparar_historico(_historico())
    dias = kpis.seleccionar_dias(_registro(), EQ, INI, FIN)["laborables"]
    return kpis.filtrar_historico(hist, dias, _dim(), **filtros), dias


# --------------------------------------------------------------------- días ---

def test_seleccion_de_dias():
    d = kpis.seleccionar_dias(_registro(), EQ, INI, FIN)
    assert d["laborables"] == [date(2026, 10, n) for n in (19, 20, 21, 22)]
    assert d["no_laborables"] == [date(2026, 10, 24)]        # sábado
    assert d["sin_foto"] == [date(2026, 10, 23)]             # viernes sin corrida
    # el domingo 25 no es "sin foto": no tiene capacidad


def test_seleccion_respeta_el_rango_y_los_ok_falsos():
    reg = pd.DataFrame({"fecha": [LUN, MAR, MIE], "ok": [True, False, True]})
    d = kpis.seleccionar_dias(reg, EQ, "2026-10-20", "2026-10-21")
    assert d["con_foto"] == [date(2026, 10, 21)]
    assert d["sin_foto"] == [date(2026, 10, 20)]             # ok=False cuenta como sin foto


# ------------------------------------------------------------ ocupabilidad ---

def test_ocupabilidad_area_y_por_persona():
    h, dias = _h()
    r = kpis.ocupabilidad(h, EQ, dias)
    # equipo: Ariana 3+9.5, Enrique 3, Sofía 6, Anghelo 4+2 = 27.5 h (sin el sábado ni "Sin asignar")
    assert aprox(r["horas"], 27.5) and aprox(r["disponibles"], 204)
    assert aprox(r["ocupabilidad"], 27.5 / 204)
    assert r["estado"] == "SUBCARGA" and r["dias"] == 4
    d = r["detalle"].set_index("persona")
    assert len(d) == 6                                       # Andrés no está
    assert aprox(d.loc["Ariana Amayo", "ocupabilidad"], 12.5 / 36)
    assert aprox(d.loc["Sofía Soto", "ocupabilidad"], 6 / 24)    # Trainee Pre: 6 h x 4 días
    assert aprox(d.loc["Enrique Rioja", "ocupabilidad"], 3 / 36)
    assert d.loc["Gianfranco Fernandez", "horas"] == 0       # sin horas igual cuenta capacidad
    assert d.loc["Marilyn Sáenz", "estado"] == "SUBCARGA"


def test_ocupabilidad_filtro_de_personas_reduce_tambien_el_denominador():
    h, dias = _h(personas=["Sofía Soto"])
    r = kpis.ocupabilidad(h, EQ, dias, personas=["Sofía Soto"])
    assert aprox(r["horas"], 6) and aprox(r["disponibles"], 24)
    assert aprox(r["ocupabilidad"], 0.25) and len(r["detalle"]) == 1


def test_ocupabilidad_sin_dias_es_sin_datos():
    hist = kpis.preparar_historico(_historico())
    h = kpis.filtrar_historico(hist, [], _dim())
    r = kpis.ocupabilidad(h, EQ, [])
    assert r["ocupabilidad"] is None and r["estado"] == "Sin datos"


def test_estado_sobrecarga_en_un_caso_extremo():
    fila = H(LUN, "Tareas", "z", "Z", "Anghelo Aguirre", 9.5)   # 9.5 de 9 h en un solo día
    hist = kpis.preparar_historico(pd.DataFrame([fila]))
    h = kpis.filtrar_historico(hist, [LUN], _dim(), personas=["Anghelo Aguirre"])
    r = kpis.ocupabilidad(h, EQ, [date(2026, 10, 19)], personas=["Anghelo Aguirre"])
    assert r["estado"] == "SOBRECARGA" and r["ocupabilidad"] > 1


# --------------------------------------------------------- horas y reparto ---

def test_horas_por_proyecto_solo_ruta_y_pm_y_solo_equipo():
    h, _ = _h()
    r = kpis.horas_por_proyecto(h).set_index("proyecto")
    assert aprox(r.loc["Flujo", "horas"], 6)                 # 3 + 3
    assert aprox(r.loc["F1: Aviation", "horas"], 15.5)       # 6 + 9.5 (el 1 de "Sin asignar" no)
    assert "Otros" not in r.index                            # Tareas no entra
    assert r.loc["Flujo", "tipo_proyecto"] == "Modelo PM"


def test_horas_programa_modelo_pm():
    h, _ = _h()
    r = kpis.horas_programa(h)
    assert aprox(r["horas"], 8)                              # PM 3+3 + Tareas 2 ("A | B")
    por_base = r["por_base"].set_index("base_origen")["horas"]
    assert aprox(por_base["PM"], 6) and aprox(por_base["Tareas"], 2)
    assert "Ruta" not in por_base.index


def test_horas_por_po_con_dos_po_cada_uno_recibe_las_horas_completas():
    h, _ = _h()
    po = kpis.horas_por_po(h, _dim())
    total = po.groupby("product_owner")["horas"].sum()
    assert aprox(total["Leticia Cajas"], 6)
    assert aprox(total["Javier Villaseca"], 15.5) and aprox(total["Lucio Benavente"], 15.5)
    m = kpis.matriz_po_persona(po)
    assert aprox(m.loc["Javier Villaseca", "Ariana Amayo"], 9.5)
    assert aprox(m.loc["Lucio Benavente", "Sofía Soto"], 6)
    assert aprox(m.loc["Leticia Cajas", "Enrique Rioja"], 3)


def test_horas_por_po_proyecto_sin_dimension_va_a_sin_po():
    h, _ = _h()
    po = kpis.horas_por_po(h, pd.DataFrame(columns=kpis.COLUMNAS_DIM))
    assert set(po["product_owner"]) == {kpis.SIN_PO}
    assert aprox(po["horas"].sum(), 21.5)                    # 6 + 15.5


def test_promedio_por_tipo_entregable():
    h, _ = _h()
    r = kpis.horas_por_tipo_entregable(h).set_index("tipo_entregable")
    # "Planes de Acción": b1 (6 h) + b2 (9.5 h) -> 15.5 h / 2 actividades
    assert aprox(r.loc["Planes de Acción", "promedio"], 7.75) and r.loc["Planes de Acción", "actividades"] == 2
    # b2 también cuenta en "Informe": 9.5 h / 1 actividad
    assert aprox(r.loc["Informe", "promedio"], 9.5)
    # Tarea con casilla marcada y sin tipo
    assert aprox(r.loc[kpis.CON_ENTREGABLE_SIN_TIPO, "promedio"], 4)
    # a1 (3+3 h, dos personas) + c2 (2 h) -> 8 h / 2 actividades
    assert aprox(r.loc[kpis.SIN_ENTREGABLE, "horas"], 8)
    assert aprox(r.loc[kpis.SIN_ENTREGABLE, "promedio"], 4)


def test_tipos_de_entregable_con_distinta_mayuscula_se_agrupan():
    filas = [
        H(LUN, "Ruta", "x1", "X1", "Ariana Amayo", 2, "p2", "F1", None, "planes de acción"),
        H(LUN, "Ruta", "x2", "X2", "Ariana Amayo", 4, "p2", "F1", None, "Planes de Acción"),
    ]
    hist = kpis.preparar_historico(pd.DataFrame(filas))
    r = kpis.horas_por_tipo_entregable(kpis.filtrar_historico(hist, [LUN], _dim()))
    assert len(r) == 1 and aprox(r["promedio"].iloc[0], 3.0)


# ----------------------------------------------------------------- filtros ---

def test_filtro_por_base_y_por_tipo_de_proyecto():
    h, _ = _h(bases=["PM"])
    assert aprox(kpis.horas_por_proyecto(h)["horas"].sum(), 6)
    h2, _ = _h(tipos_proyecto=["Modelo PM"])
    assert set(h2["base_origen"]) == {"PM"}
    h3, _ = _h(tipos_proyecto=[kpis.SIN_PROYECTO])           # Tareas y filas sin proyecto
    assert set(h3["base_origen"]) == {"Tareas"}


# ----------------------------------------------------------------- exactitud ---

def test_exactitud_global_excluye_actividades_sin_plan():
    r = kpis.exactitud_planificacion(kpis.preparar_estado(_estado()))
    # a1 5/2 + b1 10/4 + b2 5/5 + b3 2/1 + b4 4/0 + c1 3/1 = plan 29, real 13 (a2 con plan 0 afuera)
    assert aprox(r["horas_plan"], 29) and aprox(r["horas_reales"], 13)
    assert aprox(r["exactitud"], 29 / 13) and r["excluidas_sin_plan"] == 1 and r["actividades"] == 6


def test_exactitud_solo_iniciadas():
    r = kpis.exactitud_planificacion(kpis.preparar_estado(_estado()), solo_iniciadas=True)
    assert aprox(r["horas_plan"], 25) and aprox(r["exactitud"], 25 / 13)   # sin b4


def test_exactitud_de_proyectos_y_detalle():
    r = kpis.exactitud_proyectos(kpis.preparar_estado(_estado()))
    assert aprox(r["horas_plan"], 26) and aprox(r["horas_reales"], 12)    # sin Tareas
    d = r["detalle"].set_index("proyecto")
    assert aprox(d.loc["Flujo", "plan"], 5) and aprox(d.loc["Flujo", "real"], 2)
    assert aprox(d.loc["F1: Aviation", "plan"], 21) and aprox(d.loc["F1: Aviation", "exactitud"], 2.1)
    assert "Otros" not in d.index


def test_exactitud_filtrada_por_persona_y_tipo():
    est = kpis.preparar_estado(_estado())
    r = kpis.exactitud_planificacion(est, personas=["Sofía Soto"])
    assert aprox(r["horas_plan"], 10) and aprox(r["exactitud"], 2.5)      # solo b1
    r2 = kpis.exactitud_planificacion(est, tipos_proyecto=["Modelo PM"], dim=_dim())
    assert aprox(r2["horas_plan"], 5) and aprox(r2["exactitud"], 2.5)     # solo a1


def test_exactitud_sin_horas_reales_es_none():
    est = kpis.preparar_estado(_estado())
    r = kpis.exactitud_planificacion(est[est["actividad_id"] == "b4"])
    assert r["exactitud"] is None and aprox(r["horas_plan"], 4)


# ------------------------------------------------------------------ alertas ---

def test_alertas():
    hist = kpis.preparar_historico(_historico())
    dias = kpis.seleccionar_dias(_registro(), EQ, INI, FIN)
    a = kpis.alertas(hist[hist["fecha"].isin(set(dias["con_foto"]))], EQ, dias)
    assert list(a["desconocidos"]["persona"]) == ["Sin asignar"]
    assert aprox(a["desconocidos"]["horas"].iloc[0], 1)
    # Ariana registró 9.5 h el miércoles (> 9)
    assert len(a["exceso_horas_dia"]) == 1
    assert a["exceso_horas_dia"]["persona"].iloc[0] == "Ariana Amayo"
    assert a["dias_sin_foto"] == [date(2026, 10, 23)]
    assert a["dias_no_laborables"] == [date(2026, 10, 24)]
    sh = a["sin_horas"]
    assert len(sh) == 18                                     # 6 personas x 4 días - 6 con horas
    assert set(sh[sh["fecha"] == date(2026, 10, 19)]["persona"]) == {
        "Gianfranco Fernandez", "Marilyn Sáenz", "Anghelo Aguirre", "Sofía Soto",
    }
    assert len(sh[sh["fecha"] == date(2026, 10, 22)]) == 6   # el jueves solo hubo "Sin asignar"


# ---------------------------------------------------------- calcular_todo ---

def test_calcular_todo_integra_los_indicadores():
    r = kpis.calcular_todo(_datos(), EQ, INI, FIN)
    assert aprox(r["ocupabilidad"]["ocupabilidad"], 27.5 / 204)
    assert aprox(r["programa_pm"]["horas"], 8)
    assert aprox(r["exactitud_global"]["exactitud"], 29 / 13)
    assert aprox(r["exactitud_proyectos"]["exactitud"], 26 / 12)
    assert not r["matriz_po"].empty and not r["tipo_entregable"].empty
    assert r["dias"]["sin_foto"] == [date(2026, 10, 23)]


def test_calcular_todo_con_filtros():
    r = kpis.calcular_todo(_datos(), EQ, INI, FIN, personas=["Sofía Soto"], bases=["Ruta"])
    assert aprox(r["ocupabilidad"]["horas"], 6) and aprox(r["ocupabilidad"]["disponibles"], 24)
    assert list(r["horas_por_proyecto"]["proyecto"]) == ["F1: Aviation"]
    assert aprox(r["exactitud_global"]["horas_plan"], 10)    # solo b1
    # las alertas no se filtran: "Sin asignar" sigue apareciendo
    assert list(r["alertas"]["desconocidos"]["persona"]) == ["Sin asignar"]


def test_calcular_todo_con_datos_vacios_no_falla():
    vacio = {
        "historico": pd.DataFrame(columns=kpis.COLUMNAS_FOTO),
        "estado": pd.DataFrame(columns=kpis.COLUMNAS_ESTADO),
        "registro": pd.DataFrame(columns=kpis.COLUMNAS_REGISTRO),
        "dim": pd.DataFrame(columns=kpis.COLUMNAS_DIM),
    }
    r = kpis.calcular_todo(vacio, EQ, INI, FIN)
    assert r["ocupabilidad"]["estado"] == "Sin datos" and r["ocupabilidad"]["ocupabilidad"] is None
    assert r["exactitud_global"]["exactitud"] is None
    assert r["horas_por_proyecto"].empty and r["horas_por_po"].empty and r["tipo_entregable"].empty
    assert aprox(r["programa_pm"]["horas"], 0)
    assert len(r["alertas"]["dias_sin_foto"]) == 5          # lun-vie sin ninguna corrida


def test_historico_desde_csv_texto_se_interpreta_bien():
    """El CSV se lee todo como texto: fechas, números y booleanos deben convertirse."""
    csv = _historico().astype(str)
    csv["tiene_entregable"] = csv["tiene_entregable"].replace({"True": "True", "False": "False"})
    r = kpis.calcular_todo(
        {"historico": csv, "estado": _estado().astype(str), "registro": _registro().astype(str),
         "dim": _dim().astype(str)}, EQ, INI, FIN)
    assert aprox(r["ocupabilidad"]["horas"], 27.5)
    assert aprox(r["exactitud_global"]["horas_plan"], 29)


def test_columnas_faltantes_dan_error_claro():
    try:
        kpis.preparar_historico(_historico().drop(columns=["horas_hoy"]))
    except ValueError as e:
        assert "horas_hoy" in str(e) and "Disponibles" in str(e)
    else:
        raise AssertionError("Debió fallar")
