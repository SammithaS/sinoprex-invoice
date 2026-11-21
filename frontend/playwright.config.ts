import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests against a throwaway stack (see backend/scripts/e2e_server.py):
 * the real FastAPI + LangGraph pipeline on a temp SQLite database with recorded LLM outputs,
 * and the production Next.js build. Run with `pnpm e2e` (builds first).
 *
 * Browser: the installed Microsoft Edge (`channel: "msedge"`), so no Playwright browser
 * download is needed. Set E2E_BROWSER_CHANNEL=chrome (or "" for bundled Chromium) to change it.
 * On Windows, UV_PROJECT_ENVIRONMENT is passed through to uv (keep the venv path short).
 */
const API_PORT = 8020;
const WEB_PORT = 3020;
const channel = process.env.E2E_BROWSER_CHANNEL ?? "msedge";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1, // the samples depend on run order and share one database
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 20_000 },
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    ...(channel ? { channel } : {}),
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Edge"], ...(channel ? { channel } : {}) } },
  ],
  webServer: [
    {
      command: `python -m uv run --directory ../backend uvicorn scripts.e2e_server:app --port ${API_PORT}`,
      url: `http://localhost:${API_PORT}/health`,
      timeout: 180_000,
      reuseExistingServer: false,
      env: {
        PYTHONIOENCODING: "utf-8",
        HF_HUB_DISABLE_SYMLINKS_WARNING: "1",
        ...(process.env.UV_PROJECT_ENVIRONMENT
          ? { UV_PROJECT_ENVIRONMENT: process.env.UV_PROJECT_ENVIRONMENT }
          : {}),
      },
    },
    {
      command: `pnpm start --port ${WEB_PORT}`,
      url: `http://localhost:${WEB_PORT}/dashboard`,
      timeout: 120_000,
      reuseExistingServer: false,
      env: { API_URL: `http://localhost:${API_PORT}`, API_KEY: "" },
    },
  ],
});
