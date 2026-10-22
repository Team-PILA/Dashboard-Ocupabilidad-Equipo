"""Equipo, capacidad, ocupabilidad y estado de carga.

Replica las fórmulas de Power BI:
  Horas_disponible   -> Equipo.horas_disponibles / horas_disponibles_equipo
  Ocupabilidad_real  -> ocupabilidad(horas_reales, horas_disponibles)
  Estado_ocupabilidad-> estado_ocupabilidad(ocupabilidad)

Módulo puro (sin Notion ni pandas): recibe el dict de config.yaml.

Convenciones:
- Los días (`dias`) son los que cuentan para la capacidad. El llamador debe pasar solo
  los días que tienen foto diaria dentro del rango del slider, para que los días
  sin corrida del script no diluyan la ocupabilidad.
- Una persona del equipo con foto pero sin horas registradas ese día suma 0 horas
  (subcarga real) y su capacidad sí cuenta.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

EN_EQUIPO = "equipo"
EXCLUIDO = "excluido"
DESCONOCIDO = "desconocido"

SOBRECARGA = "SOBRECARGA"
CARGA_ADECUADA = "CARGA ADECUADA"
SUBCARGA = "SUBCARGA"
SIN_DATOS = "Sin datos"


def normalizar_nombre(nombre: object) -> frozenset[str]:
    """Nombre -> conjunto de palabras sin tildes, mayúsculas ni comas.

    "Rioja, Enrique" y "Enrique Rioja" dan el mismo conjunto.
    """
    if nombre is None:
        return frozenset()
    s = unicodedata.normalize("NFKD", str(nombre))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return frozenset(s.lower().replace(",", " ").split())


def a_fecha(valor: object) -> date:
    """Acepta date, datetime, pandas.Timestamp o texto ISO (AAAA-MM-DD)."""
    if isinstance(valor, datetime):  # incluye pandas.Timestamp
        return valor.date()
    if isinstance(valor, date):
        return valor
    return date.fromisoformat(str(valor)[:10])


def dias_en_rango(inicio: object, fin: object) -> list[date]:
    """Todos los días entre dos fechas, ambos incluidos."""
    ini, fi = a_fecha(inicio), a_fecha(fin)
    return [ini + timedelta(days=i) for i in range((fi - ini).days + 1)]


@dataclass(frozen=True)
class Miembro:
    nombre: str          # cómo se muestra en el dashboard
    nombre_notion: str   # cómo aparece en Notion (llave de relación)
    condicion: str       # Contratado / Trainee Pro / Trainee Pre
    incluir: bool = True


class Equipo:
    def __init__(self, config: dict):
        capacidad = config.get("capacidad_por_condicion") or {}
        if not capacidad:
            raise ValueError("Falta 'capacidad_por_condicion' en la configuración.")
        self._capacidad: dict[str, tuple[float, ...]] = {}
        for condicion, horas in capacidad.items():
            if len(horas) != 7:
                raise ValueError(
                    f"'{condicion}' debe tener 7 valores (lunes a domingo), tiene {len(horas)}."
                )
            self._capacidad[condicion] = tuple(float(h) for h in horas)

        self.feriados: frozenset[date] = frozenset(
            a_fecha(f) for f in config.get("feriados", [])
        )
        umbrales = config.get("umbrales_carga") or {}
        self.umbral_sobrecarga = float(umbrales.get("sobrecarga", 0.91))
        self.umbral_adecuada = float(umbrales.get("adecuada", 0.70))

        self.miembros: list[Miembro] = []
        self._por_clave: dict[frozenset[str], Miembro] = {}
        for m in config.get("equipo", []):
            miembro = Miembro(
                nombre=m["nombre"],
                nombre_notion=m.get("nombre_notion", m["nombre"]),
                condicion=m["condicion"],
                incluir=bool(m.get("incluir", True)),
            )
            if miembro.condicion not in self._capacidad:
                raise ValueError(
                    f"Condición desconocida '{miembro.condicion}' para {miembro.nombre}. "
                    f"Válidas: {list(self._capacidad)}"
                )
            clave = normalizar_nombre(miembro.nombre_notion)
            if not clave:
                raise ValueError(f"{miembro.nombre}: nombre_notion vacío.")
            if clave in self._por_clave:
                raise ValueError(
                    f"Nombre de Notion repetido: '{miembro.nombre_notion}' "
                    f"({self._por_clave[clave].nombre} y {miembro.nombre})."
                )
            self._por_clave[clave] = miembro
            self.miembros.append(miembro)

    # ------------------------------------------------------------ personas ---
    @property
    def miembros_incluidos(self) -> list[Miembro]:
        return [m for m in self.miembros if m.incluir]

    def clasificar(self, nombre_notion: object) -> tuple[str, Optional[Miembro]]:
        """Devuelve (EN_EQUIPO | EXCLUIDO | DESCONOCIDO, miembro o None).

        DESCONOCIDO cubre "Sin asignar", personas externas y cambios de nombre en Notion:
        no cuentan en la ocupabilidad y deben mostrarse en las alertas.
        """
        miembro = self._por_clave.get(normalizar_nombre(nombre_notion))
        if miembro is None:
            return DESCONOCIDO, None
        return (EN_EQUIPO if miembro.incluir else EXCLUIDO), miembro

    # ----------------------------------------------------------- capacidad ---
    def horas_dia(self, condicion: str, dia: object) -> float:
        """Horas disponibles de una condición en un día (0 si es feriado)."""
        d = a_fecha(dia)
        if d in self.feriados:
            return 0.0
        return self._capacidad[condicion][d.weekday()]  # lunes=0 ... domingo=6

    def horas_disponibles(self, condicion: str, dias: Iterable[object]) -> float:
        """Suma de horas disponibles de una condición en los días dados (sin repetir días)."""
        return sum(self.horas_dia(condicion, d) for d in {a_fecha(x) for x in dias})

    def horas_disponibles_persona(self, miembro: Miembro, dias: Iterable[object]) -> float:
        if not miembro.incluir:
            return 0.0
        return self.horas_disponibles(miembro.condicion, dias)

    def horas_disponibles_equipo(self, dias: Iterable[object]) -> float:
        """Capacidad total del área: suma de los miembros incluidos."""
        dias = list({a_fecha(x) for x in dias})
        return sum(self.horas_disponibles(m.condicion, dias) for m in self.miembros_incluidos)

    # ------------------------------------------------- ocupabilidad y estado ---
    @staticmethod
    def ocupabilidad(
        horas_reales: Optional[float], horas_disponibles: Optional[float]
    ) -> Optional[float]:
        """Horas registradas / horas disponibles. None si no hay capacidad (feriados, rango vacío).

        A diferencia de DIVIDE(..., 0) de Power BI, no devuelve 0 cuando el
        denominador es 0: devuelve None para poder mostrar "Sin datos".
        """
        if not horas_disponibles or horas_disponibles <= 0:
            return None
        return float(horas_reales or 0.0) / float(horas_disponibles)

    def estado_ocupabilidad(self, ocupabilidad: Optional[float]) -> str:
        if ocupabilidad is None:
            return SIN_DATOS
        if ocupabilidad > self.umbral_sobrecarga:
            return SOBRECARGA
        if ocupabilidad > self.umbral_adecuada:
            return CARGA_ADECUADA
        return SUBCARGA
