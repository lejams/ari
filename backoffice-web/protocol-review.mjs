import {blockers, ROLE_LABELS, SECTION_LABELS, statusLabel} from './api.mjs';
import {clone, getPath, setPath, stageFor, pointsFor, resolvePoint, validateDraft, markedRanges, uncoveredPassages, codePoints, compareFirst, sortedQueue, fieldLabel} from './review.mjs';

const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const sex = [['female','femme'],['male','homme'],['other','autre'],['unknown','inconnu']];
const result = [['unknown','inconnu'],['passed','réussi'],['failed','échoué']];
const difficulty = [['leicht','leicht'],['mittel','mittel'],['schwer','schwer']];
const polarity = [['present','présent'],['absent','absent'],['unknown','inconnu']];
const parts = [['arzt_patient','Arzt–Patient'],['arzt_arzt','Arzt–Arzt'],['fachbegriffe','Fachbegriffe'],['arztbrief','Arztbrief'],['general','Général']];
const sectionTitles = {location:'Localisation',patient:'Patient',anamnesis:'Anamnèse',diagnosis:'Diagnostic',examiner_questions:'Questions des examinateurs',arzt_arzt:'Arzt–Arzt',fachbegriffe:'Fachbegriffe',outcome:'Issue de l’examen',unresolved_questions:'Questions de l’IA',field_uncertainties:'Incertitudes de champ',pedagogy:'Pédagogie',pseudonymisation:'Pseudonymisation'};

function fieldsFor(record, lands) {
  const sections = new Map(Object.keys(sectionTitles).map(key => [key, []]));
  const add = (section, path, label, kind = 'text', choices = null, meta = {}) => sections.get(section).push({path,label,kind,choices,...meta});
  for (const [key,label] of [['land','Land'],['city','Ville'],['exam_body','Ärztekammer'],['exam_date','Mois (AAAA-MM)'],['specialty','Spécialité']]) add('location',`location.${key}`,label,'text',key === 'land' ? lands.map(land => [land,land]) : null);
  add('patient','patient.age_years','Âge','number'); add('patient','patient.sex','Sexe','text',sex);
  for (const [key,label] of [['occupation_de','Profession'],['presenting_complaint_de','Motif'],['summary_de','Résumé']]) add('patient',`patient.${key}`,label,key === 'summary_de' ? 'textarea' : 'text');
  record.anamnesis.forEach((item,i) => {
    const meta = {item: item.id, page: item.source_pages?.[0], sub: SECTION_LABELS[item.section]};
    add('anamnesis',`anamnesis.${i}.label_de`,'Libellé','text',null,meta);
    add('anamnesis',`anamnesis.${i}.value_de`,'Valeur','text',null,{...meta,unknown:item.polarity === 'unknown'});
    add('anamnesis',`anamnesis.${i}.polarity`,'Polarité','text',polarity,meta);
    add('anamnesis',`anamnesis.${i}.section`,'Section','text',Object.entries(SECTION_LABELS),meta);
    add('anamnesis',`anamnesis.${i}.temporality`,'Temporalité','text',null,meta);
    add('anamnesis',`anamnesis.${i}.quote_de`,'Citation source','textarea',null,meta);
  });
  add('diagnosis','diagnosis.suspected_de','Diagnostic suspecté'); add('diagnosis','diagnosis.differentials_de','Diagnostics différentiels','lines');
  record.examiner_questions.forEach((item,i) => {
    const meta = {item:item.id,page:item.source_pages?.[0]};
    add('examiner_questions',`examiner_questions.${i}.part`,'Partie','text',parts,meta);
    for (const [key,label] of [['question_de','Question'],['expected_answer_de','Réponse attendue'],['candidate_answer_de','Réponse du candidat']]) add('examiner_questions',`examiner_questions.${i}.${key}`,label,'textarea',null,meta);
  });
  add('arzt_arzt','arzt_arzt.presentation_summary_de','Présentation','textarea'); add('arzt_arzt','arzt_arzt.discussion_points_de','Points discutés','lines');
  record.fachbegriffe.forEach((item,i) => {
    for (const [key,label] of [['german','Terme'],['lay_german','Explication courante'],['french','Français']]) add('fachbegriffe',`fachbegriffe.${i}.${key}`,label,'text',null,{item:item.id});
    add('fachbegriffe',`fachbegriffe.${i}.asked`,'Demandé','checkbox',null,{item:item.id});
  });
  add('outcome','outcome.result','Résultat','text',result);
  add('outcome','outcome.examiner_feedback_de','Retour des examinateurs','lines'); add('outcome','outcome.candidate_tips_de','Conseils du candidat','lines'); add('outcome','arztbrief_notes_de','Notes Arztbrief','textarea');
  record.unresolved_questions.forEach((item,i) => {
    add('unresolved_questions',`unresolved_questions.${i}.text_fr`,'Question (FR)','textarea',null,{item:item.id});
    add('unresolved_questions',`unresolved_questions.${i}.answer`,'Réponse','textarea',null,{item:item.id});
    add('unresolved_questions',`unresolved_questions.${i}.critical`,'Critique','checkbox',null,{item:item.id});
  });
  record.field_uncertainties.forEach((item,i) => {
    add('field_uncertainties',`field_uncertainties.${i}.reason_fr`,fieldLabel(item.path),'textarea');
    add('field_uncertainties',`field_uncertainties.${i}.resolution`,'Résolution','textarea');
  });
  add('pedagogy','pedagogy.difficulty','Difficulté','text',difficulty); add('pedagogy','pedagogy.notes_fr','Notes (FR)','textarea');
  for (const list of ['critical_pitfalls','pitfalls']) record.pedagogy[list].forEach((item,i) => {
    add('pedagogy',`pedagogy.${list}.${i}.text_fr`,list === 'critical_pitfalls' ? 'Piège grave' : 'Piège','textarea');
    add('pedagogy',`pedagogy.${list}.${i}.related_item_ids`,'Éléments liés','csv');
  });
  return sections;
}
function control(field, value, attrs = '') {
  const common = `aria-label="${esc(field.label)}" ${attrs}`;
  if (field.choices) return `<select ${common}><option value="">—</option>${field.choices.map(([key,label]) => `<option value="${esc(key)}" ${key === value ? 'selected' : ''}>${esc(label)}</option>`).join('')}</select>`;
  if (field.kind === 'checkbox') return `<input type="checkbox" ${common} ${value ? 'checked' : ''}>`;
  if (['textarea','lines'].includes(field.kind)) return `<textarea rows="2" ${common}>${esc(field.kind === 'lines' ? (value || []).join('\n') : value)}</textarea>`;
  return `<input type="${field.kind === 'number' ? 'number' : 'text'}" ${common} value="${esc(field.kind === 'csv' ? (value || []).join(', ') : value)}" ${field.kind === 'number' ? 'min="0" max="120"' : ''} placeholder="${field.unknown ? 'inconnu (absent du protocole)' : '(vide)'}">`;
}
function readControl(element, kind) {
  if (kind === 'checkbox') return element.checked;
  if (kind === 'number') return element.value === '' ? null : Number(element.value);
  if (kind === 'lines') return element.value.split('\n').map(line => line.trim()).filter(Boolean);
  if (kind === 'csv') return element.value.split(',').map(line => line.trim()).filter(Boolean);
  return element.value.trim() || null;
}
export class ProtocolReview {
  constructor({client, lands, perform, notice, view}) {
    Object.assign(this, {client, lands, perform, notice, view, active:false, request:0, open:new Set(), activeId:null, mode:'text', pointDirty:false, noteMode:null});
    $('protocol').addEventListener('click', event => this.click(event));
    $('protocol').addEventListener('input', event => this.edit(event));
    $('protocol').addEventListener('change', event => this.change(event));
    window.addEventListener('keydown', event => this.key(event));
    window.addEventListener('beforeunload', event => { if (this.active && this.unsaved()) { event.preventDefault(); event.returnValue = ''; } });
  }
  canRevise() { return this.protocol && !['gold','rejected','superseded'].includes(this.protocol.status) && this.client.has('owner','physician_reviewer'); }
  canDecide() { return this.stage === 'owner' ? this.protocol.status === 'doctor_approved' && this.client.has('owner') : ['doctor_review','changes_requested'].includes(this.protocol.status) && this.client.has('physician_reviewer'); }
  dirty() { return this.protocol && JSON.stringify(this.record) !== JSON.stringify(this.protocol.record); }
  unsaved() { return this.dirty() || this.pointDirty || Boolean($('decision-notes').value?.trim() || $('pii-override').value?.trim()); }
  confirmLeave() { return !this.unsaved() || window.confirm('Des corrections ou une note ne sont pas enregistrées. Quitter ce protocole ?'); }
  deactivate() { this.active = false; this.request += 1; this.pointDirty = false; }
  async show(id) {
    const request = ++this.request, hash = `#/protocols/${id}`;
    const protocol = await this.client.protocol(id);
    const stage = stageFor(protocol), statuses = stage === 'owner' ? ['doctor_approved'] : ['doctor_review','changes_requested'];
    const [source, ...queues] = await Promise.all([this.client.protocolSource(id), ...statuses.map(status => this.client.protocols({status}))]);
    if (request !== this.request || location.hash !== hash) return;
    Object.assign(this, {protocol, record:clone(protocol.record), initial:clone(protocol.first_record || protocol.record), source, stage, hash, queue:sortedQueue(queues.flatMap(queue => queue.items)), active:true, page:protocol.page_from, pointDirty:false, noteMode:null, activeId:null, open:new Set()});
    $('decision-notes').value = ''; $('pii-override').value = '';
    this.view('protocol'); this.render();
  }
  points() { return pointsFor(this.record, this.stage, this.initial); }
  currentPoint() { return this.points().find(point => point.id === this.activeId); }
  render() {
    this.renderHeader(); this.renderPoints(); this.renderSections(); this.renderSource(); this.renderCoverage(); this.renderDiff(); this.renderDecision();
  }
  renderHeader() {
    const p = this.protocol, r = this.record;
    $('protocol-title').textContent = r.source.title_hint || r.diagnosis.suspected_de || r.patient.presenting_complaint_de || p.id;
    $('protocol-meta').textContent = [r.patient.age_years !== null ? `${r.patient.age_years} ans` : null, sex.find(([key]) => key === r.patient.sex)?.[1], r.patient.presenting_complaint_de].filter(Boolean).join(' · ');
    $('protocol-crumbs').innerHTML = `<a href="#/documents">Documents</a><span>/</span><a href="#/documents/${esc(p.document_id)}">${esc(p.document?.filename || 'Document source')}</a><span>/</span><span>Protocole ${r.source.segment_index + 1} du document</span>`;
    $('protocol-chips').innerHTML = `<span class="chip status">${esc(statusLabel(p.status))}${['doctor_review','doctor_approved'].includes(p.status) ? ` (${this.stage === 'owner' ? 'propriétaire' : 'médecin'})` : ''}</span><span class="chip">${esc([r.location.land,r.location.city].filter(Boolean).join(' · ') || 'Localisation inconnue')}</span><span class="chip">Mois ${esc(r.location.exam_date || 'inconnu')}</span><span class="chip len">${p.page_to - p.page_from + 1} pages · ${this.source.text_length.toLocaleString('fr-FR')} car.</span><span class="chip mono">${esc(p.id)} · v${p.version}</span>`;
    $('review-stage').textContent = this.stage === 'owner' ? 'Propriétaire' : 'Médecin';
    $('review-dirty').textContent = this.dirty() || this.pointDirty ? 'Modifications non enregistrées' : 'Brouillon à jour';
    $('review-saved').classList.toggle('dirty', this.dirty() || this.pointDirty);
    const index = this.queue.findIndex(item => item.id === p.id);
    $('queue-position').textContent = index < 0 ? 'File triée par longueur' : `${index + 1} / ${this.queue.length} · par longueur`;
    $('queue-prev').disabled = index <= 0; $('queue-next').disabled = index < 0 || index >= this.queue.length - 1;
  }
  renderPoints() {
    const points = this.points(), open = points.filter(point => !point.resolved);
    if (!this.activeId || !points.some(point => point.id === this.activeId)) this.activeId = open[0]?.id || points[0]?.id;
    $('checklist').innerHTML = points.map(point => `<li><button type="button" data-point="${esc(point.id)}" class="${point.resolved ? 'done' : ''} ${point.critical ? 'crit' : ''}" aria-current="${point.id === this.activeId}"><i class="dot"></i>${esc(point.label)}</button></li>`).join('');
    $('review-meter').textContent = `${points.length - open.length} / ${points.length} résolus`;
    $('review-meter-bar').style.width = `${points.length ? (points.length - open.length) / points.length * 100 : 100}%`;
    $('checklist-ok').hidden = open.length > 0;
    const point = this.currentPoint(), panel = $('active-point');
    if (!point || point.resolved && !open.length) { panel.innerHTML = '<div class="alldone"><h3>Tous les points sont résolus</h3><p>Vous pouvez encore corriger les champs et vérifier les passages source.</p></div>'; return; }
    const field = [...fieldsFor(this.record, this.lands()).values()].flat().find(field => field.path === point.path) || {path:point.path,label:fieldLabel(point.path),kind:'textarea'};
    const holder = point.holder && getPath(this.record, point.holder);
    const linked = this.source.pages.flatMap(page => markedRanges(page, this.record).map(mark => ({...mark,page:page.page_number}))).find(mark => mark.path === point.path || mark.path === `${point.holder}.quote_de`);
    const quote = point.quote || holder?.quote_de || linked?.value;
    const page = point.pages?.[0] || linked?.page;
    let input = control(field, getPath(this.record, point.path), 'id="point-value" class="fin"');
    if (point.kind === 'anamnesis') input = `<div class="choice" role="group" aria-label="Résolution">${(holder.polarity === 'unknown' ? [['unknown','Confirmer : non rapporté dans le protocole'],['corrected','Le protocole le mentionne']] : [['kept','Garder la formulation du protocole'],['corrected','Valeur corrigée'],['unknown','Non rapporté']]).map(([choice,label]) => `<button type="button" data-choice="${choice}" aria-pressed="false">${label}</button>`).join('')}</div><div class="choice" role="group" aria-label="Polarité de la valeur"><span>La valeur est :</span><button type="button" data-polarity="present" aria-pressed="false">présente</button><button type="button" data-polarity="absent" aria-pressed="false">absente</button></div><label class="flabel" for="point-value">Valeur rapportée dans la source</label>${input}`;
    else if (point.kind === 'uncertainty') input += '<div class="choice"><button type="button" data-choice="kept" aria-pressed="false">Garder la formulation du protocole</button><button type="button" data-choice="corrected" aria-pressed="false">Valeur corrigée</button></div>';
    if (point.kind === 'field') input += '<label class="flabel" for="point-resolution">Comment l’incertitude a été levée</label><textarea id="point-resolution" class="fin" rows="2"></textarea>';
    if (point.path === 'arzt_arzt') input = `<p>Corrigez la présentation ou les points discutés dans la section Arzt–Arzt.</p><button type="button" data-open-section="arzt_arzt">Ouvrir Arzt–Arzt</button><label class="flabel" for="point-resolution">Résolution</label><textarea id="point-resolution" class="fin"></textarea>`;
    panel.innerHTML = `<div class="pcard"><div class="where">${esc(sectionTitles[point.section] || point.section)}${point.critical ? '<span class="critical">Critique</span>' : ''}${point.resolved ? '<span class="state ok">Résolu</span>' : ''}</div><h3>${esc(point.title || point.label)}</h3><div class="why"><b>Pourquoi</b><span>${esc(point.reason)}</span></div><div class="quote ${quote ? '' : 'none'}">${quote ? `« ${esc(quote)} »` : 'Aucun passage source associé à ce champ.'}${page ? `<button type="button" data-page="${page}">p.${page} ↗</button>` : ''}</div><div>${input}</div><p class="guard">Seuls les faits du protocole entrent dans le cas. Une information non rapportée reste inconnue.</p><div class="pactions"><button type="button" class="btn primary" id="validate-point" ${!this.canRevise() ? 'disabled' : ''}>Valider ${point.kind === 'question' ? 'la réponse' : 'le point'} <kbd>Entrée</kbd></button><button type="button" class="btn ghost" data-skip="1">Plus tard <kbd>N</kbd></button></div></div>`;
    this.choice = null; this.polarity = null;
    if (!this.canRevise()) for (const element of panel.querySelectorAll('input,select,textarea,[data-choice],[data-polarity]')) element.disabled = true;
  }
  summaries() {
    const r = this.record;
    return {location:[r.location.land,r.location.city,r.location.exam_date].filter(Boolean).join(' · '), patient:[r.patient.age_years !== null ? `${r.patient.age_years} ans` : 'Âge inconnu',r.patient.occupation_de].filter(Boolean).join(' · '),anamnesis:`${r.anamnesis.length} éléments · ${r.anamnesis.filter(item => item.polarity === 'unknown').length} inconnus`,diagnosis:r.diagnosis.suspected_de || 'Diagnostic non indiqué',examiner_questions:`${r.examiner_questions.length} questions`,arzt_arzt:`${r.arzt_arzt.discussion_points_de.length} points discutés`,fachbegriffe:`${r.fachbegriffe.length} termes`,outcome:result.find(([key]) => key === r.outcome.result)?.[1],unresolved_questions:`${r.unresolved_questions.length} questions`,field_uncertainties:`${r.field_uncertainties.length} incertitudes`,pedagogy:r.pedagogy.difficulty || 'À remplir par le médecin',pseudonymisation:r.pseudonymisation.removed_kinds.join(' · ') || 'Aucun retrait signalé'};
  }
  renderSections() {
    const summaries = this.summaries(), changes = compareFirst(this.initial,this.record), points = this.points();
    const boxes = [];
    for (const [key,rows] of fieldsFor(this.record,this.lands())) {
      const count = points.filter(point => point.section === key && !point.resolved).length;
      const state = key === 'pseudonymisation' ? ['free','Lecture seule'] : count ? ['open',`${count} à résoudre`] : changes.some(change => change.path.startsWith(key)) ? ['edit','Modifié'] : ['ok','Sans point ouvert'];
      const details = document.createElement('details'); details.className = 'acc'; details.dataset.section = key; details.open = this.open.has(key);
      details.addEventListener('toggle', () => { if (details.open) this.open.add(key); else this.open.delete(key); });
      details.innerHTML = `<summary><span class="caret">▸</span><span class="section-title"><b>${esc(sectionTitles[key])}</b><span>${esc(summaries[key] || 'Non indiqué')}</span></span><span class="state ${state[0]}">${esc(state[1])}</span></summary><div class="acc-body"><table class="rows"><tbody>${rows.map(field => {
        const value = getPath(this.record,field.path), flagged = points.some(point => !point.resolved && point.path === field.path);
        return `<tr data-row="${esc(field.path)}" class="${flagged ? 'flag' : ''}"><td class="id">${esc(field.item || '')}</td><td class="k">${esc(field.label)}${field.sub ? `<small>${esc(field.sub)}</small>` : ''}</td><td>${control(field,value,`data-field="${esc(field.path)}" data-kind="${field.kind}" ${!this.canRevise() || field.unknown ? 'disabled' : ''}`)}</td><td>${field.page ? `<button type="button" class="pg" data-page="${field.page}">p.${field.page}</button>` : ''}</td></tr>`;
      }).join('')}</tbody></table>${key === 'pseudonymisation' ? `<p class="help">${esc(this.record.pseudonymisation.notes.join(' · ') || 'Rapport de pseudonymisation de l’extraction.')}</p>` : ''}${this.listActions(key)}</div>`;
      boxes.push(details);
    }
    $('record-sections').replaceChildren(...boxes);
  }
  listActions(key) {
    if (!this.canRevise()) return '';
    const lists = ['anamnesis','examiner_questions','fachbegriffe','unresolved_questions'].includes(key) ? [key] : key === 'pedagogy' ? ['pedagogy.critical_pitfalls','pedagogy.pitfalls'] : [];
    return lists.map(list => `<div class="list-actions"><button type="button" class="btn small" data-add="${list}">Ajouter ${esc(list.includes('pitfall') ? 'un piège' : 'un élément')}</button>${getPath(this.record,list).map((item,i) => `<button type="button" class="btn small ghost danger" data-remove="${list}" data-index="${i}" ${this.removalBlocked(list,item) ? 'disabled title="Résolvez le point avant de retirer cet élément"' : ''}>Retirer ${esc(item.id || `le piège ${i+1}`)}</button>`).join('')}</div>`).join('');
  }
  renderSource() {
    const pages = this.source.pages, page = pages.find(page => page.page_number === this.page);
    $('source-pages').innerHTML = pages.map(page => `<button type="button" data-page="${page.page_number}" class="${this.points().some(point => !point.resolved && point.pages?.includes(page.page_number)) ? 'has-open' : ''}" aria-current="${page.page_number === this.page}">p.${page.page_number}</button>`).join('');
    $('mode-text').setAttribute('aria-pressed',String(this.mode === 'text')); $('mode-image').setAttribute('aria-pressed',String(this.mode === 'image'));
    $('page-image').hidden = this.mode !== 'image'; $('page-text').hidden = this.mode !== 'text';
    if (this.mode === 'image') { $('page-image').src = this.client.pageImageUrl(this.protocol.document_id,this.page); return; }
    if (!page) { $('page-text').textContent = 'Texte source indisponible.'; return; }
    const chars = codePoints(page.text), marks = markedRanges(page,this.record), free = uncoveredPassages([page],this.record);
    const ranges = [...marks,...free.map(passage => ({...passage,free:true}))].sort((a,b) => a.start-b.start);
    let text = '', cursor = page.start;
    for (const range of ranges) {
      text += esc(chars.slice(cursor,range.start).join(''));
      text += `<mark tabindex="0" role="button" aria-label="${range.free ? 'Voir le passage sans champ' : 'Voir le champ associé'}" ${range.free ? `class="free" data-passage="${range.id}"` : `class="${this.currentPoint()?.path === range.path || (this.currentPoint()?.holder && range.path.startsWith(this.currentPoint().holder + '.')) ? 'active' : ''}" data-source-path="${esc(range.path)}"`}>${esc(chars.slice(range.start,range.end).join(''))}</mark>`;
      cursor = range.end;
    }
    text += esc(chars.slice(cursor,page.end).join(''));
    $('page-text').innerHTML = `<div class="pno">Page ${this.page} / ${this.protocol.document?.page_count || this.protocol.page_to} · texte extrait</div>${page.start ? `<div class="ctx">${esc(chars.slice(0,page.start).join(''))}</div>` : ''}${this.page === this.protocol.page_from ? '<div class="cut">Début du protocole</div>' : ''}<div class="source-segment">${text}</div>${this.page === this.protocol.page_to ? '<div class="cut">Fin du protocole</div>' : ''}${page.end < chars.length ? `<div class="ctx">${esc(chars.slice(page.end).join(''))}</div>` : ''}`;
  }
  renderCoverage() {
    const passages = uncoveredPassages(this.source.pages,this.record), seen = new Set(this.source.seen_passages || []);
    $('coverage-items').innerHTML = passages.length ? passages.map(passage => `<div class="free-item ${seen.has(passage.id) ? 'seen' : ''}" data-cover="${passage.id}"><q>${esc(passage.text)}</q><div class="meta"><span>Sans correspondance automatique : à vérifier dans les champs.</span><span><button type="button" class="btn small ghost" data-page="${passage.page}" data-highlight-passage="${passage.id}">p.${passage.page} ↗</button>${seen.has(passage.id) ? '<span class="state ok">Vu</span>' : `<button type="button" class="btn small" data-seen="${passage.id}" ${!this.canRevise() ? 'disabled' : ''}>Marquer vu</button>`}</span></div></div>`).join('') : '<p class="help">Aucun passage sans correspondance textuelle détecté. Vérifiez toujours le sens des champs.</p>';
  }
  renderDiff() {
    const changes = compareFirst(this.initial,this.record);
    $('diff-count').textContent = String(changes.length);
    const fmt = value => value === null || value === undefined ? '∅' : typeof value === 'string' ? value : JSON.stringify(value);
    $('protocol-diff').innerHTML = changes.length ? `<ul>${changes.map(change => `<li><span class="path">${esc(change.path)}</span><span><del>${esc(fmt(change.before))}</del> → <ins>${esc(fmt(change.after))}</ins></span></li>`).join('')}</ul>` : '<p class="none">Aucun changement pour l’instant.</p>';
    $('protocol-history').innerHTML = `<ul>${(this.protocol.events || []).map(event => `<li>${esc(new Date(event.created_at).toLocaleString('fr-FR'))} · ${esc(event.event_type)} · ${esc(event.actor.name)}</li>`).join('')}${(this.protocol.reviews || []).map(review => `<li>${esc(review.reviewer_name)} · ${esc(review.stage)} · ${esc(review.decision)} · ${esc(review.notes)}</li>`).join('')}</ul>`;
  }
  renderDecision() {
    const open = blockers(this.record,this.stage), unseen = uncoveredPassages(this.source.pages,this.record).filter(passage => !(this.source.seen_passages || []).includes(passage.id)).length;
    $('decision-state').innerHTML = open.length ? `<b>${open.length} points ouverts</b> avant ${this.stage === 'owner' ? 'validation' : 'approbation'}` : `<b>Prêt</b>${unseen ? ` · ${unseen} passage(s) sans champ non vu(s)` : ''} · la version est enregistrée avec la décision`;
    const actions = $('decision-actions'); actions.innerHTML = '';
    const add = (label,action,className = '') => { const button = document.createElement('button'); button.type='button'; button.className=`btn ${className}`; button.textContent=label; button.dataset.action=action; actions.append(button); return button; };
    if (this.canDecide()) {
      add('Rejeter','reject','ghost danger'); add(this.stage === 'owner' ? 'Renvoyer au médecin' : 'Demander des corrections','request_changes');
      const approve=add(this.stage === 'owner' ? 'Valider en gold et passer au suivant' : 'Approuver et passer au suivant','approve','primary'); approve.disabled=open.length > 0 || this.pointDirty; approve.id='approve-protocol';
    } else $('decision-state').textContent = `Statut « ${statusLabel(this.protocol.status)} ». ${this.stage === 'owner' ? 'Validation propriétaire' : 'Relecture médecin'} · ${this.client.me.roles.map(role => ROLE_LABELS[role]).join(', ')}`;
    if (this.canRevise()) add('Enregistrer une version','save','ghost');
    if (this.protocol.status === 'extracted' && this.client.has('owner')) add('Envoyer en relecture','release','primary');
    $('decision-note-row').hidden = !this.noteMode; $('send-decision').textContent = this.noteMode === 'reject' ? 'Rejeter' : 'Envoyer la demande';
    $('pii-override-block').hidden = !(this.stage === 'owner' && this.canDecide() && this.protocol.pii.length);
    $('pii-card').hidden = this.protocol.pii.length === 0;
    $('pii-table').innerHTML = this.protocol.pii.map(finding => `<p>${esc(finding.kind)} · ${esc(finding.path)} · ${esc(finding.excerpt)}</p>`).join('');
    $('delete-protocol').hidden = !(this.client.has('owner') && this.canRevise());
  }
  update() { this.renderHeader(); this.renderDiff(); this.renderDecision(); }
  edit(event) {
    const element = event.target;
    if (element.closest?.('#active-point')) { this.pointDirty = true; this.update(); return; }
    if (!element.dataset?.field || !this.canRevise()) return;
    const value = readControl(element,element.dataset.kind);
    setPath(this.record,element.dataset.field,value);
    if (element.dataset.field.endsWith('.polarity') && value === 'unknown') setPath(this.record,element.dataset.field.replace(/polarity$/,'value_de'),null);
    this.update();
  }
  change(event) {
    if (event.target.dataset?.field) { this.edit(event); this.renderPoints(); this.renderSource(); this.renderCoverage(); if (event.target.dataset.field.endsWith('.polarity')) this.renderSections(); }
  }
  async click(event) {
    const element = event.target.closest?.('button,mark'); if (!element || element.disabled) return;
    const data = element.dataset;
    if (data.point) { if (this.pointDirty && !window.confirm('Abandonner la résolution en cours ?')) return; this.pointDirty=false; this.activeId=data.point; this.renderPoints(); this.followPointSource(); this.update(); return; }
    if (data.choice) { this.choice=data.choice; this.pointDirty=true; for (const choice of $('active-point').querySelectorAll('[data-choice]')) choice.setAttribute('aria-pressed',String(choice.dataset.choice === data.choice)); if ($('point-value')) $('point-value').disabled=data.choice === 'unknown'; this.update(); return; }
    if (data.polarity) { this.polarity=data.polarity; this.pointDirty=true; for (const choice of $('active-point').querySelectorAll('[data-polarity]')) choice.setAttribute('aria-pressed',String(choice.dataset.polarity === data.polarity)); this.update(); return; }
    if (data.skip) return this.nextPoint(Number(data.skip));
    if (element.id === 'validate-point') return this.perform(() => this.validatePoint());
    if (data.openSection) return this.openField(data.openSection);
    if (data.sourcePath) return this.openField(data.sourcePath);
    if (data.passage) { $('coverage-items').querySelector(`[data-cover="${data.passage}"]`)?.scrollIntoView({block:'center'}); return; }
    if (data.page) { this.page=Number(data.page); this.renderSource(); if (data.highlightPassage) $('page-text').querySelector(`[data-passage="${data.highlightPassage}"]`)?.classList.add('active'); return; }
    if (element.id === 'mode-text' || element.id === 'mode-image') { this.mode=element.id === 'mode-text' ? 'text' : 'image'; this.renderSource(); return; }
    if (element.id === 'queue-prev' || element.id === 'queue-next') return this.moveQueue(element.id === 'queue-next' ? 1 : -1);
    if (data.seen) return this.perform(async () => { await this.client.markReviewSeen(this.protocol.id,this.protocol.version,this.protocol.content_hash,data.seen); this.source.seen_passages=[...new Set([...(this.source.seen_passages || []),data.seen])]; this.renderCoverage(); this.renderDecision(); });
    if (data.add || data.remove) return this.editList(data);
    if (data.action === 'save') return this.perform(() => this.save());
    if (data.action === 'approve') return this.perform(() => this.decide('approve'));
    if (['reject','request_changes'].includes(data.action)) { this.noteMode=data.action; this.renderDecision(); $('decision-notes').focus(); return; }
    if (data.action === 'release') return this.perform(async () => { await this.save(); await this.client.releaseProtocol(this.protocol.id); await this.show(this.protocol.id); });
    if (element.id === 'send-decision') return this.perform(() => this.decide(this.noteMode));
    if (element.id === 'cancel-decision') { this.noteMode=null; $('decision-notes').value=''; this.renderDecision(); return; }
    if (element.id === 'delete-protocol') return this.perform(async () => { if (!window.confirm('Retirer ce protocole du circuit de relecture ? Son historique sera conservé.')) return; await this.client.deleteProtocol(this.protocol.id); this.record=clone(this.protocol.record); this.pointDirty=false; $('decision-notes').value=''; $('pii-override').value=''; location.hash=`#/documents/${this.protocol.document_id}`; });
    if (element.id === 'review-help-toggle') return this.help(true);
    if (element.id === 'close-review-help') return this.help(false);
  }
  openField(path) {
    const point = this.points().find(point => !point.resolved && (point.path === path || point.holder === path.replace(/\.[^.]+$/,'')));
    if (point) { if (this.pointDirty && !window.confirm('Abandonner la résolution en cours ?')) return; this.activeId=point.id; this.pointDirty=false; this.renderPoints(); this.followPointSource(); $('active-point').scrollIntoView({block:'center'}); }
    else { this.open.add(path.split('.')[0]); this.renderSections(); const row = $('record-sections').querySelector(`[data-row="${path}"]`) || $('record-sections').querySelector(`[data-section="${path.split('.')[0]}"]`); row?.classList.add('hl'); row?.scrollIntoView({block:'center'}); row?.querySelector('input,select,textarea')?.focus(); }
  }
  followPointSource() {
    const point=this.currentPoint();
    const linked=this.source.pages.find(page => markedRanges(page,this.record).some(mark => mark.path === point?.path || (point?.holder && mark.path.startsWith(point.holder + '.'))));
    this.page=point?.pages?.[0] || linked?.page_number || this.page; this.renderSource();
  }
  nextPoint(offset) {
    if (this.pointDirty && !window.confirm('Abandonner la résolution en cours ?')) return;
    const points=this.points(), index=points.findIndex(point => point.id === this.activeId);
    this.activeId=points[(index+offset+points.length)%points.length]?.id; this.pointDirty=false; this.renderPoints(); this.followPointSource(); this.update();
  }
  validatePoint() {
    if (!this.canRevise()) return;
    const point=this.currentPoint(); if (!point) return;
    if (['uncertainty','anamnesis'].includes(point.kind) && !this.choice) throw new Error('Choisissez comment résoudre ce point.');
    const element=$('point-value'), field=[...fieldsFor(this.record,this.lands()).values()].flat().find(field => field.path === point.path);
    const value=element ? readControl(element,field?.kind || 'text') : undefined;
    // A section-wide doubt is resolved by an explicit note, not by replacing its object.
    const draft=clone(this.record);
    resolvePoint(draft,point,{value,choice:this.choice || 'kept',polarity:this.polarity,resolution:$('point-resolution')?.value || ''});
    this.record=draft;
    this.pointDirty=false;
    this.activeId=this.points().find(candidate => !candidate.resolved && candidate.id !== point.id)?.id || point.id;
    this.render();
  }
  removalBlocked(list,item) { return Boolean(item.uncertainty || (list === 'unresolved_questions' && item.critical && !item.answer)); }
  editList(data) {
    if (!this.canRevise()) return;
    const list=data.add || data.remove, items=getPath(this.record,list);
    if (data.remove && this.removalBlocked(list,items[Number(data.index)])) throw new Error('Résolvez ce point avant de retirer l’élément.');
    if (data.remove) { if (!window.confirm('Retirer cet élément du brouillon ?')) return; items.splice(Number(data.index),1); }
    else {
      const prefix={anamnesis:'a',examiner_questions:'q',fachbegriffe:'t',unresolved_questions:'u'}[list]; let n=items.length+1; while (items.some(item => item.id === `${prefix}${String(n).padStart(2,'0')}`)) n++;
      const id=`${prefix}${String(n).padStart(2,'0')}`;
      const blank={anamnesis:{id,section:'sonstiges',label_de:'',value_de:null,polarity:'unknown',temporality:null,quote_de:null,source_pages:[],uncertainty:null},examiner_questions:{id,part:'general',question_de:'',expected_answer_de:null,candidate_answer_de:null,source_pages:[],uncertainty:null},fachbegriffe:{id,german:'',lay_german:null,french:null,asked:true},unresolved_questions:{id,text_fr:'',critical:true,answer:null}}[list] || {text_fr:'',related_item_ids:[]};
      items.push(blank);
    }
    this.open.add(list.split('.')[0]); this.render();
  }
  async save() {
    if (this.pointDirty) throw new Error('Validez le point en cours avant d’enregistrer.');
    if (!this.dirty()) return;
    validateDraft(this.record);
    const revised=await this.client.revise(this.protocol.id,this.protocol.version,this.protocol.content_hash,this.record);
    this.protocol=revised; this.record=clone(revised.record); this.update();
    this.notice(`Version ${revised.version} enregistrée.`);
  }
  async decide(decision) {
    if (!this.canDecide()) return;
    const notes=$('decision-notes').value.trim(), override=$('pii-override').value.trim(), stage=this.stage;
    if (decision !== 'approve' && !notes) throw new Error('Ajoutez une note pour expliquer la décision.');
    if (decision === 'approve' && (blockers(this.record,stage).length || this.pointDirty)) throw new Error('Résolvez tous les points avant approbation.');
    await this.save();
    const moved=await this.client.decide(this.protocol.id,stage,{version:this.protocol.version,protocol_hash:this.protocol.content_hash,decision,notes,...(stage === 'owner' && override ? {pii_override_note:override} : {})});
    this.protocol=moved; this.record=clone(moved.record); $('decision-notes').value=''; $('pii-override').value=''; this.noteMode=null;
    this.notice(`Décision enregistrée : ${statusLabel(moved.status)}.`);
    if (decision === 'approve') {
      const statuses=stage === 'owner' ? ['doctor_approved'] : ['doctor_review','changes_requested'];
      const queues=await Promise.all(statuses.map(status => this.client.protocols({status}))), next=sortedQueue(queues.flatMap(queue => queue.items)).find(item => item.id !== moved.id);
      this.active=false; location.hash=next ? `#/protocols/${next.id}` : '#/review';
      if (!next) this.notice('Relecture terminée : aucun protocole restant dans cette file.');
    } else this.render();
  }
  moveQueue(offset) {
    const index=this.queue.findIndex(item => item.id === this.protocol.id), next=this.queue[index+offset];
    if (index < 0 || !next) return;
    location.hash=`#/protocols/${next.id}`;
  }
  help(open) {
    $('review-shortcuts').hidden=!open;
    if (open) { this.returnFocus=document.activeElement; $('close-review-help').focus(); }
    else this.returnFocus?.focus();
  }
  key(event) {
    if (!this.active || $('main').getAttribute('aria-busy') === 'true') return;
    if (!$('review-shortcuts').hidden) {
      if (event.key === 'Escape') { event.preventDefault(); this.help(false); }
      if (event.key === 'Tab') { event.preventDefault(); $('close-review-help').focus(); }
      return;
    }
    const typing=/INPUT|TEXTAREA|SELECT/.test(event.target.tagName);
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') { event.preventDefault(); if (this.canRevise()) this.perform(() => this.save()); return; }
    if ((event.key === 'Enter' || event.key === ' ') && event.target.tagName === 'MARK') { event.preventDefault(); event.target.click(); return; }
    if (event.key === 'Enter' && event.target.tagName !== 'TEXTAREA' && event.target.tagName !== 'BUTTON' && (!typing || event.target.closest?.('#active-point'))) { event.preventDefault(); this.perform(() => this.validatePoint()); return; }
    if (typing || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === 'n' || event.key === 'N') { event.preventDefault(); this.nextPoint(event.shiftKey ? -1 : 1); }
    if (event.key === '[' || event.key === ']') { event.preventDefault(); this.page=Math.min(this.protocol.page_to,Math.max(this.protocol.page_from,this.page+(event.key === ']' ? 1 : -1))); this.renderSource(); }
    if (event.key === 'j' || event.key === 'k') this.moveQueue(event.key === 'j' ? 1 : -1);
    if (event.key === 'a') this.perform(() => this.decide('approve'));
    if (event.key === '?') this.help(true);
    if (event.key === 'Enter' && event.target.tagName === 'MARK') event.target.click();
  }
}
