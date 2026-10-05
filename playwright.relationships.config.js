const base = require("./playwright.config");
module.exports = { ...base, testMatch: ["relationships.browser.spec.js"] };
