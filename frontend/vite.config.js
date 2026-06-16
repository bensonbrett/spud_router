import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      // In dev, proxy all /api calls to the running backend
      '/api': {
        target: 'http://localhost:8080',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: '../dist',      // Output to repo root dist/ (one level up from frontend/)
    emptyOutDir: true,
  },
})
