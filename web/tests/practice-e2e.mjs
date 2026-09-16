// Opt-in browser acceptance test against `python -m ari.demo` only.
// Requires Playwright and Chromium; never uses a provider or private case database.
import assert from "node:assert/strict";
import {createRequire} from "node:module";
const {chromium} = createRequire(import.meta.url)("playwright");
const base = process.env.ARI_E2E_URL || "http://127.0.0.1:8010";
if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(base)) throw new Error("Local disposable demo only");
const browser = await chromium.launch({headless: true,
  ...(process.env.ARI_E2E_CHROME ? {executablePath: process.env.ARI_E2E_CHROME} : {})});
const errors = [];
async function onboard(page, official = false) {
  await page.getByLabel("Land", {exact:true}).selectOption("Bayern");
  await page.getByRole("button", {name:"Continuer →", exact:true}).click();
  await page.getByLabel("Spécialité visée").fill("Médecine interne");
  if (official) {
    await page.getByRole("radio", {name:"Officiel / certifié", exact:true}).check();
    await page.getByLabel("Certificat obtenu").fill("Goethe-Zertifikat B2");
    assert.match(await page.locator("#proof-status").innerText(), /aucun document n’est déposé ni vérifié/);
  }
  await page.getByRole("button", {name:"Créer mon profil et pratiquer", exact:true}).click();
  // A new profile without an estimate lands on the placement test; the learner may skip it.
  await page.locator("#placement:not([hidden])").waitFor();
  assert.match(await page.locator("#placement-status").innerText(), /Niveau de départ/);
  await page.goto(base + "/#home");
  await page.locator("#home:not([hidden])").waitFor();
}
try {
  const context = await browser.newContext();
  const page = await context.newPage();
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(base);
  await onboard(page);
  assert.match(await page.locator("#profile-goal").innerText(), /niveau non mesuré/);
  await page.goto(base + "/#cases");
  await page.getByRole("radio", {name: /Examen/}).check();
  // Server commits the start but the browser loses its response: retry must not duplicate it.
  await page.route("**/api/practice/runs", async route => {
    await route.fetch(); await route.abort("failed");
  }, {times: 1});
  await page.locator("#catalog article").filter({hasText: "Expliquer deux termes"}).getByRole("button", {name: "Commencer en Examen →"}).click();
  await page.locator("#error:visible").waitFor();
  await page.getByRole("button", {name: "Réessayer", exact: true}).click();
  await page.getByRole("heading", {name: "Was bedeutet oral in einfachen Worten?"}).waitFor();
  assert.equal(await page.locator("#coaching").isVisible(), false);
  assert.equal(await page.locator("#training-feedback").isVisible(), false);
  await page.getByLabel("Votre réponse en allemand").fill("Durch den Mund.");
  await page.route("**/api/practice/runs/*/answers", async route => {
    await route.fetch(); await route.abort("failed");
  }, {times: 1});
  await page.getByRole("button", {name: "Envoyer ma réponse"}).click();
  await page.locator("#error:visible").waitFor();
  await page.getByRole("button", {name: "Réessayer", exact: true}).click();
  await page.getByRole("heading", {name: "Was bedeutet bilateral in einfachen Worten?"}).waitFor();
  assert.equal(await page.locator("#training-feedback").isVisible(), false);
  await page.getByRole("button", {name: "Mettre en pause"}).click();
  await page.getByRole("button", {name: "Reprendre", exact: true}).waitFor();
  await page.reload();
  await page.getByRole("button", {name: "Reprendre", exact: true}).click();
  await page.getByLabel("Votre réponse en allemand").fill("Beidseitig.");
  await page.getByRole("button", {name: "Envoyer ma réponse"}).click();
  await page.getByText("Toutes les réponses sont enregistrées.", {exact: false}).waitFor();
  await page.getByRole("button", {name: "Terminer et voir le feedback"}).click();
  await page.getByRole("heading", {name: "Votre feedback · Évalué selon cette méthode"}).waitFor();
  assert.match(await page.locator("#feedback").innerText(), /Lexique : 5.00 \/ 5/);
  assert.match(await page.locator("#feedback").innerText(), /Durch den Mund/);
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
  await page.locator("#catalog article").filter({hasText: "Présenter Alex Exemple"}).getByRole("button", {name: "Préparer cette session →"}).click();
  await page.getByRole("button", {name:"Commencer la session →", exact:true}).click();
  await page.locator("#coaching:visible").waitFor();
  assert.match(await page.locator("#exercise-context").innerText(), /inconnue/);
  await page.getByLabel("Votre réponse en allemand").fill("Alex Beispiel ist 40 Jahre alt und kommt für eine Kommunikationsübung.");
  await page.getByRole("button", {name: "Envoyer ma réponse"}).click();
  await page.locator("#training-feedback:visible").waitFor();
  await page.getByLabel("Votre réponse en allemand").fill("Zu Medikamenten liegen keine Angaben vor.");
  await page.getByRole("button", {name: "Envoyer ma réponse"}).click();
  await page.getByText("Toutes les réponses sont enregistrées.", {exact: false}).waitFor();
  await page.getByRole("button", {name: "Terminer et voir le feedback"}).click();
  await page.getByRole("heading", {name: "Votre feedback · Évalué selon cette méthode"}).waitFor();
  assert.match(await page.locator("#feedback").innerText(), /Présentation structurée : 5.00/);
  // A second browser context cannot access the saved run or the first profile's history.
  const privateRunUrl = page.url();
  const stranger = await browser.newContext();
  const other = await stranger.newPage();
  await other.goto(privateRunUrl);
  await other.getByRole("heading", {name:"Quel est votre objectif ?",exact:true}).waitFor();
  await onboard(other, true);
  await other.getByRole("button", {name: "Historique", exact: true}).click();
  await other.getByText("Pas encore de session enregistrée.", {exact: false}).waitFor();
  // Mobile layout has no page-level horizontal overflow.
  await page.setViewportSize({width: 390, height: 844});
  await page.goto(base + "/#cases");
  await page.locator("#cases:not([hidden])").waitFor();
  assert.equal(await page.locator("#voice-cases article").count(), 1);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  assert.deepEqual(errors, []);
  await page.screenshot({path: process.env.ARI_E2E_SCREENSHOT || "/tmp/ari-mvp-mobile.png", fullPage: true});
  console.log("Browser E2E passed: onboarding, both phases, modes, resume, feedback, history, progression, isolation, mobile.");
} finally { await browser.close(); }
