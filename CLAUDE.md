# Instrucciones para Claude Code: Dashboard de Ocupabilidad

Eres el ejecutor. El diseño ya está decidido: **no lo cambies ni lo rediseñes**.
Si algo del diseño choca con los datos reales, DETENTE y repórtalo con evidencia
(nombre de columna, tipo recibido, ejemplo de valor). No improvises soluciones.

## Contexto
Notion -> Python -> CSV local -> (después Supabase) -> Streamlit.
Ya existen: `extract/notion_client.py` (cliente, paginación, `valor_propiedad`, `lista`,
`titulo_pagina`), `extract/intervenciones.py` (patrón a seguir) y `tests/test_parseo.py`.
Lee esos archivos primero y replica su estilo: una función pura
`paginas_a_dataframe(paginas, cols)` (testeable sin API), una `extraer(cliente, config)` y un `main()`.

## Tarea 1: tres extractores
Crea `extract/actividades_pm.py`, `extract/actividades_entregables.py` y
`extract/tareas_extracurriculares.py`. Cada uno guarda `data/processed/<nombre>.csv`
(utf-8-sig) con EXACTAMENTE estas columnas:

`base_origen, actividad_id, actividad, persona, horas_hoy, horas_plan, horas_reales,
proyecto_id, proyecto, programa, tipos_entregable, tiene_entregable, estado`

Mapeo de columnas de Notion (nombres exactos; ponlos en `config.yaml` bajo `columnas:`):

| Campo | Actividades PM | Actividades & Entregables | Tareas Extracurriculares |
|---|---|---|---|
| base_origen | "PM" | "Ruta" | "Tareas" |
| actividad | Actividad | título de la página | Actividad-Tarea |
| persona (personas) | PM / Team Member | Team Member | Asignado a |
| horas_hoy | HH Asignadas Hoy | HH Asignadas Hoy | Horas Asignadas Hoy |
| horas_plan | HH Plan | Horas Asignadas PI | Horas Asignadas Totales |
| horas_reales | HH Acumuladas | Horas Reales PI | Horas Reales Totales |
| proyecto | relación Proyecto | relación Proyecto | select Proyecto/Servicio (sin proyecto_id) |
| programa | "Modelo PM" fijo | None | relación Programas (resolver nombre) |
| tipos_entregable | Entregable (multi-select) | Entregable (Descripción) (select) | None |
| tiene_entregable | True si hay tipos | True si hay tipo | casilla ¿Tiene Entregable? |
| estado | Estado | Estado | Estado |

Reglas:
1. **Una fila por persona.** Si hay 2 personas, 2 filas con las MISMAS horas completas (no se reparten).
2. Si no hay persona: una fila con persona = "Sin asignar".
3. Horas vacías en `horas_hoy` -> 0. En `horas_plan` y `horas_reales` déjalas como None si están vacías.
4. `tipos_entregable`: lista normalizada (strip, espacios colapsados, sin duplicados
   ignorando mayúsculas), guardada como texto separado por " | ". No expandas filas por tipo aquí.
5. `proyecto` (A&E y PM) se resuelve con el `proyecto_id` de la relación contra
   `data/processed/dim_intervenciones.csv` (primer match). NO agregues Product Owner a estas tablas:
   se une después en `kpis.py` para no duplicar horas.
6. Para Tareas, resuelve `programa` consultando la base `programas` (config) y mapeando id -> título.
   Si hay varios programas, sepáralos con " | ".
7. Ignora páginas archivadas. No filtres por horas: el extractor devuelve todas las filas.
8. Si una columna esperada no existe en la base, falla con un mensaje que liste las
   columnas disponibles. Nunca devuelvas ceros en silencio.

## Tarea 2: pruebas
Agrega a `tests/` pruebas con páginas simuladas (como `test_parseo.py`) que cubran: dos personas,
cero personas, horas vacías, tipos de entregable duplicados, relación de programa y proyecto.
Corre `python -m pytest -q` y deben pasar todas.

## Tarea 3: validación con datos reales (solo si existe `.env` con NOTION_TOKEN y IDs en config.yaml)
Ejecuta cada extractor y entrégame, por base: número de filas, suma de `horas_hoy`,
número de filas con persona "Sin asignar", y las primeras 3 filas. Yo las comparo a mano con Notion.

## Prohibido
- Imprimir, registrar o commitear el token. `.env` y `data/` ya están en `.gitignore`.
- Subir datos a servicios externos. Todo es local.
- Cambiar el esquema de columnas, renombrar archivos existentes o añadir dependencias sin avisar.

## Al terminar
Resume en máximo 10 líneas: qué creaste, resultado de pytest, resultados de la Tarea 3 y
cualquier discrepancia entre lo esperado y lo que devolvió Notion.
