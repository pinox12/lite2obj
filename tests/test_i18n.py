"""Tests de selección de idioma y del catálogo de traducciones."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from lite2obj import i18n

_SRC = Path(__file__).parents[1] / "src" / "lite2obj"


def test_system_language_reads_locale_environment(monkeypatch):
    monkeypatch.delenv("LITE2OBJ_LANG", raising=False)
    monkeypatch.setenv("LANGUAGE", "")
    monkeypatch.setenv("LC_ALL", "")
    monkeypatch.setenv("LC_MESSAGES", "")
    monkeypatch.setenv("LANG", "es_ES.UTF-8")
    assert i18n.system_language() == "es"

    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert i18n.system_language() == "en"


def test_unsupported_system_language_falls_back_to_english(monkeypatch):
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    monkeypatch.setenv("LANGUAGE", "")
    monkeypatch.setenv("LC_ALL", "")
    monkeypatch.setenv("LC_MESSAGES", "")
    monkeypatch.setattr(i18n.locale, "getlocale", lambda: (None, None))
    assert i18n.system_language() == "en"


def test_configure_honours_environment_and_explicit_value(monkeypatch):
    monkeypatch.setenv("LITE2OBJ_LANG", "es")
    assert i18n.configure() == "es"
    assert i18n.configure("en") == "en"
    assert i18n._("Listo") == "Ready"

    monkeypatch.setenv("LITE2OBJ_LANG", "fr")
    assert i18n.configure() == "en"
    assert i18n._("Listo") == "Ready"


def test_spanish_is_the_source_language():
    i18n.set_language("es")
    assert i18n._("Listo") == "Listo"
    assert i18n._("Renderizando…") == "Renderizando…"


def test_translation_formats_placeholders():
    i18n.set_language("en")
    assert i18n._("Archivo    {path}").format(path="x.obj") == "File       x.obj"
    assert (
        i18n._("{faces:,} caras · {vertices:,} vértices · {uvs:,} UVs").format(
            faces=1200, vertices=800, uvs=400
        )
        == "1,200 faces · 800 vertices · 400 UVs"
    )


def test_extract_language_option_removes_flag_from_any_position():
    remaining, language = i18n.extract_language_option(["info", "a.litematic", "--lang", "es"])
    assert remaining == ["info", "a.litematic"]
    assert language == "es"

    remaining, language = i18n.extract_language_option(["--lang=en", "list"])
    assert remaining == ["list"]
    assert language == "en"


def test_every_literal_used_with_translation_has_an_english_entry():
    """Evita que una cadena nueva se quede sin traducir."""
    missing: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (isinstance(node.func, ast.Name) and node.func.id == "_"):
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            value = node.args[0].value
            if isinstance(value, str) and value not in i18n.TRANSLATIONS["en"]:
                missing.append(f"{path.relative_to(_SRC)}:{node.lineno}: {value[:60]}")
    assert not missing, "Cadenas sin traducción:\n" + "\n".join(missing)


def test_gui_filters_are_translated(monkeypatch):
    monkeypatch.setenv("LITE2OBJ_LANG", "en")
    i18n.set_language("en")
    from lite2obj.gui import app

    assert app._litematic_filter() == "Litematica (*.litematic);;All files (*)"
    assert app._png_filter() == "PNG (*.png);;All files (*)"
    assert app._asset_pack_filter() == "lite2obj assets (*.l2oassets);;All files (*)"
    assert app._jar_filter() == "Minecraft client (*.jar);;All files (*)"
    assert app._pack_filter() == "Resource pack (*.zip);;All files (*)"


def test_cli_output_uses_selected_language(monkeypatch, capsys):
    from lite2obj.cli import main

    fixture = Path(__file__).parent / "fixtures" / "store_1x4x4.litematic"
    monkeypatch.setenv("LITE2OBJ_LANG", "en")
    i18n.set_language("en")
    assert main(["info", str(fixture)]) == 0
    output = capsys.readouterr().out
    assert "file" in output
    assert "region" in output

    monkeypatch.setenv("LITE2OBJ_LANG", "es")
    i18n.set_language("es")
    assert main(["info", str(fixture)]) == 0
    output = capsys.readouterr().out
    assert "archivo" in output
    assert "región" in output


@pytest.mark.parametrize("value", ["es_ES", "Spanish_Spain", "es-ES", "spa"])
def test_language_aliases(value):
    assert i18n.normalize_language(value) == "es"
