const base = require("./playwright.config");
module.exports = { ...base, testMatch: ["funding.browser.spec.js"] };
