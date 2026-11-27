import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
export default defineConfig({plugins:[react(),tailwindcss()],server:{proxy:{'/api':{target:process.env.DRAWING_API_URL||'http://127.0.0.1:8000',changeOrigin:true},'/comfy':{target:process.env.DRAWING_API_URL||'http://127.0.0.1:8000',changeOrigin:true,ws:true}}}});
