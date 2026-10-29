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

Cada día laborable a las 17:32 (hora de Lima) se toma una **foto** de la columna "Horas Asignadas Hoy" y se guarda en `data/snapshots/`. Con esas fotos se arma el histórico.

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

## Despliegue automático
`.github/workflows/foto.yml` corre de lunes a viernes a las 8:30 y 17:30 (Lima): extrae de Notion y
sube la foto a Supabase (`--extraer --subir --solo-db`, sin escribir CSV). **El repositorio debe ser privado.**

- **Secrets de GitHub** (Settings > Secrets and variables > Actions): `NOTION_TOKEN` y
  `SUPABASE_DB_URL` (conexión del rol `cron_rw`, escritura). No se necesita ningún otro.
- **Secret de Streamlit** (app > Settings > Secrets): `SUPABASE_DB_URL_RO` (rol `dashboard_ro`, solo lectura).
  Sin él, o si la conexión falla, la app lee los CSV locales.
- **Lanzar a mano:** Actions > "Foto diaria de ocupabilidad" > Run workflow. `momento`: `auto` decide por
  la hora de Lima (antes de las 12 = mañana). `fecha` es opcional; las horas de Notion son las de hoy,
  así que una fecha pasada abortará por la validación de frescura.
- **Si falla:** la corrida queda en rojo y no se guarda nada (la escritura es todo o nada; la foto
  anterior sigue intacta). Abre el log: un error de Notion (token vencido, base sin compartir o columna
  renombrada) ocurre antes de tocar la base; un `Error de base de datos` o `Verificación fallida` indica
  un problema de conexión o de datos. Repetir la corrida es seguro: reemplaza la foto de ese día y momento.
  GitHub puede demorar unos minutos los horarios programados.

## Acceso
La app pide usuario y clave antes de leer cualquier dato. Si faltan las credenciales, no abre.
En Streamlit (app > Settings > Secrets) o en `.streamlit/secrets.toml`:
```toml
APP_USUARIO = "usuario"
APP_CLAVE = "una-clave-de-16-o-mas-caracteres"
```
Usa una clave de 16 caracteres o más. Tras 5 intentos fallidos el formulario se bloquea 60 segundos.

En local, sin credenciales: `OCUPABILIDAD_SIN_CLAVE=1 streamlit run app.py` (PowerShell:
`$env:OCUPABILIDAD_SIN_CLAVE="1"; streamlit run app.py`). La variable solo vale si no hay `APP_USUARIO`/`APP_CLAVE`: si existen, siempre se pide la clave.

## Seguridad
`.env` y `data/` no se suben al repo (están en `.gitignore`).

## Estado
MVP en pruebas: el histórico recién empieza a acumularse.