import {ProtocolReview} from './protocol-review.mjs';
import {BackofficeApi, PERSONA_LABELS, PHASE_LABELS, REVIEW_TYPE_LABELS, ROLE_LABELS, statusLabel, statusTone} from './api.mjs';

const client = new BackofficeApi();
const $ = id => document.getElementById(id);
const views = ['login', 'invitation', 'dashboard', 'documents', 'document', 'review', 'protocol', 'gold', 'gold-detail', 'registry', 'scenario', 'accounts'];
let lands = [], busy = false, queuedRoute = false;
let current = {documentId: null, goldId: null, scenario: null};
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
  if (name !== 'protocol' && protocolReview.active) protocolReview.deactivate();
  if (name !== 'document') {
    documentRequestVersion += 1;
    if (documentPollTimer !== null) { clearTimeout(documentPollTimer); documentPollTimer = null; }
  }
  for (const id of views) $(id).hidden = id !== name;
  document.body.classList.toggle('review-mode', name === 'protocol');
  const authenticated = Boolean(client.me);
  $('sidebar').hidden = !authenticated;
  $('main').classList.toggle('public', !authenticated);
  for (const element of document.querySelectorAll('[data-role]')) element.hidden = !client.has(element.dataset.role);
  for (const element of document.querySelectorAll('.sidebar nav button')) element.setAttribute('aria-current', (name === 'protocol' && element.dataset.route === '/review') || location.hash.slice(1) === element.dataset.route || (element.dataset.route !== '/' && location.hash.slice(1).startsWith(element.dataset.route)) ? 'page' : 'false');
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
$('logout').onclick = () => perform(async () => { if (protocolReview.active && !protocolReview.confirmLeave()) return; protocolReview.deactivate(); await client.logout(); location.hash = '#/login'; await route(); });
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
    if (segment.protocol) actions.append(button(['doctor_review', 'changes_requested', 'doctor_approved'].includes(segment.protocol.status) ? 'Commencer / reprendre la relecture' : `Ouvrir ${segment.protocol.id}`, () => { location.hash = `#/protocols/${segment.protocol.id}`; return route(); }, 'primary'));
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

// A single workspace owns protocol draft state and review interactions.
const protocolReview = new ProtocolReview({client, lands: () => lands, perform, notice, view});
async function showProtocol(id) { return protocolReview.show(id); }

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
  if (protocolReview.active && location.hash !== protocolReview.hash) {
    if (!protocolReview.confirmLeave()) { history.replaceState(null, '', protocolReview.hash); return; }
    protocolReview.deactivate();
  } else if (protocolReview.active && location.hash === protocolReview.hash) return;
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
