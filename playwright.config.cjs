const {defineConfig} = require('@playwright/test');

module.exports = defineConfig({
  testDir: './tests/browser',
  workers: 1,
  retries: 0,
  forbidOnly: !!process.env.CI,
  use: {baseURL: 'http://127.0.0.1:8771', headless: true},
  webServer: {
    command: 'uv run --no-sync python -m tests.browser_server',
    url: 'http://127.0.0.1:8771',
    reuseExistingServer: false,
    timeout: 30000,
  },
});
