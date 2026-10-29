"""Dashboard de Ocupabilidad del Equipo (Streamlit).

    streamlit run app.py

Lee de Supabase (rol de solo lectura) si hay SUPABASE_DB_URL_RO en st.secrets o en el entorno;
si no hay o la conexión falla, lee los CSV de data/processed/ (los genera build_master.py).
Calcula todo con kpis.py. No consulta Notion: solo muestra lo último que guardó la foto diaria.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import streamlit as st

import kpis
import vista
from acceso import exigir_acceso
from capacidad import Equipo

RAIZ = Path(__file__).resolve().parent
AZUL = "#2a78d6"                      # serie única (slot 1 de la paleta)
RAMPA_AZUL = ["#cde2fb", "#2a78d6", "#0d366b"]
GRIS = "#898781"
BASES = {
    "PM": "Actividades PM",
    "Ruta": "Actividades & Entregables",
    "Tareas": "Tareas Extracurriculares",
}
ARCHIVOS = ["historico_horas_diarias.csv", "estado_actual.csv",
            "registro_corridas.csv", "dim_intervenciones.csv"]
MAX_PROYECTOS = 12
# Streamlit dibuja los gráficos de Altair con la altura indicada como TOTAL (ejes y leyenda
# incluidos). Estas constantes suman esas bandas a la altura de la zona de barras.
ALTO_EJES = 70      # eje x: marcas, etiquetas, título y márgenes
ALTO_LEYENDA = 45   # leyenda inferior del gráfico por trabajador
ALTO_ETIQ_X = 60    # etiquetas inclinadas del mapa de calor
ALTO_DIARIA = 260   # zona de barras del gráfico de ocupabilidad diaria


# ------------------------------------------------------------------ ayudas ---

def _mostrar(chart) -> None:
    """Dibuja un gráfico a todo el ancho (la forma de pedirlo cambió entre versiones de Streamlit)."""
    try:
        st.altair_chart(chart, width="stretch")
    except Exception:
        st.altair_chart(chart, use_container_width=True)


def _tabla(df: pd.DataFrame, **kw) -> None:
    try:
        st.dataframe(df, width="stretch", hide_index=True, **kw)
    except Exception:
        st.dataframe(df, use_container_width=True, hide_index=True, **kw)


def _seccion(nombre: str, fn, *args) -> None:
    """Si una sección falla, el resto del dashboard sigue. Con OCUPABILIDAD_DEBUG=1 se propaga."""
    try:
        fn(*args)
    except Exception as e:  # noqa: BLE001
        if os.environ.get("OCUPABILIDAD_DEBUG"):
            raise
        st.error(f"No se pudo mostrar «{nombre}»: {e}")


def _firma(raiz: Path, config: dict) -> tuple:
    carpeta = Path(raiz) / config["rutas"]["processed"]
    return tuple(
        (n, (carpeta / n).stat().st_mtime_ns if (carpeta / n).exists() else 0) for n in ARCHIVOS
    )


@st.cache_data(show_spinner=False)
def _cargar(raiz: str, processed: str, firma: tuple) -> dict:
    """La firma (fecha de modificación de los CSV) invalida el caché cuando hay una foto nueva."""
    return kpis.cargar_datos(Path(raiz), {"rutas": {"processed": processed}})


def _hay_db_ro() -> bool:
    """¿Hay credencial de solo lectura? Se busca en st.secrets, en el entorno y en .env."""
    try:
        if "SUPABASE_DB_URL_RO" in st.secrets:      # db.conectar lee la variable del entorno
            os.environ.setdefault("SUPABASE_DB_URL_RO", str(st.secrets["SUPABASE_DB_URL_RO"]))
    except Exception:  # noqa: BLE001 - sin secrets.toml Streamlit lanza una excepción
        pass
    from dotenv import load_dotenv

    load_dotenv(RAIZ / ".env")
    return bool(os.environ.get("SUPABASE_DB_URL_RO"))


@st.cache_data(ttl=60, show_spinner=False)
def _leer_db(config_yaml: dict) -> tuple[dict, dict]:
    """Datos y config desde Supabase (rol de solo lectura). Siempre cierra la conexión."""
    import db

    conn = db.conectar(solo_lectura=True)
    try:
        return db.cargar_datos_db(conn), db.config_desde_db(conn, config_yaml)
    finally:
        conn.close()


def _cargar_origen(raiz: Path, config_yaml: dict) -> tuple[dict, dict, str]:
    """(datos, config, fuente). Supabase si hay credencial; si no o si falla, los CSV locales.

    Los mensajes visibles nunca incluyen la cadena de conexión ni el texto de la excepción.
    """
    fallo_db = False
    if _hay_db_ro():
        try:
            datos, config = _leer_db(config_yaml)
            return datos, config, "Supabase"
        except Exception as e:  # noqa: BLE001 - se informa solo el tipo, nunca el mensaje crudo
            fallo_db = True
            print(f"Supabase no disponible ({type(e).__name__}).", file=sys.stderr)

    firma = _firma(raiz, config_yaml)
    if any(mtime for _, mtime in firma):
        if fallo_db:
            st.warning("No se pudo leer Supabase; se muestran datos locales.")
        return _cargar(str(raiz), config_yaml["rutas"]["processed"], firma), config_yaml, "CSV local"

    st.error("Por ahora no hay datos para mostrar"
             + (": no se pudo leer Supabase y tampoco hay archivos locales." if fallo_db else "."))
    st.info(vista.aviso_ultima_foto(None, None, None))
    st.stop()


# ----------------------------------------------------------------- gráficos ---

def _barras_h(df: pd.DataFrame, cat: str, val: str, titulo_val: str, tooltip: list,
              formato: str = ".1f"):
    base = alt.Chart(df).encode(
        y=alt.Y(f"{cat}:N", sort=list(df[cat]), title=None,
                axis=alt.Axis(labelLimit=300, ticks=False, domain=False)),
        x=alt.X(f"{val}:Q", title=titulo_val),
        tooltip=tooltip,
    )
    barras = base.mark_bar(color=AZUL, size=14, cornerRadiusEnd=4)
    textos = base.mark_text(align="left", dx=5, fontSize=11).encode(
        text=alt.Text(f"{val}:Q", format=formato))
    return (barras + textos).properties(height=max(60, 28 * len(df)) + ALTO_EJES)


def _grafico_diaria(largo: pd.DataFrame, totales: pd.DataFrame):
    """Barras apiladas por estado, una por integrante, con el total encima y la capacidad del día."""
    orden = list(totales["persona"])
    tope = max(float(totales["capacidad"].max()), float(totales["total"].max()), 1.0) * 1.18
    escala = alt.Scale(domain=[0, tope], nice=False)
    titulo_y = "Horas asignadas hoy"
    x = alt.X("persona:N", sort=orden, title=None,
              axis=alt.Axis(labelAngle=0, labelLimit=170, ticks=False, domain=False))
    barras = alt.Chart(largo).mark_bar(size=38).encode(
        x=x,
        y=alt.Y("horas:Q", scale=escala, title=titulo_y, stack="zero"),
        color=alt.Color(
            "estado:N",
            scale=alt.Scale(domain=list(vista.COLOR_ESTADO_TAREA),
                            range=list(vista.COLOR_ESTADO_TAREA.values())),
            legend=alt.Legend(title="Estado de la actividad", orient="bottom"),
        ),
        order=alt.Order("orden:Q", sort="ascending"),
        tooltip=[alt.Tooltip("persona:N", title="Persona"),
                 alt.Tooltip("estado:N", title="Estado"),
                 alt.Tooltip("horas:Q", title="Horas", format=".1f")],
    )
    capacidad = alt.Chart(totales).mark_tick(color=GRIS, thickness=2, size=56).encode(
        x=x, y=alt.Y("capacidad:Q", scale=escala, title=titulo_y),
        tooltip=[alt.Tooltip("persona:N", title="Persona"),
                 alt.Tooltip("capacidad:Q", title="Capacidad del día (h)", format=".1f"),
                 alt.Tooltip("etiqueta:N", title="Horas asignadas"),
                 alt.Tooltip("ocup_etiqueta:N", title="Ocupabilidad"),
                 alt.Tooltip("estado_carga:N", title="Estado de carga")],
    )
    totales_txt = alt.Chart(totales).mark_text(dy=-10, fontSize=12, fontWeight="bold").encode(
        x=x, y=alt.Y("total:Q", scale=escala, title=titulo_y), text="etiqueta:N")
    return (barras + capacidad + totales_txt).properties(
        height=ALTO_DIARIA + ALTO_EJES + ALTO_LEYENDA)


def _grafico_trabajadores(d: pd.DataFrame):
    orden = list(d["persona"])
    tope = max(110.0, float(d["pct"].max()) * 1.12)
    base = alt.Chart(d).encode(
        y=alt.Y("persona:N", sort=orden, title=None,
                axis=alt.Axis(ticks=False, domain=False, labelLimit=300)),
        x=alt.X("pct:Q", title="Ocupabilidad (%)", scale=alt.Scale(domain=[0, tope])),
    )
    barras = base.mark_bar(size=14, cornerRadiusEnd=4).encode(
        color=alt.Color(
            "estado:N",
            scale=alt.Scale(domain=list(vista.COLOR_ESTADO), range=list(vista.COLOR_ESTADO.values())),
            legend=alt.Legend(title="Estado", orient="bottom"),
        ),
        tooltip=[alt.Tooltip("persona:N", title="Persona"),
                 alt.Tooltip("condicion:N", title="Condición"),
                 alt.Tooltip("horas:Q", title="Horas", format=".1f"),
                 alt.Tooltip("disponibles:Q", title="Disponibles", format=".1f"),
                 alt.Tooltip("etiqueta:N", title="Ocupabilidad"),
                 alt.Tooltip("estado:N", title="Estado")],
    )
    textos = base.mark_text(align="left", dx=5, fontSize=11).encode(text="etiqueta:N")
    # umbrales oficiales del equipo (líneas finas y continuas)
    cortes = pd.DataFrame({"x": [70.0, 91.0]})
    reglas = alt.Chart(cortes).mark_rule(strokeWidth=1, color=GRIS).encode(x="x:Q")
    return (barras + textos + reglas).properties(
        height=max(120, 32 * len(d)) + ALTO_EJES + ALTO_LEYENDA)


def _heatmap(largo: pd.DataFrame):
    d = largo[largo["horas"] > 0]
    base = alt.Chart(d).encode(
        x=alt.X("persona:N", title=None, axis=alt.Axis(labelAngle=-35)),
        y=alt.Y("product_owner:N", title=None, axis=alt.Axis(labelLimit=240)),
    )
    celdas = base.mark_rect(cornerRadius=3).encode(
        color=alt.Color("horas:Q", scale=alt.Scale(range=RAMPA_AZUL), legend=alt.Legend(title="Horas")),
        tooltip=[alt.Tooltip("product_owner:N", title="Product Owner"),
                 alt.Tooltip("persona:N", title="Persona"),
                 alt.Tooltip("horas:Q", title="Horas", format=".1f")],
    )
    umbral = float(d["horas"].max()) * 0.55
    textos = base.mark_text(fontSize=11).encode(
        text=alt.Text("horas:Q", format=".1f"),
        color=alt.condition(alt.datum.horas > umbral, alt.value("white"), alt.value("#0b0b0b")),
    )
    n_po = d["product_owner"].nunique()
    return (celdas + textos).properties(height=max(120, 34 * n_po) + ALTO_EJES + ALTO_ETIQ_X)


# ----------------------------------------------------------------- secciones ---

def _diaria(r: dict) -> None:
    d = r["diaria"]
    momento = d.get("momento", "cierre")
    etiqueta = vista.ETIQUETA_MOMENTO.get(momento, momento)
    if d["dia"] is None:
        st.subheader("Ocupabilidad diaria")
        st.info("No hay fotos diarias para ese día.")
        return
    st.subheader(f"Ocupabilidad diaria · {vista.fmt_fecha(d['dia'])} · {etiqueta}")
    if not d.get("foto_disponible", True):
        st.info(f"No hay foto de {etiqueta} para el {vista.fmt_fecha(d['dia'])}. "
                "Elige el otro momento u otro día.")
        return
    st.caption(
        f"Equipo: {vista.fmt_horas(d['horas'])} asignadas de {vista.fmt_horas(d['capacidad'])} "
        f"de capacidad · {vista.fmt_pct(d['ocupabilidad'])} · {vista.etiqueta_estado(d['estado'])}")
    aviso = vista.aviso_estados(d["estados_sin_clasificar"])
    if aviso:
        st.warning(aviso)
    if d["resumen"].empty:
        st.info("Sin integrantes para los filtros elegidos.")
        return
    _mostrar(_grafico_diaria(d["largo"], vista.df_diaria_totales(d["resumen"])))
    alcance = (f"Solo se cuentan actividades en estado {', '.join(r['estados_validos'])}."
               if r.get("estados_validos")
               else "Se muestran todos los estados de las actividades: es la planificación del día.")
    st.caption("Horas «asignadas hoy» de cada integrante, apiladas según el estado de la actividad. "
               "La marca gris es su capacidad del día; un 0 significa que no registró horas. " + alcance)
    with st.expander("Ver tabla"):
        _tabla(vista.tabla_diaria(d["resumen"]))


def _tarjetas(r: dict, equipo: Equipo) -> None:
    t1, t2, t3, t4 = st.columns([1.4, 1, 1, 1])
    oc = r["ocupabilidad"]
    with t1:
        st.caption("Ocupabilidad del área")
        st.markdown(vista.html_hero(vista.fmt_pct(oc["ocupabilidad"]), oc["estado"]),
                    unsafe_allow_html=True)
        st.caption(f"{vista.fmt_horas(oc['horas'])} de {vista.fmt_horas(oc['disponibles'])} "
                   f"disponibles · {oc['dias']} día(s) laborable(s)")
    eg, ep = r["exactitud_global"], r["exactitud_proyectos"]
    alcance = "con horas en el rango" if r.get("rango_activo") else "al día de hoy"
    with t2:
        st.metric("Exactitud de planificación global", vista.fmt_pct(eg["exactitud"]),
                  help="Horas planificadas ÷ horas reales (acumulado de cada actividad a la última "
                       "corrida). Con el rango activo, solo las actividades con horas en ese rango. "
                       "Mayor a 100%: se planificó más de lo ejecutado.")
        st.caption(f"Plan {vista.fmt_horas(eg['horas_plan'])} · Real {vista.fmt_horas(eg['horas_reales'])} "
                   f"· {eg['actividades']} actividades · {alcance}")
    with t3:
        st.metric("Exactitud de planificación de proyectos", vista.fmt_pct(ep["exactitud"]),
                  help="Igual que la global, solo con Actividades & Entregables y Actividades PM.")
        st.caption(f"Plan {vista.fmt_horas(ep['horas_plan'])} · Real {vista.fmt_horas(ep['horas_reales'])} "
                   f"· {ep['actividades']} actividades · {alcance}")
    with t4:
        pm = r["programa_pm"]
        st.metric(f"Horas del programa {pm['programa']}", vista.fmt_horas(pm["horas"]),
                  help="Horas-persona de las actividades de ese programa en el rango.")
        por_base = dict(zip(pm["por_base"]["base_origen"], pm["por_base"]["horas"]))
        st.caption(" · ".join(f"{BASES.get(b, b)} {h:.1f} h" for b, h in por_base.items()) or "Sin horas")


def _por_trabajador(r: dict) -> None:
    st.subheader("Ocupabilidad por trabajador")
    detalle = r["ocupabilidad"]["detalle"]
    if detalle.empty:
        st.info("Sin datos para los filtros elegidos.")
        return
    _mostrar(_grafico_trabajadores(vista.df_grafico_trabajadores(detalle)))
    st.caption("Las líneas marcan los cortes del equipo: 70% y 91%.")
    with st.expander("Ver tabla"):
        _tabla(vista.df_trabajadores(detalle))


def _por_proyecto(r: dict) -> None:
    st.subheader("Horas por proyecto")
    df = r["horas_por_proyecto"]
    if df.empty:
        st.info("Sin horas de proyectos en el rango.")
        return
    top = df.head(MAX_PROYECTOS)
    _mostrar(_barras_h(top, "proyecto", "horas", "Horas-persona", [
        alt.Tooltip("proyecto:N", title="Proyecto"),
        alt.Tooltip("tipo_proyecto:N", title="Tipo"),
        alt.Tooltip("horas:Q", title="Horas", format=".1f")]))
    if len(df) > MAX_PROYECTOS:
        st.caption(f"Se muestran los {MAX_PROYECTOS} proyectos con más horas de {len(df)}.")
    with st.expander("Ver tabla"):
        _tabla(df.rename(columns={"proyecto": "Proyecto", "tipo_proyecto": "Tipo", "horas": "Horas"})
               [["Proyecto", "Tipo", "Horas"]])


def _por_po(r: dict) -> None:
    st.subheader("Horas por Product Owner y persona")
    po = r["horas_por_po"]
    if po.empty or (po["horas"] <= 0).all():
        st.info("Sin horas de proyectos en el rango.")
        return
    _mostrar(_heatmap(po))
    st.caption("Un proyecto con dos Product Owner cuenta sus horas completas en cada uno; "
               "no incluye Tareas Extracurriculares.")
    with st.expander("Ver tabla"):
        _tabla(r["matriz_po"].round(2).reset_index().rename(columns={"product_owner": "Product Owner"}))


def _por_tipo(r: dict) -> None:
    st.subheader("Promedio de horas por tipo de entregable")
    df = r["tipo_entregable"]
    if df.empty:
        st.info("Sin horas en el rango.")
        return
    _mostrar(_barras_h(df, "tipo_entregable", "promedio", "Horas por actividad", [
        alt.Tooltip("tipo_entregable:N", title="Tipo de entregable"),
        alt.Tooltip("promedio:Q", title="Promedio (h por actividad)", format=".2f"),
        alt.Tooltip("horas:Q", title="Horas", format=".1f"),
        alt.Tooltip("actividades:Q", title="Actividades")], formato=".2f"))
    st.caption("Una actividad con dos tipos cuenta en ambos. «Sin entregable» y «Con entregable "
               "(sin tipo)» agrupan lo que aún no tiene tipo registrado.")
    with st.expander("Ver tabla"):
        _tabla(df.rename(columns={"tipo_entregable": "Tipo de entregable", "horas": "Horas",
                                  "actividades": "Actividades", "promedio": "Promedio"}).round(2))


def _exactitud_proyectos(r: dict) -> None:
    st.subheader("Exactitud de planificación por proyecto")
    detalle = r["exactitud_proyectos"]["detalle"]
    if detalle.empty:
        st.info("Sin actividades con horas planificadas.")
        return
    _tabla(vista.tabla_exactitud(detalle))
    st.caption("«—» significa que el proyecto aún no tiene horas reales. Plan y real son acumulados a la "
               "última corrida; con el rango activo, solo cuentan las actividades con horas en ese rango.")


def _alertas(r: dict, config: dict) -> None:
    a = r["alertas"]
    t = vista.alertas_para_mostrar(a)
    umbral = float(config.get("alerta_horas_dia", 9))
    st.subheader(f"Alertas de calidad de datos ({vista.n_alertas(a)})")
    tabs = st.tabs([f"No reconocidos ({len(t['desconocidos'])})",
                    f"Sin horas ({len(t['sin_horas'])})",
                    f"Más de {umbral:g} h/día ({len(t['exceso_horas_dia'])})",
                    f"Días sin foto ({len(t['dias_sin_foto'])})"])
    textos = [
        "Personas fuera de la lista del equipo con horas (incluye «Sin asignar»). No cuentan en la ocupabilidad.",
        "Integrantes sin horas registradas en un día laborable con foto.",
        "Personas que registraron más horas de las que caben en un día.",
        "Días laborables en que el script no corrió; no entran al cálculo.",
    ]
    for tab, clave, texto in zip(tabs, ["desconocidos", "sin_horas", "exceso_horas_dia", "dias_sin_foto"], textos):
        with tab:
            st.caption(texto)
            if t[clave].empty:
                st.success("Sin alertas.")
            else:
                _tabla(t[clave])
    if a["dias_no_laborables"]:
        st.caption("Días con foto que se ignoran por ser fin de semana o feriado: "
                   + ", ".join(vista.fmt_fecha(d) for d in a["dias_no_laborables"]))


def _como_se_calcula(equipo: Equipo) -> None:
    with st.expander("Cómo se calcula"):
        st.markdown(
            "- **Ocupabilidad** = horas «asignadas hoy» registradas ÷ horas disponibles, solo en los días "
            "laborables con foto (sin fines de semana, feriados ni días en que el script no corrió).\n"
            f"- **Estado:** más de {equipo.umbral_sobrecarga:.0%} es SOBRECARGA, más de "
            f"{equipo.umbral_adecuada:.0%} es CARGA ADECUADA y de ahí hacia abajo SUBCARGA.\n"
            "- Todas las horas son **horas-persona**: si dos personas figuran en una actividad, cada una "
            "cuenta las horas completas.\n"
            "- Solo cuentan las personas del equipo. Quien no esté en la lista aparece en las alertas.\n"
            "- **Exactitud** = horas planificadas ÷ horas reales, acumulado a la última corrida. Se excluyen "
            "las actividades sin plan; con el interruptor activo, también las que aún no tienen horas reales. "
            "Con «Filtrar por rango de fechas» encendido, solo cuentan las actividades que tuvieron horas "
            "del equipo dentro del rango.\n"
            "- **Ocupabilidad diaria** no depende del rango: usa su propio calendario."
        )


# ---------------------------------------------------------------------- app ---

def main(raiz: Path = RAIZ, config: dict | None = None) -> None:
    st.set_page_config(page_title="Ocupabilidad del Equipo", page_icon="📊", layout="wide")
    exigir_acceso()      # nada de lo que sigue (config, datos, títulos) corre sin sesión válida
    if config is None:
        from extract.notion_client import cargar_config
        config = cargar_config()
    hoy = datetime.now(ZoneInfo(config.get("zona_horaria", "America/Lima"))).date()

    encabezado, recargar = st.columns([6, 1])
    with encabezado:
        st.title("Ocupabilidad del Equipo")
    with recargar:
        if st.button("Recargar datos"):
            st.cache_data.clear()
            st.rerun()

    datos, config, fuente = _cargar_origen(raiz, config)   # config: el de la base si se usó
    equipo = Equipo(config)
    st.caption(f"Fuente: {fuente}")
    ultima = vista.ultima_corrida(datos["registro"])
    if ultima is None:
        st.info(vista.aviso_ultima_foto(None, equipo, hoy))
        st.stop()
    hora = f" (corrida {ultima['ejecutado_en'][11:16]})" if ultima["ejecutado_en"] else ""
    st.caption(f"Última foto: {vista.fmt_fecha(ultima['fecha'])} · "
               f"{vista.ETIQUETA_MOMENTO.get(ultima['momento'], ultima['momento'])}{hora}. "
               "El equipo registra horas hasta las 17:30; la foto del día se reemplaza al volver a correr.")
    aviso = vista.aviso_ultima_foto(vista.ultima_corrida(datos["registro"], "cierre"), equipo, hoy)
    if aviso:
        st.warning(aviso)

    # --- una sola fila de filtros sobre todo lo que filtran ---
    op = vista.opciones_filtros(equipo, datos["dim"])
    ini_def, fin_def = vista.rango_por_defecto(datos["registro"], hoy)
    with st.container(border=True):
        f1, f1b, f2, f3, f4, f5 = st.columns([2.2, 1.5, 2, 1.8, 1.8, 1.5])
        filtrar_rango = f1.toggle(
            "Filtrar por rango de fechas", value=False,
            help="Apagado: se ve todo el histórico. Encendido: todos los indicadores (excepto el "
                 "detalle diario) usan solo las fechas que elijas.")
        rango = (f1.date_input("Rango de fechas", value=(ini_def, fin_def), format="DD/MM/YYYY")
                 if filtrar_rango else (ini_def, fin_def))
        # El detalle diario NO depende del rango: calendario propio, de la primera a la última foto
        # (de cualquier momento) y, debajo, la elección entre las dos fotos del día.
        fotos = vista.fechas_con_foto(datos["registro"], momento=None)
        dia = (f1b.date_input("Día del detalle diario", value=fotos[-1], min_value=fotos[0],
                              max_value=fotos[-1], format="DD/MM/YYYY") if fotos else None)
        personas = f2.multiselect("Persona", op["personas"], default=op["personas"])
        bases = f3.multiselect("Base de origen", list(BASES), default=list(BASES), format_func=BASES.get)
        tipos = f4.multiselect("Tipo de proyecto", op["tipos"], default=op["tipos"])
        solo_ini = f5.toggle("Exactitud: solo con horas reales", value=True,
                             help="Excluye de la exactitud las actividades planificadas que aún no "
                                  "tienen horas reales; si no, inflan el cociente.")
        # Antes del mediodía abre en Early Morning; después, en Cierre de turno.
        ahora_lima = datetime.now(ZoneInfo(config.get("zona_horaria", "America/Lima")))
        momento = st.radio(
            "Detalle diario", ["manana", "cierre"], index=0 if ahora_lima.hour < 12 else 1,
            horizontal=True, format_func=lambda m: vista.ETIQUETA_MOMENTO[m],
            help="Early Morning: planificación de las 8:30 con todos los estados. Cierre de turno: "
                 "lo ejecutado a las 17:30, solo En curso y Done. Solo afecta al gráfico diario.")
    if not (isinstance(rango, (tuple, list)) and len(rango) == 2):
        st.info("Elige la fecha de inicio y la de fin del rango.")
        st.stop()

    f1.caption(vista.texto_periodo(filtrar_rango, rango[0], rango[1]))
    if filtrar_rango:
        sin_fotos = vista.aviso_rango_sin_fotos(datos["registro"], rango[0], rango[1])
        if sin_fotos:
            st.warning(sin_fotos)

    try:
        r = kpis.calcular_todo(
            datos, equipo, rango[0], rango[1], config=config,
            personas=vista.valor_filtro(personas, op["personas"]),
            bases=vista.valor_filtro(bases, list(BASES)),
            tipos_proyecto=vista.valor_filtro(tipos, op["tipos"]),
            solo_iniciadas=solo_ini, dia=dia, rango_activo=filtrar_rango, momento=momento,
        )
    except ValueError as e:
        st.error(f"Los datos de {fuente} no tienen el formato esperado: {e}")
        st.stop()

    _seccion("Ocupabilidad diaria", _diaria, r)
    _seccion("Indicadores principales", _tarjetas, r, equipo)
    izq, der = st.columns(2)
    with izq:
        _seccion("Ocupabilidad por trabajador", _por_trabajador, r)
    with der:
        _seccion("Horas por proyecto", _por_proyecto, r)
    izq, der = st.columns(2)
    with izq:
        _seccion("Horas por Product Owner", _por_po, r)
    with der:
        _seccion("Promedio por tipo de entregable", _por_tipo, r)
    izq, der = st.columns(2)
    with izq:
        _seccion("Exactitud por proyecto", _exactitud_proyectos, r)
    with der:
        _seccion("Alertas", _alertas, r, config)
    _como_se_calcula(equipo)


if __name__ == "__main__":
    main()
