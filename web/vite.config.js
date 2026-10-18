import { defineConfig } from 'vite';

/**
 * Modo desarrollo con npm: `npm run dev` sirve el visor y reenvía /api al servidor de Python
 * (`uv run lite2obj view`). El `index.html` es el mismo que sirve Python, así que no hay dos
 * versiones del visor.
 */
export default defineConfig({
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: true,
  },
});
