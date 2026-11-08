"""Selección del idioma de la interfaz y traducción de sus textos.

El español es el idioma de origen: los textos visibles se escriben en español y el
catálogo ``_EN`` contiene su traducción al inglés. Si el idioma del sistema no está
soportado se usa el inglés. ``LITE2OBJ_LANG`` (``en`` o ``es``) y la opción ``--lang``
permiten forzar un idioma.
"""

from __future__ import annotations

import locale
import os
from collections.abc import Sequence
from typing import Final

__all__ = [
    "DEFAULT_LANGUAGE",
    "LANGUAGE_LABELS",
    "SUPPORTED_LANGUAGES",
    "TRANSLATIONS",
    "configure",
    "current_language",
    "extract_language_option",
    "is_explicit",
    "normalize_language",
    "set_language",
    "system_language",
]

DEFAULT_LANGUAGE: Final = "en"
SUPPORTED_LANGUAGES: Final = ("en", "es")
LANGUAGE_LABELS: Final = {"en": "English", "es": "Español"}

_ALIASES: Final[dict[str, str]] = {
    "en": "en",
    "eng": "en",
    "english": "en",
    "es": "es",
    "spa": "es",
    "spanish": "es",
    "espanol": "es",
    "español": "es",
}

#: Traducciones al inglés de los textos escritos en español en el código.
_EN: Final[dict[str, str]] = {
    # -- filtros de archivos -------------------------------------------------
    "Litematica (*.litematic);;Todos los archivos (*)": ("Litematica (*.litematic);;All files (*)"),
    "PNG (*.png);;Todos los archivos (*)": "PNG (*.png);;All files (*)",
    "Assets de lite2obj (*.l2oassets);;Todos los archivos (*)": (
        "lite2obj assets (*.l2oassets);;All files (*)"
    ),
    "Minecraft client (*.jar);;Todos los archivos (*)": ("Minecraft client (*.jar);;All files (*)"),
    "Resource pack (*.zip);;Todos los archivos (*)": ("Resource pack (*.zip);;All files (*)"),
    # -- estado de los assets ------------------------------------------------
    "✗  Assets base de Minecraft no configurados": ("✗  Minecraft base assets not configured"),
    "Genera el archivo .l2oassets desde un client jar.": (
        "Generate the .l2oassets file from a client jar."
    ),
    "✗  El archivo de assets no es válido": "✗  The assets file is not valid",
    "{name} · vuelve a generarlo desde un client jar.": (
        "{name} · regenerate it from a client jar."
    ),
    "desconocida": "unknown",
    "Generados desde Minecraft {version} · {name}": ("Generated from Minecraft {version} · {name}"),
    "⚠  Hay bloques que no están en los assets": ("⚠  Some blocks are not in the assets"),
    "{detail}\n{count:,} bloque(s) omitidos: {shown}. "
    "Actualiza el .l2oassets con un client jar más reciente.": (
        "{detail}\n{count:,} block(s) omitted: {shown}. "
        "Update the .l2oassets with a newer client jar."
    ),
    "✓  Assets base de Minecraft configurados": "✓  Minecraft base assets configured",
    "y {count} más": "and {count} more",
    # -- panel ---------------------------------------------------------------
    "Esquemático": "Schematic",
    "Abre un .litematic o arrástralo sobre la ventana.": (
        "Open a .litematic file or drag it onto the window."
    ),
    "Abrir…": "Open…",
    "Recargar": "Reload",
    "Vuelve a leer el esquemático actual": "Reload the current schematic",
    "Ningún esquemático abierto.": "No schematic open.",
    "Assets de Minecraft": "Minecraft assets",
    "Generar archivo de assets…": "Generate assets file…",
    "Elige el client jar de Minecraft y genera el .l2oassets": (
        "Choose the Minecraft client jar and generate the .l2oassets"
    ),
    "Opcional. Arriba = mayor prioridad. Los assets que falten se toman del "
    "archivo vanilla. Puedes añadir varios .zip a la vez o una carpeta con varios "
    "packs.": (
        "Optional. Top = highest priority. Missing assets are taken from the vanilla "
        "file. You can add several .zip files at once or a folder containing several "
        "packs."
    ),
    "Marca los packs que quieres aplicar al render": (
        "Check the packs you want to apply to the render"
    ),
    "Añadir .zip…": "Add .zip…",
    "Añade uno o varios resource packs comprimidos": ("Add one or more compressed resource packs"),
    "Añadir carpeta…": "Add folder…",
    "Añade un pack o todos los que contenga una carpeta": (
        "Add a pack or every pack inside a folder"
    ),
    "Subir": "Move up",
    "Bajar": "Move down",
    "Quitar": "Remove",
    "Hay cambios sin aplicar. Pulsa Renderizar (F5).": (
        "There are unapplied changes. Press Render (F5)."
    ),
    "Renderizar": "Render",
    "Vuelve a generar la malla con las opciones actuales (F5)": (
        "Rebuild the mesh with the current options (F5)"
    ),
    "Vista y render": "View and render",
    "Descartar caras ocultas": "Cull hidden faces",
    "Activa el culling por cullface del modelo": ("Enable culling using the model's cullface"),
    "Teñir biomas (plains)": "Tint biomes (plains)",
    "Dibujar mobs": "Draw mobs",
    "Criaturas como cerdos, aldeanos o monstruos (desactivado)": (
        "Creatures such as pigs, villagers or monsters (disabled)"
    ),
    "Dibujar items caídos": "Draw dropped items",
    "Items caídos en el suelo (desactivado)": "Items dropped on the ground (disabled)",
    "Suavizar texturas": "Smooth textures",
    "Filtrado lineal con mipmaps en el visor": ("Linear filtering with mipmaps in the viewer"),
    "Mostrar rejilla": "Show grid",
    "Mostrar ejes": "Show axes",
    "Resolución del atlas": "Atlas resolution",
    "Multiplica el tamaño de las texturas del atlas": ("Multiplies the size of the atlas textures"),
    "Capas Y": "Y layers",
    "Primera capa visible": "First visible layer",
    "Última capa visible": "Last visible layer",
    "Color de fondo": "Background color",
    "Exportar a Blender": "Export to Blender",
    "Carpeta": "Folder",
    "Nombre": "Name",
    "Ninguna": "None",
    "Por bloque": "By block",
    "Por transparencia": "By transparency",
    "Agrupar": "Group by",
    "Escala OBJ": "OBJ scale",
    "Z arriba (nativo de Blender)": "Z up (Blender native)",
    "Exportar OBJ…": "Export OBJ…",
    "Escribe <nombre>.obj, <nombre>.mtl y atlas.png": (
        "Writes <name>.obj, <name>.mtl and atlas.png"
    ),
    "Estadísticas": "Statistics",
    # -- acciones y menús ----------------------------------------------------
    "Abrir esquemático…": "Open schematic…",
    "Guardar captura…": "Save screenshot…",
    "Salir": "Quit",
    "Reencuadrar": "Frame",
    "Rejilla": "Grid",
    "Ejes": "Axes",
    "Elige el client jar de Minecraft y extrae blockstates, modelos y texturas": (
        "Choose the Minecraft client jar and extract blockstates, models and textures"
    ),
    "Asociar archivos .litematic…": "Associate .litematic files…",
    "Haz que los .litematic se abran con lite2obj al hacer doble clic": (
        "Make .litematic files open with lite2obj on double click"
    ),
    "Quitar asociación de .litematic": "Remove .litematic association",
    "Deshace la asociación creada desde lite2obj": ("Undo the association created by lite2obj"),
    "Renderizar litemática de prueba": "Render test litematic",
    "Abre y renderiza all_blocks_and_entities.litematic": (
        "Open and render all_blocks_and_entities.litematic"
    ),
    "Acerca de lite2obj": "About lite2obj",
    "Archivo": "File",
    "Ver": "View",
    "Ayuda": "Help",
    "Principal": "Main",
    # -- estado y diálogos ---------------------------------------------------
    "Listo": "Ready",
    "Error": "Error",
    "OpenGL no disponible": "OpenGL unavailable",
    "Faltan las texturas de Minecraft": "Minecraft textures are missing",
    "No se encontró el archivo de assets de Minecraft.": (
        "The Minecraft assets file was not found."
    ),
    "También puedes generarlo ahora eligiendo el client jar.": (
        "You can also generate it now by choosing the client jar."
    ),
    "Generar ahora…": "Generate now…",
    "Cerrar": "Close",
    "Añadir resource packs": "Add resource packs",
    "No se encontraron resource packs en la carpeta.": (
        "No resource packs were found in the folder."
    ),
    "{directory}\n\nElige un .zip, una carpeta con 'assets' o una carpeta "
    "que contenga varios packs.": (
        "{directory}\n\nChoose a .zip, a folder with 'assets' or a folder containing several packs."
    ),
    "Algunos resource packs no son válidos.": "Some resource packs are not valid.",
    "\n\nDeben ser archivos .zip o carpetas con una subcarpeta 'assets'.": (
        "\n\nThey must be .zip files or folders with an 'assets' subfolder."
    ),
    "No se pudo leer el esquemático.": "Could not read the schematic.",
    "Exportación completada": "Export complete",
    "Se escribieron los archivos:": "The following files were written:",
    "No se pudo completar la operación.": "The operation could not be completed.",
    "No se pudo crear el contexto OpenGL 3.3.\n\n"
    "En Linux con NVIDIA + Wayland prueba a ejecutar con QT_QPA_PLATFORM=xcb.": (
        "Could not create the OpenGL 3.3 context.\n\n"
        "On Linux with NVIDIA + Wayland, try running with QT_QPA_PLATFORM=xcb."
    ),
    "Abrir esquemático": "Open schematic",
    "Carpeta de exportación": "Export folder",
    "Guardar captura": "Save screenshot",
    "Selecciona el client jar de Minecraft": "Select the Minecraft client jar",
    "Guardar archivo de assets": "Save assets file",
    "No se pudo guardar la captura.": "Could not save the screenshot.",
    "Espera a que termine la operación actual": ("Wait for the current operation to finish"),
    "No se pudo leer el client jar.": "Could not read the client jar.",
    "Generando assets desde {name}…": "Generating assets from {name}…",
    "Archivo de assets generado": "Assets file generated",
    "Assets de Minecraft {version} guardados en {name}": (
        "Minecraft {version} assets saved to {name}"
    ),
    "Minecraft: {version}\nArchivos: {files:,}\nTamaño: {size:.1f} MB\n\n"
    "{path}\n\nEl programa ya usa este archivo. Vuelve a renderizar si había "
    "una malla abierta.": (
        "Minecraft: {version}\nFiles: {files:,}\nSize: {size:.1f} MB\n\n"
        "{path}\n\nThe program already uses this file. Render again if a mesh "
        "was open."
    ),
    "Abriendo litemática de prueba…": "Opening test litematic…",
    "No se encontró el esquemático de prueba.": ("The test schematic was not found."),
    "Se esperaba en {path}": "Expected at {path}",
    "Asociación de archivos": "File association",
    "La asociación automática sólo está disponible en Linux y Windows.": (
        "Automatic association is only available on Linux and Windows."
    ),
    "Asociar archivos .litematic": "Associate .litematic files",
    "Los archivos .litematic se abrirán con lite2obj al hacer doble clic.\n\n"
    "Programa: {command}\nAsociación instalada: {state}\n\n"
    "La asociación se guarda sólo para tu usuario. ¿Continuar?": (
        ".litematic files will open with lite2obj on double click.\n\n"
        "Program: {command}\nInstalled association: {state}\n\n"
        "The association is stored only for your user. Continue?"
    ),
    "activa": "active",
    "inactiva": "inactive",
    "No se pudo asociar el archivo .litematic.": ("Could not associate the .litematic file."),
    "Asociación creada": "Association created",
    "Archivos .litematic asociados a lite2obj": (".litematic files associated with lite2obj"),
    "Renderizador de Litematica": "Litematica renderer",
    "Renderiza esquemáticos .litematic y los exporta a OBJ para Blender": (
        "Renders .litematic schematics and exports them to OBJ for Blender"
    ),
    "Esquemático de Litematica": "Litematica schematic",
    "Los archivos .litematic dejarán de abrirse con lite2obj al hacer doble clic.\n\n¿Continuar?": (
        ".litematic files will no longer open with lite2obj on double click.\n\nContinue?"
    ),
    "No se pudo quitar la asociación.": "Could not remove the association.",
    "Asociación eliminada": "Association removed",
    "Asociación de .litematic eliminada": ".litematic association removed",
    "lite2obj\n\nRenderiza esquemáticos de Litematica y los exporta a OBJ + MTL "
    "+ atlas para Blender.\n\nPython + PySide6 + OpenGL de Qt · texturas del "
    "archivo de assets.": (
        "lite2obj\n\nRenders Litematica schematics and exports them to OBJ + MTL "
        "+ atlas for Blender.\n\nPython + PySide6 + Qt OpenGL · textures from the "
        "assets file."
    ),
    # -- información y estadísticas -----------------------------------------
    "{width} × {height} × {depth} bloques": ("{width} × {height} × {depth} blocks"),
    "{regions} región(es) · {blocks:,} bloques": ("{regions} region(s) · {blocks:,} blocks"),
    "Autor: {author}": "Author: {author}",
    "Capas Y: {low}–{high} de {maximum}": "Y layers: {low}–{high} of {maximum}",
    "Tamaño: {width} × {height} × {depth} bloques\n"
    "Caras: {faces:,} ({triangles:,} triángulos)\n"
    "Bloques: {states:,} estados distintos\n"
    "Sprites: {sprites} · atlas {atlas_width}×{atlas_height}\n"
    "Tiempo: {elapsed:.2f} s": (
        "Size: {width} × {height} × {depth} blocks\n"
        "Faces: {faces:,} ({triangles:,} triangles)\n"
        "Blocks: {states:,} distinct states\n"
        "Sprites: {sprites} · atlas {atlas_width}×{atlas_height}\n"
        "Time: {elapsed:.2f} s"
    ),
    "⚠ Omitidos: {count:,} bloques ({names})": ("⚠ Omitted: {count:,} blocks ({names})"),
    "Hay cambios pendientes; vuelve a exportar": ("There are pending changes; export again"),
    "Malla lista": "Mesh ready",
    "Opciones cambiadas": "Options changed",
    "Abre un esquemático antes de exportar": ("Open a schematic before exporting"),
    "Renderizando antes de exportar…": "Rendering before export…",
    "{faces:,} caras · {vertices:,} vértices · {uvs:,} UVs": (
        "{faces:,} faces · {vertices:,} vertices · {uvs:,} UVs"
    ),
    "Exportado: {path}": "Exported: {path}",
    "Captura guardada: {path}": "Screenshot saved: {path}",
    "Renderizando…": "Rendering…",
    "Exportando…": "Exporting…",
    "Generando assets…": "Generating assets…",
    # -- CLI -----------------------------------------------------------------
    "Interfaz de línea de comandos de lite2obj.": ("lite2obj command-line interface."),
    "idioma de la interfaz (en, es)": "interface language (en, es)",
    "  archivo      {path}": "  file         {path}",
    "  autor        {author}": "  author       {author}",
    "  descripción  {description}": "  description  {description}",
    "  tamaño       {width} x {height} x {depth}": ("  size         {width} x {height} x {depth}"),
    "  regiones     {count}": "  regions      {count}",
    "  bloques      {count}": "  blocks       {count}",
    "  volumen      {volume}": "  volume       {volume}",
    "  data version {version} (formato v{major}.{minor})": (
        "  data version {version} (format v{major}.{minor})"
    ),
    "  región '{name}'": "  region '{name}'",
    "    origen    {origin}": "    origin    {origin}",
    "    tamaño    {width} x {height} x {depth}": ("    size      {width} x {height} x {depth}"),
    "    paleta    {count} estados": "    palette   {count} states",
    "    entidades {count} tile entities": "    entities  {count} tile entities",
    "  {count} tipos de bloque distintos; los más frecuentes:": (
        "  {count} distinct block types; the most frequent:"
    ),
    "El archivo de assets no es válido: {path}": ("The assets file is not valid: {path}"),
    "Archivo de assets: {path}": "Assets file: {path}",
    "No se buscan esquemáticos ni resource packs automáticamente.": (
        "Schematics and resource packs are not scanned automatically."
    ),
    "No se indicó ningún client jar. Usa --jar <ruta> o define LITE2OBJ_CLIENT_JAR.": (
        "No client jar was given. Use --jar <path> or set LITE2OBJ_CLIENT_JAR."
    ),
    "Error: {error}": "Error: {error}",
    "Minecraft  {version}": "Minecraft  {version}",
    "Archivo    {path}": "File       {path}",
    "Contenido  {files} archivos, {size}": "Contents   {files} files, {size}",
    "Tamaño     {size}": "Size       {size}",
    "El programa usará este archivo si está en los datos del usuario.": (
        "The program will use this file if it is in the user data directory."
    ),
    "No se encontró ningún archivo de assets. Genera uno con 'lite2obj assets build'.": (
        "No assets file was found. Generate one with 'lite2obj assets build'."
    ),
    "El archivo no es válido: {path}": "The file is not valid: {path}",
    "OBJ    {path}  ({size})": "OBJ    {path}  ({size})",
    "MTL    {path}": "MTL    {path}",
    "atlas  {path}  ({size} B)": "atlas  {path}  ({size} B)",
    "{faces} caras, {vertices} vértices, {uvs} UVs": (
        "{faces} faces, {vertices} vertices, {uvs} UVs"
    ),
    "muestra metadatos y recuento de bloques": ("show metadata and block counts"),
    "archivo .litematic": ".litematic file",
    "cuántos tipos de bloque listar (0 = todos)": ("how many block types to list (0 = all)"),
    "muestra el archivo de assets activo": "show the active assets file",
    "gestiona el archivo de assets de Minecraft": ("manage the Minecraft assets file"),
    "extrae blockstates, modelos y texturas de un client jar": (
        "extract blockstates, models and textures from a client jar"
    ),
    "client jar de Minecraft (obligatorio si no defines LITE2OBJ_CLIENT_JAR)": (
        "Minecraft client jar (required if LITE2OBJ_CLIENT_JAR is not set)"
    ),
    "archivo .l2oassets de salida (por defecto, en los datos del usuario)": (
        "output .l2oassets file (defaults to the user data directory)"
    ),
    "sobrescribe el archivo si ya existe": "overwrite the file if it exists",
    "muestra el manifiesto de un archivo de assets": ("show the manifest of an assets file"),
    "archivo .l2oassets (por defecto, el que usa el programa)": (
        ".l2oassets file (defaults to the one used by the program)"
    ),
    "exporta a OBJ + MTL + atlas": "export to OBJ + MTL + atlas",
    "carpeta de salida": "output folder",
    "nombre base de los archivos": "base name of the files",
    "ruta a un resource pack (.zip o carpeta); repetible, el último tiene prioridad": (
        "path to a resource pack (.zip or folder); repeatable, the last one wins"
    ),
    "exporta con el eje Z arriba (Blender)": "export with the Z axis up (Blender)",
    "agrupa las caras del OBJ por bloque o por modo de transparencia": (
        "group OBJ faces by block or transparency mode"
    ),
    "no descarta caras ocultas": "do not cull hidden faces",
    "desactiva los tintes de bioma": "disable biome tints",
    "no dibuja entidades ni *block entities* (cofres, marcos, cuadros...)": (
        "do not draw entities or block entities (chests, frames, paintings...)"
    ),
    "dibuja criaturas (desactivado por defecto; requiere entidades)": (
        "draw creatures (disabled by default; requires entities)"
    ),
    "dibuja items caídos (desactivado por defecto; requiere entidades)": (
        "draw dropped items (disabled by default; requires entities)"
    ),
    "multiplica la resolución de las texturas del atlas": ("multiply the atlas texture resolution"),
    "escala de la geometría exportada": "scale of the exported geometry",
    "abre la aplicación de escritorio": "open the desktop application",
    "archivo .litematic a abrir": ".litematic file to open",
    "Abre la aplicación de escritorio de lite2obj.": ("Open the lite2obj desktop application."),
    # -- errores de la biblioteca -------------------------------------------
    "No se encontraron las texturas de Minecraft.\n\n"
    "Falta el archivo {name}. El programa no busca instalaciones de Minecraft "
    "ni resource packs en el disco.\n\n"
    "Genera el archivo en un equipo con Minecraft instalado:\n"
    "  lite2obj assets build --jar <ruta-al-client.jar>\n\n"
    "Después colócalo junto al programa, en los datos del usuario o define "
    "LITE2OBJ_ASSET_PACK.": (
        "Minecraft textures were not found.\n\n"
        "The file {name} is missing. The program does not scan the disk for "
        "Minecraft installations or resource packs.\n\n"
        "Generate the file on a machine with Minecraft installed:\n"
        "  lite2obj assets build --jar <path-to-client.jar>\n\n"
        "Then place it next to the program, in the user data directory or set "
        "LITE2OBJ_ASSET_PACK."
    ),
    "Se necesita al menos una fuente de recursos": ("At least one resource source is required"),
    "El archivo de assets no es válido: {path}. Genera uno con 'lite2obj assets build'.": (
        "The assets file is not valid: {path}. Generate one with 'lite2obj assets build'."
    ),
    "Resource pack no válido: {path}": "Invalid resource pack: {path}",
    "No existe el client jar: {path}": "The client jar does not exist: {path}",
    "El client jar no es un zip válido: {path}": ("The client jar is not a valid zip: {path}"),
    "El archivo ya existe: {path}": "The file already exists: {path}",
    "El jar no contiene assets de Minecraft reconocibles (se esperaban rutas como {prefix}).": (
        "The jar does not contain recognizable Minecraft assets (expected paths like {prefix})."
    ),
    "No se pudieron escribir los archivos de asociación: {error}": (
        "Could not write the association files: {error}"
    ),
    "No se pudo borrar {path}: {error}": "Could not delete {path}: {error}",
    "No se pudo escribir en el Registro de Windows: {error}": (
        "Could not write to the Windows Registry: {error}"
    ),
    "Asociación creada para los archivos .litematic.\n"
    "Programa: {command}\nEntrada: {entry}\n\n"
    "Si el gestor de archivos no lo detecta al momento, cierra la sesión o "
    "vuelve a abrir el diálogo «Abrir con».": (
        "Association created for .litematic files.\n"
        "Program: {command}\nEntry: {entry}\n\n"
        "If the file manager does not detect it immediately, log out or reopen "
        'the "Open with" dialog.'
    ),
    "Asociación de .litematic eliminada.": ".litematic association removed.",
    "No había una asociación instalada.": "There was no installed association.",
    "Asociación creada para los archivos .litematic.\n"
    "Programa: {command}\n\n"
    "Windows puede pedirte que confirmes lite2obj la primera vez que abras un "
    "archivo.": (
        "Association created for .litematic files.\n"
        "Program: {command}\n\n"
        "Windows may ask you to confirm lite2obj the first time you open a file."
    ),
    "Ancho de bit inválido: {bits}": "Invalid bit width: {bits}",
    "BlockStates demasiado pequeño: {longs} longs para {count} entradas de {bits} bits": (
        "BlockStates too small: {longs} longs for {count} entries of {bits} bits"
    ),
    "La raíz NBT no es un compuesto": "The NBT root is not a compound",
    "El archivo no contiene 'Regions'": "The file does not contain 'Regions'",
    "Compresión desconocida: {compression!r}": "Unknown compression: {compression!r}",
    "Fin de buffer inesperado leyendo array": ("Unexpected end of buffer while reading array"),
    "Anidamiento NBT demasiado profundo (posible archivo corrupto)": (
        "NBT nesting too deep (possible corrupt file)"
    ),
    "La raíz NBT debería ser TAG_Compound, es {tag}": (
        "The NBT root should be TAG_Compound, got {tag}"
    ),
    "Fin de buffer inesperado en {context} (pedían {count})": (
        "Unexpected end of buffer in {context} (requested {count})"
    ),
    "Longitud de array negativa: {length}": "Negative array length: {length}",
    "Longitud de lista negativa: {length}": "Negative list length: {length}",
    "Tag desconocido: {tag}": "Unknown tag: {tag}",
    "Eje de rotación inválido: {axis!r}": "Invalid rotation axis: {axis!r}",
    "Dirección de cara inválida: {direction!r}": ("Invalid face direction: {direction!r}"),
    "No se pudo resolver el blockstate o el modelo.": (
        "Could not resolve the blockstate or model."
    ),
    "{block}: ninguna variante coincide con {props}": ("{block}: no variant matches {props}"),
    "{block}: ningún multipart coincide con {props}": ("{block}: no multipart matches {props}"),
    "No hay blockstate para {block}": "No blockstate for {block}",
    "No se encontró el modelo {model!r}": "Model not found: {model!r}",
    "{block}: sin 'variants' ni 'multipart'": ("{block}: no 'variants' or 'multipart'"),
    "No hay blockstate para {block!r}": "No blockstate for {block!r}",
    "Fin de buffer inesperado en {position} (pedían {count} bytes)": (
        "Unexpected end of buffer at {position} (requested {count} bytes)"
    ),
    "Fin de buffer inesperado en {position}": ("Unexpected end of buffer at {position}"),
    "# lite2obj - {faces} caras, {vertices} vertices\n": (
        "# lite2obj - {faces} faces, {vertices} vertices\n"
    ),
    "# origen {origin}  tamano {size}\n": "# origin {origin}  size {size}\n",
    "up_axis desconocido: {axis!r}": "Unknown up_axis: {axis!r}",
    "group_by desconocido: {value!r}": "Unknown group_by: {value!r}",
    "Las texturas no caben en un atlas de 8192x8192": ("Textures do not fit in an 8192x8192 atlas"),
    "shader principal: {log}": "main shader: {log}",
    "shader de líneas: {log}": "line shader: {log}",
}

TRANSLATIONS: Final[dict[str, dict[str, str]]] = {"en": _EN, "es": {}}

_explicit = False


def _normalize_one(value: str) -> str | None:
    text = value.strip().lower().split(".", 1)[0].replace("-", "_")
    if not text:
        return None
    return _ALIASES.get(text) or _ALIASES.get(text.split("_", 1)[0])


def normalize_language(value: str | None) -> str | None:
    """Devuelve el código soportado (``en``/``es``) o ``None`` si no lo está."""
    if not value:
        return None
    for part in str(value).split(":"):
        code = _normalize_one(part)
        if code is not None:
            return code
    return None


def system_language() -> str:
    """Idioma del sistema, o inglés si no está soportado."""
    for variable in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        code = normalize_language(os.environ.get(variable))
        if code is not None:
            return code
    try:
        name = locale.getlocale()[0]
    except ValueError, TypeError:
        name = None
    code = normalize_language(name)
    if code is not None:
        return code
    try:
        from PySide6.QtCore import QLocale
    except Exception:  # pragma: no cover - Qt puede no estar disponible
        return DEFAULT_LANGUAGE
    return normalize_language(QLocale.system().name()) or DEFAULT_LANGUAGE


def set_language(language: str | None = None) -> str:
    """Fija el idioma activo. ``None`` vuelve a detectar el del sistema."""
    global _current, _explicit
    text = str(language).strip() if language is not None else ""
    if text and text.lower() not in ("system", "auto"):
        _current = normalize_language(text) or DEFAULT_LANGUAGE
        _explicit = True
    else:
        _current = system_language()
        _explicit = False
    return _current


def configure(preference: str | None = None) -> str:
    """Aplica la preferencia, ``LITE2OBJ_LANG`` o el idioma del sistema."""
    text = str(preference).strip() if preference is not None else ""
    if text and text.lower() not in ("system", "auto"):
        return set_language(text)
    environment = os.environ.get("LITE2OBJ_LANG", "").strip()
    if environment:
        return set_language(environment)
    if _explicit:
        return _current
    return set_language(None)


def current_language() -> str:
    """Código del idioma activo."""
    return _current


def is_explicit() -> bool:
    """Cierto si el idioma se fijó a mano (no por detección del sistema)."""
    return _explicit


def _(message: str, /, **values: object) -> str:
    """Traduce ``message`` y sustituye los valores con ``str.format``."""
    template = TRANSLATIONS.get(_current, {}).get(message, message)
    return template.format(**values) if values else template


def extract_language_option(argv: Sequence[str]) -> tuple[list[str], str | None]:
    """Quita ``--lang`` de ``argv`` y devuelve el resto y el idioma pedido."""
    remaining: list[str] = []
    language: str | None = None
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument == "--lang" and index + 1 < len(argv):
            language = argv[index + 1]
            index += 2
            continue
        if argument.startswith("--lang="):
            language = argument.split("=", 1)[1]
            index += 1
            continue
        remaining.append(argument)
        index += 1
    return remaining, language


# El idioma del sistema se detecta al importar; los puntos de entrada pueden cambiarlo.
_current: str = system_language()
