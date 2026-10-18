# Visor web

Visor Three.js del esquemático ya renderizado por el backend Python. Todo el modelo llega
como **un solo búfer binario** (posiciones, normales, UVs e índices) y el JSON de metadatos
sólo indica el desplazamiento y el tamaño de cada parte, así que no hay que recorrer ni copiar
geometría en el navegador.

## Sin npm (modo por defecto)

`uv run lite2obj view` sirve estos mismos archivos y el navegador resuelve `three` con el
*importmap* de `index.html` (CDN de unpkg). No hace falta `npm install` para ver nada.

```
web/
├── index.html      # importmap + estructura de la interfaz
├── style.css
└── src/main.js     # escena, carga del búfer, materiales por modo de transparencia
```

## Con npm / Vite

`index.html` importa `three` como módulo *bare*. Eso funciona igual con Vite, que reescribe el
import a `node_modules` e ignora el importmap; no hay dos versiones del visor.

```bash
# terminal 1: backend
uv run lite2obj view --port 8000

# terminal 2: visor con recarga en caliente (proxy de /api al backend)
npm install
npm run dev        # http://127.0.0.1:5173
```

`npm run build` genera `dist/`, que puedes servir con cualquier servidor estático siempre que
`/api` apunte al backend.

## Detalles que importan

- **`texture.flipY = false`**: las UV del atlas tienen `v` hacia abajo, como la imagen.
- **Filtro `Nearest`** con mipmaps: look de Minecraft con atlas de potencia de dos.
- **Tres materiales** (`opaque`, `cutout` con `alphaTest`, `translucent` con `depthWrite:false`)
  y `geometry.addGroup()` por modo; los triángulos ya vienen ordenados para que los
  translúcidos se dibujen al final.
- **Iluminación de bloque**: ambiente alto + una direccional, para que las caras se distingan
  sin sombras sucias.
- Enlace directo a un esquemático con `?path=/ruta/al/esquema.litematic`.
