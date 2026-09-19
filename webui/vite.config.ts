import {resolve} from 'node:path';
import {defineConfig} from 'vite';

export default defineConfig({
  plugins: [],
  root: 'app',
  publicDir: '../public',
  base: '/',
  server: {
    host: '127.0.0.1',
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8081',
        ws: true,
      },
    },
  },
  build: {
    outDir: resolve(import.meta.dirname, '../otto-master/src/otto_master/web'),
    emptyOutDir: true,
    rollupOptions: {
      input: {
        index: resolve(import.meta.dirname, 'app/index.html'),
        assembly: resolve(import.meta.dirname, 'app/assembly.html'),
      },
    },
  },
});
