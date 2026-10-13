// Deterministic presentation helpers; the server remains authoritative for approval.
export const clone = value => JSON.parse(JSON.stringify(value));
export const getPath = (record, path) => path.split('.').reduce((value, key) => value?.[key], record);
export function setPath(record, path, value) {
  const keys = path.split('.'), key = keys.pop();
  keys.reduce((holder, part) => holder[part], record)[key] = value;
}
export const stageFor = protocol => protocol.status === 'doctor_approved' ? 'owner' : 'doctor';
export function sortedQueue(items) {
  return [...items].sort((a, b) => (a.source_text_length ?? Infinity) - (b.source_text_length ?? Infinity) || a.id.localeCompare(b.id));
}
const labels = {patient: 'Motif de consultation', diagnosis: 'Diagnostic', arzt_arzt: 'Arzt–Arzt', outcome: 'Résultat'};
const mainFields = {patient: 'patient.presenting_complaint_de', diagnosis: 'diagnosis.suspected_de', arzt_arzt: 'arzt_arzt.presentation_summary_de', outcome: 'outcome.result'};
export function pointsFor(record, stage, initial = record) {
  const points = new Map();
  for (const source of [initial, record]) {
    for (const key of Object.keys(labels)) if (source[key]?.uncertainty) points.set(key, {id: key, kind: 'uncertainty', label: labels[key], reason: source[key].uncertainty, path: mainFields[key], holder: key, section: key});
    for (const section of ['anamnesis', 'examiner_questions']) for (const original of source[section] || []) {
      if (!original.uncertainty) continue;
      const index = record[section].findIndex(item => item.id === original.id);
      if (index < 0) continue;
      const item = record[section][index];
      points.set(`${section}.${item.id}`, {id: `${section}.${item.id}`, kind: section === 'anamnesis' ? 'anamnesis' : 'uncertainty', label: item.label_de || item.question_de, reason: original.uncertainty, path: `${section}.${index}.${section === 'anamnesis' ? 'value_de' : 'question_de'}`, holder: `${section}.${index}`, section, quote: item.quote_de, pages: item.source_pages});
    }
    for (const original of source.field_uncertainties || []) {
      const index = record.field_uncertainties.findIndex(item => item.path === original.path);
      if (index < 0 || original.resolution) continue;
      points.set(`field.${original.path}`, {id: `field.${original.path}`, kind: 'field', label: fieldLabel(original.path), reason: original.reason_fr, path: original.path, holder: `field_uncertainties.${index}`, section: original.path.split('.')[0]});
    }
    for (const original of source.unresolved_questions || []) {
      if (!original.critical || original.answer) continue;
      const index = record.unresolved_questions.findIndex(item => item.id === original.id);
      if (index < 0) continue;
      points.set(`question.${original.id}`, {id: `question.${original.id}`, kind: 'question', label: 'Question du médecin', title: original.text_fr, reason: 'L’IA demande une réponse du médecin avant approbation.', path: `unresolved_questions.${index}.answer`, section: 'unresolved_questions', critical: true});
    }
    if (!source.pedagogy?.difficulty) points.set('difficulty', {id: 'difficulty', kind: 'required', label: 'Difficulté', reason: 'Champ réservé au médecin, obligatoire avant approbation.', path: 'pedagogy.difficulty', section: 'pedagogy'});
    if (stage === 'owner' && !source.location?.land) points.set('land', {id: 'land', kind: 'required', label: 'Land', reason: 'Le Land doit être renseigné avant validation en gold.', path: 'location.land', section: 'location'});
  }
  return [...points.values()].map(point => ({...point, resolved: point.kind === 'field' ? Boolean(getPath(record, `${point.holder}.resolution`)) : ['uncertainty', 'anamnesis'].includes(point.kind) ? !getPath(record, `${point.holder}.uncertainty`) : Boolean(getPath(record, point.path))}));
}
export function resolvePoint(record, point, {value, choice = 'kept', polarity, resolution = ''} = {}) {
  if (point.kind === 'anamnesis') {
    const item = getPath(record, point.holder);
    if (choice === 'unknown') { item.polarity = 'unknown'; item.value_de = null; }
    else {
      if (!String(value ?? '').trim()) throw new Error('Renseignez la valeur rapportée dans le protocole.');
      if (choice === 'corrected' && item.polarity === 'unknown' && !['present', 'absent'].includes(polarity)) throw new Error('Choisissez si la valeur est présente ou absente.');
      item.value_de = String(value).trim();
      if (choice === 'corrected' && polarity) item.polarity = polarity;
    }
    item.uncertainty = null;
  } else {
    if (point.kind === 'question' && !String(value ?? '').trim()) throw new Error('Ajoutez une réponse fondée sur le protocole.');
    if (point.kind === 'required' && !value) throw new Error('Choisissez une valeur.');
    if (value !== undefined) setPath(record, point.path, value === '' ? null : value);
    if (point.kind === 'field') {
      if (!resolution.trim()) throw new Error('Expliquez la résolution de cette incertitude.');
      setPath(record, `${point.holder}.resolution`, resolution.trim());
    } else if (point.kind === 'uncertainty') setPath(record, `${point.holder}.uncertainty`, null);
  }
}
export function validateDraft(record) {
  for (const item of record.anamnesis || []) {
    if (item.polarity === 'unknown' && item.value_de !== null) throw new Error(`${item.label_de} : une valeur inconnue doit rester vide.`);
    if (item.polarity !== 'unknown' && !item.value_de?.trim()) throw new Error(`${item.label_de} : renseignez une valeur ou choisissez « inconnu ».`);
  }
}
export function sourceMarks(record) {
  const marks = [];
  const add = (value, path, section, pages) => { if (typeof value === 'string' && value.trim().length >= 3) marks.push({value, path, section, pages}); };
  add(record.patient.presenting_complaint_de, 'patient.presenting_complaint_de', 'patient');
  add(record.diagnosis.suspected_de, 'diagnosis.suspected_de', 'diagnosis');
  for (const section of ['anamnesis', 'examiner_questions', 'fachbegriffe']) record[section].forEach((item, i) => {
    for (const key of ['quote_de', 'value_de', 'question_de', 'german']) add(item[key], `${section}.${i}.${key}`, section, item.source_pages);
  });
  for (const key of ['presentation_summary_de', 'discussion_points_de']) {
    const values = record.arzt_arzt[key];
    (Array.isArray(values) ? values : [values]).forEach(value => add(value, `arzt_arzt.${key}`, 'arzt_arzt'));
  }
  return marks;
}
// Offsets exchanged with Python are Unicode code-point offsets, not UTF-16 indices.
export const codePoints = text => Array.from(text);
export function markedRanges(page, record) {
  const chars = codePoints(page.text), text = chars.slice(page.start, page.end).join(''), found = [];
  for (const mark of sourceMarks(record)) {
    if (mark.pages?.length && !mark.pages.includes(page.page_number)) continue;
    let from = 0, index;
    while ((index = text.indexOf(mark.value, from)) !== -1) {
      const start = page.start + codePoints(text.slice(0, index)).length;
      found.push({...mark, start, end: start + codePoints(mark.value).length});
      from = index + mark.value.length;
    }
  }
  const result = [];
  for (const mark of found.sort((a, b) => a.start - b.start || b.end - a.end)) if (!result.some(previous => mark.start < previous.end)) result.push(mark);
  return result;
}
export function uncoveredPassages(pages, record) {
  const free = [];
  for (const page of pages || []) {
    const chars = codePoints(page.text), ranges = markedRanges(page, record);
    let offset = page.start;
    for (const line of chars.slice(page.start, page.end).join('').split(/(?<=\n)/)) {
      const length = codePoints(line).length, start = offset; offset += length;
      // Keep an entire unmatched line as a candidate; matches are not a semantic coverage claim.
      if (line.trim() && !ranges.some(mark => mark.start < offset && mark.end > start)) {
        const leading = codePoints(line).findIndex(char => !/\s/.test(char));
        const end = offset - codePoints(line.match(/\s*$/)?.[0] || '').length;
        free.push({id: `${page.page_number}:${start + leading}:${end}`, page: page.page_number, start: start + leading, end, text: line.trim()});
      }
    }
  }
  return free;
}
export function compareFirst(first, current) {
  const changed = [];
  const walk = (before, after, path = '') => {
    if (JSON.stringify(before) === JSON.stringify(after)) return;
    if (before && after && typeof before === 'object' && typeof after === 'object' && !Array.isArray(before) && !Array.isArray(after)) for (const key of new Set([...Object.keys(before), ...Object.keys(after)])) walk(before[key], after[key], path ? `${path}.${key}` : key);
    else changed.push({path, before, after});
  }; walk(first, current); return changed;
}
export const fieldLabel = path => ({'location.land': 'Land', 'location.city': 'Ville', 'location.exam_body': 'Ärztekammer', 'location.exam_date': 'Mois', 'location.specialty': 'Spécialité', 'patient.age_years': 'Âge', 'patient.sex': 'Sexe', 'diagnosis.suspected_de': 'Diagnostic suspecté', 'outcome.result': 'Résultat', arzt_arzt: 'Arzt–Arzt'}[path] || path);
