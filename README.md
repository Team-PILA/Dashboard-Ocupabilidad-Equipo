# Dashboard de Ocupabilidad del Equipo

Dashboard en Streamlit que mide la ocupabilidad del equipo a partir de las horas que se registran en Notion.

## Qué muestra
- **Ocupabilidad diaria** por integrante, apilada por estado (Done, En curso, Stuck, Sin empezar).
- Ocupabilidad del área y por trabajador en un rango de fechas.
- Horas por proyecto, por programa "Modelo de PM" y por Product Owner × persona.
- Promedio de horas por tipo de entregable.
- Exactitud de planificación (global y por proyecto).
- Alertas de calidad de datos.

## Cómo funciona
Notion API → extractores en Python → CSV en `data/` → Streamlit.

Cada día laborable a las 17:30 (hora de Lima) se toma una **foto** de la columna "Horas Asignadas Hoy" y se guarda en `data/snapshots/`. Con esas fotos se arma el histórico.

## Requisitos
- Python 3.14 (3.15 aún no tiene instalador de PyYAML para Windows)
- Una integración de Notion compartida con las bases (menú "..." > Conexiones)

## Instalación
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Crea un archivo `.env` en la raíz:
```
NOTION_TOKEN=tu_token
```
Completa los IDs de las bases en `config.yaml`.

## Uso
```bash
python build_master.py --extraer   # extrae de Notion y genera la foto del día
streamlit run app.py               # abre el dashboard
python -m pytest -q                # pruebas
```

## Estructura
| Archivo | Función |
|---|---|
| `extract/` | Extractores de Notion |
| `build_master.py` | Foto diaria, histórico y registro de corridas |
| `capacidad.py` | Capacidad y reglas de ocupabilidad |
| `kpis.py` | Cálculo de indicadores |
| `vista.py` | Formatos y tablas del dashboard |
| `app.py` | Dashboard Streamlit |
| `config.yaml` | Bases de Notion y parámetros |

## Seguridad
`.env` y `data/` no se suben al repo (están en `.gitignore`).

## Estado
MVP en pruebas: el histórico recién empieza a acumularse.