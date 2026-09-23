import {BackofficeApi, PERSONA_LABELS, PHASE_LABELS, REVIEW_TYPE_LABELS, ROLE_LABELS, SECTION_LABELS, UNCERTAINTY_PATHS, blockers, statusLabel, statusTone} from './api.mjs';

const client = new BackofficeApi();
const $ = id => document.getElementById(id);
const views = ['login', 'invitation', 'dashboard', 'documents', 'document', 'review', 'protocol', 'gold', 'gold-detail', 'registry', 'scenario', 'accounts'];
let lands = [], busy = false, queuedRoute = false;
let current = {protocol: null, record: null, page: 1, textMode: false, documentId: null, goldId: null, scenario: null};
let documentPollTimer = null, documentRequestVersion = 0;

const node = (tag, text, className) => { const element = document.createElement(tag); if (text !== undefined) element.textContent = text; if (className) element.className = className; return element; };
const badge = status => node('span', statusLabel(status), `badge ${statusTone(status)}`.trim());
const formatDate = value => value ? new Date(value).toLocaleString('fr-FR') : '—';
function button(text, action, className = '') { const element = node('button', text, className); element.type = 'button'; element.addEventListener('click', () => perform(action)); return element; }
function link(text, hash) { const element = node('a', text); element.href = `#${hash}`; return element; }
function table(target, headers, rows) {
  target.replaceChildren();
  const head = node('tr'); for (const header of headers) head.append(node('th', header)); target.append(head);
  for (const cells of rows) { const row = node('tr'); for (const cell of cells) { const td = node('td'); if (cell && typeof cell === 'object' && cell.tagName) td.append(cell); else td.textContent = cell ?? '—'; row.append(td); } target.append(row); }
}
function fillLands(select) { for (const land of lands) { const option = document.createElement('option'); option.value = land; option.textContent = land; select.append(option); } }
function view(name) {
  if (name !== 'document') {
    documentRequestVersion += 1;
    if (documentPollTimer !== null) { clearTimeout(documentPollTimer); documentPollTimer = null; }
  }
  for (const id of views) $(id).hidden = id !== name;
  const authenticated = Boolean(client.me);
  $('sidebar').hidden = !authenticated;
  $('main').classList.toggle('public', !authenticated);
  for (const element of document.querySelectorAll('[data-role]')) element.hidden = !client.has(element.dataset.role);
  for (const element of document.querySelectorAll('.sidebar nav button')) element.setAttribute('aria-current', location.hash.slice(1) === element.dataset.route || (element.dataset.route !== '/' && location.hash.slice(1).startsWith(element.dataset.route)) ? 'page' : 'false');
  window.scrollTo(0, 0);
}
async function perform(action, {background = false} = {}) {
  if (busy) return;
  if (!background) { busy = true; $('main').setAttribute('aria-busy', 'true'); $('error').hidden = true; }
  try { await action(); }
  catch (error) {
    if (error.status === 401) { client.me = null; location.hash = '#/login'; }
    $('error-text').textContent = error.message; $('error').hidden = false;
  }
  finally {
    if (!background) {
      busy = false; $('main').setAttribute('aria-busy', 'false');
      if (queuedRoute) { queuedRoute = false; perform(route); }
    }
  }
}
function notice(text) { $('notice').textContent = text; setTimeout(() => { if ($('notice').textContent === text) $('notice').textContent = ''; }, 6000); }

// ----- auth -------------------------------------------------------------------------------------
async function showLogin() { view('login'); }
async function showInvitation(token) {
  const who = await client.invitation(token);
  $('invitation-who').textContent = `${who.display_name} · ${who.email}`;
  $('invitation-form').dataset.token = token;
  view('invitation');
}
$('login-form').onsubmit = event => { event.preventDefault(); perform(async () => { await client.login($('login-email').value.trim(), $('login-password').value); $('login-password').value = ''; await afterLogin(); }); };
$('invitation-form').onsubmit = event => { event.preventDefault(); perform(async () => {
  const password = $('invitation-password').value;
  if (password.length < 12) throw new Error('Le mot de passe doit faire au moins 12 caractères.');
  if (password !== $('invitation-confirm').value) throw new Error('Les deux saisies diffèrent.');
  await client.acceptInvitation($('invitation-form').dataset.token, password);
  $('invitation-password').value = ''; $('invitation-confirm').value = '';
  await afterLogin();
}); };
async function afterLogin() { $('me-name').textContent = client.me.display_name; $('me-roles').textContent = client.me.roles.map(role => ROLE_LABELS[role] || role).join(', '); await loadLands(); location.hash = '#/'; await route(); }
$('logout').onclick = () => perform(async () => { await client.logout(); location.hash = '#/login'; await route(); });
$('error-close').onclick = () => { $('error').hidden = true; };
async function loadLands() {
  if (lands.length) return;
  lands = await client.lands();
  for (const id of ['upload-land', 'review-land', 'gold-land']) fillLands($(id));
}

// ----- dashboard ---------------------------------------------------------------------------------
async function showDashboard() {
  const data = await client.dashboard();
  const tiles = [['Documents', Object.values(data.documents_by_status).reduce((a, b) => a + b, 0)], ['À relire (médecin)', data.review_queue], ['À valider (propriétaire)', data.owner_queue], ['Protocoles gold', data.gold_total], ['Tâches en file', (data.jobs_by_status.queued || 0) + (data.jobs_by_status.failed || 0)], ['Tâches abandonnées', data.jobs_by_status.dead || 0]];
  $('dashboard-tiles').replaceChildren(...tiles.map(([label, value]) => { const tile = node('div', undefined, 'tile'); tile.append(node('strong', String(value)), node('span', label)); return tile; }));
  table($('dashboard-protocols'), ['Statut', 'Nombre'], Object.entries(data.protocols_by_status).map(([status, count]) => [badge(status), String(count)]));
  table($('dashboard-gold'), ['Land', 'Gold'], Object.entries(data.gold_by_land).sort().map(([land, count]) => [land, String(count)]));
  table($('dashboard-jobs'), ['Statut', 'Nombre'], Object.entries(data.jobs_by_status).map(([status, count]) => [badge(status), String(count)]));
  const worker = data.worker || {status: 'unavailable'};
  $('dashboard-worker').textContent = ({online: 'Worker opérationnel', offline: 'Worker indisponible', unavailable: 'État du worker non configuré'})[worker.status] || 'État du worker inconnu';
  view('dashboard');
}

// ----- documents -----------------------------------------------------------------------------------
async function showDocuments() {
  const data = await client.documents($('documents-status').value);
  $('documents-list').replaceChildren(...data.items.map(document => {
    const card = node('article');
    const left = node('div'); left.append(node('h3', document.filename), node('p', `${document.declaration.land || 'Land inconnu'} · ${document.page_count ?? '?'} pages · déposé le ${formatDate(document.created_at)} · droits ${document.declaration.rights}`, 'help'));
    const right = node('div', undefined, 'row'); right.append(badge(document.status), button('Ouvrir', () => { location.hash = `#/documents/${document.id}`; return route(); }));
    card.append(left, right); return card;
  }));
  $('documents-empty').hidden = data.items.length > 0;
  view('documents');
}
$('documents-status').onchange = () => perform(showDocuments);
$('upload-form').onsubmit = event => { event.preventDefault(); perform(async () => {
  const file = $('upload-file').files[0];
  if (!file) throw new Error('Choisissez un fichier PDF.');
  if (!$('upload-provenance').value.trim() || !$('upload-consent').value.trim()) throw new Error('Provenance et consentement sont obligatoires.');
  const result = await client.upload(file, {
    land: $('upload-land').value, city: $('upload-city').value.trim(), exam_body: $('upload-exam-body').value.trim(), exam_date: $('upload-exam-date').value.trim(), specialty: $('upload-specialty').value.trim(),
    rights: $('upload-rights').value, rights_evidence: $('upload-rights-evidence').value.trim(), provenance: $('upload-provenance').value.trim(), consent_declaration: $('upload-consent').value.trim(),
  });
  notice(result.duplicate ? 'Ce fichier avait déjà été déposé : aucune nouvelle extraction.' : 'Document déposé ; l’extraction démarre en arrière-plan.');
  $('upload-form').reset();
  location.hash = `#/documents/${result.document.id}`; await route();
}); };
function renderDocumentProgress(progress) {
  const stages = [['uploaded', 'Déposé'], ['text_extraction', 'Texte'], ['segmentation', 'Découpage'], ['protocol_extraction', 'Protocoles'], ['ready', 'Terminé']];
  const active = stages.findIndex(([stage]) => stage === progress.stage);
  const failed = stages.findIndex(([stage]) => stage === progress.failed_stage);
  $('document-progress-label').textContent = progress.label;
  $('document-progress-count').textContent = progress.total === null ? 'En cours…' : `${progress.completed}/${progress.total}`;
  $('document-progress-stages').replaceChildren(...stages.map(([stage, label], index) => {
    const state = progress.stage === 'failed' ? (index === Math.max(failed, 0) ? 'failed' : '') : index < active ? 'done' : index === active ? 'current' : '';
    return node('li', label, state);
  }));
  $('document-progress-bar').style.width = progress.percent === null ? '' : `${progress.percent}%`;
  $('document-progress-bar').parentElement?.classList.toggle('indeterminate', progress.percent === null && progress.stage !== 'failed');
  $('document-progress-error').textContent = progress.error ? `Erreur : ${progress.error}` : '';
}
function scheduleDocumentPoll(id, progress) {
  if (documentPollTimer !== null) { clearTimeout(documentPollTimer); documentPollTimer = null; }
  if (progress.stage === 'ready' || progress.stage === 'failed') return;
  documentPollTimer = setTimeout(() => {
    documentPollTimer = null;
    if (current.documentId === id && location.hash === `#/documents/${id}`) {
      if (busy) { scheduleDocumentPoll(id, progress); return; }
      perform(() => showDocument(id), {background: true});
    }
  }, 2000);
}
async function showDocument(id) {
  if (documentPollTimer !== null) { clearTimeout(documentPollTimer); documentPollTimer = null; }
  const requestVersion = ++documentRequestVersion;
  current.documentId = id;
  const data = await client.document(id);
  if (requestVersion !== documentRequestVersion || location.hash !== `#/documents/${id}`) return;
  const document_ = data.document, declaration = document_.declaration;
  $('document-title').textContent = document_.filename;
  $('document-meta').textContent = `${document_.page_count ?? '?'} pages · ${statusLabel(document_.status)} · empreinte ${document_.id.slice(0, 12)}…`;
  const dl = $('document-declaration'); dl.replaceChildren();
  for (const [label, value] of [['Land', declaration.land], ['Ville', declaration.city], ['Ärztekammer', declaration.exam_body], ['Mois', declaration.exam_date], ['Spécialité', declaration.specialty], ['Droits', declaration.rights], ['Preuve', declaration.rights_evidence], ['Provenance', declaration.provenance], ['Consentement', declaration.consent_declaration], ['Usage prévu', declaration.intended_use]]) { dl.append(node('dt', label), node('dd', value || '—')); }
  $('document-original').href = client.originalUrl(id);
  table($('document-jobs'), ['Statut', 'Tâches'], Object.entries(data.jobs).filter(([, count]) => count).map(([status, count]) => [badge(status), String(count)]));
  $('document-error').textContent = data.last_error ? `Dernière erreur : ${data.last_error}` : '';
  renderDocumentProgress(data.progress);
  $('document-segments-count').textContent = `${data.segments.length} segment(s)`;
  table($('document-segments'), ['#', 'Pages', 'Début', 'Confiance', 'Statut', 'Protocole', ''], data.segments.map(segment => {
    const actions = node('div', undefined, 'row wrap');
    if (segment.protocol) actions.append(link(`${segment.protocol.id} · ${statusLabel(segment.protocol.status)}`, `/protocols/${segment.protocol.id}`));
    else if (client.has('owner')) {
      if (segment.status !== 'discarded') actions.append(button('Écarter', () => client.setSegmentStatus(segment.id, 'discarded').then(() => showDocument(id)), 'danger'));
      if (segment.status !== 'pending') actions.append(button('Extraire', () => client.setSegmentStatus(segment.id, 'pending').then(() => showDocument(id))));
      if (['pending', 'failed'].includes(segment.status) && ['failed', 'dead'].includes(segment.extraction_job?.status)) actions.append(button('Réessayer', () => client.retrySegmentExtraction(segment.id).then(() => showDocument(id))));
    }
    return [String(segment.index), `${segment.page_from}–${segment.page_to}`, segment.start_marker.slice(0, 60), `${Math.round(segment.confidence * 100)} % ${segment.origin === 'manual' ? '(manuel)' : ''}`, badge(segment.status), actions, ''];
  }));
  view('document');
  scheduleDocumentPoll(id, data.progress);
}
$('document-release').onclick = () => perform(async () => { const result = await client.releaseDocument(current.documentId); notice(`${result.released} protocole(s) envoyé(s) en relecture.`); await showDocument(current.documentId); });
$('document-retry-blocked').onclick = () => perform(async () => { const result = await client.retryBlockedExtractions(current.documentId); notice(`${result.requeued} tâche(s) relancée(s).`); await showDocument(current.documentId); });
$('segment-form').onsubmit = event => { event.preventDefault(); perform(async () => {
  await client.addSegment(current.documentId, {page_from: Number($('segment-from').value), page_to: Number($('segment-to').value), start_marker: $('segment-marker').value.trim()});
  $('segment-form').reset(); notice('Segment ajouté ; extraction lancée.'); await showDocument(current.documentId);
}); };

// ----- review queue --------------------------------------------------------------------------------
async function showReview() {
  const data = await client.protocols({status: $('review-status').value, land: $('review-land').value});
  $('review-list').replaceChildren(...data.items.map(item => {
    const card = node('article');
    const left = node('div'); left.append(node('h3', `${item.id} · v${item.version}`), node('p', [item.land || 'Land ?', item.specialty, item.presenting_complaint_de, `pages ${item.page_from}–${item.page_to}`].filter(Boolean).join(' · '), 'help'));
    const right = node('div', undefined, 'row wrap');
    right.append(badge(item.status), node('span', `${item.open_doctor_blockers} point(s) ouvert(s)`, `badge ${item.open_doctor_blockers ? 'warn' : 'ok'}`));
    if (item.pii_findings) right.append(node('span', `${item.pii_findings} PII`, 'badge bad'));
    right.append(button('Ouvrir', () => { location.hash = `#/protocols/${item.id}`; return route(); }, 'primary'));
    card.append(left, right); return card;
  }));
  $('review-empty').hidden = data.items.length > 0;
  view('review');
}
$('review-status').onchange = () => perform(showReview); $('review-land').onchange = () => perform(showReview);

// ----- protocol: source pane -----------------------------------------------------------------------
async function showPage(number) {
  const protocol = current.protocol;
  current.page = Math.min(Math.max(number, protocol.page_from), protocol.page_to);
  $('page-label').textContent = `Page ${current.page} / ${protocol.page_from}–${protocol.page_to}`;
  $('page-prev').disabled = current.page <= protocol.page_from; $('page-next').disabled = current.page >= protocol.page_to;
  $('page-image').hidden = current.textMode; $('page-text').hidden = !current.textMode;
  if (current.textMode) $('page-text').textContent = (await client.pageText(protocol.document_id, current.page)).text;
  else $('page-image').src = client.pageImageUrl(protocol.document_id, current.page);
}
$('page-prev').onclick = () => perform(() => showPage(current.page - 1));
$('page-next').onclick = () => perform(() => showPage(current.page + 1));
$('page-toggle').onclick = () => perform(async () => { current.textMode = !current.textMode; $('page-toggle').textContent = current.textMode ? 'Image' : 'Texte'; await showPage(current.page); });

// ----- protocol: structured editor -----------------------------------------------------------------
// Every control writes straight into `current.record`; the checklist re-renders on each change.
function field(label, get, set, options = {}) {
  const wrapper = node('div'); if (options.flag) wrapper.className = 'field-flag';
  const id = `f-${Math.random().toString(36).slice(2, 9)}`;
  const labelNode = node('label', label); labelNode.htmlFor = id; wrapper.append(labelNode);
  let input;
  if (options.choices) {
    input = document.createElement('select');
    const choices = options.nullable ? [['', '—'], ...options.choices] : options.choices;
    for (const [value, text] of choices) { const option = document.createElement('option'); option.value = value; option.textContent = text; input.append(option); }
    input.value = get() ?? '';
  } else if (options.kind === 'checkbox') { input = document.createElement('input'); input.type = 'checkbox'; input.checked = Boolean(get()); }
  else if (options.kind === 'lines' || options.kind === 'textarea') { input = document.createElement('textarea'); input.rows = options.rows || 2; input.value = options.kind === 'lines' ? (get() || []).join('\n') : (get() ?? ''); }
  else { input = document.createElement('input'); input.type = options.kind || 'text'; input.value = get() ?? ''; }
  input.id = id;
  input.addEventListener('input', () => {
    let value;
    if (options.kind === 'checkbox') value = input.checked;
    else if (options.kind === 'lines') value = input.value.split('\n').map(line => line.trim()).filter(Boolean);
    else if (options.kind === 'number') value = input.value === '' ? null : Number(input.value);
    else value = input.value === '' ? null : input.value;
    set(value); renderChecklist();
  });
  wrapper.append(input); return wrapper;
}
function section(title, ...children) { const card = node('article', undefined, 'card'); card.append(node('h2', title), ...children); return card; }
function grid(...children) { const element = node('div', undefined, 'form-grid'); element.append(...children); return element; }
function itemList(items, title, render, blank) {
  const box = node('div', undefined, 'items');
  const draw = () => {
    box.replaceChildren(...items.map((item, index) => {
      const card = node('div', undefined, 'item');
      const head = node('div', undefined, 'item-head'); head.append(node('strong', `${title} ${item.id || index + 1}`), button('Retirer', () => { items.splice(index, 1); draw(); renderChecklist(); }, 'danger'));
      card.append(head, render(item)); return card;
    }), button(`Ajouter ${title.toLowerCase()}`, () => { items.push(blank(items)); draw(); renderChecklist(); }));
  };
  draw(); return box;
}
const nextId = (items, prefix) => { let n = items.length + 1; while (items.some(item => item.id === `${prefix}${String(n).padStart(2, '0')}`)) n += 1; return `${prefix}${String(n).padStart(2, '0')}`; };
const POLARITIES = [['present', 'présent'], ['absent', 'absent'], ['unknown', 'inconnu']];
const PARTS = [['arzt_patient', 'Arzt–Patient'], ['arzt_arzt', 'Arzt–Arzt'], ['fachbegriffe', 'Fachbegriffe'], ['arztbrief', 'Arztbrief'], ['general', 'Général']];
function renderEditor() {
  const record = current.record, readOnly = !canRevise();
  const sections = $('record-sections'); sections.replaceChildren();
  sections.append(
    section('Localisation', grid(
      field('Land', () => record.location.land, v => { record.location.land = v; }, {choices: lands.map(l => [l, l]), nullable: true, flag: !record.location.land}),
      field('Ville', () => record.location.city, v => { record.location.city = v; }),
      field('Ärztekammer', () => record.location.exam_body, v => { record.location.exam_body = v; }),
      field('Mois (AAAA-MM)', () => record.location.exam_date, v => { record.location.exam_date = v; }),
      field('Spécialité', () => record.location.specialty, v => { record.location.specialty = v; }),
    )),
    section('Patient', grid(
      field('Âge (ans)', () => record.patient.age_years, v => { record.patient.age_years = v; }, {kind: 'number'}),
      field('Sexe', () => record.patient.sex, v => { record.patient.sex = v || 'unknown'; }, {choices: [['female', 'femme'], ['male', 'homme'], ['other', 'autre'], ['unknown', 'inconnu']]}),
      field('Profession (DE)', () => record.patient.occupation_de, v => { record.patient.occupation_de = v; }),
      field('Motif de consultation (DE)', () => record.patient.presenting_complaint_de, v => { record.patient.presenting_complaint_de = v; }),
    ), field('Résumé (DE)', () => record.patient.summary_de, v => { record.patient.summary_de = v; }, {kind: 'textarea', rows: 3}),
      field('Incertitude (vide = levée)', () => record.patient.uncertainty, v => { record.patient.uncertainty = v; }, {flag: Boolean(record.patient.uncertainty)})),
    section('Anamnèse', itemList(record.anamnesis, 'Élément', item => grid(
      field('Section', () => item.section, v => { item.section = v; }, {choices: Object.entries(SECTION_LABELS)}),
      field('Libellé (DE)', () => item.label_de, v => { item.label_de = v; }),
      field('Valeur (DE)', () => item.value_de, v => { item.value_de = v; }),
      field('Polarité', () => item.polarity, v => { item.polarity = v; if (v === 'unknown') item.value_de = null; }, {choices: POLARITIES}),
      field('Temporalité', () => item.temporality, v => { item.temporality = v; }),
      field('Citation (DE, pseudonymisée)', () => item.quote_de, v => { item.quote_de = v; }),
      field('Incertitude (vide = levée)', () => item.uncertainty, v => { item.uncertainty = v; }, {flag: Boolean(item.uncertainty)}),
    ), items => ({id: nextId(items, 'a'), section: 'sonstiges', label_de: '', value_de: null, polarity: 'unknown', temporality: null, quote_de: null, source_pages: [], uncertainty: null}))),
    section('Diagnostic', field('Diagnostic suspecté (DE)', () => record.diagnosis.suspected_de, v => { record.diagnosis.suspected_de = v; }),
      field('Diagnostics différentiels (un par ligne)', () => record.diagnosis.differentials_de, v => { record.diagnosis.differentials_de = v; }, {kind: 'lines'}),
      field('Incertitude (vide = levée)', () => record.diagnosis.uncertainty, v => { record.diagnosis.uncertainty = v; }, {flag: Boolean(record.diagnosis.uncertainty)})),
    section('Questions des examinateurs', itemList(record.examiner_questions, 'Question', item => grid(
      field('Partie', () => item.part, v => { item.part = v; }, {choices: PARTS}),
      field('Question (DE)', () => item.question_de, v => { item.question_de = v; }),
      field('Réponse attendue (DE)', () => item.expected_answer_de, v => { item.expected_answer_de = v; }),
      field('Réponse du candidat (DE)', () => item.candidate_answer_de, v => { item.candidate_answer_de = v; }),
      field('Incertitude (vide = levée)', () => item.uncertainty, v => { item.uncertainty = v; }, {flag: Boolean(item.uncertainty)}),
    ), items => ({id: nextId(items, 'q'), part: 'general', question_de: '', expected_answer_de: null, candidate_answer_de: null, source_pages: [], uncertainty: null}))),
    section('Arzt–Arzt', field('Présentation (DE)', () => record.arzt_arzt.presentation_summary_de, v => { record.arzt_arzt.presentation_summary_de = v; }, {kind: 'textarea', rows: 3}),
      field('Points discutés (un par ligne)', () => record.arzt_arzt.discussion_points_de, v => { record.arzt_arzt.discussion_points_de = v; }, {kind: 'lines'}),
      field('Incertitude (vide = levée)', () => record.arzt_arzt.uncertainty, v => { record.arzt_arzt.uncertainty = v; }, {flag: Boolean(record.arzt_arzt.uncertainty)})),
    section('Fachbegriffe', itemList(record.fachbegriffe, 'Terme', item => grid(
      field('Terme (DE)', () => item.german, v => { item.german = v; }),
      field('Explication courante (DE)', () => item.lay_german, v => { item.lay_german = v; }),
      field('Français', () => item.french, v => { item.french = v; }),
      field('Demandé par les examinateurs', () => item.asked, v => { item.asked = v; }, {kind: 'checkbox'}),
    ), items => ({id: nextId(items, 't'), german: '', lay_german: null, french: null, asked: true}))),
    section('Issue de l’examen', grid(field('Résultat', () => record.outcome.result, v => { record.outcome.result = v || 'unknown'; }, {choices: [['passed', 'réussi'], ['failed', 'échoué'], ['unknown', 'inconnu']]})),
      field('Retour des examinateurs (un par ligne)', () => record.outcome.examiner_feedback_de, v => { record.outcome.examiner_feedback_de = v; }, {kind: 'lines'}),
      field('Conseils du candidat (un par ligne)', () => record.outcome.candidate_tips_de, v => { record.outcome.candidate_tips_de = v; }, {kind: 'lines'}),
      field('Incertitude (vide = levée)', () => record.outcome.uncertainty, v => { record.outcome.uncertainty = v; }, {flag: Boolean(record.outcome.uncertainty)}),
      field('Notes Arztbrief (DE)', () => record.arztbrief_notes_de, v => { record.arztbrief_notes_de = v; }, {kind: 'textarea'})),
    section('Questions de l’IA au médecin', itemList(record.unresolved_questions, 'Question', item => grid(
      field('Question (FR)', () => item.text_fr, v => { item.text_fr = v; }),
      field('Réponse', () => item.answer, v => { item.answer = v; }, {flag: item.critical && !item.answer}),
      field('Critique', () => item.critical, v => { item.critical = v; }, {kind: 'checkbox'}),
    ), items => ({id: nextId(items, 'u'), text_fr: '', critical: true, answer: null}))),
    section('Incertitudes de champ', itemList(record.field_uncertainties, 'Incertitude', item => grid(
      field('Champ', () => item.path, v => { item.path = v; }, {choices: UNCERTAINTY_PATHS.map(p => [p, p])}),
      field('Raison (FR)', () => item.reason_fr, v => { item.reason_fr = v; }),
      field('Résolution (vide = ouverte)', () => item.resolution, v => { item.resolution = v; }, {flag: !item.resolution}),
    ), () => ({path: 'arzt_arzt', reason_fr: '', resolution: null}))),
    section('Pédagogie (médecin)', grid(field('Difficulté', () => record.pedagogy.difficulty, v => { record.pedagogy.difficulty = v; }, {choices: [['leicht', 'leicht'], ['mittel', 'mittel'], ['schwer', 'schwer']], nullable: true, flag: !record.pedagogy.difficulty})),
      field('Notes (FR)', () => record.pedagogy.notes_fr, v => { record.pedagogy.notes_fr = v; }, {kind: 'textarea'}),
      node('h3', 'Erreurs graves à ne pas commettre'), itemList(record.pedagogy.critical_pitfalls, 'Piège grave', item => grid(
        field('Texte (FR)', () => item.text_fr, v => { item.text_fr = v; }),
        field('Éléments liés (ids séparés par des virgules)', () => item.related_item_ids.join(', '), v => { item.related_item_ids = (v || '').split(',').map(s => s.trim()).filter(Boolean); }),
      ), () => ({text_fr: '', related_item_ids: []})),
      node('h3', 'Autres pièges'), itemList(record.pedagogy.pitfalls, 'Piège', item => grid(
        field('Texte (FR)', () => item.text_fr, v => { item.text_fr = v; }),
        field('Éléments liés (ids séparés par des virgules)', () => item.related_item_ids.join(', '), v => { item.related_item_ids = (v || '').split(',').map(s => s.trim()).filter(Boolean); }),
      ), () => ({text_fr: '', related_item_ids: []}))),
    section('Pseudonymisation (IA)', node('p', `Retiré : ${record.pseudonymisation.removed_kinds.join(', ') || 'rien'}${record.pseudonymisation.notes.length ? ' · ' + record.pseudonymisation.notes.join(' · ') : ''}`, 'help')),
  );
  for (const control of sections.querySelectorAll('input,select,textarea,button')) control.disabled = readOnly;
  renderChecklist();
}
function renderChecklist() {
  const stage = current.protocol.status === 'doctor_approved' ? 'owner' : 'doctor';
  const open = blockers(current.record, stage);
  $('checklist').replaceChildren(...open.map(text => node('li', text)));
  $('checklist-ok').hidden = open.length > 0;
}
function canRevise() { const status = current.protocol.status; return !['gold', 'rejected', 'superseded'].includes(status) && (client.has('physician_reviewer') || client.has('owner')); }
function renderDecision() {
  const protocol = current.protocol, actions = $('decision-actions'); actions.replaceChildren();
  const physician = client.has('physician_reviewer'), owner = client.has('owner');
  const doctorStage = ['doctor_review', 'changes_requested'].includes(protocol.status);
  $('pii-override-block').hidden = !(owner && protocol.status === 'doctor_approved' && protocol.pii.length);
  if (canRevise()) actions.append(button('Enregistrer une version', saveVersion, 'primary'));
  if (protocol.status === 'extracted' && owner) { $('decision-help').textContent = 'Ce protocole n’est pas encore en relecture.'; actions.append(button('Envoyer en relecture', () => client.releaseProtocol(protocol.id).then(() => showProtocol(protocol.id)))); }
  else if (doctorStage && physician) {
    $('decision-help').textContent = 'Enregistrez d’abord vos corrections, puis décidez. L’approbation exige une checklist vide.';
    actions.append(button('Approuver', () => decide('doctor', 'approve')), button('Demander des corrections', () => decide('doctor', 'request_changes')), button('Rejeter', () => decide('doctor', 'reject'), 'danger'));
  } else if (protocol.status === 'doctor_approved' && owner) {
    $('decision-help').textContent = 'Approuvé par le médecin. Valider gèle le protocole comme gold, vérité terrain des futurs cas.';
    actions.append(button('Valider en gold', () => decide('owner', 'approve'), 'primary'), button('Renvoyer au médecin', () => decide('owner', 'request_changes')), button('Rejeter', () => decide('owner', 'reject'), 'danger'));
  } else {
    const mine = client.me.roles.map(role => ROLE_LABELS[role] || role).join(', ');
    const needed = doctorStage ? 'un compte « Relecteur médecin »' : protocol.status === 'doctor_approved' ? 'un compte « Propriétaire »' : protocol.status === 'extracted' ? 'le propriétaire, qui doit d’abord l’envoyer en relecture' : 'personne : le protocole est clos';
    $('decision-help').textContent = `Statut « ${statusLabel(protocol.status)} ». La décision revient à ${needed}. Votre compte : ${mine}.`;
  }
  if (owner && !['gold', 'rejected', 'superseded'].includes(protocol.status)) {
    actions.append(button('Supprimer le protocole', deleteCurrentProtocol, 'danger'));
  }
}
async function deleteCurrentProtocol() {
  const protocol = current.protocol;
  if (!window.confirm('Supprimer ce protocole ? Il sera retiré du circuit de relecture et conservé dans l’historique.')) return;
  await client.deleteProtocol(protocol.id);
  notice('Protocole supprimé du circuit de relecture.');
  location.hash = `#/documents/${protocol.document_id}`;
  await route();
}
async function saveVersion() {
  const protocol = current.protocol;
  const revised = await client.revise(protocol.id, protocol.version, protocol.content_hash, current.record);
  notice(`Version ${revised.version} enregistrée.`); await showProtocol(protocol.id, revised);
}
async function decide(stage, decision) {
  const protocol = current.protocol, notes = $('decision-notes').value.trim();
  if (decision !== 'approve' && !notes) throw new Error('Expliquez la décision dans les notes.');
  if (JSON.stringify(current.record) !== JSON.stringify(protocol.record)) throw new Error('Des modifications ne sont pas enregistrées : cliquez d’abord sur « Enregistrer une version ».');
  const body = {version: protocol.version, protocol_hash: protocol.content_hash, decision, notes};
  if (stage === 'owner' && $('pii-override').value.trim()) body.pii_override_note = $('pii-override').value.trim();
  const moved = await client.decide(protocol.id, stage, body);
  $('decision-notes').value = ''; notice(`Décision enregistrée : ${statusLabel(moved.status)}.`);
  await showProtocol(protocol.id, moved);
}
async function showProtocol(id, preloaded = null) {
  const protocol = preloaded || await client.protocol(id);
  current.protocol = protocol; current.record = JSON.parse(JSON.stringify(protocol.record));
  $('protocol-eyebrow').textContent = `Protocole · version ${protocol.version}`;
  $('protocol-title').textContent = `${protocol.id}`;
  $('protocol-meta').textContent = [statusLabel(protocol.status), protocol.land || 'Land ?', protocol.specialty, `pages ${protocol.page_from}–${protocol.page_to}`, protocol.document?.filename].filter(Boolean).join(' · ');
  $('pii-card').hidden = protocol.pii.length === 0;
  table($('pii-table'), ['Type', 'Champ', 'Extrait masqué'], protocol.pii.map(f => [f.kind, f.path, f.excerpt]));
  table($('protocol-history'), ['Quand', 'Événement', 'Par', 'Détail'], protocol.events.map(e => [formatDate(e.created_at), e.event_type, e.actor.name, Object.entries(e.payload).filter(([k]) => k !== 'actor_name').map(([k, v]) => `${k}=${v}`).join(' ')]).concat(protocol.reviews.map(r => [formatDate(r.reviewed_at), `revue ${r.stage} · ${r.decision}`, r.reviewer_name, r.notes])));
  table($('protocol-diff'), ['Champ', 'Avant', 'Après'], protocol.diff_to_previous.map(c => [c.path, JSON.stringify(c.before), JSON.stringify(c.after)]));
  renderEditor(); renderDecision();
  view('protocol');
  current.page = protocol.page_from; await showPage(protocol.page_from);
}

// ----- gold and accounts -----------------------------------------------------------------------------
async function showGold() {
  const data = await client.gold($('gold-land').value);
  table($('gold-table'), ['Protocole', 'Land', 'Ville', 'Spécialité', 'Mois', 'Motif', 'Difficulté', 'Figé le'], data.items.map(g => [link(g.protocol_id, `/gold/${g.protocol_id}`), g.land, g.city, g.specialty, g.exam_date, g.presenting_complaint_de, g.difficulty, formatDate(g.frozen_at)]));
  $('gold-empty').hidden = data.items.length > 0;
  view('gold');
}
$('gold-land').onchange = () => perform(showGold);

// ----- gold detail: bundle drafts -----------------------------------------------------------------------
const describeRequest = request => request ? `${(request.phases || []).map(p => PHASE_LABELS[p] || p).join(' + ')} · patient ${PERSONA_LABELS[request.persona_variant] || request.persona_variant} · ${request.cefr} · révision ${request.revision}` : '—';
async function showGoldDetail(id) {
  current.goldId = id;
  const [gold, drafts] = await Promise.all([client.goldDetail(id), client.bundleDrafts(id)]);
  const record = gold.record;
  $('gold-title').textContent = gold.protocol_id;
  $('gold-meta').textContent = [gold.location.land, gold.location.city, gold.location.specialty, gold.location.exam_date, `version ${gold.protocol_version}`, `figé le ${formatDate(gold.frozen_at)}`].filter(Boolean).join(' · ');
  const dl = $('gold-summary'); dl.replaceChildren();
  for (const [label, value] of [['Motif', record.patient.presenting_complaint_de], ['Patient', [record.patient.age_years ? `${record.patient.age_years} ans` : null, {female: 'femme', male: 'homme'}[record.patient.sex]].filter(Boolean).join(', ')], ['Diagnostic suspecté', record.diagnosis.suspected_de], ['Éléments d’anamnèse', String(record.anamnesis.length)], ['Fachbegriffe demandés', String(record.fachbegriffe.filter(t => t.asked).length)], ['Difficulté', record.pedagogy.difficulty], ['Pièges graves', record.pedagogy.critical_pitfalls.map(p => p.text_fr).join(' · ')], ['Droits', gold.rights], ['Hash gold', gold.protocol_hash.slice(0, 16) + '…']]) dl.append(node('dt', label), node('dd', value || '—'));
  $('gold-protocol-link').href = `#/protocols/${gold.protocol_id}`;
  for (const input of document.querySelectorAll('[name=phase]')) input.disabled = !drafts.available_phases.includes(input.value);
  $('draft-revision').value = String(drafts.next_revision);
  $('draft-jobs-count').textContent = drafts.jobs.length ? `${drafts.jobs.length} en attente ou en échec` : 'aucune';
  table($('draft-jobs'), ['Demandé le', 'Requête', 'Statut', 'Tentatives', 'Dernière erreur'], drafts.jobs.map(job => [formatDate(job.created_at), describeRequest(job.request), badge(job.status), String(job.attempts), job.last_error]));
  table($('draft-table'), ['Créé le', 'Requête', 'Statut', 'Cas', 'Scénarios', 'Détail', ''], drafts.items.map(draft => {
    const scenarios = node('div', undefined, 'row wrap');
    for (const ref of draft.scenario_refs) scenarios.append(draft.status === 'imported' ? link(PHASE_LABELS[ref.phase] || ref.phase, `/registry/${ref.id}/${ref.version}`) : node('span', PHASE_LABELS[ref.phase] || ref.phase));
    const actions = node('div', undefined, 'row wrap');
    if (draft.status === 'draft' && client.has('owner')) actions.append(button('Importer dans le registre', () => client.importBundleDraft(draft.id).then(() => { notice('Brouillon importé : les scénarios attendent leurs deux revues.'); return showGoldDetail(id); }), 'primary'));
    return [formatDate(draft.created_at), describeRequest(draft.request), badge(draft.status), draft.case_id ? `${draft.case_id}@${draft.case_version}` : '—', scenarios, draft.validation_errors.length ? draft.validation_errors.join(' · ') : (draft.imported_at ? `importé le ${formatDate(draft.imported_at)}` : '—'), actions];
  }));
  $('draft-empty').hidden = drafts.items.length > 0;
  view('gold-detail');
}
$('draft-form').onsubmit = event => { event.preventDefault(); perform(async () => {
  const phases = [...document.querySelectorAll('[name=phase]:checked')].map(input => input.value);
  if (!phases.length) throw new Error('Choisissez au moins une phase.');
  const revision = Number($('draft-revision').value || 1);
  const job = await client.requestBundleDraft(current.goldId, {phases, persona_variant: $('draft-persona').value, cefr: $('draft-cefr').value, revision});
  notice(`Génération en file (tâche ${job.id.slice(0, 8)}…). Rechargez la page dans quelques instants.`);
  await showGoldDetail(current.goldId);
}); };

// ----- registry -------------------------------------------------------------------------------------------
async function showRegistry() {
  const data = await client.registryScenarios($('registry-status').value);
  table($('registry-table'), ['Cas', 'Phase', 'Version', 'Land', 'Titre', 'Statut', ''], data.items.map(s => [s.case_id, PHASE_LABELS[s.phase] || s.phase, s.version, s.land, s.title, badge(s.status), button('Ouvrir', () => { location.hash = `#/registry/${s.id}/${s.version}`; return route(); })]));
  $('registry-empty').hidden = data.items.length > 0;
  view('registry');
}
$('registry-status').onchange = () => perform(showRegistry);
function reviewTypesFor() { return [['clinical', 'physician_reviewer'], ['linguistic', 'linguistic_reviewer']].filter(([, role]) => client.has(role)).map(([type]) => type); }
async function showScenario(id, version) {
  const report = await client.registryScenario(id, version);
  current.scenario = {id, version, report};
  const bundle = report.bundle, scenario = bundle.scenarios[0], case_ = bundle.cases[0];
  $('scenario-eyebrow').textContent = `Registre · ${PHASE_LABELS[scenario.phase] || scenario.phase} · version ${version}`;
  $('scenario-title').textContent = case_.title;
  $('scenario-meta').textContent = [statusLabel(report.status), case_.location.land || 'Land ?', `${case_.facts.length} faits`, `cas ${case_.id}@${case_.version}`, `hash ${report.scenario_hash.slice(0, 12)}…`].filter(Boolean).join(' · ');
  $('scenario-blockers').replaceChildren(...report.blockers.map(text => node('li', text)));
  $('scenario-ok').hidden = report.blockers.length > 0;
  table($('scenario-reviews'), ['Quand', 'Type', 'Décision', 'Par', 'Notes'], report.reviews.map(r => [formatDate(r.reviewed_at), REVIEW_TYPE_LABELS[r.review_type] || r.review_type, r.decision, r.reviewer_name, r.notes]));
  const types = reviewTypesFor(), select = $('review-type'); select.replaceChildren();
  for (const type of types) { const option = document.createElement('option'); option.value = type; option.textContent = `Revue ${REVIEW_TYPE_LABELS[type]}`; select.append(option); }
  const reviewable = report.status === 'draft_unvalidated' && types.length > 0;
  $('review-form').hidden = !reviewable;
  $('review-help').textContent = report.status !== 'draft_unvalidated' ? 'Un scénario publié ou retiré ne se relit plus : une correction passe par une nouvelle version.' : types.length ? 'Votre revue porte sur le contenu exact affiché (hash). Une approbation clinique et une approbation linguistique, par deux comptes distincts, débloquent la publication.' : 'Les revues reviennent aux comptes « Relecteur médecin » (clinique) et « Relecteur linguistique » (langue).';
  const actions = $('scenario-actions'); actions.replaceChildren();
  if (client.has('owner')) {
    if (report.status === 'draft_unvalidated') { const publish = button('Publier aux apprenants', () => client.registryPublish(id, version).then(() => { notice('Scénario publié.'); return showScenario(id, version); }), 'primary'); publish.disabled = report.blockers.length > 0; actions.append(publish); }
    if (report.status === 'published') actions.append(button('Retirer', () => client.registryWithdraw(id, version).then(() => { notice('Scénario retiré.'); return showScenario(id, version); }), 'danger'));
    $('scenario-help').textContent = report.status === 'draft_unvalidated' ? 'Le bouton s’active quand la liste est vide.' : '';
  } else $('scenario-help').textContent = 'La publication revient au propriétaire.';
  $('scenario-bundle').textContent = JSON.stringify(bundle, null, 2);
  view('scenario');
}
$('review-form').onsubmit = event => { event.preventDefault(); perform(async () => {
  const notes = $('review-notes').value.trim();
  if (!notes) throw new Error('Expliquez votre décision dans les notes.');
  const {id, version} = current.scenario;
  await client.registryReview(id, version, {review_type: $('review-type').value, decision: $('review-decision').value, notes});
  $('review-notes').value = ''; notice('Revue enregistrée.');
  await showScenario(id, version);
}); };
async function showAccounts() {
  const data = await client.accounts();
  table($('accounts-table'), ['Nom', 'E-mail', 'Rôles', 'État', ''], data.items.map(account => {
    const actions = node('div', undefined, 'row wrap');
    if (account.active) actions.append(button('Nouveau lien', () => client.reinvite(account.id).then(showInvitationLink)), button('Désactiver', () => client.deactivate(account.id).then(showAccounts), 'danger'));
    return [account.display_name, account.email, account.roles.map(r => ROLE_LABELS[r] || r).join(', '), account.active ? (account.has_password ? 'Actif' : 'Invité, sans mot de passe') : 'Désactivé', actions];
  }));
  view('accounts');
}
function showInvitationLink(result) { $('invitation-link').textContent = `Lien d’invitation pour ${result.account.email} (valable jusqu’au ${formatDate(result.expires_at)}), à transmettre par un canal sûr : ${result.invitation_url}`; $('invitation-link').hidden = false; }
$('account-form').onsubmit = event => { event.preventDefault(); perform(async () => {
  const roles = [...document.querySelectorAll('[name=role]:checked')].map(input => input.value);
  if (!roles.length) throw new Error('Choisissez au moins un rôle.');
  const result = await client.createAccount({email: $('account-email').value.trim(), display_name: $('account-name').value.trim(), roles});
  $('account-form').reset(); showInvitationLink(result); await showAccounts();
}); };

// ----- routing -----------------------------------------------------------------------------------------
async function route() {
  const path = location.hash.slice(1) || '/';
  const invitation = path.match(/^\/invitation\/([A-Za-z0-9_-]+)$/);
  if (invitation) return showInvitation(invitation[1]);
  if (!client.me) return showLogin();
  const protocol = path.match(/^\/protocols\/([A-Za-z0-9_.-]+)$/);
  const document_ = path.match(/^\/documents\/([a-f0-9]{64})$/);
  const gold = path.match(/^\/gold\/([A-Za-z0-9_.-]+)$/);
  const scenario = path.match(/^\/registry\/([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)$/);
  if (protocol) return showProtocol(protocol[1]);
  if (document_) return showDocument(document_[1]);
  if (gold) return showGoldDetail(gold[1]);
  if (scenario) return showScenario(scenario[1], scenario[2]);
  const routes = {'/': showDashboard, '/documents': showDocuments, '/review': showReview, '/gold': showGold, '/registry': showRegistry, '/accounts': showAccounts, '/login': showDashboard};
  return (routes[path] || showDashboard)();
}
for (const element of document.querySelectorAll('[data-route]')) element.onclick = () => { location.hash = `#${element.dataset.route}`; };
window.addEventListener('hashchange', () => {
  documentRequestVersion += 1;
  if (busy) { queuedRoute = true; return; }
  perform(route);
});
perform(async () => { await client.restore(); if (client.me) { $('me-name').textContent = client.me.display_name; $('me-roles').textContent = client.me.roles.map(role => ROLE_LABELS[role] || role).join(', '); await loadLands(); } await route(); });
