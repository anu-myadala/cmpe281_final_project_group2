import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The dev server proxies the C1 API, the WebSocket and the C2/C3 stub paths to one
// backend process (STUBS_INLINE=1 mounts the stubs under /c2/api/v1 and /c3/api/v1).
const api = process.env.VITE_PROXY_API ?? 'http://localhost:8000';
const stubs = process.env.VITE_PROXY_STUBS ?? api;

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: api, changeOrigin: false },
      '/ws': { target: api.replace(/^http/, 'ws'), ws: true },
      '/c2': { target: stubs, changeOrigin: false },
      '/c3': { target: stubs, changeOrigin: false },
    },
  },
  preview: {
    port: 4173,
    proxy: {
      '/api': { target: api, changeOrigin: false },
      '/ws': { target: api.replace(/^http/, 'ws'), ws: true },
      '/c2': { target: stubs, changeOrigin: false },
      '/c3': { target: stubs, changeOrigin: false },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (!id.includes('node_modules')) return undefined;
          if (/[\\/](recharts|recharts-scale|d3-[a-z-]+|victory-vendor|decimal\.js-light|internmap|fast-equals|react-smooth)[\\/]/.test(id)) return 'charts';
          if (/[\\/](leaflet|react-leaflet|@react-leaflet)[\\/]/.test(id)) return 'map';
          return 'vendor';
        },
      },
    },
  },
});
