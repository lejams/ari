import {PracticeClient, phaseLabel, stateLabel, dimensionText} from './practice-client.mjs';
import {LexiconClient, RATINGS, lexiconStateLabel, ratingLabel, sourceLabel} from './lexicon-client.mjs';
import {PlacementClient, LEVEL_LABELS, PHASE_LABELS, describeResult} from './placement-client.mjs';
import {PcmRecorder} from './pcm-recorder.mjs';
import {completedDayKeys, lastLocalDays, localDayKey, scrollDelta} from './calendar.mjs';

const client = new PracticeClient();
const lexicon = new LexiconClient((path, options) => client.api(path, options));
const placement = new PlacementClient((path, options) => client.api(path, options));
let reviewQueue = [], reviewTotal = 0, reviewEntry = null;
let placementAttempt = null, placementListens = 0, recorder = null, recordedAudio = null, recordTimer = null, providerMode = 'fake';
const $ = id => document.getElementById(id);
const views = ['onboarding', 'home', 'warmup', 'cases', 'exam', 'vocab', 'exercise', 'history', 'progress', 'placement'];
const fields = ['goal', 'land', 'date', 'minutes', 'situation', 'specialty', 'level', 'certificate-level', 'certificate-issuer', 'certificate-date'];
// What the onboarding form sends to the profile; land, goal and dates are server-side now.
function profileDetails() {
  const source = document.querySelector('[name=source]:checked').value;
  return {
    declared_level: source === 'official' ? $('draft-certificate-level').value : $('draft-level').value,
    level_source: source === 'official' ? 'certificate' : 'self',
    certificate_issuer: source === 'official' ? $('draft-certificate-issuer').value : null,
    certificate_level: source === 'official' ? $('draft-certificate-level').value : null,
    certificate_date: source === 'official' ? $('draft-certificate-date').value || null : null,
    exam_date: $('draft-date').value || null,
    minutes_per_day: Number($('draft-minutes').value) || 30,
    land: $('draft-land').value || null,
    situation: $('draft-situation').value,
    specialty: $('draft-specialty').value.trim() || null,
  };
}
function fillFromProfile(details) {
  if (!details) return;
  if (details.declared_level) $('draft-level').value = details.declared_level;
  document.querySelector(`[name=source][value=${details.level_source === 'certificate' ? 'official' : 'self'}]`).checked = true;
  if (details.certificate_level) $('draft-certificate-level').value = details.certificate_level;
  if (details.certificate_issuer) $('draft-certificate-issuer').value = details.certificate_issuer;
  $('draft-certificate-date').value = details.certificate_date || '';
  $('draft-date').value = details.exam_date || '';
  if (details.minutes_per_day) $('draft-minutes').value = String(details.minutes_per_day);
  if (details.land) $('draft-land').value = details.land;
  if (details.situation) $('draft-situation').value = details.situation;
  $('draft-specialty').value = details.specialty || '';
}
let currentRun = null, retryAction = null, busy = false, selected = null;
let catalog = [], voiceCases = [], historyItems = [];
const node = (tag, text, className) => {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
};
function actionButton(text, action, primary = false) {
  const element = node('button', text, primary ? 'primary' : '');
  element.type = 'button';
  element.addEventListener('click', () => perform(action));
  return element;
}
const formatDate = value => new Date(value).toLocaleString('fr-FR');
const localKey = () => `ari:onboarding:v3:${client.profile?.id || 'anonymous'}`;
function readDraft() {
  try {
    const value = JSON.parse(localStorage.getItem(localKey()) || '{}');
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch { return {}; }
}
function writeDraft() {
  const draft = {step: $('person-step').hidden ? 1 : 2, target: $('target').value};
  for (const field of fields) draft[field] = $('draft-' + field).value;
  draft.source = document.querySelector('[name=source]:checked').value;
  try { localStorage.setItem(localKey(), JSON.stringify(draft)); }
  catch { $('notice').textContent = 'La sauvegarde locale est indisponible dans ce navigateur.'; }
  return draft;
}
function sourceState() {
  const official = document.querySelector('[name=source]:checked').value === 'official';
  $('self-note').hidden = official;
  $('official-note').hidden = !official;
  $('declared-level-block').hidden = official;
  $('date-label').textContent = $('draft-goal').value === 'job' ? 'Date prévue de prise de poste' : 'Date prévue de l’examen';
}
function onboardingStep(step, focus = false) {
  $('project-step').hidden = step === 2;
  $('person-step').hidden = step !== 2;
  $('onboarding-step').textContent = step === 2 ? '02 / 02 · Votre profil' : '01 / 02 · Votre objectif';
  $('onboarding-title').textContent = step === 2 ? 'Parlez-nous de vous' : 'Quel est votre objectif ?';
  if (focus) { $('onboarding-title').tabIndex = -1; $('onboarding-title').focus(); }
}
function restoreDraft() {
  $('profile-form').reset();
  const draft = readDraft();
  for (const field of fields) {
    const input = $('draft-' + field);
    if (typeof draft[field] !== 'string') continue;
    if (input.tagName === 'SELECT' && !Array.from(input.options).some(option => option.value === draft[field])) continue;
    input.value = draft[field];
  }
  if (['self', 'official'].includes(draft.source)) document.querySelector(`[name=source][value=${draft.source}]`).checked = true;
  fillFromProfile(client.profile?.details);
  $('target').value = client.profile?.goal.target_cefr || (['B2', 'C1'].includes(draft.target) ? draft.target : 'C1');
  $('save-profile').textContent = client.profile ? 'Enregistrer mes préférences' : 'Créer mon profil et pratiquer';
  onboardingStep(draft.step === 2 ? 2 : 1);
  sourceState();
}
function view(name, changeHash = true) {
  for (const id of views) $(id).hidden = id !== name;
  document.querySelectorAll('[data-view]').forEach(button => button.setAttribute('aria-current', button.dataset.view === name ? 'page' : 'false'));
  if (name !== 'exercise') document.body.classList.remove('exam-active');
  if (changeHash && !['onboarding', 'exercise'].includes(name)) history.replaceState(null, '', '#' + name);
  window.scrollTo({top: 0, behavior: 'instant'});
}
async function perform(action) {
  if (busy) return;
  busy = true; retryAction = action; $('error').hidden = true; $('notice').textContent = 'Chargement…';
  $('main').setAttribute('aria-busy', 'true');
  try { await action(); $('notice').textContent = ''; }
  catch (error) { $('notice').textContent = ''; $('error-text').textContent = `${error.message} Vous pouvez réessayer ; aucune réussite n’est supposée.`; $('error').hidden = false; }
  finally { busy = false; $('main').setAttribute('aria-busy', 'false'); }
}
async function loadCatalog() {
  const [exercises, cases] = await Promise.all([client.api('/api/exercises'), client.api('/api/cases?approved_only=true')]);
  catalog = exercises.items; voiceCases = cases;
}
function prepare(item, kind, mode = 'training') {
  selected = {item, kind, mode};
  if (mode === 'exam') return startSelected();
  $('warmup-title').textContent = item.title;
  $('warmup-copy').textContent = kind === 'voice' ? 'Une courte préparation avant votre consultation en allemand.' : `${phaseLabel(item.phase)} · ${item.duration_minutes} minutes indicatives.`;
  view('warmup');
}
async function startSelected() {
  if (!selected) { await showCases(); return; }
  const {item, kind, mode} = selected;
  if (kind === 'voice') {
    location.href = `/voice.html?case=${encodeURIComponent(item.id)}&mode=${mode}`;
  } else await showRun(await client.start(item, mode));
}
function caseCard(item, kind, mode) {
  const card = node('article', undefined, 'card case-card');
  const synthetic = item.provenance === 'synthetic_demo';
  card.append(node('span', synthetic ? 'Démo synthétique · non validée' : kind === 'voice' ? 'Consultation vocale disponible' : 'Exercice publié', 'badge'));
  const title = node('h2', item.title); title.lang = 'de'; card.append(title);
  card.append(node('p', item.public_summary || item.summary || '', 'muted'));
  if (item.limitation) card.append(node('p', item.limitation, 'help'));
  card.append(actionButton(mode === 'exam' ? 'Commencer en Examen →' : 'Préparer cette session →', () => prepare(item, kind, mode)));
  return card;
}
function renderCatalog() {
  const query = $('case-search').value.trim().toLocaleLowerCase('fr-FR');
  const filter = $('case-filter').value;
  const mode = document.querySelector('[name=mode]:checked').value;
  $('voice-cases').replaceChildren(); $('catalog').replaceChildren();
  const matches = item => [item.title, item.public_summary, item.summary].filter(Boolean).join(' ').toLocaleLowerCase('fr-FR').includes(query);
  for (const item of voiceCases) if (['all', 'voice'].includes(filter) && matches(item)) $('voice-cases').append(caseCard(item, 'voice', mode));
  for (const item of catalog) if ((filter === 'all' || filter === item.phase) && matches(item)) $('catalog').append(caseCard(item, 'practice', mode));
  $('catalog-empty').hidden = Boolean($('catalog').children.length + $('voice-cases').children.length);
}
function renderCalendar() {
  const days = lastLocalDays(), practiced = completedDayKeys(historyItems), track = $('daytrack');
  let selectedIndex = days.length - 1;
  const describe = index => {
    const day = days[index], count = historyItems.filter(item => item.status === 'completed' && item.answered > 0 && item.ended_at && localDayKey(item.ended_at) === localDayKey(day)).length;
    return `${day.toLocaleDateString('fr-FR', {weekday:'long', day:'numeric', month:'long', year:'numeric'})} · ${count ? `${count} session${count > 1 ? 's' : ''} terminée${count > 1 ? 's' : ''} avec réponse.` : 'Aucune pratique terminée enregistrée.'}`;
  };
  const summary = index => { $('day-summary').textContent = describe(index); };
  const controls = () => {
    $('cal-prev').disabled = track.scrollLeft <= 1;
    $('cal-next').disabled = track.scrollLeft >= track.scrollWidth - track.clientWidth - 2;
  };
  track.replaceChildren(...days.map((day, index) => {
    const done = practiced.has(localDayKey(day)), today = index === days.length - 1;
    const button = node('button', undefined, `day${done ? ' done' : ''}${today ? ' today' : ''}`);
    button.type = 'button';
    button.setAttribute('aria-label', describe(index) + (today ? ' Aujourd’hui.' : ''));
    button.setAttribute('aria-pressed', String(today)); button.tabIndex = today ? 0 : -1;
    button.append(node('span', day.toLocaleDateString('fr-FR',{weekday:'short'}),'weekday'), node('span', String(day.getDate()),'date'), node('span',day.toLocaleDateString('fr-FR',{month:'short'}),'month'), node('span',done ? '✓' : '—','mark'));
    button.addEventListener('click', () => {
      selectedIndex = index;
      Array.from(track.children).forEach((child, i) => { child.setAttribute('aria-pressed', String(i === index)); child.tabIndex = i === index ? 0 : -1; });
      summary(index);
    });
    button.addEventListener('mouseenter', () => summary(index));
    button.addEventListener('focus', () => summary(index));
    button.addEventListener('keydown', event => {
      const next = {ArrowLeft:index-1, ArrowRight:index+1, Home:0, End:days.length-1}[event.key];
      if (next === undefined) return;
      event.preventDefault(); const target = track.children[Math.max(0, Math.min(days.length-1,next))];
      target.click(); target.focus(); target.scrollIntoView({block:'nearest',inline:'nearest'});
    });
    return button;
  }));
  track.onmouseleave = () => summary(selectedIndex);
  track.onfocusout = event => { if (!track.contains(event.relatedTarget)) summary(selectedIndex); };
  track.onwheel = event => { const delta = scrollDelta(event, track); if (delta) { event.preventDefault(); track.scrollLeft += delta; } };
  track.onscroll = controls;
  $('cal-prev').onclick = () => track.scrollBy({left:-300,behavior:'smooth'});
  $('cal-next').onclick = () => track.scrollBy({left:300,behavior:'smooth'});
  summary(selectedIndex);
  requestAnimationFrame(() => { track.scrollLeft = track.scrollWidth; controls(); });
}

// ----- weekly programme -----------------------------------------------------------------
const SLOT_LABELS = {lexicon_review: 'Réviser mon carnet', lexicon_maintenance: 'Entretien du carnet', fachbegriffe: 'Fachbegriffe', arzt_arzt: 'Arzt–Arzt', voice_training: 'Consultation · Training', voice_exam: 'Consultation · Examen', placement: 'Test de niveau'};
const SLOT_STATES = {done: '✓', today: '○', todo: '·', missed: '—'};
const PHASE_LABELS_FR = {positionnement: 'Positionnement', prerequis: 'Prérequis', fondations: 'Fondations', anamnese: 'Anamnèse', examen: 'Examen'};
function slotTitle(slot) {
  const rec = slot.recommended;
  return rec ? `${SLOT_LABELS[slot.kind]} · ${rec.title}` : SLOT_LABELS[slot.kind] || slot.kind;
}
function slotButtonLabel(slot) {
  return {lexicon_review: 'Réviser →', lexicon_maintenance: 'Entretenir →', placement: 'Passer le test →', voice_training: 'Préparer la consultation →', voice_exam: 'Commencer en Examen →', fachbegriffe: 'Faire l’exercice →', arzt_arzt: 'Faire l’exercice →'}[slot.kind] || 'Commencer →';
}
async function runSlot(slot) {
  const rec = slot.recommended;
  if (slot.kind === 'lexicon_review' || slot.kind === 'lexicon_maintenance') return showVocab();
  if (slot.kind === 'placement') return showPlacement();
  if (rec?.kind === 'voice') {
    await loadCatalog();
    const item = voiceCases.find(c => c.id === rec.id && c.version === rec.version) || rec;
    return prepare(item, 'voice', rec.mode);
  }
  if (rec?.kind === 'practice') {
    await loadCatalog();
    const item = catalog.find(c => c.scenario_id === rec.scenario_id && c.scenario_version === rec.scenario_version) || rec;
    return prepare(item, 'practice', 'training');
  }
  return showCases();
}
const DAY_LETTERS = ['L', 'M', 'M', 'J', 'V', 'S', 'D'];
let weekProgram = null, weekSelectedOffset = 0;
function renderHero(program) {
  const week = program.week, model = program.model, next = week.next;
  const today = week.slots.filter(slot => slot.state === 'today' || (slot.state === 'done' && slot.date === week.today));
  const minutesToday = today.reduce((total, slot) => total + slot.minutes, 0);
  const context = [`${PHASE_LABELS_FR[week.phase] || week.phase}`, `niveau ${model.level.reference || 'non estimé'}`, minutesToday ? `${minutesToday} min prévues aujourd’hui` : 'rien de prévu aujourd’hui'].join(' · ');
  $('recommendation').replaceChildren(node('p', 'Votre prochaine étape', 'eyebrow'));
  if (next) {
    $('recommendation').append(node('h1', slotTitle(next)), node('p', context, 'hero-context'));
    $('recommendation').append(actionButton(slotButtonLabel(next), () => runSlot(next), true));
    const why = node('details', undefined, 'hero-why'); why.append(node('summary', 'Pourquoi cette proposition ?'), node('p', next.rationale));
    $('recommendation').append(why);
  } else {
    $('recommendation').append(node('h1', 'Semaine accomplie.'), node('p', context, 'hero-context'), node('p', 'Tous les créneaux prévus sont faits. Le programme se recalcule lundi, ou dès que votre carnet ou votre profil change.'));
    $('recommendation').append(actionButton('Explorer la bibliothèque →', showCases));
  }
}
function renderWeekDay(offset) {
  weekSelectedOffset = offset;
  const week = weekProgram.week;
  Array.from($('week-days').children).forEach((pill, index) => pill.setAttribute('aria-pressed', String(index === offset)));
  const day = new Date(week.week_start + 'T00:00:00'); day.setDate(day.getDate() + offset);
  const slots = week.slots.filter(slot => slot.day_offset === offset);
  const detail = $('week-day-detail');
  detail.replaceChildren(node('p', day.toLocaleDateString('fr-FR', {weekday: 'long', day: 'numeric', month: 'long'}), 'eyebrow'));
  if (!slots.length) { detail.append(node('p', 'Rien de prévu ce jour-là.', 'help')); return; }
  for (const slot of slots) {
    const row = node('div', undefined, 'slot-row'); row.dataset.state = slot.state;
    row.append(node('span', SLOT_STATES[slot.state] || '·', 'slot-state'), node('strong', SLOT_LABELS[slot.kind] || slot.kind), node('span', `${slot.minutes} min${slot.recommended?.title ? ` · ${slot.recommended.title}` : ''}`, 'slot-note'));
    if (slot.state !== 'done') row.append(actionButton(slotButtonLabel(slot), () => runSlot(slot)));
    detail.append(row);
  }
}
function renderWeek(program) {
  weekProgram = program;
  const week = program.week, model = program.model;
  $('week-phase').textContent = `${PHASE_LABELS_FR[week.phase] || week.phase} · niveau ${model.level.reference || 'non estimé'}${model.exam.weeks_left !== null ? ` · examen dans ${model.exam.weeks_left} semaine${model.exam.weeks_left > 1 ? 's' : ''}` : ''}`;
  $('week-budget').textContent = `${week.planned_minutes} / ${week.budget_minutes} min`;
  $('week-message').textContent = week.message;
  $('week-limitations').textContent = week.limitations;
  const todayOffset = Math.max(0, Math.round((new Date(week.today + 'T00:00:00') - new Date(week.week_start + 'T00:00:00')) / 86400000));
  $('week-days').replaceChildren(...DAY_LETTERS.map((letter, offset) => {
    const slots = week.slots.filter(slot => slot.day_offset === offset);
    const state = !slots.length ? 'rest' : slots.every(slot => slot.state === 'done') ? 'done' : slots.some(slot => slot.state === 'missed') ? 'missed' : offset === todayOffset ? 'today' : 'todo';
    const pill = node('button', undefined, 'week-pill'); pill.type = 'button'; pill.dataset.state = state; pill.setAttribute('role', 'listitem');
    pill.append(node('span', letter, 'week-pill-day'), node('span', slots.length ? String(slots.length) : '·', 'week-pill-count'));
    pill.title = slots.map(slot => SLOT_LABELS[slot.kind]).join(', ') || 'repos';
    pill.addEventListener('click', () => renderWeekDay(offset));
    return pill;
  }));
  renderWeekDay(todayOffset);
}
async function home() {
  if (!client.profile) { restoreDraft(); view('onboarding', false); return; }
  await loadCatalog(); historyItems = (await client.api('/api/history')).items;
  const details = client.profile.details || {};
  const estimated = details.estimated_level ? `niveau estimé ${details.estimated_level} le ${new Date(details.estimated_at).toLocaleDateString('fr-FR')}` : 'niveau non mesuré';
  $('profile-goal').textContent = `Objectif personnel ${client.profile.goal.target_cefr} · ${estimated}.`;
  $('goal-target').value = client.profile.goal.target_cefr;
  $('local-profile-summary').textContent = [details.land, details.minutes_per_day ? `${details.minutes_per_day} min/jour` : '', details.declared_level ? `niveau ${details.declared_level} ${details.level_source === 'certificate' ? 'certifié (déclaré)' : 'auto-évalué'}` : '', details.exam_date ? `examen le ${new Date(details.exam_date).toLocaleDateString('fr-FR')}` : ''].filter(Boolean).join(' · ') || 'Renseignez vos préférences pour calibrer votre pratique.';
  $('placement-cta').textContent = details.estimated_level ? 'Refaire le test de niveau' : 'Passer le test de niveau';
  const program = await client.api('/api/program');
  renderHero(program);
  renderWeek(program);
  view('home');
}
async function showCases() { await loadCatalog(); renderCatalog(); view('cases'); }
async function showExam() {
  await loadCatalog();
  $('exam-cases').replaceChildren(...voiceCases.map(item => caseCard(item,'voice','exam')), ...catalog.map(item => caseCard(item,'practice','exam')));
  if (!$('exam-cases').children.length) $('exam-cases').append(node('p','Aucun scénario approuvé ou exercice disponible pour le mode Examen.','availability'));
  view('exam');
}
function feedbackItem(item) {
  const card = node('article',undefined,'card'); card.append(node('h3',item.expected_behavior),node('p',`${stateLabel(item.state)} · poids ${item.weight}`));
  if (item.evidence) {
    const quote = node('blockquote',item.evidence.submitted_text); quote.lang = 'de'; card.append(quote,node('p',`Preuve : question ${item.evidence.question_id}, réponse ${item.evidence.event_id}`,'help'));
  }
  if (item.state === 'not_matched') card.append(node('p','Cette formulation n’a pas été reconnue par comparaison textuelle ; cela ne signifie pas qu’elle est médicalement ou linguistiquement fausse.','help'));
  for (const variant of item.accepted_answers || []) { const quote = node('blockquote',variant); quote.lang='de'; card.append(quote); }
  card.append(node('p',item.coaching_fr,'muted')); return card;
}
async function showRun(run) {
  currentRun = run; history.replaceState(null,'',`#practice/${encodeURIComponent(run.id)}`);
  const examActive = run.mode === 'exam' && run.status !== 'completed';
  document.body.classList.toggle('exam-active', examActive);
  $('exercise-title').textContent = run.content.title;
  $('exercise-meta').textContent = `${phaseLabel(run.content.phase)} · ${run.mode === 'exam' ? 'Examen' : 'Training'} · ${stateLabel(run.status)}`;
  $('exercise-warning').textContent = (run.content.provenance === 'synthetic_demo' ? 'Démo synthétique non validée. ' : '') + run.content.limitation;
  $('exercise-context').hidden = !run.context.length; $('exercise-context').replaceChildren();
  for (const fact of run.context) { const line=node('p',fact.text_de); line.lang='de'; $('exercise-context').append(line); if(fact.polarity==='unknown') $('exercise-context').append(node('p','Information inconnue : ne pas déduire une absence.','help')); }
  $('answer-history').replaceChildren(); $('answer-history').hidden=examActive;
  if(!examActive) for(const answer of run.answers){const card=node('article',undefined,'card'); const prompt=node('h3',answer.prompt_de); prompt.lang='de'; const quote=node('blockquote',answer.text);quote.lang='de';card.append(prompt,quote);$('answer-history').append(card);}
  $('answer-form').hidden = !run.current_question || run.status !== 'active';
  $('answer').value = client.pendingText(run);
  $('question-step').textContent = `Étape ${Math.min(run.answers.length+1,run.question_count)} / ${run.question_count}`;
  $('question').textContent=run.current_question?.prompt_de || '';
  $('coaching').hidden=run.mode!=='training'; $('coaching').textContent=run.mode==='training' ? run.current_question?.coaching_fr || '' : '';
  $('training-feedback').replaceChildren(); $('training-feedback').hidden=run.mode!=='training'||!run.training_feedback;
  if(run.mode==='training'&&run.training_feedback) $('training-feedback').append(node('h2','Retour Training'),feedbackItem(run.training_feedback));
  for(const action of ['pause','resume','finish']) $(action).hidden=action==='pause'?run.status!=='active':action==='resume'?run.status!=='paused':run.status==='completed';
  $('finish-note').textContent=run.status==='completed'?'Cette tentative est terminée et ne peut plus être modifiée.':run.answers.length<run.question_count?'Vous pouvez terminer maintenant : les questions sans réponse restent non évaluées.':'Toutes les réponses sont enregistrées. Terminez pour recevoir votre feedback.';
  $('feedback').replaceChildren();
  if(run.feedback){const hero=node('article',undefined,'card');hero.append(node('p','Votre prochaine étape','eyebrow'),node('h2',`Votre feedback · ${stateLabel(run.feedback.state)}`),node('p',run.feedback.next_step));$('feedback').append(hero,node('p',run.feedback.limitations,'availability'),node('p',`${run.content.scenario_id}@${run.content.scenario_version} · rubrique ${run.content.rubric_id}@${run.content.rubric_version}`,'help'));for(const dimension of run.feedback.dimensions)$('feedback').append(node('p',dimensionText(dimension)));for(const item of run.feedback.items)$('feedback').append(feedbackItem(item));const actions=node('div',undefined,'feedback-actions');actions.append(actionButton('Voir ma progression',showProgress,true),actionButton('Choisir une autre session',showCases));$('feedback').append(actions);}
  view('exercise',false);
}
async function openHistoryItem(item) { if(item.kind==='voice') location.href=`/voice.html?session=${encodeURIComponent(item.id)}`; else await showRun(await client.getRun(item.id)); }
async function showHistory() {
  historyItems=(await client.api('/api/history')).items; $('history-list').replaceChildren();
  if(!historyItems.length)$('history-list').append(node('p','Pas encore de session enregistrée. Choisissez un exercice pour commencer.','availability'));
  for(const item of historyItems){const card=node('article',undefined,'card');card.append(node('h2',item.content.title),node('p',`${phaseLabel(item.content.phase)} · ${item.mode} · ${stateLabel(item.status)} · ${formatDate(item.ended_at||item.created_at)}`),node('p',`Feedback : ${stateLabel(item.feedback_state)}`,'help'),actionButton(item.has_feedback?'Voir le feedback':'Reprendre cette session',()=>openHistoryItem(item)));$('history-list').append(card);}view('history');
}
const ERROR_LABELS = {gender: 'Genre', case: 'Cas et déclinaison', verb_form: 'Forme verbale', word_order: 'Ordre des mots', word_choice: 'Choix du mot', register: 'Registre', other: 'Autre'};
const VERDICT_LABELS = {acknowledged: 'Réaction adaptée', partial: 'Réaction minimale', ignored: 'Pas de réaction'};
const percent = value => value === null || value === undefined ? '—' : `${Math.round(value * 100)} %`;
function trendArrow(current, previous, higherIsBetter = true) {
  if (current === null || current === undefined || previous === null || previous === undefined) return '';
  if (Math.abs(current - previous) < 0.01) return ' →';
  const better = higherIsBetter ? current > previous : current < previous;
  return better ? ' ↑' : ' ↓';
}
function progressTile(label, value, note, state) {
  const tile = node('div', undefined, 'tile'); tile.dataset.state = state; tile.setAttribute('role', 'listitem');
  tile.append(node('span', value, 'tile-value'), node('strong', label), node('span', note, 'tile-note'));
  return tile;
}
function renderHeatmap(structure) {
  const table = $('heatmap'); table.replaceChildren();
  if (!structure.rows.length) { $('axis-structure-note').textContent = 'Aucune session avec sections d’anamnèse pour le moment.'; return; }
  const head = node('tr'); head.append(node('th', 'Session'));
  for (const column of structure.columns) { const th = node('th', column.label); th.lang = 'de'; head.append(th); }
  table.append(head);
  for (const row of structure.rows) {
    const tr = node('tr');
    tr.append(node('th', `${new Date(row.date).toLocaleDateString('fr-FR', {day: 'numeric', month: 'short'})}${row.order_respected ? '' : ' ·'}`));
    for (const column of structure.columns) {
      const ratio = row.cells[column.id];
      const td = node('td', ratio === null || ratio === undefined ? '' : ratio === 1 ? '✓' : ratio === 0 ? '—' : '◐');
      td.dataset.heat = ratio === null || ratio === undefined ? 'none' : String(Math.round(ratio * 4));
      td.title = `${column.label} : ${percent(ratio)}`;
      tr.append(td);
    }
    table.append(tr);
  }
  const weakest = structure.weakest.map(section => `${section.label} (${percent(section.ratio)})`).join(', ');
  $('axis-structure-note').textContent = `${structure.sessions} session${structure.sessions > 1 ? 's' : ''} · ordre canonique respecté ${percent(structure.order_respected_rate)} des fois${weakest ? ` · à renforcer : ${weakest}` : ''}. Un point après la date signale un ordre différent.`;
}
function renderBars(container, items, max) {
  container.replaceChildren(...items.map(item => {
    const row = node('div', undefined, 'bar-row');
    const fill = node('i'); if (fill.style) fill.style.width = `${max ? Math.round((item.value / max) * 100) : 0}%`;
    const bar = node('div', undefined, 'bar'); bar.append(fill);
    row.append(node('span', item.label, 'bar-label'), bar, node('span', item.text, 'bar-value'));
    return row;
  }));
}
function renderAxes(axes) {
  const s = axes.structure, c = axes.communication, l = axes.language, x = axes.lexicon, r = axes.regularity, v = axes.level;
  const lastWeek = r.weeks[r.weeks.length - 1];
  const weakest = s.weakest[0];
  const avg = Object.values(s.averages).filter(value => value !== null);
  const coverage = avg.length ? avg.reduce((a, b) => a + b, 0) / avg.length : null;
  $('progress-tiles').replaceChildren(
    progressTile('Structure', percent(coverage), weakest ? `à renforcer : ${weakest.label}` : 'aucune section suivie', coverage === null ? 'none' : coverage >= 0.8 ? 'ok' : coverage >= 0.5 ? 'warn' : 'bad'),
    progressTile('Moments sensibles', `${percent(c.acknowledged_rate)}${trendArrow(c.acknowledged_rate, c.previous_rate)}`, `${c.counts.acknowledged || 0} adaptées · ${c.counts.ignored || 0} ignorées`, c.acknowledged_rate === null ? 'none' : c.acknowledged_rate >= 0.8 ? 'ok' : c.acknowledged_rate >= 0.5 ? 'warn' : 'bad'),
    progressTile('Langue', l.errors_per_session === null ? '—' : `${l.errors_per_session}${trendArrow(l.errors_per_session, l.previous_errors_per_session, false)}`, l.recurring.length ? `récurrent : ${ERROR_LABELS[l.recurring[0].category]}` : 'erreurs par session', l.errors_per_session === null ? 'none' : l.errors_per_session <= 1 ? 'ok' : l.errors_per_session <= 2 ? 'warn' : 'bad'),
    progressTile('Carnet', String(x.acquired), `acquis · ${x.active} à travailler`, x.acquired ? 'ok' : x.active ? 'warn' : 'none'),
    progressTile('Niveau', v.history.length ? v.history[v.history.length - 1].band : '—', v.history.length ? `estimé le ${new Date(v.history[v.history.length - 1].date).toLocaleDateString('fr-FR')}` : 'test non passé', v.history.length ? 'ok' : 'none'),
    progressTile('Rythme', String(lastWeek.voice_sessions + lastWeek.practice_runs), `sessions cette semaine · ${lastWeek.voice_minutes} min de voix`, lastWeek.voice_sessions + lastWeek.practice_runs ? 'ok' : 'warn'),
  );
  renderHeatmap(s);
  $('empathy-timeline').replaceChildren(...(c.timeline.length ? c.timeline.map(item => { const dot = node('span', VERDICT_LABELS[item.verdict] || item.verdict, 'verdict'); dot.dataset.verdict = item.verdict; dot.title = `${new Date(item.date).toLocaleDateString('fr-FR')} · ${item.cue}`; return dot; }) : [node('p', 'Aucun moment sensible jugé pour le moment.', 'help')]));
  $('axis-communication-note').textContent = c.acknowledged_rate === null ? '' : `Réaction adaptée ${percent(c.acknowledged_rate)} sur les cinq derniers moments${c.previous_rate !== null ? ` (avant : ${percent(c.previous_rate)})` : ''}.`;
  const categories = Object.keys({...l.previous_by_category, ...l.by_category});
  const maxErrors = Math.max(1, ...categories.map(k => Math.max(l.by_category[k] || 0, l.previous_by_category[k] || 0)));
  renderBars($('error-bars'), categories.sort((a, b) => (l.by_category[b] || 0) - (l.by_category[a] || 0)).map(k => ({label: ERROR_LABELS[k] || k, value: l.by_category[k] || 0, text: `${l.by_category[k] || 0}${l.previous_by_category[k] !== undefined ? ` (avant ${l.previous_by_category[k]})` : ''}`})), maxErrors);
  if (!categories.length) $('error-bars').append(node('p', 'Aucune erreur de langue relevée sur vos dernières sessions.', 'help'));
  $('error-examples').replaceChildren(...l.recurring.flatMap(item => item.examples.map(example => { const quote = node('blockquote', example.text); quote.lang = 'fr'; return quote; })));
  renderBars($('lexicon-axis'), [
    {label: 'Acquis', value: x.acquired, text: String(x.acquired)},
    {label: 'À travailler', value: x.active, text: String(x.active)},
    {label: 'Ajoutés (30 j)', value: x.added_last_30_days, text: String(x.added_last_30_days)},
    {label: 'Acquis (30 j)', value: x.acquired_last_30_days, text: String(x.acquired_last_30_days)},
  ], Math.max(1, x.acquired, x.active, x.added_last_30_days));
  $('level-history').replaceChildren(...(v.history.length ? v.history.map(item => node('p', `${new Date(item.date).toLocaleDateString('fr-FR')} · ${item.band} · vocabulaire-grammaire ${item.vocab_grammar}, écoute ${item.listening}, oral ${item.speaking || 'non évalué'}${item.speaking && !item.speaking_counted ? ' (non compté)' : ''}`)) : [node('p', 'Aucun test de niveau terminé.', 'help')]));
  const maxWeek = Math.max(1, ...r.weeks.map(w => w.voice_sessions + w.practice_runs));
  renderBars($('week-bars'), r.weeks.map(w => ({label: `${new Date(w.start).toLocaleDateString('fr-FR', {day: 'numeric', month: 'short'})}`, value: w.voice_sessions + w.practice_runs, text: `${w.voice_sessions + w.practice_runs} session${w.voice_sessions + w.practice_runs > 1 ? 's' : ''} · ${w.voice_minutes} min`})), maxWeek);
}
async function showProgress() {
  const progress=await client.api('/api/progression');historyItems=(await client.api('/api/history')).items;renderCalendar();
  $('progress-note').textContent=progress.axes.limitations;$('progress-series-note').textContent=progress.limitations;renderAxes(progress.axes);$('progress-list').replaceChildren();
  if(!progress.groups.length)$('progress-list').append(node('p','Pas de données comparables : terminez un exercice pour retrouver ses dimensions.','availability'));
  for(const excluded of progress.excluded||[])$('progress-list').append(node('p',`${excluded.reason} · session ${excluded.run_id}. Le feedback reste disponible dans l’historique.`,'help'));
  for(const group of progress.groups){const card=node('article',undefined,'card');card.append(node('h2',`${group.content.title} · ${group.mode}`),node('p',`Contenu ${group.content.scenario_id}@${group.content.scenario_version} · rubrique ${group.content.rubric_id}@${group.content.rubric_version}`,'help'));for(const point of group.points){card.append(node('h3',formatDate(point.created_at)));for(const dimension of point.dimensions)card.append(node('p',dimensionText(dimension)));card.append(actionButton('Voir les preuves',()=>openHistoryItem({id:point.run_id,kind:group.kind})));}$('progress-list').append(card);}view('progress');
}
function renderReviewCard() {
  reviewEntry = reviewQueue[0] || null;
  $('vocab-review').hidden = !reviewEntry;
  if (!reviewEntry) return;
  $('review-progress').textContent = `Révision ${reviewTotal - reviewQueue.length + 1} / ${reviewTotal}`;
  $('review-source').textContent = reviewEntry.zone === 'acquired' ? `Entretien · ${sourceLabel(reviewEntry.source)}` : sourceLabel(reviewEntry.source);
  // Recto: the French cue when we have one, else the German word itself (recall the meaning).
  const hasCue = Boolean(reviewEntry.translation);
  $('review-front').textContent = hasCue ? reviewEntry.translation : reviewEntry.lemma;
  $('review-front').lang = hasCue ? 'fr' : 'de';
  $('review-example').hidden = true; $('review-example').textContent = reviewEntry.example || '';
  $('review-form').hidden = !hasCue; $('review-input').value = '';
  $('review-back').hidden = hasCue;
  $('review-answer').textContent = reviewEntry.lemma;
  $('review-verdict').textContent = hasCue ? '' : 'Ce mot n’a pas encore de traduction : évaluez votre souvenir de son sens.';
  $('review-ratings').replaceChildren(...RATINGS.map(rating => actionButton(ratingLabel(rating), () => rateReview(rating), rating === 'good')));
  if (!hasCue && reviewEntry.example) $('review-example').hidden = false;
}
function revealReview(typed) {
  const suggestion = LexiconClient.suggest(reviewEntry, typed);
  $('review-form').hidden = true; $('review-back').hidden = false;
  if (reviewEntry.example) $('review-example').hidden = false;
  $('review-verdict').textContent = suggestion === 'good' ? 'Votre réponse correspond. Évaluez la facilité du rappel.'
    : suggestion === 'again' ? `Vous avez écrit « ${typed.trim()} ». Comparez et évaluez honnêtement.` : 'Évaluez votre rappel.';
  Array.from($('review-ratings').children).forEach(button => button.classList.toggle('primary', suggestion ? button.textContent === ratingLabel(suggestion) : button.textContent === ratingLabel('good')));
}
async function rateReview(rating) {
  const entry = reviewEntry;
  await lexicon.review(entry, rating);
  reviewQueue = reviewQueue.filter(item => item.id !== entry.id);
  if (rating === 'again') reviewQueue.push(entry); // Due again immediately: back of the queue.
  if (!reviewQueue.length) { await showVocab(); return; }
  renderReviewCard();
}
let vocabZone = 'active', vocabOverview = null;
function entryCard(entry) {
  const card = node('article', undefined, 'card word-card');
  const title = node('h3', entry.lemma); title.lang = 'de';
  card.append(node('span', lexiconStateLabel(entry.state), 'badge'), title);
  if (entry.translation) card.append(node('p', entry.translation));
  if (entry.example) { const example = node('p', entry.example, 'muted'); example.lang = 'de'; card.append(example); }
  const next = entry.due ? (entry.zone === 'acquired' ? 'entretien à faire' : 'à revoir maintenant') : `prochain passage ${new Date(entry.due_at).toLocaleDateString('fr-FR')}`;
  card.append(node('p', `${sourceLabel(entry.source)} · ${next}`, 'help'));
  const history = node('p', [
    `repéré le ${new Date(entry.created_at).toLocaleDateString('fr-FR')}`,
    `${entry.repetitions} révision${entry.repetitions > 1 ? 's' : ''}${entry.lapses ? ` · ${entry.lapses} oubli${entry.lapses > 1 ? 's' : ''}` : ''}`,
    `utilisé dans ${entry.used_sessions} session${entry.used_sessions > 1 ? 's' : ''}`,
    entry.last_reviewed_at ? `dernière révision le ${new Date(entry.last_reviewed_at).toLocaleDateString('fr-FR')}` : 'jamais révisé',
  ].join(' · '), 'help');
  card.append(history);
  card.append(actionButton('Archiver', async () => { await lexicon.archive(entry, true); await showVocab(); }));
  return card;
}
function renderVocabList() {
  if (!vocabOverview) return;
  const query = $('vocab-search').value.trim().toLocaleLowerCase('fr-FR');
  const source = $('vocab-source').value;
  const entries = vocabOverview.entries.filter(entry => entry.zone === vocabZone
    && (source === 'all' || entry.source === source)
    && (!query || `${entry.lemma} ${entry.translation}`.toLocaleLowerCase('fr-FR').includes(query)));
  for (const tab of document.querySelectorAll('.tab')) tab.setAttribute('aria-selected', String(tab.dataset.zone === vocabZone));
  $('tab-active').textContent = `À travailler (${vocabOverview.active_count})`;
  $('tab-acquired').textContent = `Acquis (${vocabOverview.acquired_count})`;
  $('vocab-zone-note').textContent = vocabZone === 'active'
    ? 'Mots repérés, révisés ou utilisés une fois : ils reviennent par répétition espacée jusqu’à être acquis.'
    : `Mots utilisés spontanément dans deux sessions et révisés trois fois : ils reviennent ${vocabOverview.maintenance_cadence_days === 7 ? 'chaque semaine' : 'chaque mois'} en entretien.`;
  $('vocab-list').replaceChildren(...(entries.length ? entries.map(entryCard) : [node('p', vocabZone === 'active' ? 'Aucun mot à travailler avec ces filtres.' : 'Aucun mot acquis pour le moment : utilisez un mot de votre carnet dans deux sessions et révisez-le trois fois.', 'availability')]));
}
async function showVocab() {
  lexicon.profileId = client.profile.id;
  const overview = await lexicon.overview();
  vocabOverview = overview;
  const acquired = overview.acquired_count, active = overview.active_count;
  $('vocab-summary').textContent = overview.entries.length
    ? `${active} mot${active > 1 ? 's' : ''} à travailler · ${overview.due_count} à revoir aujourd’hui · ${acquired} acquis${overview.maintenance_due_count ? ` · ${overview.maintenance_due_count} en entretien` : ''}.`
    : 'Votre carnet se remplit à chaque session : mots manqués, termes du cas non utilisés, passages dans une autre langue.';
  $('vocab-limitations').textContent = overview.limitations;
  $('vocab-cadence').value = String(overview.maintenance_cadence_days);
  // Due active words first, then acquired words whose maintenance is due.
  reviewQueue = [...overview.entries.filter(entry => entry.zone === 'active' && entry.due), ...overview.entries.filter(entry => entry.zone === 'acquired' && entry.due)];
  reviewTotal = reviewQueue.length;
  renderReviewCard();
  $('vocab-empty').hidden = reviewTotal > 0;
  renderVocabList();
  view('vocab');
}
// ----- placement test ---------------------------------------------------------------
function placementBusyReset() {
  clearInterval(recordTimer); recordTimer = null; recordedAudio = null;
  if (recorder) { recorder.stop().catch(() => {}); recorder = null; }
}
async function showPlacement() {
  placement.profileId = client.profile.id;
  placementBusyReset();
  const overview = await placement.overview();
  $('placement-description').textContent = overview.set?.description_fr || 'Aucun test de niveau publié pour le moment.';
  $('placement-limitations').textContent = overview.limitations;
  $('placement-intro').hidden = false; $('placement-run').hidden = true; $('placement-result').hidden = true;
  const latest = overview.latest;
  $('placement-start').hidden = !overview.available;
  $('placement-resume').hidden = !(latest && latest.status === 'active');
  $('placement-status').textContent = !overview.available ? 'Le test sera disponible dès qu’un jeu de questions aura été relu et publié.'
    : latest?.status === 'completed' ? `Dernière estimation : ${latest.result.band} (${new Date(latest.ended_at).toLocaleDateString('fr-FR')}). Vous pouvez refaire le test.`
    : latest?.status === 'active' ? `Un test est en cours (${PHASE_LABELS[latest.phase]}).`
    : `Environ quinze minutes. Niveau de départ : ${overview.declared_level || 'A2'} (votre niveau déclaré).`;
  placementAttempt = latest && latest.status === 'active' ? latest : null;
  view('placement');
}
async function startPlacement(resume = false) {
  const health = await client.api('/api/health'); providerMode = health.provider_mode;
  placementAttempt = resume && placementAttempt ? await placement.get(placementAttempt.id) : await placement.start();
  renderPlacement(placementAttempt);
}
function renderPlacement(attempt) {
  placementAttempt = attempt; placementListens = 0; placementBusyReset();
  $('placement-intro').hidden = true;
  if (attempt.status === 'completed') { renderPlacementResult(attempt); return; }
  $('placement-run').hidden = false; $('placement-result').hidden = true;
  const p = attempt.progress;
  $('placement-phase').textContent = `${PHASE_LABELS[attempt.phase]} · étape ${attempt.phase === 'mcq' ? 1 : attempt.phase === 'listening' ? 2 : 3} / 3`;
  $('placement-progress').textContent = attempt.phase === 'mcq' ? `Question ${p.mcq_answered + 1} · le test s’adapte à vos réponses, ${p.mcq_max} questions au plus`
    : attempt.phase === 'listening' ? `Écoute ${p.listening_answered + 1} / ${p.listening_total} · deux écoutes maximum par extrait`
    : `Prise de parole ${p.speaking_answered + 1} / ${p.speaking_total}`;
  $('placement-item').replaceChildren();
  const item = attempt.current_item;
  const isChoice = item && (item.kind === 'mcq' || item.kind === 'listening');
  $('placement-choice-form').hidden = !isChoice; $('placement-speaking').hidden = item?.kind !== 'speaking';
  $('placement-quit').hidden = attempt.phase === 'mcq';
  if (!item) return;
  if (item.kind === 'listening') {
    const audio = node('audio'); audio.controls = true; audio.preload = 'auto'; audio.src = placement.audioUrl(attempt, item.id);
    audio.addEventListener('play', () => { placementListens += 1; if (placementListens > 2) { audio.pause(); audio.currentTime = 0; $('placement-progress').textContent = 'Deux écoutes effectuées : répondez maintenant.'; } });
    const wrap = node('div', undefined, 'availability'); wrap.append(node('p', 'Écoutez l’extrait, puis répondez à la question.'), audio); wrap.lang = attempt.language.split('-')[0];
    $('placement-item').append(wrap);
  }
  if (isChoice) {
    const question = item.kind === 'mcq' ? item.stem : item.question;
    $('placement-question').textContent = question; $('placement-question').lang = attempt.language.split('-')[0];
    const legend = $('placement-question');
    $('placement-options').replaceChildren(legend, ...item.options.map((option, index) => {
      const label = node('label'); const input = node('input'); input.type = 'radio'; input.name = 'placement-option'; input.value = String(index);
      label.append(input, document.createTextNode(` ${option}`)); label.lang = attempt.language.split('-')[0]; return label;
    }));
  }
  if (item.kind === 'speaking') {
    $('placement-speaking-fr').textContent = item.prompt_fr; $('placement-speaking-target').textContent = item.prompt_target; $('placement-speaking-target').lang = attempt.language.split('-')[0];
    $('placement-record-status').textContent = `Visez environ ${item.target_seconds} secondes. Appuyez sur Enregistrer, parlez, puis appuyez de nouveau pour arrêter.`;
    $('placement-record').textContent = 'Enregistrer'; $('placement-send').disabled = true;
    $('placement-text-form').hidden = providerMode !== 'fake'; $('placement-record').hidden = providerMode === 'fake'; $('placement-send').hidden = providerMode === 'fake';
  }
}
function renderPlacementResult(attempt) {
  $('placement-run').hidden = true; $('placement-result').hidden = false;
  const result = attempt.result;
  $('placement-band').textContent = `${result.band} · ${LEVEL_LABELS[result.band] || ''}`;
  $('placement-detail').textContent = describeResult(result) + ` · ${result.mcq_answered} questions, ${result.listening_answered} écoutes, ${result.speaking_answered} prises de parole.`;
  $('placement-speaking-feedback').replaceChildren(...attempt.speaking_feedback.map(item => {
    const card = node('article', undefined, 'card'); card.append(node('h3', `Expression orale · ${item.estimated_level} (confiance ${Math.round(item.confidence * 100)} %)`));
    const quote = node('blockquote', item.transcript); quote.lang = attempt.language.split('-')[0]; card.append(quote);
    for (const observation of item.observations) card.append(node('p', observation, 'muted'));
    return card;
  }));
}
async function toggleRecording() {
  if (!recorder) {
    recorder = new PcmRecorder(); recordedAudio = null; $('placement-send').disabled = true;
    await recorder.start();
    const started = Date.now();
    $('placement-record').textContent = 'Arrêter';
    recordTimer = setInterval(() => { $('placement-record-status').textContent = `Enregistrement… ${Math.floor((Date.now() - started) / 1000)} s`; }, 500);
    return;
  }
  clearInterval(recordTimer); recordTimer = null;
  recordedAudio = await recorder.stop(); recorder = null;
  const seconds = Math.round(recordedAudio.byteLength / 48000);
  $('placement-record').textContent = 'Réenregistrer';
  $('placement-record-status').textContent = seconds < 1 ? 'Enregistrement trop court, réessayez.' : `${seconds} s enregistrées. Envoyez, ou réenregistrez.`;
  $('placement-send').disabled = seconds < 1;
}
async function navigate(name) {
  if(!client.profile)return home();
  const routes={home,cases:showCases,exam:showExam,vocab:showVocab,history:showHistory,progress:showProgress,placement:showPlacement,warmup:()=>selected?view('warmup'):showCases()};
  await (routes[name]||home)();
}
async function route() { const match=location.hash.match(/^#practice\/([A-Za-z0-9_.-]+)$/);if(client.profile&&match)return showRun(await client.getRun(match[1]));return navigate(location.hash.slice(1)||'home'); }
$('retry').onclick=()=>retryAction&&perform(retryAction);
$('next-profile').onclick=()=>{if($('draft-land').reportValidity()&&$('draft-date').reportValidity()){onboardingStep(2,true);writeDraft();}};
$('back-project').onclick=()=>{onboardingStep(1,true);writeDraft();};
$('profile-form').addEventListener('input',()=>{sourceState();writeDraft();});
$('profile-form').addEventListener('change',()=>{sourceState();writeDraft();});
$('profile-form').onsubmit=event=>{event.preventDefault();if($('person-step').hidden)return;if(!$('draft-land').value){onboardingStep(1);$('draft-land').reportValidity();return;}if(!$('draft-specialty').reportValidity())return;const draft=writeDraft();const details=profileDetails();perform(async()=>{const created=!client.profile;if(!client.profile){await client.restoreProfile();if(!client.profile)await client.createProfile($('target').value,details);try{localStorage.setItem(localKey(),JSON.stringify(draft));localStorage.removeItem('ari:onboarding:v3:anonymous');}catch{}}if(!created||client.profile.details?.declared_level!==details.declared_level){await client.updateProfile(details);}if(client.profile.goal.target_cefr!==$('target').value){await client.api(`/api/learners/${encodeURIComponent(client.profile.id)}/goal`,{method:'PATCH',body:JSON.stringify({target_cefr:$('target').value})});await client.restoreProfile();}if(created&&!client.profile.details?.estimated_level){await showPlacement();return;}await home();});};
$('goal-form').onsubmit=event=>{event.preventDefault();perform(async()=>{await client.api(`/api/learners/${encodeURIComponent(client.profile.id)}/goal`,{method:'PATCH',body:JSON.stringify({target_cefr:$('goal-target').value})});await client.restoreProfile();await home();});};
$('edit-profile').onclick=()=>{restoreDraft();history.replaceState(null,'','#onboarding');view('onboarding',false);};
$('case-search').oninput=renderCatalog;$('case-filter').onchange=renderCatalog;document.querySelectorAll('[name=mode]').forEach(input=>input.onchange=renderCatalog);
$('warmup-start').onclick=()=>perform(startSelected);
$('placement-start').onclick=()=>perform(()=>startPlacement(false));
$('placement-resume').onclick=()=>perform(()=>startPlacement(true));
$('placement-again').onclick=()=>perform(()=>startPlacement(false));
$('placement-choice-form').onsubmit=event=>{event.preventDefault();const chosen=document.querySelector('[name=placement-option]:checked');if(!chosen){$('notice').textContent='Choisissez une réponse.';return;}const attempt=placementAttempt;perform(async()=>renderPlacement(await placement.answer(attempt,attempt.current_item.id,Number(chosen.value))));};
$('placement-record').onclick=()=>perform(toggleRecording);
$('placement-send').onclick=()=>{const attempt=placementAttempt,audio=recordedAudio;perform(async()=>renderPlacement(await placement.speak(attempt,attempt.current_item.id,{audio})));};
$('placement-text-form').onsubmit=event=>{event.preventDefault();const attempt=placementAttempt,text=$('placement-text').value.trim();if(!text)return;perform(async()=>{$('placement-text').value='';renderPlacement(await placement.speak(attempt,attempt.current_item.id,{text}));});};
$('placement-quit').onclick=()=>{const attempt=placementAttempt;perform(async()=>{await client.restoreProfile();renderPlacement(await placement.finish(attempt));await client.restoreProfile();});};
for(const tab of document.querySelectorAll('.tab'))tab.onclick=()=>{vocabZone=tab.dataset.zone;renderVocabList();};
$('vocab-search').oninput=renderVocabList;$('vocab-source').onchange=renderVocabList;
$('vocab-cadence-form').onsubmit=event=>{event.preventDefault();const days=Number($('vocab-cadence').value);perform(async()=>{await client.updateProfile({maintenance_cadence_days:days});await showVocab();});};
$('review-form').onsubmit=event=>{event.preventDefault();if(reviewEntry)revealReview($('review-input').value);};
$('review-reveal').onclick=()=>{if(reviewEntry)revealReview('');};
$('vocab-add-form').onsubmit=event=>{event.preventDefault();if(!$('add-lemma').reportValidity())return;const lemma=$('add-lemma').value,translation=$('add-translation').value,example=$('add-example').value;perform(async()=>{await lexicon.add(lemma,translation,example);$('vocab-add-form').reset();await showVocab();});};
$('answer-form').onsubmit=event=>{event.preventDefault();const run=currentRun,text=$('answer').value;perform(async()=>showRun(await client.answer(run,text)));};
for(const action of ['pause','resume','finish'])$(action).onclick=()=>{const run=currentRun;perform(async()=>showRun(await client.action(run,action)));};
for(const button of document.querySelectorAll('[data-view]'))button.onclick=()=>perform(()=>navigate(button.dataset.view));
window.addEventListener('hashchange',()=>perform(route));
window.addEventListener('resize',()=>{if(!$('progress').hidden){const selected=$('daytrack').querySelector('[aria-pressed=true]');selected?.scrollIntoView({block:'nearest',inline:'nearest'});}});
window.addEventListener('focus',()=>{const today=new Date().toLocaleDateString('fr-FR',{weekday:'long',day:'numeric',month:'long'});if($('today-label').textContent!==today){$('today-label').textContent=today;if(!$('progress').hidden)renderCalendar();}});
$('today-label').textContent=new Date().toLocaleDateString('fr-FR',{weekday:'long',day:'numeric',month:'long'});
perform(async()=>{await client.restoreProfile();if(location.hash==='#onboarding'&&client.profile){restoreDraft();view('onboarding',false);}else await route();});
