from datetime import date, datetime
from pathlib import Path

import yaml

from capacidad import (
    CARGA_ADECUADA,
    DESCONOCIDO,
    EN_EQUIPO,
    EXCLUIDO,
    SIN_DATOS,
    SOBRECARGA,
    SUBCARGA,
    Equipo,
    a_fecha,
    dias_en_rango,
    normalizar_nombre,
)

RAIZ = Path(__file__).resolve().parent.parent


def _equipo() -> Equipo:
    """Usa el mismo bloque que se pega en config.yaml, para probarlo también a él."""
    with open(RAIZ / "config_capacidad.yaml", encoding="utf-8") as f:
        return Equipo(yaml.safe_load(f))


def _base_config(**cambios):
    cfg = {
        "capacidad_por_condicion": {"Contratado": [9, 9, 9, 9, 5, 0, 0]},
        "equipo": [{"nombre": "A", "nombre_notion": "A B", "condicion": "Contratado"}],
        "feriados": [],
    }
    cfg.update(cambios)
    return cfg


# ---------------------------------------------------------------- nombres ---

def test_normalizar_ignora_orden_tildes_mayusculas_y_comas():
    assert normalizar_nombre("Rioja, Enrique") == normalizar_nombre("Enrique Rioja")
    assert normalizar_nombre("MARILYN SÁENZ") == normalizar_nombre("marilyn saenz")
    assert normalizar_nombre("Marilyn Sáenz Segura") != normalizar_nombre("Marilyn Sáenz")
    assert normalizar_nombre(None) == frozenset()


def test_clasificar():
    eq = _equipo()
    tipo, m = eq.clasificar("Soto, Ana")
    assert tipo == EN_EQUIPO and m.nombre == "Sofía Soto"  # se muestra el nombre preferido
    assert eq.clasificar("Rioja, Enrique")[0] == EN_EQUIPO
    assert eq.clasificar("Enrique Rioja")[0] == EN_EQUIPO
    assert eq.clasificar("Marilyn Sáenz Segura")[0] == EN_EQUIPO
    assert eq.clasificar("Andrés Pérez")[0] == EXCLUIDO
    assert eq.clasificar("Marilyn Sáenz")[0] == DESCONOCIDO  # no es el nombre de Notion
    assert eq.clasificar("Sin asignar")[0] == DESCONOCIDO
    assert eq.clasificar("")[0] == DESCONOCIDO
    assert eq.clasificar(None)[0] == DESCONOCIDO


# -------------------------------------------------------------- capacidad ---

def test_semana_sin_feriados():
    eq = _equipo()
    semana = dias_en_rango("2026-10-12", "2026-10-18")  # lunes a domingo
    assert eq.horas_disponibles("Contratado", semana) == 41   # 9*4 + 5
    assert eq.horas_disponibles("Trainee Pro", semana) == 41
    assert eq.horas_disponibles("Trainee Pre", semana) == 30  # 6*5
    assert eq.horas_disponibles_equipo(semana) == 4 * 41 + 41 + 30  # 235


def test_semana_con_feriado_entre_semana():
    eq = _equipo()
    semana = dias_en_rango("2026-10-05", "2026-10-11")  # jueves 8 = Angamos
    assert eq.horas_disponibles("Contratado", semana) == 32   # 9*3 + 5
    assert eq.horas_disponibles("Trainee Pre", semana) == 24  # 6*4
    assert eq.horas_disponibles_equipo(semana) == 4 * 32 + 32 + 24  # 184


def test_feriado_en_fin_de_semana_no_cambia_nada():
    eq = _equipo()
    assert eq.horas_dia("Contratado", "2026-06-07") == 0  # domingo
    semana = dias_en_rango("2026-06-01", "2026-06-07")
    assert eq.horas_disponibles("Contratado", semana) == 41


def test_octubre_2026_completo_calculado_a_mano():
    # Contratado: 17 días Lun-Jue*9 + 5 viernes*5 = 178, menos el feriado del jueves 8 (9) = 169
    # Trainee Pre: 22 días hábiles*6 = 132, menos el feriado (6) = 126
    eq = _equipo()
    octubre = dias_en_rango("2026-10-01", "2026-10-31")
    assert eq.horas_disponibles("Contratado", octubre) == 169
    assert eq.horas_disponibles("Trainee Pre", octubre) == 126
    assert eq.horas_disponibles_equipo(octubre) == 4 * 169 + 169 + 126  # 971


def test_dias_repetidos_no_se_suman_dos_veces():
    eq = _equipo()
    assert eq.horas_disponibles("Contratado", ["2026-10-12", "2026-10-12", date(2026, 10, 12)]) == 9


def test_excluido_no_suma_capacidad():
    eq = _equipo()
    andres = [m for m in eq.miembros if m.nombre == "Andres Perez"][0]
    assert eq.horas_disponibles_persona(andres, ["2026-10-12"]) == 0
    assert len(eq.miembros_incluidos) == 6
    assert len(eq.miembros) == 7


def test_fechas_en_varios_formatos():
    assert a_fecha("2026-10-12") == date(2026, 10, 12)
    assert a_fecha(datetime(2026, 10, 12, 17, 30)) == date(2026, 10, 12)
    assert a_fecha(date(2026, 10, 12)) == date(2026, 10, 12)
    assert a_fecha("2026-10-12T17:30:00") == date(2026, 10, 12)


# ------------------------------------------------------ ocupabilidad/estado ---

def test_ocupabilidad_y_sin_datos():
    assert Equipo.ocupabilidad(20, 40) == 0.5
    assert Equipo.ocupabilidad(None, 40) == 0.0
    assert Equipo.ocupabilidad(10, 0) is None   # sin capacidad -> "Sin datos"
    assert Equipo.ocupabilidad(10, None) is None


def test_estado_en_los_limites():
    eq = _equipo()
    assert eq.estado_ocupabilidad(None) == SIN_DATOS
    assert eq.estado_ocupabilidad(0.0) == SUBCARGA
    assert eq.estado_ocupabilidad(0.70) == SUBCARGA          # <= 0.70
    assert eq.estado_ocupabilidad(0.7001) == CARGA_ADECUADA  # > 0.70
    assert eq.estado_ocupabilidad(0.91) == CARGA_ADECUADA    # no es > 0.91
    assert eq.estado_ocupabilidad(0.9101) == SOBRECARGA
    assert eq.estado_ocupabilidad(1.3) == SOBRECARGA


def test_ejemplo_completo_semana():
    # Contratado que registra 36 h en una semana de 41 h disponibles -> 87.8% -> adecuada
    eq = _equipo()
    semana = dias_en_rango("2026-10-12", "2026-10-18")
    oc = eq.ocupabilidad(36, eq.horas_disponibles("Contratado", semana))
    assert round(oc, 3) == 0.878
    assert eq.estado_ocupabilidad(oc) == CARGA_ADECUADA


# ----------------------------------------------------------- validaciones ---

def _debe_fallar(config, texto):
    try:
        Equipo(config)
    except ValueError as e:
        assert texto in str(e), str(e)
    else:
        raise AssertionError(f"Debió fallar con: {texto}")


def test_validaciones_de_configuracion():
    _debe_fallar(_base_config(capacidad_por_condicion={"Contratado": [9, 9, 9]}), "7 valores")
    _debe_fallar(_base_config(capacidad_por_condicion={}), "capacidad_por_condicion")
    _debe_fallar(
        _base_config(equipo=[{"nombre": "A", "condicion": "Otra"}]), "Condición desconocida"
    )
    _debe_fallar(
        _base_config(
            equipo=[
                {"nombre": "A", "nombre_notion": "Rioja, Enrique", "condicion": "Contratado"},
                {"nombre": "B", "nombre_notion": "Enrique Rioja", "condicion": "Contratado"},
            ]
        ),
        "repetido",
    )
