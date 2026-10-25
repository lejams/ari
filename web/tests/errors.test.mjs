import assert from "node:assert/strict";
import {NETWORK_MESSAGE, TIMEOUT_MESSAGE, clearFieldErrorOnInput, fieldErrorMessage, guardedFetch, microphoneErrorMessage, showFieldError, validateFields} from "../errors.mjs";

// Network failures become French; an HTTP answer or another error passes through untouched.
await assert.rejects(() => guardedFetch(async () => { throw new TypeError("Failed to fetch"); }, "/x"), {message: NETWORK_MESSAGE});
await assert.rejects(() => guardedFetch(async () => { throw new DOMException("timed out", "TimeoutError"); }, "/x"), {message: TIMEOUT_MESSAGE});
await assert.rejects(() => guardedFetch(async () => { throw new DOMException("aborted", "AbortError"); }, "/x"), {name: "AbortError"});
const answer = {ok: false, status: 503};
assert.equal(await guardedFetch(async () => answer, "/x"), answer);

// Each getUserMedia failure says what to do, and names the button to press again.
const denied = microphoneErrorMessage(new DOMException("Permission denied", "NotAllowedError"), "Commencer la consultation");
assert.match(denied, /micro est bloqué.*Autorisez-le.*« Commencer la consultation »/);
assert.doesNotMatch(denied, /Permission denied/);
assert.match(microphoneErrorMessage({name: "NotFoundError"}), /Aucun micro détecté.*réessayez\.$/);
assert.match(microphoneErrorMessage({name: "NotReadableError"}, "Enregistrer"), /déjà utilisé.*« Enregistrer »/);
assert.match(microphoneErrorMessage(new Error("weird")), /n’a pas pu être ouvert/);

// A tiny DOM: the message goes under the field (or after its label) and is announced.
class Field {
  constructor(doc, attrs) {
    Object.assign(this, {ownerDocument: doc, attributes: {}, listeners: {}, tagName: "INPUT", type: "text", value: "", minLength: -1, maxLength: -1, validationMessage: "", parentElement: null, focused: false}, attrs);
    this.validity = {};
  }
  checkValidity() { return !Object.values(this.validity).some(Boolean); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  insertAdjacentElement(_, element) { this.ownerDocument.nodes.set(element.id, element); element.after = this; }
  addEventListener(type, handler) { (this.listeners[type] ??= []).push(handler); }
  focus() { this.focused = true; }
}
const doc = {nodes: new Map(), getElementById(id) { return this.nodes.get(id) ?? null; }, createElement: () => ({remove() { doc.nodes.delete(this.id); }})};
const land = new Field(doc, {id: "draft-land", tagName: "SELECT"});
land.validity.valueMissing = true;
assert.equal(fieldErrorMessage(land), "Choisissez une option dans la liste.");
const password = new Field(doc, {name: "password", form: {id: "password-form"}, minLength: 12, value: "court"});
password.parentElement = {tagName: "LABEL", insertAdjacentElement: (_, element) => { doc.nodes.set(element.id, element); element.after = "label"; }};
password.setAttribute("aria-describedby", "password-hint");
password.validity.tooShort = true;
assert.equal(fieldErrorMessage(password), "Au moins 12 caractères (actuellement 5).");
const email = new Field(doc, {id: "email", type: "email"});
email.validity.typeMismatch = true;
assert.match(fieldErrorMessage(email), /adresse e-mail valide/);
const date = new Field(doc, {id: "draft-date", type: "date"});
date.validity.badInput = true;
assert.equal(fieldErrorMessage(date), "Date incomplète ou invalide.");

assert.equal(validateFields([land, password]), false);
assert.equal(land.focused, true, "the first invalid field gets the focus");
assert.equal(password.focused, false);
assert.equal(doc.getElementById("draft-land-error").textContent, "Choisissez une option dans la liste.");
assert.equal(land.getAttribute("aria-invalid"), "true");
assert.equal(land.getAttribute("aria-describedby"), "draft-land-error");
assert.equal(doc.getElementById("password-form-password-error").after, "label", "outside the label, so out of the name");
assert.equal(password.getAttribute("aria-describedby"), "password-hint password-form-password-error");

// Fixing the value clears the message and keeps the field's own hint.
clearFieldErrorOnInput(password);
password.validity.tooShort = false;
password.listeners.input[0]();
assert.equal(doc.getElementById("password-form-password-error"), null);
assert.equal(password.getAttribute("aria-invalid"), null);
assert.equal(password.getAttribute("aria-describedby"), "password-hint");
showFieldError(land, "");
assert.equal(land.getAttribute("aria-describedby"), null);
console.log("Errors: network, microphone and field messages are French and announced");
