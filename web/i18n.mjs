/** Minimal UI locale switcher (FR / EN / DE). Pedagogical content stays German. */
export const LOCALES = ['fr', 'en', 'de'];

const STRINGS = {
  fr: {
    nav_home: "Aujourd'hui",
    nav_cases: 'Consultation',
    nav_exam: 'Examen',
    nav_progress: 'Progression',
    nav_vocab: 'Lexique',
    nav_history: 'Bilan',
    nav_programme: 'Programme',
    nav_suivi: 'Suivi',
    nav_pratique: 'Pratique',
    crumb_prefix: 'ARI / ',
    search_ph: 'Rechercher…',
    resume: 'Reprendre la séance',
    skip: 'Aller au contenu',
    brand_sub: 'Allemand médical',
    sidefoot: 'Votre pratique, un jour à la fois. Un entraînement, pas une certification.',
  },
  en: {
    nav_home: 'Today',
    nav_cases: 'Consultation',
    nav_exam: 'Exam',
    nav_progress: 'Progress',
    nav_vocab: 'Lexicon',
    nav_history: 'Review',
    nav_programme: 'Programme',
    nav_suivi: 'Follow-up',
    nav_pratique: 'Practice',
    crumb_prefix: 'ARI / ',
    search_ph: 'Search…',
    resume: 'Resume session',
    skip: 'Skip to content',
    brand_sub: 'Medical German',
    sidefoot: 'Your practice, one day at a time. Training, not certification.',
  },
  de: {
    nav_home: 'Heute',
    nav_cases: 'Konsultation',
    nav_exam: 'Prüfung',
    nav_progress: 'Fortschritt',
    nav_vocab: 'Lexikon',
    nav_history: 'Bilanz',
    nav_programme: 'Programm',
    nav_suivi: 'Verlauf',
    nav_pratique: 'Praxis',
    crumb_prefix: 'ARI / ',
    search_ph: 'Suchen…',
    resume: 'Sitzung fortsetzen',
    skip: 'Zum Inhalt',
    brand_sub: 'Medizinisches Deutsch',
    sidefoot: 'Ihr Training, Tag für Tag. Übung, keine Zertifizierung.',
  },
};

const VIEW_LABELS = {
  fr: { home: "Aujourd'hui", cases: 'Consultation', exam: 'Examen', progress: 'Progression', vocab: 'Lexique', history: 'Bilan', placement: 'Test de niveau', onboarding: 'Profil', warmup: 'Échauffement', exercise: 'Exercice' },
  en: { home: 'Today', cases: 'Consultation', exam: 'Exam', progress: 'Progress', vocab: 'Lexicon', history: 'Review', placement: 'Placement', onboarding: 'Profile', warmup: 'Warm-up', exercise: 'Exercise' },
  de: { home: 'Heute', cases: 'Konsultation', exam: 'Prüfung', progress: 'Fortschritt', vocab: 'Lexikon', history: 'Bilanz', placement: 'Einstufung', onboarding: 'Profil', warmup: 'Aufwärmen', exercise: 'Übung' },
};

let locale = 'fr';

export function getLocale() {
  return locale;
}

export function t(key) {
  return (STRINGS[locale] && STRINGS[locale][key]) || STRINGS.fr[key] || key;
}

export function viewLabel(name) {
  return (VIEW_LABELS[locale] && VIEW_LABELS[locale][name]) || VIEW_LABELS.fr[name] || name;
}

export function applyLocale(code) {
  if (!LOCALES.includes(code)) code = 'fr';
  locale = code;
  if (document.documentElement) document.documentElement.lang = code;
  document.querySelectorAll('[data-i18n]').forEach((el) => {
    const key = el.getAttribute('data-i18n');
    const value = t(key);
    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') el.placeholder = value;
    else el.textContent = value;
  });
  document.querySelectorAll('.lang-switch [data-lang]').forEach((btn) => {
    btn.classList.toggle('on', btn.getAttribute('data-lang') === code);
    btn.setAttribute('aria-pressed', String(btn.getAttribute('data-lang') === code));
  });
  const crumb = document.getElementById('crumb-view');
  if (crumb && crumb.dataset.view) crumb.textContent = viewLabel(crumb.dataset.view);
  try { localStorage.setItem('ari-lang', code); } catch { /* ignore */ }
  if (typeof document.dispatchEvent === 'function' && typeof CustomEvent === 'function') {
    document.dispatchEvent(new CustomEvent('ari:locale', { detail: { locale: code } }));
  }
}

export function initLocale() {
  let saved = 'fr';
  try { saved = localStorage.getItem('ari-lang') || 'fr'; } catch { /* ignore */ }
  applyLocale(LOCALES.includes(saved) ? saved : 'fr');
  document.querySelectorAll('.lang-switch [data-lang]').forEach((btn) => {
    btn.addEventListener('click', () => applyLocale(btn.getAttribute('data-lang')));
  });
}
