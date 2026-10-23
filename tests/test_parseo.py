from extract.intervenciones import SIN_PO, paginas_a_dataframe
from extract.notion_client import lista, titulo_pagina, valor_propiedad

COLS = {
    "proyecto": "Proyecto",
    "tipo_proyecto": "Tipo de Proyecto",
    "product_owner": "Product Owner",
    "sponsor": "Sponsor",
    "status": "Status",
}


def _ms(*nombres):
    return {"type": "multi_select", "multi_select": [{"name": n} for n in nombres]}


def _pagina(pid, nombre, tipo, status, sponsor, pos):
    return {
        "id": pid,
        "archived": False,
        "properties": {
            "Proyecto": {"type": "title", "title": [{"plain_text": nombre}]},
            "Tipo de Proyecto": {"type": "select", "select": {"name": tipo}},
            "Status": {"type": "status", "status": {"name": status}},
            "Sponsor": _ms(*sponsor),
            "Product Owner": _ms(*pos),
        },
    }


def test_valor_propiedad_tipos_basicos():
    assert valor_propiedad({"type": "number", "number": 3.5}) == 3.5
    assert valor_propiedad({"type": "number", "number": None}) is None
    assert valor_propiedad({"type": "checkbox", "checkbox": False}) is False
    assert valor_propiedad({"type": "select", "select": None}) is None
    assert valor_propiedad({"type": "select", "select": {"name": " Otros "}}) == "Otros"
    assert valor_propiedad({"type": "people", "people": [{"id": "1", "name": "Ana"}]}) == ["Ana"]
    assert valor_propiedad({"type": "relation", "relation": [{"id": "abc"}]}) == ["abc"]
    assert valor_propiedad(None) is None


def test_fecha_y_formula():
    f = {"type": "date", "date": {"start": "2026-09-21", "end": "2026-09-29"}}
    assert valor_propiedad(f) == {"inicio": "2026-09-21", "fin": "2026-09-29"}
    frm = {"type": "formula", "formula": {"type": "number", "number": 8}}
    assert valor_propiedad(frm) == 8


def test_rollup_array():
    r = {
        "type": "rollup",
        "rollup": {
            "type": "array",
            "array": [{"type": "select", "select": {"name": "Ruta de Eficiencia"}}],
        },
    }
    assert valor_propiedad(r) == ["Ruta de Eficiencia"]


def test_lista_y_titulo():
    assert lista(None) == []
    assert lista("x") == ["x"]
    assert lista(["a", None, ""]) == ["a"]
    p = {"properties": {"Nombre": {"type": "title", "title": [{"plain_text": "Hola"}]}}}
    assert titulo_pagina(p) == "Hola"


def test_intervenciones_expande_dos_po_y_sin_po():
    paginas = [
        _pagina("1", "F2: Procura", "Ruta de Eficiencia", "In progress", ["Turgay Kircar"],
                ["Juan Pablo Taboada", "Javier Villaseca"]),
        _pagina("2", "Flujo de proyectos", "Modelo PM", "In progress", ["Leticia Cajas"],
                ["Leticia Cajas"]),
        _pagina("3", "Sin PO aún", "Ruta de Eficiencia", "Not started", [], []),
    ]
    df = paginas_a_dataframe(paginas, COLS)
    assert len(df) == 4
    assert df[df.proyecto_id == "1"]["product_owner"].tolist() == [
        "Juan Pablo Taboada",
        "Javier Villaseca",
    ]
    assert df[df.proyecto_id == "3"]["product_owner"].tolist() == [SIN_PO]
    assert df[df.proyecto_id == "2"]["tipo_proyecto"].iloc[0] == "Modelo PM"


def test_intervenciones_ignora_archivadas():
    p = _pagina("9", "Borrada", "Ruta de Eficiencia", "Done", [], ["X"])
    p["archived"] = True
    assert paginas_a_dataframe([p], COLS).empty
