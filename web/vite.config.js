import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

const isDemo = process.env.VITE_DEMO_MODE === 'true';

export default defineConfig({
  base: process.env.VITE_BASE_PATH || '/',
  publicDir: isDemo ? 'demo-assets' : 'public',
  define: {
    __DEMO_MODE__: JSON.stringify(isDemo),
  },
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': { target: process.env.DRAWING_API_URL || 'http://127.0.0.1:8000', changeOrigin: true },
      '/comfy': { target: process.env.DRAWING_API_URL || 'http://127.0.0.1:8000', changeOrigin: true, ws: true },
    },
  },
});
