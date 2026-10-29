"""Control de acceso del dashboard (usuario y clave) antes de leer cualquier dato.

    from acceso import exigir_acceso
    exigir_acceso()      # justo después de st.set_page_config(...)

Credenciales: APP_USUARIO y APP_CLAVE en st.secrets o, si no están, en el entorno.
Falla cerrada: si faltan, la app no abre. Solo OCUPABILIDAD_SIN_CLAVE=1 (desarrollo local) la deja pasar.
Los mensajes visibles nunca incluyen el usuario, la clave ni indican qué campo falló.
"""
from __future__ import annotations

import hmac
import os
import time

import streamlit as st

MAX_FALLOS = 5
SEGUNDOS_BLOQUEO = 60


def _secreto(nombre: str) -> str:
    """st.secrets y, si no, el entorno. Sin secrets.toml Streamlit lanza una excepción."""
    try:
        valor = st.secrets[nombre]
    except Exception:  # noqa: BLE001 - falta el archivo o la clave
        valor = None
    if valor is None or str(valor) == "":
        valor = os.environ.get(nombre)
    return "" if valor is None else str(valor)


def _iguales(recibido: str, esperado: str) -> bool:
    return hmac.compare_digest(recibido.encode("utf-8"), esperado.encode("utf-8"))


def _cerrar_sesion() -> None:
    st.session_state.clear()
    st.rerun()


def exigir_acceso() -> None:
    """Detiene el script (st.stop) mientras no haya una sesión válida."""
    usuario, clave = _secreto("APP_USUARIO"), _secreto("APP_CLAVE")
    if not usuario or not clave:
        if os.environ.get("OCUPABILIDAD_SIN_CLAVE") == "1":
            return       # solo desarrollo local: con credenciales configuradas siempre se pide la clave
        st.error("Acceso no configurado")
        st.stop()
        return

    estado = st.session_state
    if estado.get("acceso_ok") is True:
        if st.sidebar.button("Cerrar sesión", key="acceso_salir"):
            _cerrar_sesion()
        return

    if time.time() < estado.get("acceso_bloqueo_hasta", 0):
        st.error("Demasiados intentos. Espera un minuto antes de volver a intentarlo.")
        st.stop()
        return

    st.title("Acceso restringido")
    with st.form("acceso_form"):
        u = st.text_input("Usuario")
        c = st.text_input("Clave", type="password")
        enviado = st.form_submit_button("Entrar")

    if enviado:
        ok_u = _iguales(u, usuario)          # se evalúan ambos siempre, sin cortocircuito
        ok_c = _iguales(c, clave)
        if ok_u & ok_c:
            estado["acceso_ok"] = True
            estado["acceso_fallos"] = 0
            st.rerun()
        else:
            time.sleep(1)
            estado["acceso_fallos"] = estado.get("acceso_fallos", 0) + 1
            if estado["acceso_fallos"] >= MAX_FALLOS:
                estado["acceso_fallos"] = 0
                estado["acceso_bloqueo_hasta"] = time.time() + SEGUNDOS_BLOQUEO
                st.error("Demasiados intentos. Espera un minuto antes de volver a intentarlo.")
            else:
                st.error("Credenciales incorrectas")
    st.stop()
