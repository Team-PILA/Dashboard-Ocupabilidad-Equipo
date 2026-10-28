"""Valida la estructura de .github/workflows/foto.yml (sin ejecutarlo)."""
from pathlib import Path

import yaml

RUTA = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "foto.yml"


def _wf() -> dict:
    wf = yaml.safe_load(RUTA.read_text(encoding="utf-8"))
    wf["on"] = wf.pop(True, wf.get("on"))      # YAML 1.1 lee la clave `on` como True
    return wf


def test_dos_cron_en_dias_habiles_y_ejecucion_manual():
    on = _wf()["on"]
    assert [s["cron"] for s in on["schedule"]] == ["30 13 * * 1-5", "30 22 * * 1-5"]
    entradas = on["workflow_dispatch"]["inputs"]
    assert entradas["momento"]["options"] == ["auto", "manana", "cierre"]
    assert entradas["momento"]["default"] == "auto"
    assert "fecha" in entradas


def test_un_job_con_limites_y_permisos_minimos():
    wf = _wf()
    assert wf["permissions"] == {"contents": "read"}
    assert wf["concurrency"]["cancel-in-progress"] is False and wf["concurrency"]["group"]
    assert list(wf["jobs"]) == ["foto"]
    job = wf["jobs"]["foto"]
    assert job["runs-on"] == "ubuntu-latest" and job["timeout-minutes"] == 15
    py = next(p for p in job["steps"] if str(p.get("uses", "")).startswith("actions/setup-python"))
    assert py["with"]["python-version"] == "3.12" and py["with"]["cache"] == "pip"


def test_solo_dos_secrets_y_comando_correcto():
    pasos = _wf()["jobs"]["foto"]["steps"]
    texto = RUTA.read_text(encoding="utf-8")
    assert "${{ secrets.NOTION_TOKEN }}" in texto and "${{ secrets.SUPABASE_DB_URL }}" in texto
    assert texto.count("secrets.") == 2                      # nada más
    paso = next(p for p in pasos if "build_master.py" in p.get("run", ""))
    assert "--extraer --subir --solo-db" in paso["run"]
    assert "pip install -r requirements.txt" in " ".join(p.get("run", "") for p in pasos)
    assert "auto" in paso["run"]                             # con auto no se pasa --momento
    assert "SUPABASE_DB_URL_RO" not in texto                 # la credencial de lectura es de Streamlit
