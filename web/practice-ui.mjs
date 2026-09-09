import {PracticeClient, phaseLabel, stateLabel, dimensionText} from './practice-client.mjs';
import {completedDayKeys, lastLocalDays, localDayKey, scrollDelta} from './calendar.mjs';

const client = new PracticeClient();
const $ = id => document.getElementById(id);
const views = ['onboarding', 'home', 'warmup', 'cases', 'exam', 'vocab', 'exercise', 'history', 'progress'];
const fields = ['goal', 'land', 'date', 'minutes', 'situation', 'specialty', 'level'];
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
  $('target').value = client.profile?.goal.target_cefr || (['B2', 'C1', 'C2'].includes(draft.target) ? draft.target : 'C1');
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
async function home() {
  if (!client.profile) { restoreDraft(); view('onboarding', false); return; }
  await loadCatalog(); historyItems = (await client.api('/api/history')).items;
  $('profile-goal').textContent = `Objectif personnel ${client.profile.goal.target_cefr} · niveau non mesuré.`;
  $('goal-target').value = client.profile.goal.target_cefr;
  const draft = readDraft();
  $('local-profile-summary').textContent = draft.land ? `${draft.land} · ${draft.minutes} min/jour · niveau ${draft.level} déclaré. Préférences conservées uniquement sur cet appareil.` : 'Vous pouvez renseigner vos préférences sur cet appareil.';
  const first = voiceCases[0] || catalog[0], kind = voiceCases[0] ? 'voice' : 'practice';
  $('recommendation').replaceChildren(node('p','Votre prochaine étape','eyebrow'),node('h2',first?.title || 'Aucune session disponible.'));
  if (first) {
    $('recommendation').append(node('p', 'Prenez quelques minutes pour préparer votre prochain échange.'), actionButton('Préparer ma session →', () => prepare(first,kind), true));
    const why = node('details'); why.append(node('summary','Pourquoi cette proposition ?'),node('p', first.provenance === 'synthetic_demo' ? 'Le serveur de démonstration propose cet exercice synthétique de test. Il ne constitue pas une recommandation pédagogique personnalisée.' : 'Ce contenu est le premier du catalogue disponible, en privilégiant les consultations vocales. Cette sélection ne repose pas sur un niveau mesuré et ne constitue pas un programme calibré.'));
    $('recommendation').append(why);
  } else $('recommendation').append(node('p','Aucun cas ou exercice publié n’est disponible. Vos sessions précédentes restent accessibles dans l’historique.'));
  view('home'); renderCalendar();
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
async function showProgress() {
  const progress=await client.api('/api/progression');$('progress-note').textContent=progress.limitations;$('progress-list').replaceChildren();
  if(!progress.groups.length)$('progress-list').append(node('p','Pas de données comparables : terminez un exercice pour retrouver ses dimensions.','availability'));
  for(const excluded of progress.excluded||[])$('progress-list').append(node('p',`${excluded.reason} · session ${excluded.run_id}. Le feedback reste disponible dans l’historique.`,'help'));
  for(const group of progress.groups){const card=node('article',undefined,'card');card.append(node('h2',`${group.content.title} · ${group.mode}`),node('p',`Contenu ${group.content.scenario_id}@${group.content.scenario_version} · rubrique ${group.content.rubric_id}@${group.content.rubric_version}`,'help'));for(const point of group.points){card.append(node('h3',formatDate(point.created_at)));for(const dimension of point.dimensions)card.append(node('p',dimensionText(dimension)));card.append(actionButton('Voir les preuves',()=>openHistoryItem({id:point.run_id,kind:group.kind})));}$('progress-list').append(card);}view('progress');
}
async function navigate(name) {
  if(!client.profile)return home();
  const routes={home,cases:showCases,exam:showExam,vocab:()=>view('vocab'),history:showHistory,progress:showProgress,warmup:()=>selected?view('warmup'):showCases()};
  await (routes[name]||home)();
}
async function route() { const match=location.hash.match(/^#practice\/([A-Za-z0-9_.-]+)$/);if(client.profile&&match)return showRun(await client.getRun(match[1]));return navigate(location.hash.slice(1)||'home'); }
$('retry').onclick=()=>retryAction&&perform(retryAction);
$('next-profile').onclick=()=>{if($('draft-land').reportValidity()&&$('draft-date').reportValidity()){onboardingStep(2,true);writeDraft();}};
$('back-project').onclick=()=>{onboardingStep(1,true);writeDraft();};
$('profile-form').addEventListener('input',()=>{sourceState();writeDraft();});
$('profile-form').addEventListener('change',()=>{sourceState();writeDraft();});
$('profile-form').onsubmit=event=>{event.preventDefault();if($('person-step').hidden)return;if(!$('draft-land').value){onboardingStep(1);$('draft-land').reportValidity();return;}if(!$('draft-specialty').reportValidity())return;const draft=writeDraft();perform(async()=>{if(!client.profile){await client.restoreProfile();if(!client.profile)await client.createProfile($('target').value);try{localStorage.setItem(localKey(),JSON.stringify(draft));localStorage.removeItem('ari:onboarding:v3:anonymous');}catch{}}else if(client.profile.goal.target_cefr!==$('target').value){await client.api(`/api/learners/${encodeURIComponent(client.profile.id)}/goal`,{method:'PATCH',body:JSON.stringify({target_cefr:$('target').value})});await client.restoreProfile();}await home();});};
$('goal-form').onsubmit=event=>{event.preventDefault();perform(async()=>{await client.api(`/api/learners/${encodeURIComponent(client.profile.id)}/goal`,{method:'PATCH',body:JSON.stringify({target_cefr:$('goal-target').value})});await client.restoreProfile();await home();});};
$('edit-profile').onclick=()=>{restoreDraft();history.replaceState(null,'','#onboarding');view('onboarding',false);};
$('case-search').oninput=renderCatalog;$('case-filter').onchange=renderCatalog;document.querySelectorAll('[name=mode]').forEach(input=>input.onchange=renderCatalog);
$('warmup-start').onclick=()=>perform(startSelected);
$('answer-form').onsubmit=event=>{event.preventDefault();const run=currentRun,text=$('answer').value;perform(async()=>showRun(await client.answer(run,text)));};
for(const action of ['pause','resume','finish'])$(action).onclick=()=>{const run=currentRun;perform(async()=>showRun(await client.action(run,action)));};
for(const button of document.querySelectorAll('[data-view]'))button.onclick=()=>perform(()=>navigate(button.dataset.view));
window.addEventListener('hashchange',()=>perform(route));
window.addEventListener('resize',()=>{if(!$('home').hidden){const selected=$('daytrack').querySelector('[aria-pressed=true]');selected?.scrollIntoView({block:'nearest',inline:'nearest'});}});
window.addEventListener('focus',()=>{const today=new Date().toLocaleDateString('fr-FR',{weekday:'long',day:'numeric',month:'long'});if($('today-label').textContent!==today){$('today-label').textContent=today;if(!$('home').hidden)renderCalendar();}});
$('today-label').textContent=new Date().toLocaleDateString('fr-FR',{weekday:'long',day:'numeric',month:'long'});
perform(async()=>{await client.restoreProfile();if(location.hash==='#onboarding'&&client.profile){restoreDraft();view('onboarding',false);}else await route();});
