"""Pruebas de acceso.py y de su uso en app.py, con Streamlit SIMULADO (sin red ni base real)."""
import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests import test_app_humo as th

USUARIO = "usuario-prueba"
CLAVE = "clave-de-prueba-123456"
CON_SECRETS = {"APP_USUARIO": USUARIO, "APP_CLAVE": CLAVE}
ENTORNO = ("OCUPABILIDAD_SIN_CLAVE", "APP_USUARIO", "APP_CLAVE")


@pytest.fixture(autouse=True)
def entorno_limpio(monkeypatch):
    for k in ENTORNO:
        monkeypatch.delenv(k, raising=False)


class _SinArchivo:
    """Como st.secrets cuando no existe secrets.toml: cualquier acceso lanza error."""
    def __getitem__(self, k):
        raise FileNotFoundError("sin secrets.toml")


def _modulo(monkeypatch, secrets=None, u="", c="", enviar=True):
    """acceso.py importado contra un Streamlit simulado. secrets=None: st.secrets lanza error."""
    st = MagicMock()
    st.session_state = {}
    st.stop.side_effect = RuntimeError("st.stop")
    st.rerun.side_effect = RuntimeError("st.rerun")
    st.secrets = _SinArchivo() if secrets is None else dict(secrets)
    st.text_input.side_effect = lambda label, **k: {"Usuario": u, "Clave": c}[label]
    st.form_submit_button.return_value = enviar
    st.sidebar.button.return_value = False
    monkeypatch.setitem(sys.modules, "streamlit", st)
    monkeypatch.delitem(sys.modules, "acceso", raising=False)
    acceso = importlib.import_module("acceso")
    monkeypatch.setattr(acceso.time, "sleep", lambda s: None)
    return acceso, st


def _detiene(acceso):
    with pytest.raises(RuntimeError, match="st.stop"):
        acceso.exigir_acceso()


def _con_rerun(acceso):
    with pytest.raises(RuntimeError, match="st.rerun"):
        acceso.exigir_acceso()


def test_sin_secrets_y_sin_variable_de_desarrollo_falla_cerrada(monkeypatch):
    acceso, st = _modulo(monkeypatch, secrets=None)
    _detiene(acceso)
    assert "Acceso no configurado" in str(st.error.call_args)
    assert not st.text_input.called


def test_falta_solo_una_credencial_tambien_falla_cerrada(monkeypatch):
    acceso, st = _modulo(monkeypatch, secrets={"APP_USUARIO": USUARIO})
    _detiene(acceso)
    assert "Acceso no configurado" in str(st.error.call_args)


def test_variable_de_desarrollo_deja_pasar(monkeypatch):
    monkeypatch.setenv("OCUPABILIDAD_SIN_CLAVE", "1")
    acceso, st = _modulo(monkeypatch, secrets=None)
    acceso.exigir_acceso()
    assert not st.stop.called and not st.error.called and not st.text_input.called


def test_variable_de_desarrollo_no_salta_la_clave_si_hay_credenciales(monkeypatch):
    monkeypatch.setenv("OCUPABILIDAD_SIN_CLAVE", "1")
    acceso, st = _modulo(monkeypatch, secrets=CON_SECRETS, u="x", c="y")
    _detiene(acceso)
    assert "Credenciales incorrectas" in str(st.error.call_args)
    assert st.text_input.called and st.session_state.get("acceso_ok") is not True


def test_variable_de_desarrollo_no_salta_la_clave_con_credenciales_en_el_entorno(monkeypatch):
    monkeypatch.setenv("OCUPABILIDAD_SIN_CLAVE", "1")
    monkeypatch.setenv("APP_USUARIO", USUARIO)
    monkeypatch.setenv("APP_CLAVE", CLAVE)
    acceso, st = _modulo(monkeypatch, secrets=None, enviar=False)
    _detiene(acceso)
    assert st.text_input.called


def test_credenciales_desde_el_entorno(monkeypatch):
    monkeypatch.setenv("APP_USUARIO", USUARIO)
    monkeypatch.setenv("APP_CLAVE", CLAVE)
    acceso, st = _modulo(monkeypatch, secrets=None, u=USUARIO, c=CLAVE)
    _con_rerun(acceso)
    assert st.session_state["acceso_ok"] is True


@pytest.mark.parametrize("u,c", [(USUARIO, "otra"), ("otro", CLAVE), ("otro", "otra"), ("", "")])
def test_credencial_incorrecta_se_detiene_con_mensaje_generico(monkeypatch, u, c):
    acceso, st = _modulo(monkeypatch, secrets=CON_SECRETS, u=u, c=c)
    _detiene(acceso)
    assert "Credenciales incorrectas" in str(st.error.call_args)
    assert st.session_state.get("acceso_ok") is not True
    assert not st.rerun.called


def test_mismo_mensaje_para_usuario_o_clave_incorrectos(monkeypatch):
    a1, st1 = _modulo(monkeypatch, secrets=CON_SECRETS, u=USUARIO, c="mala")
    _detiene(a1)
    a2, st2 = _modulo(monkeypatch, secrets=CON_SECRETS, u="malo", c=CLAVE)
    _detiene(a2)
    assert st1.error.call_args_list == st2.error.call_args_list


def test_credenciales_correctas_pasan_y_marcan_la_sesion(monkeypatch):
    acceso, st = _modulo(monkeypatch, secrets=CON_SECRETS, u=USUARIO, c=CLAVE)
    _con_rerun(acceso)
    assert st.session_state["acceso_ok"] is True
    # con la sesión marcada, la siguiente ejecución pasa sin formulario y ofrece cerrar sesión
    st.text_input.reset_mock()
    acceso.exigir_acceso()
    assert not st.text_input.called and not st.stop.call_count
    assert "Cerrar sesión" in str(st.sidebar.button.call_args)


def test_cerrar_sesion_borra_la_sesion_y_recarga(monkeypatch):
    acceso, st = _modulo(monkeypatch, secrets=CON_SECRETS)
    st.session_state["acceso_ok"] = True
    st.sidebar.button.return_value = True
    _con_rerun(acceso)
    assert "acceso_ok" not in st.session_state


def test_tras_cinco_fallos_se_bloquea(monkeypatch):
    acceso, st = _modulo(monkeypatch, secrets=CON_SECRETS, u="x", c="y")
    for _ in range(5):
        _detiene(acceso)
    assert st.session_state["acceso_bloqueo_hasta"] > 0
    # bloqueado: aunque ahora las credenciales sean correctas, no se muestra el formulario ni se evalúa
    st.text_input.reset_mock()
    st.text_input.side_effect = lambda label, **k: USUARIO if label == "Usuario" else CLAVE
    _detiene(acceso)
    assert not st.text_input.called
    assert st.session_state.get("acceso_ok") is not True
    assert "Demasiados intentos" in str(st.error.call_args)
    # pasado el bloqueo, vuelve a aceptar intentos
    st.session_state["acceso_bloqueo_hasta"] = 0
    _con_rerun(acceso)
    assert st.session_state["acceso_ok"] is True


def test_cuatro_fallos_aun_no_bloquean(monkeypatch):
    acceso, st = _modulo(monkeypatch, secrets=CON_SECRETS, u="x", c="y")
    for _ in range(4):
        _detiene(acceso)
    assert not st.session_state.get("acceso_bloqueo_hasta")


def test_ningun_mensaje_visible_contiene_usuario_ni_clave(monkeypatch):
    for u, c in [(USUARIO, "mala"), ("malo", CLAVE), ("malo", "mala")]:
        acceso, st = _modulo(monkeypatch, secrets=CON_SECRETS, u=u, c=c)
        _detiene(acceso)
        visible = " ".join(str(m.call_args_list) for m in (
            st.error, st.warning, st.info, st.title, st.caption, st.markdown, st.subheader))
        assert USUARIO not in visible and CLAVE not in visible


def test_el_campo_clave_es_de_tipo_password(monkeypatch):
    acceso, st = _modulo(monkeypatch, secrets=CON_SECRETS, enviar=False)
    _detiene(acceso)
    clave = [c for c in st.text_input.call_args_list if c.args[0] == "Clave"][0]
    assert clave.kwargs.get("type") == "password"
    assert "Acceso restringido" in str(st.title.call_args)


# ------------------------------------------------- app.py: nada antes del acceso ---

def test_antes_del_acceso_no_se_lee_ningun_dato(monkeypatch):
    import db
    import kpis

    app, st, previos = th._importar_app(sin_clave=False)
    try:
        st.session_state = {}
        st.secrets = dict(CON_SECRETS)
        st.text_input.side_effect = lambda label, **k: ""
        st.form_submit_button.return_value = False
        conectar, cargar_db, cargar_csv = MagicMock(), MagicMock(), MagicMock()
        hay_db = MagicMock(return_value=True)
        monkeypatch.setattr(db, "conectar", conectar)
        monkeypatch.setattr(db, "cargar_datos_db", cargar_db)
        monkeypatch.setattr(kpis, "cargar_datos", cargar_csv)
        app._hay_db_ro = hay_db
        with pytest.raises(RuntimeError, match="st.stop"):
            app.main(Path("."), th._config())
        assert not conectar.called and not cargar_db.called and not cargar_csv.called
        assert not hay_db.called
        # solo el formulario es visible: ni título del dashboard, ni botón de recarga, ni métricas
        assert "Ocupabilidad del Equipo" not in str(st.title.call_args_list)
        assert not st.button.called and not st.metric.called and not st.altair_chart.called
        assert not st.dataframe.called
        # set_page_config sigue siendo la primera llamada de Streamlit
        assert st.mock_calls[0][0] == "set_page_config"
    finally:
        th._restaurar(previos)


def test_antes_del_acceso_sin_configurar_tampoco_lee_datos(monkeypatch):
    import kpis

    app, st, previos = th._importar_app(sin_clave=False)
    try:
        st.session_state = {}
        st.secrets = {}
        cargar_csv = MagicMock()
        monkeypatch.setattr(kpis, "cargar_datos", cargar_csv)
        with pytest.raises(RuntimeError, match="st.stop"):
            app.main(Path("."), th._config())
        assert "Acceso no configurado" in str(st.error.call_args)
        assert not cargar_csv.called
    finally:
        th._restaurar(previos)
