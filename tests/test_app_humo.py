"""Prueba de humo de app.py con Streamlit y Altair SIMULADOS.

Comprueba que el flujo de main() corre de principio a fin con datos reales en disco (nombres
bien escritos, datos que llegan, secciones que se dibujan). NO comprueba que la API real de
Streamlit/Altair acepte cada llamada: eso solo se ve ejecutando `streamlit run app.py`.
"""
import importlib
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import yaml

from tests import test_kpis as tk

RAIZ = Path(__file__).resolve().parent.parent


class _Dato:
    """Simula alt.datum: admite atributos y comparaciones."""
    def __getattr__(self, nombre):
        return self

    def __gt__(self, otro):
        return self


def _col():
    c = MagicMock()
    c.date_input.side_effect = lambda *a, value=None, **k: value
    c.multiselect.side_effect = lambda label, options, default=None, **k: default
    c.toggle.side_effect = lambda *a, value=False, **k: value
    c.selectbox.side_effect = lambda label, options, index=0, **k: options[index]
    return c


def _falso_streamlit():
    st = MagicMock()
    st.cache_data = lambda *a, **k: (lambda f: f)
    st.cache_data.clear = lambda: None
    st.columns.side_effect = lambda spec, **k: [_col() for _ in range(spec if isinstance(spec, int) else len(spec))]
    st.tabs.side_effect = lambda etiquetas: [MagicMock() for _ in etiquetas]
    st.button.return_value = False
    st.radio.side_effect = lambda label, options, index=0, **k: "cierre" if "cierre" in options else options[index]
    st.stop.side_effect = RuntimeError("st.stop")
    return st


def _importar_app():
    previos = {k: sys.modules.get(k) for k in ("streamlit", "altair", "app")}
    st, alt = _falso_streamlit(), MagicMock()
    alt.datum = _Dato()
    sys.modules["streamlit"], sys.modules["altair"] = st, alt
    sys.modules.pop("app", None)
    app = importlib.import_module("app")
    return app, st, previos


def _restaurar(previos):
    for k, v in previos.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


def _config():
    with open(RAIZ / "config_capacidad.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["rutas"] = {"processed": "data/processed", "snapshots": "data/snapshots"}
    cfg["zona_horaria"] = "America/Lima"
    return cfg


def _escribir_csv(raiz: Path):
    proc = raiz / "data" / "processed"
    proc.mkdir(parents=True)
    tk._historico().to_csv(proc / "historico_horas_diarias.csv", index=False)
    tk._estado().to_csv(proc / "estado_actual.csv", index=False)
    reg = tk._registro()
    reg["ejecutado_en"] = reg["fecha"] + "T17:31:00-05:00"
    reg.to_csv(proc / "registro_corridas.csv", index=False)
    tk._dim().to_csv(proc / "dim_intervenciones.csv", index=False)


def test_app_corre_completa_con_datos():
    app, st, previos = _importar_app()
    os.environ["OCUPABILIDAD_DEBUG"] = "1"      # que cualquier error de una sección se propague
    try:
        with tempfile.TemporaryDirectory() as t:
            raiz = Path(t)
            _escribir_csv(raiz)
            app.main(raiz, _config())
        # diario, trabajadores, proyectos, PO, tipo
        assert st.altair_chart.call_count >= 5, st.altair_chart.call_count
        subtitulos = " ".join(str(c.args[0]) for c in st.subheader.call_args_list if c.args)
        assert "Ocupabilidad diaria · 24/10/2026" in subtitulos           # la última foto del rango
        assert not st.error.called and not st.stop.called
        html = " ".join(str(c.args[0]) for c in st.markdown.call_args_list if c.args)
        assert "13%" in html and "SUBCARGA" in html                          # tarjeta principal
    finally:
        os.environ.pop("OCUPABILIDAD_DEBUG", None)
        _restaurar(previos)


def test_app_sin_datos_avisa_y_se_detiene():
    app, st, previos = _importar_app()
    try:
        with tempfile.TemporaryDirectory() as t:
            try:
                app.main(Path(t), _config())
            except RuntimeError as e:
                assert "st.stop" in str(e)
            else:
                raise AssertionError("Debió detenerse sin datos")
        assert st.info.called and "build_master.py" in str(st.info.call_args)
        assert st.altair_chart.call_count == 0
    finally:
        _restaurar(previos)


def test_una_seccion_rota_no_tumba_el_dashboard():
    app, st, previos = _importar_app()
    try:
        def falla(*a):
            raise ValueError("boom")
        app._seccion("Prueba", falla, 1)           # sin OCUPABILIDAD_DEBUG: se muestra el error
        assert st.error.called and "Prueba" in str(st.error.call_args)
    finally:
        _restaurar(previos)


def test_app_no_falla_con_los_filtros_vacios():
    app, st, previos = _importar_app()
    os.environ["OCUPABILIDAD_DEBUG"] = "1"
    try:
        orig = st.columns.side_effect

        def columnas_vacias(spec, **k):
            cols = orig(spec, **k)
            for c in cols:                      # multiselect sin nada elegido
                c.multiselect.side_effect = lambda label, options, default=None, **kw: []
            return cols
        st.columns.side_effect = columnas_vacias
        with tempfile.TemporaryDirectory() as t:
            raiz = Path(t)
            _escribir_csv(raiz)
            app.main(raiz, _config())
        assert not st.error.called
    finally:
        os.environ.pop("OCUPABILIDAD_DEBUG", None)
        _restaurar(previos)


def test_app_con_rango_activado_y_sin_fotos_en_el_rango():
    app, st, previos = _importar_app()
    os.environ["OCUPABILIDAD_DEBUG"] = "1"
    try:
        orig = st.columns.side_effect

        def columnas_con_rango(spec, **k):
            cols = orig(spec, **k)
            for c in cols:
                c.toggle.side_effect = lambda label, value=False, **kw: (
                    True if "rango" in label.lower() else value)
                from datetime import date
                c.date_input.side_effect = lambda label, value=None, **kw: (
                    (date(2026, 10, 5), date(2026, 10, 9)) if "Rango" in label else value)
            return cols
        st.columns.side_effect = columnas_con_rango
        with tempfile.TemporaryDirectory() as t:
            raiz = Path(t)
            _escribir_csv(raiz)
            app.main(raiz, _config())
        avisos = " ".join(str(c.args[0]) for c in st.warning.call_args_list if c.args)
        assert "No hay fotos en el rango" in avisos
        assert not st.error.called
    finally:
        os.environ.pop("OCUPABILIDAD_DEBUG", None)
        _restaurar(previos)


def test_app_en_early_morning_sin_foto_de_la_manana_avisa_sin_romperse():
    app, st, previos = _importar_app()
    os.environ["OCUPABILIDAD_DEBUG"] = "1"
    try:
        st.radio.side_effect = lambda label, options, index=0, **k: "manana"
        with tempfile.TemporaryDirectory() as t:
            raiz = Path(t)
            _escribir_csv(raiz)            # los datos de prueba solo tienen fotos de cierre
            app.main(raiz, _config())
        info = " ".join(str(c.args[0]) for c in st.info.call_args_list if c.args)
        assert "No hay foto de Early Morning 8:30" in info
        subtitulos = " ".join(str(c.args[0]) for c in st.subheader.call_args_list if c.args)
        assert "Early Morning 8:30" in subtitulos
        assert not st.error.called
    finally:
        os.environ.pop("OCUPABILIDAD_DEBUG", None)
        _restaurar(previos)
