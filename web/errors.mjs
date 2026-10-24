// Learner-facing wording for failures the browser reports in English ("Failed to fetch",
// "Permission denied"). Shared by the app, the voice page and the sign-in page.
export const NETWORK_MESSAGE = "Connexion impossible : vérifiez votre réseau.";
export const TIMEOUT_MESSAGE = "Le serveur ne répond pas pour l’instant.";

// fetch() rejects with a TypeError when offline and a TimeoutError from AbortSignal.timeout.
export async function guardedFetch(fetcher, path, options) {
  try { return await fetcher(path, options); }
  catch (error) {
    if (error?.name === "TimeoutError") throw new Error(TIMEOUT_MESSAGE, {cause: error});
    if (error instanceof TypeError) throw new Error(NETWORK_MESSAGE, {cause: error});
    throw error;
  }
}

// getUserMedia failures, named by the DOMException. `retry` names the button to press again.
export function microphoneErrorMessage(error, retry) {
  const again = retry ? `, puis cliquez de nouveau sur « ${retry} »` : ", puis réessayez";
  switch (error?.name) {
    case "NotAllowedError":
    case "SecurityError":
      return `Le micro est bloqué pour ARI. Autorisez-le dans le navigateur (icône à gauche de l’adresse, ou réglages du site)${again}.`;
    case "NotFoundError":
    case "OverconstrainedError":
      return `Aucun micro détecté. Branchez ou activez un micro${again}.`;
    case "NotReadableError":
    case "AbortError":
      return `Le micro est déjà utilisé par une autre application. Fermez-la${again}.`;
    default:
      return `Le micro n’a pas pu être ouvert${again}.`;
  }
}

// Plain-language reason for a field the browser considers invalid.
export function fieldErrorMessage(input) {
  const v = input.validity;
  if (v.customError) return input.validationMessage; // Set by our own code, already in French.
  if (v.valueMissing) return input.tagName === "SELECT" ? "Choisissez une option dans la liste." : "Ce champ est obligatoire.";
  if (v.typeMismatch && input.type === "email") return "Saisissez une adresse e-mail valide, par exemple nom@exemple.fr.";
  if (v.tooShort) return `Au moins ${input.minLength} caractères (actuellement ${input.value.length}).`;
  if (v.tooLong) return `Au plus ${input.maxLength} caractères.`;
  if (v.badInput) return input.type === "date" ? "Date incomplète ou invalide." : "Valeur invalide.";
  if (v.rangeUnderflow || v.rangeOverflow) return "Valeur hors des limites permises.";
  return input.validationMessage ? "Valeur invalide." : "";
}

// Shows `message` under `input` (aria-describedby, aria-invalid) or clears it when empty.
// An input wrapped in its <label> gets the message after the label, so it stays out of the name.
export function showFieldError(input, message) {
  const id = input.id ? `${input.id}-error` : `${input.form?.id}-${input.name}-error`;
  let note = input.ownerDocument.getElementById(id);
  if (!note && message) {
    note = input.ownerDocument.createElement("span");
    note.id = id; note.className = "field-error";
    (input.parentElement?.tagName === "LABEL" ? input.parentElement : input).insertAdjacentElement("afterend", note);
  }
  const described = (input.getAttribute("aria-describedby") || "").split(" ").filter(token => token && token !== id);
  if (message) { note.textContent = message; described.push(id); input.setAttribute("aria-invalid", "true"); }
  else { if (note) note.remove(); input.removeAttribute("aria-invalid"); }
  if (described.length) input.setAttribute("aria-describedby", described.join(" "));
  else input.removeAttribute("aria-describedby");
}

// Checks `inputs` in order, shows each problem under its field, focuses the first one.
export function validateFields(inputs) {
  let first = null;
  for (const input of inputs) {
    const message = input.checkValidity() ? "" : fieldErrorMessage(input);
    showFieldError(input, message);
    if (message && !first) first = input;
  }
  first?.focus();
  return !first;
}

// Clears a field's message as soon as its value becomes valid.
export function clearFieldErrorOnInput(input) {
  const clear = () => { if (input.getAttribute("aria-invalid") === "true" && input.checkValidity()) showFieldError(input, ""); };
  input.addEventListener("input", clear);
  input.addEventListener("change", clear);
}
