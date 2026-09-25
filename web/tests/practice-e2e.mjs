// Opt-in browser acceptance test against `python -m ari.demo` (synthetic French `cases/dev`).
// Requires Playwright and Chromium; never uses a provider or private case database.
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import {readFile} from "node:fs/promises";
const {chromium} = createRequire(import.meta.url)("playwright");
const base = process.env.ARI_E2E_URL || "http://127.0.0.1:8010";
if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(base)) throw new Error("Local disposable demo only");
const browser = await chromium.launch({headless: true,
  ...(process.env.ARI_E2E_CHROME ? {executablePath: process.env.ARI_E2E_CHROME} : {})});
const errors = [];
const password = "correct horse battery";

async function activationToken(page, email) {
  const logPath = process.env.ARI_E2E_EMAIL_LOG;
  assert.ok(logPath, "ARI_E2E_EMAIL_LOG must point at the demo email log");
  const deadline = Date.now() + 10000;
  while (Date.now() < deadline) {
    const log = await readFile(logPath, "utf8").catch(() => "");
    const start = log.lastIndexOf(`à ${email} :`);
    if (start !== -1) {
      const match = log.slice(start).match(/#jeton=([A-Za-z0-9_-]{1,128})/);
      if (match) return match[1];
    }
    await page.waitForTimeout(100);
  }
  throw new Error(`No activation link found in ${logPath} for ${email}`);
}

async function createAccount(page) {
  const email = `e2e-${crypto.randomUUID()}@example.test`;
  await page.goto(base + "/connexion#inscription");
  await page.locator("#signup-form").getByLabel("Adresse e-mail", {exact:true}).fill(email);
  await page.getByRole("button", {name:"Recevoir mon lien", exact:true}).click();
  await page.getByText(/Si cette adresse peut recevoir un lien/).waitFor();
  const token = await activationToken(page, email);
  await page.goto(`${base}/connexion#jeton=${token}`);
  await page.locator("#password-form").waitFor();
  await page.locator("#password-form input[name=password]").fill(password);
  await page.locator("#password-form input[name=confirm]").fill(password);
  await page.getByRole("button", {name:"Enregistrer et continuer", exact:true}).click();
  await page.locator("#onboarding:not([hidden])").waitFor();
}

async function onboard(page, official = false) {
  await page.locator("#profile-form").getByLabel("Land", {exact:true}).selectOption("Bayern");
  await page.getByRole("button", {name:"Continuer →", exact:true}).click();
  await page.getByLabel("Spécialité visée").fill("Médecine interne");
  if (official) {
    await page.getByRole("radio", {name:"Officiel / certifié", exact:true}).check();
    await page.getByLabel("Niveau du certificat").selectOption("B2");
    await page.getByLabel("Organisme").selectOption("goethe");
    assert.match(await page.locator("#proof-status").innerText(), /aucun document n’est déposé ni vérifié/);
  }
  await page.getByRole("button", {name:"Créer mon profil et pratiquer", exact:true}).click();
  // A new profile without an estimate lands on the placement test; the learner may skip it.
  await page.locator("#placement:not([hidden])").waitFor();
  assert.match(await page.locator("#placement-status").innerText(), /Niveau de départ/);
  await page.goto(base + "/app#home");
  await page.locator("#home:not([hidden])").waitFor();
}
try {
  const context = await browser.newContext();
  const page = await context.newPage();
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(base + "/app");
  await createAccount(page);
  await onboard(page);
  assert.match(await page.locator("#profile-goal").innerText(), /niveau non mesuré/);
  // The weekly programme is computed from the profile: a declared B2 without estimate is "anamnese".
  assert.match(await page.locator("#week-phase").innerText(), /Anamnèse · niveau B2/);
  assert.equal(await page.locator("#week-days .week-pill").count(), 7, "the week strip has seven days");
  assert.match(await page.locator("#recommendation h1").innerText(), /./, "the hero names the next step");
  await page.goto(base + "/app#cases");
  // The catalogue opens on the learner's Land (Bayern, from onboarding) and counts its cases.
  await page.getByText(/Bayern : 1 cas publié/).waitFor();
  assert.equal(await page.locator("#case-land").inputValue(), "Bayern");
  await page.getByRole("radio", {name: /Examen/}).check();
  // Server commits the start but the browser loses its response: retry must not duplicate it.
  await page.route("**/api/practice/runs", async route => {
    await route.fetch(); await route.abort("failed");
  }, {times: 1});
  await page.locator("#catalog article").filter({hasText: "Fachbegriffe"}).getByRole("button", {name: "Commencer en Examen →"}).click();
  await page.locator("#error:visible").waitFor();
  await page.getByRole("button", {name: "Réessayer", exact: true}).click();
  await page.getByRole("heading", {name: "Que signifie « oral » en termes simples ?"}).waitFor();
  assert.equal(await page.locator("#coaching").isVisible(), false);
  assert.equal(await page.locator("#training-feedback").isVisible(), false);
  await page.getByLabel("Votre réponse en allemand").fill("Par la bouche.");
  await page.route("**/api/practice/runs/*/answers", async route => {
    await route.fetch(); await route.abort("failed");
  }, {times: 1});
  await page.getByRole("button", {name: "Envoyer ma réponse"}).click();
  await page.locator("#error:visible").waitFor();
  await page.getByRole("button", {name: "Réessayer", exact: true}).click();
  await page.getByRole("heading", {name: "Que signifie « bilatéral » en termes simples ?"}).waitFor();
  assert.equal(await page.locator("#training-feedback").isVisible(), false);
  await page.getByRole("button", {name: "Mettre en pause"}).click();
  await page.getByRole("button", {name: "Reprendre", exact: true}).waitFor();
  await page.reload();
  await page.getByRole("button", {name: "Reprendre", exact: true}).click();
  await page.getByLabel("Votre réponse en allemand").fill("Des deux côtés.");
  await page.getByRole("button", {name: "Envoyer ma réponse"}).click();
  await page.getByText("Toutes les réponses sont enregistrées.", {exact: false}).waitFor();
  await page.getByRole("button", {name: "Terminer et voir le feedback"}).click();
  await page.getByRole("heading", {name: "Votre feedback · Évalué selon cette méthode"}).waitFor();
  assert.match(await page.locator("#feedback").innerText(), /Lexique : 5.00 \/ 5/);
  assert.match(await page.locator("#feedback").innerText(), /Par la bouche/);
  await page.getByRole("button", {name: "Voir ma progression"}).click();
  await page.locator("#progress:not([hidden])").waitFor();
  assert.match(await page.locator("#progress-list").innerText(), /Lexique : 5.00/);
  await page.getByRole("button", {name: "Historique", exact: true}).click();
  await page.locator("#history:not([hidden])").waitFor();
  assert.match(await page.locator("#history-list").innerText(), /exam/);
  assert.equal(await page.locator("#history-list article").count(), 1);
  await page.getByRole("button", {name: "Voir le feedback", exact: true}).click();
  await page.getByRole("heading", {name: "Votre feedback · Évalué selon cette méthode"}).waitFor();
  await page.getByRole("button", {name: "Choisir une autre session"}).click();
  await page.getByRole("radio", {name: /Training/}).check();
  await page.locator("#catalog article").filter({hasText: "Arzt–Arzt"}).getByRole("button", {name: "Préparer cette session →"}).click();
  await page.getByRole("button", {name:"Commencer la session →", exact:true}).click();
  await page.locator("#coaching:visible").waitFor();
  assert.match(await page.locator("#exercise-context").innerText(), /Camille Martin/);
  await page.getByLabel("Votre réponse en allemand").fill("Camille Martin, 52 ans, consulte pour une douleur thoracique.");
  await page.getByRole("button", {name: "Envoyer ma réponse"}).click();
  await page.locator("#training-feedback:visible").waitFor();
  await page.getByLabel("Votre réponse en allemand").fill("Aucune allergie connue.");
  await page.getByRole("button", {name: "Envoyer ma réponse"}).click();
  await page.getByText("Toutes les réponses sont enregistrées.", {exact: false}).waitFor();
  await page.getByRole("button", {name: "Terminer et voir le feedback"}).click();
  await page.getByRole("heading", {name: "Votre feedback · Évalué selon cette méthode"}).waitFor();
  assert.match(await page.locator("#feedback").innerText(), /Présentation structurée : 5.00/);
  // A second browser context cannot access the saved run or the first profile's history.
  const privateRunUrl = page.url();
  const stranger = await browser.newContext();
  const other = await stranger.newPage();
  await createAccount(other);
  await other.getByRole("heading", {name:"Quel est votre objectif ?",exact:true}).waitFor();
  await onboard(other, true);
  await other.goto(privateRunUrl);
  await other.locator("#error:visible").waitFor();
  await other.getByRole("button", {name: "Historique", exact: true}).click();
  await other.getByText("Pas encore de session enregistrée.", {exact: false}).waitFor();
  // Mobile layout has no page-level horizontal overflow.
  await page.setViewportSize({width: 390, height: 844});
  await page.goto(base + "/app#cases");
  await page.locator("#cases:not([hidden])").waitFor();
  assert.equal(await page.locator("#voice-cases article").count(), 1);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  assert.deepEqual(errors, []);
  await page.screenshot({path: process.env.ARI_E2E_SCREENSHOT || "/tmp/ari-mvp-mobile.png", fullPage: true});
  console.log("Browser E2E passed: onboarding, both phases, modes, resume, feedback, history, progression, isolation, mobile.");
} finally { await browser.close(); }
