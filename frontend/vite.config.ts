import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { fileURLToPath, URL } from 'node:url'

// The FastAPI backend from ../backend. Override with BACKEND_URL if it
// runs somewhere other than the default uvicorn port.
const backend = process.env.BACKEND_URL ?? 'http://127.0.0.1:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL('./src', import.meta.url))
    },
  },
  optimizeDeps: {
    // The Open-Meteo layer locates its WebAssembly binary with
    // `new URL('om_file_format.web.wasm', import.meta.url)`. Pre-bundling
    // rewrites that module into .vite/deps/, where the .wasm does not exist,
    // so the reader 404s and no weather tiles are ever requested. Serving the
    // package from its own directory keeps the relative path valid.
    exclude: ['@openmeteo/weather-map-layer'],
  },
  server: {
    // Proxy the backend's API routes so the dev server and the API share
    // an origin - same as production, where the backend serves this build
    // itself. Keeps fetch() paths relative and CORS out of the picture.
    proxy: {
      '/query': backend,
      '/health': backend,
      '/voice': backend,
      '/docs': backend,
      '/openapi.json': backend,
      '/research': backend,
    },
  },
})
