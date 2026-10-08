const base=require('./playwright.config');
const {randomUUID}=require('node:crypto');
module.exports={...base,testMatch:['*.browser.spec.js','browser.spec.js'],
webServer:{...base.webServer,command:'python -m tests.browser_server --port 8765 --db data/validation-'+randomUUID()+'.db'}};
