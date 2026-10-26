from datetime import date
from pathlib import Path

import pandas as pd
import yaml

import kpis
import vista
from capacidad import Equipo
from tests import test_kpis as tk

RAIZ = Path(__file__).resolve().parent.parent
with open(RAIZ / "config_capacidad.yaml", encoding="utf-8") as _f:
    EQ = Equipo(yaml.safe_load(_f))


def _resultado():
    return kpis.calcular_todo(tk._datos(), EQ, tk.INI, tk.FIN)


# ----------------------------------------------------------------- formatos ---

def test_formatos_y_valores_vacios():
    assert vista.fmt_pct(0.1348) == "13%" and vista.fmt_pct(0.1348, 1) == "13.5%"
    assert vista.fmt_pct(None) == "—" and vista.fmt_pct(float("nan")) == "—"
    assert vista.fmt_horas(2028.25) == "2,028.2 h" and vista.fmt_horas(None) == "—"
    assert vista.fmt_fecha("2026-10-23") == "23/10/2026"
    assert vista.etiqueta_estado("SOBRECARGA") == "▲ SOBRECARGA"
    assert vista.etiqueta_estado("Sin datos") == "– Sin datos"


def test_hero_lleva_icono_texto_y_escapa_html():
    h = vista.html_hero("132%", "SOBRECARGA")
    assert "132%" in h and "▲" in h and "SOBRECARGA" in h and vista.COLOR_ESTADO["SOBRECARGA"] in h
    assert "&lt;b&gt;" in vista.html_hero("<b>", "SUBCARGA")        # no deja pasar HTML ajeno


# ------------------------------------------------------------------- tablas ---

def test_tabla_y_grafico_de_trabajadores():
    detalle = _resultado()["ocupabilidad"]["detalle"].copy()
    # un integrante sin capacidad (ocupabilidad None) debe quedar al final y como "Sin datos"
    detalle.loc[detalle["persona"] == "Marilyn Sáenz", ["ocupabilidad", "estado"]] = [None, "Sin datos"]
    t = vista.df_trabajadores(detalle)
    assert t["Persona"].iloc[0] == "Ariana Amayo"                      # 12.5 / 36 = 34.7%, la más alta
    assert t["Persona"].iloc[-1] == "Marilyn Sáenz" and t["Ocupabilidad"].iloc[-1] == "—"
    assert t[t["Persona"] == "Ariana Amayo"]["Ocupabilidad"].iloc[0] == "35%"
    assert t[t["Persona"] == "Sofía Soto"]["Estado"].iloc[0] == "▼ SUBCARGA"
    g = vista.df_grafico_trabajadores(detalle)
    assert abs(g["pct"].iloc[0] - 12.5 / 36 * 100) < 1e-9
    assert g[g["persona"] == "Marilyn Sáenz"]["pct"].iloc[0] == 0.0     # None se dibuja en 0 pero etiquetado "—"
    assert g[g["persona"] == "Marilyn Sáenz"]["etiqueta"].iloc[0] == "—"


def test_tabla_exactitud_muestra_guion_sin_horas_reales():
    est = kpis.preparar_estado(tk._estado())
    detalle = kpis.exactitud_proyectos(est)["detalle"]
    t = vista.tabla_exactitud(detalle).set_index("Proyecto")
    assert t.loc["F1: Aviation", "Plan (h)"] == 21.0 and t.loc["F1: Aviation", "Exactitud"] == "210%"
    sin_reales = kpis.exactitud_planificacion(est[est["actividad_id"] == "b4"])["detalle"]
    assert vista.tabla_exactitud(sin_reales)["Exactitud"].iloc[0] == "—"
    assert vista.tabla_exactitud(pd.DataFrame()).empty


def test_alertas_para_mostrar_y_conteo():
    a = _resultado()["alertas"]
    t = vista.alertas_para_mostrar(a)
    assert list(t["desconocidos"]["Persona"]) == ["Sin asignar"]
    assert t["exceso_horas_dia"]["Día"].iloc[0] == "21/10/2026"
    assert list(t["dias_sin_foto"]["Día"]) == ["23/10/2026"]
    assert len(t["sin_horas"]) == 18 and t["sin_horas"]["Día"].iloc[0] == "19/10/2026"
    assert vista.n_alertas(a) == 1 + 18 + 1 + 1


# ------------------------------------------------------------------ filtros ---

def test_opciones_y_valor_de_filtros():
    op = vista.opciones_filtros(EQ, tk._dim())
    assert op["personas"] == ["Gianfranco Fernandez", "Ariana Amayo", "Anghelo Aguirre",
                              "Marilyn Sáenz", "Enrique Rioja", "Sofía Soto"]
    assert op["tipos"] == ["Modelo PM", "Ruta de Eficiencia", kpis.SIN_PROYECTO]
    assert vista.opciones_filtros(EQ, None)["tipos"] == [kpis.SIN_PROYECTO]
    assert vista.valor_filtro(["a", "b"], ["b", "a"]) is None        # todo seleccionado = sin filtro
    assert vista.valor_filtro(["a"], ["a", "b"]) == ["a"]
    assert vista.valor_filtro([], ["a", "b"]) == []


def test_rango_por_defecto():
    assert vista.rango_por_defecto(tk._registro(), date(2026, 10, 30)) == (date(2026, 10, 19), date(2026, 10, 24))
    vacio = pd.DataFrame(columns=kpis.COLUMNAS_REGISTRO)
    assert vista.rango_por_defecto(vacio, date(2026, 10, 30)) == (date(2026, 10, 30), date(2026, 10, 30))


# ----------------------------------------------------------- vista diaria ---

def _diaria_jue():
    from tests import test_diaria as td
    return kpis.ocupabilidad_diaria(td._hist(), EQ, date(2026, 10, 22))


def test_fechas_con_foto():
    assert vista.fechas_con_foto(tk._registro()) == [date(2026, 10, n) for n in (19, 20, 21, 22, 24)]
    assert vista.fechas_con_foto(tk._registro(), "2026-10-20", "2026-10-22") == [
        date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22)]
    assert vista.fechas_con_foto(tk._registro(), "2026-11-01") == []


def test_totales_y_tabla_diaria():
    res = _diaria_jue()["resumen"]
    t = vista.df_diaria_totales(res)
    assert list(t["persona"])[:2] == ["Ariana Amayo", "Sofía Soto"]
    assert t["etiqueta"].iloc[0] == "6.5 h" and t["ocup_etiqueta"].iloc[0] == "72%"
    assert t[t["persona"] == "Marilyn Sáenz"]["etiqueta"].iloc[0] == "0 h"       # quien no registró se ve
    tabla = vista.tabla_diaria(res)
    assert list(tabla.columns) == ["Persona", "Condición", "Done", "En curso", "Stuck", "Sin empezar",
                                   "Otro", "Total (h)", "Capacidad (h)", "Ocupabilidad", "Estado"]
    fila = tabla[tabla["Persona"] == "Sofía Soto"].iloc[0]
    assert fila["Stuck"] == 1.0 and fila["Capacidad (h)"] == 6.0 and fila["Estado"] == "● CARGA ADECUADA"
    sin_otro = res.assign(Otro=0.0)
    assert "Otro" not in vista.tabla_diaria(sin_otro).columns                  # solo aparece si hay horas


def test_aviso_de_estados_sin_clasificar():
    assert vista.aviso_estados([]) is None
    msg = vista.aviso_estados(["Estado raro", "(vacío)"])
    assert "«Estado raro»" in msg and "estados" in msg
    assert set(vista.COLOR_ESTADO_TAREA) == set(kpis.ESTADOS_ORDEN)            # un color por estado


# ------------------------------------------------------------------- avisos ---

def test_ultima_corrida():
    reg = tk._registro()
    reg["ejecutado_en"] = reg["fecha"] + "T17:31:00-05:00"
    u = vista.ultima_corrida(reg)
    assert u == {"fecha": date(2026, 10, 24), "ejecutado_en": "2026-10-24T17:31:00-05:00",
                 "momento": "cierre"}                                   # sin columna momento = cierre
    assert vista.ultima_corrida(pd.DataFrame(columns=kpis.COLUMNAS_REGISTRO)) is None
    assert vista.ultima_corrida(reg.assign(ok=False)) is None


def test_dia_laborable_anterior_salta_fines_de_semana_y_feriados():
    assert vista.dia_laborable_anterior(EQ, date(2026, 10, 26)) == date(2026, 10, 23)   # lunes -> viernes
    assert vista.dia_laborable_anterior(EQ, date(2026, 10, 20)) == date(2026, 10, 19)
    assert vista.dia_laborable_anterior(EQ, date(2026, 10, 9)) == date(2026, 10, 7)     # jueves 8 es feriado


def test_aviso_de_ultima_foto():
    f = lambda d: {"fecha": d, "ejecutado_en": None}  # noqa: E731
    assert "Todavía no hay fotos" in vista.aviso_ultima_foto(None, EQ, date(2026, 10, 23))
    # hoy viernes 23: la foto de hoy aún no existe (se toma a las 17:30); la del jueves 22 sí -> sin aviso
    assert vista.aviso_ultima_foto(f(date(2026, 10, 22)), EQ, date(2026, 10, 23)) is None
    msg = vista.aviso_ultima_foto(f(date(2026, 10, 21)), EQ, date(2026, 10, 23))
    assert "22/10/2026" in msg and "21/10/2026" in msg
    # fin de semana: la foto del viernes es suficiente
    assert vista.aviso_ultima_foto(f(date(2026, 10, 23)), EQ, date(2026, 10, 24)) is None
    assert vista.aviso_ultima_foto(f(date(2026, 10, 22)), EQ, date(2026, 10, 26)) is not None


def test_fotos_de_manana_y_de_cierre_se_distinguen():
    reg = tk._registro().assign(momento="cierre", ejecutado_en="x")
    manana = pd.DataFrame({"fecha": ["2026-10-26"], "ok": [True], "momento": ["manana"],
                           "ejecutado_en": ["2026-10-26T08:31:00-05:00"]})
    reg = pd.concat([reg, manana], ignore_index=True)
    assert vista.fechas_con_foto(reg)[-1] == date(2026, 10, 24)                    # rangos: solo cierre
    assert vista.fechas_con_foto(reg, momento=None)[-1] == date(2026, 10, 26)      # calendario diario
    assert vista.fechas_con_foto(reg, momento="manana") == [date(2026, 10, 26)]
    u = vista.ultima_corrida(reg)
    assert u["fecha"] == date(2026, 10, 26) and u["momento"] == "manana"
    assert vista.ultima_corrida(reg, "cierre")["fecha"] == date(2026, 10, 24)
    assert vista.rango_por_defecto(reg, date(2026, 10, 30)) == (date(2026, 10, 19), date(2026, 10, 24))
