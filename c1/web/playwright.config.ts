import { defineConfig, devices } from '@playwright/test';

// The smoke test starts the C1 API (SQLite + in-process MongoDB mock, seeded) and the Vite dev
// server, then drives the pages as the owner and as the administrator.
const API_PORT = 8000;
const WEB_PORT = 5173;

export default defineConfig({
  testDir: './tests',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: `cd ../api && DATABASE_URL=sqlite+aiosqlite:///./e2e.db MONGO_URL=mock SEED_ON_START=1 OVERVIEW_LOCAL_FALLBACK=1 python3 -m uvicorn app.main:app --port ${API_PORT}`,
      url: `http://localhost:${API_PORT}/api/v1/health`,
      reuseExistingServer: true,
      timeout: 120_000,
    },
    {
      command: `npx vite --port ${WEB_PORT} --strictPort`,
      url: `http://localhost:${WEB_PORT}`,
      reuseExistingServer: true,
      timeout: 120_000,
    },
  ],
});
