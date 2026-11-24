import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The API only accepts its own origin, so the dev proxy presents itself as that origin.
const target = 'http://127.0.0.1:8765'
export default defineConfig({
  plugins: [react()],
  server: { host: '127.0.0.1', proxy: { '/api': { target, changeOrigin: true, headers: { origin: target } } } },
})
