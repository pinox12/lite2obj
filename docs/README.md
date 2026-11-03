# Landing de lite2obj (GitHub Pages)

Esta carpeta se publica tal cual con el workflow `.github/workflows/pages.yml`.

## Antes de publicar

1. Sustituye `TU-USUARIO` en el atributo `data-repo` de `index.html` por el usuario u
   organización real de GitHub. Los enlaces a GitHub, Releases e Issues se generan a partir
   de ese valor.
2. En **Settings → Pages → Build and deployment**, elige **GitHub Actions** como origen.
3. Haz push a `main` o `master`. El workflow publica `docs/` en Pages.

## Archivos

| Archivo | Uso |
|---|---|
| `index.html` | Contenido de la landing (hero, características, descargas, FAQ) |
| `styles.css` | Estilos; sin dependencias externas ni build |
| `main.js` | Menú móvil, cabecera al hacer scroll y enlaces a GitHub |
| `assets/icon.png` | Icono de la app y favicon |
| `assets/preview.svg` | Ilustración isométrica del visor |
| `assets/preview.png` | Copia rasterizada para la imagen social (`og:image`) |

No se incluye ningún `.l2oassets`: la landing no necesita texturas y las builds se publican
sin ellas.
