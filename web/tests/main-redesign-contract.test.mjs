import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const [html, ui] = await Promise.all([
  readFile(new URL("../index.html", import.meta.url), "utf8"),
  readFile(new URL("../practice-ui.mjs", import.meta.url), "utf8"),
]);

assert.match(html, /01 \/ 02/);
assert.match(html, /FSP/);
assert.match(html, /Kenntnisprüfung/);
assert.match(html, /Prise de poste/);
assert.match(html, /Consultation de positionnement indisponible/);
assert.match(html, /Dépôt indisponible/);
assert.match(html, /21 jours/);
assert.match(html, /class="bottomnav"/);
assert.match(ui, /localStorage\.getItem/);
assert.match(ui, /\/voice\.html\?case=.*mode=/);
assert.match(ui, /ended_at/);
assert.match(ui, /ArrowLeft/);
assert.match(ui, /Home:0/);
assert.match(ui, /item\.answered > 0/);
assert.doesNotMatch(ui, /answered_count/);
console.log("Main redesign contract: local onboarding, honest availability, calendar evidence and voice routes passed");
