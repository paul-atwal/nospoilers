import path from 'path';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig(() => {
  const devApiProxy = process.env.VITE_DEV_API_PROXY;
  return {
      server: {
        port: 3000,
        host: '0.0.0.0',
        ...(devApiProxy ? { proxy: { '/api': { target: devApiProxy, changeOrigin: false } } } : {}),
      },
      plugins: [react()],
      test: {
        environment: 'jsdom',
      },
      resolve: {
        alias: {
          '@': path.resolve(__dirname, '.'),
        }
      }
  };
});
