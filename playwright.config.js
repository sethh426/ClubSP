const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "./tests",
  testMatch: ["browser.spec.js", "communications.browser.spec.js", "knowledge.browser.spec.js", "gmail.browser.spec.js", "opportunities.browser.spec.js"],
  workers: 1,
  timeout: 45000,
  use: { baseURL: "http://127.0.0.1:8765", trace: "retain-on-failure" },
  projects: [
    { name: "desktop", use: { browserName: "chromium", viewport: { width: 1440, height: 1000 } } },
    { name: "mobile", use: { browserName: "chromium", viewport: { width: 390, height: 844 } } },
  ],
  webServer: {
    command: "python -m tests.browser_server --port 8765 --db data/browser-test.db",
    url: "http://127.0.0.1:8765/api/health",
    reuseExistingServer: false,
    timeout: 30000,
  },
});
