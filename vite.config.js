import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    strictPort: true,
    host: true,
    proxy: {
      '/api': {
        target: 'http://backend:8002',
        changeOrigin: true,
      },
      '/sidecar': {
        // Sidecar runs on the host (Claude Agent SDK needs the local `claude`
        // CLI for Max auth — can't run inside Docker easily). `host.docker.internal`
        // is the Mac/Windows Docker alias for the host machine.
        target: 'http://host.docker.internal:5179',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/sidecar/, ''),
      },
    },
  },
})
