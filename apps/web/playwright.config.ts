import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  use: { baseURL: 'http://127.0.0.1:4173' },
  webServer: [
    {
      command: 'python -m uvicorn roghyar.main:app --app-dir ../api --host 127.0.0.1 --port 8000 --no-access-log',
      url: 'http://127.0.0.1:8000/api/v1/health/live',
    },
    {
      command: 'npm run preview -- --host 127.0.0.1 --strictPort',
      url: 'http://127.0.0.1:4173',
    },
  ],
});
