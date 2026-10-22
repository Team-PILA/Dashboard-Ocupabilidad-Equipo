# Dashboard de Ocupabilidad del Equipo

Notion -> Python -> CSV local (por ahora) -> Supabase (después) -> Streamlit.

## Preparación (Fase 0)

1. Crea una integración en Notion (Configuración > Conexiones > Desarrollar integraciones)
   y copia el token a un archivo `.env` (ver `.env.example`).
2. Comparte con esa integración las 5 bases: Intervenciones, Programas,
   Actividades PM, Actividades & Entregables y Tareas Extracurriculares
   (menú "..." de la base > Conexiones).
3. Copia el ID de cada base (los 32 caracteres del enlace, antes de `?v=`) en `config.yaml`.
4. Instala dependencias:

   ```
   py -3.14 -m venv .venv
   .venv\Scripts\Activate.ps1       # Windows
   pip install -r requirements.txt
   ```

## Uso

```
python -m extract.intervenciones     # genera data/processed/dim_intervenciones.csv
python -m pytest -q                  # pruebas del parseo (no usan la API)
```

## Estructura

```
config.yaml        IDs de bases, nombres de columnas, capacidad, rutas
extract/           cliente de Notion y un extractor por fuente
data/raw/          salida cruda (ignorada por git)
data/processed/    CSV normalizados y dimensiones (ignorada por git)
data/snapshots/    una foto por día: snapshot_AAAA-MM-DD.csv (ignorada por git)
tests/             pruebas
```

## Notas

- `data/` y `.env` están en `.gitignore`: contienen horas por persona y el token.
- "HH Asignadas Hoy" solo existe en el día actual en Notion; el histórico solo
  se acumula guardando una foto diaria (a las 5:30 pm hora de Lima).
