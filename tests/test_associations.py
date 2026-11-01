"""Tests de la asociación de archivos ``.litematic`` (Linux y Windows)."""

from __future__ import annotations

import sys

import pytest

from lite2obj import associations


def test_desktop_entry_quotes_command():
    entry = associations._desktop_entry(["/ruta/con espacios/lite2obj.AppImage"])

    assert 'Exec="/ruta/con espacios/lite2obj.AppImage" %f' in entry
    assert "MimeType=application/x-litematic;" in entry
    assert "StartupWMClass=lite2obj" in entry


def test_mime_xml_declares_litematic():
    xml = associations._mime_xml()

    assert 'type="application/x-litematic"' in xml
    assert 'glob pattern="*.litematic"' in xml
    assert 'value="\\x1f\\x8b"' in xml


def test_windows_open_command_quotes_executable():
    command = associations._windows_open_command([r"C:\Program Files\lite2obj\lite2obj.exe"])

    assert command == '"C:\\Program Files\\lite2obj\\lite2obj.exe" "%1"'


def test_command_uses_appimage_path(tmp_path, monkeypatch):
    appimage = tmp_path / "lite2obj.AppImage"
    appimage.write_bytes(b"")
    monkeypatch.setenv("APPIMAGE", str(appimage))

    assert associations.command() == [str(appimage.resolve())]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="sólo Linux")
def test_linux_install_and_uninstall(tmp_path, monkeypatch):
    monkeypatch.setattr(associations, "_data_home", lambda: tmp_path)
    monkeypatch.setattr(associations, "_run", lambda argv: True)
    monkeypatch.setattr(associations, "command", lambda: ["/opt/lite2obj.AppImage"])

    detail = associations.install()
    assert "Asociación creada" in detail

    desktop = tmp_path / "applications" / associations.DESKTOP_ID
    mime = tmp_path / "mime" / "packages" / "lite2obj.xml"
    assert desktop.is_file()
    assert mime.is_file()
    assert "Exec=/opt/lite2obj.AppImage %f" in desktop.read_text(encoding="utf-8")
    assert associations.is_associated()

    config = tmp_path / "config"
    config.mkdir()
    mimeapps = config / "mimeapps.list"
    mimeapps.write_text(
        "[Default Applications]\n"
        "application/x-litematic=lite2obj.desktop;\n"
        "text/plain=otro.desktop;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(associations, "_config_home", lambda: config)

    detail = associations.uninstall()
    assert "eliminada" in detail
    assert not desktop.exists()
    assert not mime.exists()
    assert not associations.is_associated()
    remaining = mimeapps.read_text(encoding="utf-8")
    assert "application/x-litematic" not in remaining
    assert "text/plain=otro.desktop;" in remaining


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="sólo Linux")
def test_linux_uninstall_without_association(tmp_path, monkeypatch):
    monkeypatch.setattr(associations, "_data_home", lambda: tmp_path)
    monkeypatch.setattr(associations, "_run", lambda argv: True)

    detail = associations.uninstall()
    assert "No había" in detail


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="sólo Linux")
def test_linux_association_detects_moved_appimage(tmp_path, monkeypatch):
    monkeypatch.setattr(associations, "_data_home", lambda: tmp_path)
    monkeypatch.setattr(associations, "_run", lambda argv: True)
    monkeypatch.setattr(associations, "command", lambda: ["/opt/viejo/lite2obj.AppImage"])

    associations.install()
    assert associations.is_associated()

    monkeypatch.setattr(associations, "command", lambda: ["/opt/nuevo/lite2obj.AppImage"])
    assert not associations.is_associated()
