import { defineConfig } from 'vite';
import { VitePWA } from 'vite-plugin-pwa';

const proxy = {
  '/api/v1': process.env.ROGHYAR_API_ORIGIN ?? 'http://127.0.0.1:8000',
};

export default defineConfig({
  server: { proxy },
  preview: { proxy },
  plugins: [
    VitePWA({
      registerType: 'autoUpdate',
      manifest: {
        name: 'ROGHYAR AI',
        short_name: 'ROGHYAR AI',
        start_url: '/',
        display: 'standalone',
        theme_color: '#16463d',
        background_color: '#f5f8f7',
        icons: [
          { src: '/icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: '/icon-512.png', sizes: '512x512', type: 'image/png' },
        ],
      },
      workbox: {
        navigateFallbackDenylist: [/^\/api(?:\/|$)/],
        globPatterns: ['**/*.{js,css,html,png}'],
      },
    }),
  ],
});
