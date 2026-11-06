// Cross-browser pass (Chromium here, WebKit in run-webkit.py): the same in-page measure on every page,
// nine formats, light and dark. See README.md. Needs a running `python -m ari.demo` (learner side) or
// back-office (TARGET=bo) and a seeded account (seed-e2e-accounts.py).
import {createRequire} from "node:module";
import {readFile, writeFile, mkdir} from "node:fs/promises";
const require = createRequire(import.meta.url);
const here = new URL(".", import.meta.url).pathname;
const {chromium} = require("playwright");
const axeSrc = await readFile(require.resolve("axe-core/axe.min.js"), "utf8");
const measure = await readFile(here + "measure.js", "utf8");
const perf = await readFile(here + "scrollperf.js", "utf8");
const base = process.env.BASE || "http://127.0.0.1:8010";
const accounts = JSON.parse(await readFile(process.env.ARI_QA_ACCOUNT_FILE || "accounts.json", "utf8"));
const only = process.env.ONLY ? process.env.ONLY.split(",") : null;
const shotDir = process.env.SHOTS || "qa-shots/chromium";
const outFile = process.env.OUT || "qa-chromium.json";
await mkdir(shotDir, {recursive: true});
export const VPS = [
  ["d1920", 1920, 1080, false], ["d1440", 1440, 900, false], ["d1280", 1280, 720, false],
  ["t1024", 1024, 768, true], ["t768", 768, 1024, true],
  ["m430", 430, 932, true], ["m390", 390, 844, true], ["m360", 360, 740, true], ["m320", 320, 568, true],
];
const BO = process.env.TARGET === "bo";
const BOPAGES = [["bo-login", "/#/login", null], ...["", "documents", "review", "registry", "exercises", "gold", "costs", "accounts"].map(r => ["bo-" + (r || "home"), "/#/" + r, "bo"])];
const PAGES = BO ? BOPAGES : [
  ["landing", "/", null], ["login", "/connexion", null],
  ...["home","programme","cases","history","vocab","progress","exercises","exam","placement","drill"].map(v => ["app-" + v, "/app#" + v, v]),
  ["voice-ready", "/voice.html?case=ARI-DEV-FR&mode=training", "voice"], ["voice-exam", "/voice.html?case=ARI-DEV-FR&mode=exam", "voice"],
];
const pageOnly = process.env.PAGES ? process.env.PAGES.split(",") : null;
if (pageOnly) PAGES.splice(0, PAGES.length, ...PAGES.filter(p => pageOnly.some(x => p[0].startsWith(x))));
const browser = await chromium.launch();
async function login(page) {
  if (BO) { await page.goto(base + "/#/login"); await page.waitForTimeout(500);
    if (await page.locator("#login-email").isVisible()) { await page.locator("#login-email").fill((process.env.ARI_QA_BO_EMAIL || "owner@example.test")); await page.locator("#login-password").fill((process.env.ARI_QA_BO_PASSWORD || "correct horse battery")); await page.locator("#login-form button.primary, #login-form button[type=submit]").first().click(); await page.waitForTimeout(1200); } return; }
  await page.goto(base + "/connexion");
  await page.locator("#login-form").getByLabel("Adresse e-mail", {exact: true}).fill(accounts[0].email);
  await page.locator("#login-form input[name=password]").fill(accounts[0].password);
  await page.getByRole("button", {name: "Se connecter", exact: true}).click();
  await page.waitForURL(/\/app/, {timeout: 8000}).catch(() => {});
  await page.waitForTimeout(600);
}
async function onboardIfNeeded(page) {
  if (BO) return;
  if (!await page.locator("#onboarding:not([hidden])").count()) return;
  await page.locator("#profile-form").getByRole("combobox", {name: "Land", exact: true}).selectOption("Bayern");
  await page.getByRole("button", {name: "Continuer →", exact: true}).click();
  await page.getByLabel("Spécialité visée").fill("Médecine interne");
  await page.getByRole("button", {name: "Créer mon profil et pratiquer", exact: true}).click();
  await page.locator("#placement:not([hidden])").waitFor();
  await page.getByRole("button", {name: "Faire le test plus tard", exact: true}).click();
  await page.locator("#home:not([hidden])").waitFor();
}
const results = [];
for (const [vn, w, h, touch] of VPS) for (const scheme of ["light", "dark"]) {
  if (only && !only.includes(vn)) continue;
  const ctx = await browser.newContext({viewport: {width: w, height: h}, isMobile: touch && w < 800, hasTouch: touch, deviceScaleFactor: touch ? 2 : 1, colorScheme: scheme, reducedMotion: "no-preference"});
  const page = await ctx.newPage(); const errs = [];
  page.on("console", m => { if (m.type() === "error") errs.push(m.text().slice(0, 140)); });
  page.on("pageerror", e => errs.push("pageerror " + e.message.slice(0, 140)));
  await login(page); await onboardIfNeeded(page);
  for (const [pn, url, view] of PAGES) {
    errs.length = 0;
    const authed = view !== null;
    if (pn === "login" || pn === "landing" || pn === "bo-login") { const c2 = await browser.newContext({viewport: {width: w, height: h}, isMobile: touch && w < 800, hasTouch: touch, deviceScaleFactor: touch ? 2 : 1, colorScheme: scheme});
      var pg = await c2.newPage(); pg.on("pageerror", e => errs.push("pageerror " + e.message.slice(0, 140))); pg.on("console", m => { if (m.type() === "error") errs.push(m.text().slice(0, 140)); }); }
    else pg = page;
    await pg.goto(base + url.split("#")[0], {waitUntil: "load"});
    if (BO) { await pg.evaluate(u => { location.hash = u.split("#")[1]; }, url); }
    else if (view && view !== "voice") { await pg.evaluate(v => { location.hash = v; }, view); }
    await pg.waitForTimeout(pn === "landing" ? 2200 : 900);
    const m = await pg.evaluate(measure);
    const p = process.env.NOPERF ? {} : await pg.evaluate(perf);
    await pg.waitForTimeout(pn === "landing" ? 4200 : 700);
    await pg.addScriptTag({content: axeSrc});
    const ax = await pg.evaluate(async () => (await window.axe.run(document, {runOnly: {type: "tag", values: ["wcag2a","wcag2aa","wcag21a","wcag21aa","best-practice"]}})).violations.map(v => v.impact + " " + v.id + " x" + v.nodes.length));
    if (!process.env.NOSHOT) await pg.screenshot({path: `${shotDir}/${vn}-${scheme}-${pn}.png`, fullPage: false});
    results.push({engine: "chromium", vp: vn, scheme, page: pn, m, perf: p, axe: ax, errs: [...errs]});
    if (pn === "login" || pn === "landing" || pn === "bo-login") await pg.context().close();
  }
  await ctx.close();
  await writeFile(outFile, JSON.stringify(results));
  console.error("done", vn, scheme);
}
await writeFile(outFile, JSON.stringify(results));
await browser.close();
