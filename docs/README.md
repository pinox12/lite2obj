# Landing de lite2obj (GitHub Pages)

Esta carpeta se publica tal cual con el workflow `.github/workflows/pages.yml`.

## Antes de publicar

1. Sustituye `TU-USUARIO` en el atributo `data-repo` de `index.html` y de `en/index.html`
   por el usuario u organización real de GitHub. Los enlaces a GitHub, Releases e Issues se
   generan a partir de ese valor.
2. Activa Pages una vez en **Settings → Pages → Build and deployment** y elige **GitHub
   Actions** como origen. El `GITHUB_TOKEN` del workflow no puede crear el sitio
   (`Resource not accessible by integration`), así que este paso es manual. Pages necesita un
   repositorio público o un plan que lo admita.
3. Haz push a `main` o `master`. El workflow publica `docs/` en Pages.

La web está en español (`index.html`) e inglés (`en/index.html`). Los enlaces de idioma del
menú y del pie permiten cambiar entre las dos versiones.

## Archivos

| Archivo | Uso |
|---|---|
| `index.html` | Landing en español |
| `en/index.html` | Landing en inglés |
| `styles.css` | Estilos compartidos; sin dependencias externas ni build |
| `main.js` | Menú móvil, cabecera al hacer scroll y enlaces a GitHub |
| `assets/icon.png` | Icono de la app y favicon |
| `assets/preview.svg` | Ilustración isométrica del visor |
| `assets/preview.png` | Copia rasterizada para la imagen social (`og:image`) |

No se incluye ningún `.l2oassets`: la landing no necesita texturas y las builds se publican
sin ellas.
