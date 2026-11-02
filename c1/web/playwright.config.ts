import { defineConfig, devices } from '@playwright/test';

// Two ways to run the end-to-end tests:
//  * default: the config starts the C1 API (SQLite + in-process MongoDB mock, seeded) and the
//    Vite dev server, and runs tests/smoke.spec.ts against http://localhost:5173;
//  * E2E_BASE_URL=http://localhost:8080 npx playwright test: against a running docker compose
//    stack (nginx on :8080). No server is started, and tests/stack.spec.ts (live map, alert
//    monitor, MailHog, model upload and deployment) runs too.
const API_PORT = 8000;
const WEB_PORT = 5173;
const external = process.env.E2E_BASE_URL;

export default defineConfig({
  testDir: './tests',
  timeout: 120_000,
  expect: { timeout: 15_000 },
  retries: 0,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: external ?? `http://localhost:${WEB_PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: external
    ? undefined
    : [
        {
          command: `cd ../api && DATABASE_URL=sqlite+aiosqlite:///./e2e.db STORAGE_DIR=./e2e-objects MONGO_URL=mock SEED_ON_START=1 OVERVIEW_LOCAL_FALLBACK=1 python3 -m uvicorn app.main:app --port ${API_PORT}`,
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
