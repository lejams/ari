# ARI : plan d'exécution pour Claude Code

Version 1.1, 22 octobre 2026 (1.0 du 15 octobre 2026, ajout de l'action 13 : tests et audit UI/UX). Ce document est l'instruction de travail. Il est autonome : il ne suppose pas l'accès aux rapports d'audit dont il est issu.

## 0. Comment utiliser ce document

- Les actions sont **ordonnées par dépendance réelle**. Ne pas commencer une action tant que ses dépendances ne sont pas fusionnées sur `main`.
- Une action = une branche, une PR, des tests verts, une note de fin (ce qui a été fait, ce qui a été découvert, ce qui reste ouvert).
- Avant toute action : lire `docs/`, `backend/pyproject.toml` (contrats import-linter), `.github/workflows/ci.yml`, `docs/CI_CD.md`, et les fichiers listés dans « Lire d'abord ». Relever les commandes réelles de test, lint, typage et migration et les utiliser telles quelles.
- Règle de périmètre : **aucune abstraction sans usage FSP immédiat**. Si une généralisation paraît élégante mais n'est pas requise par l'action en cours, ne pas la faire, la noter dans la note de fin.
- Règle d'arrêt : si une tâche exige de trancher un point listé en section 12 (« Points réservés au propriétaire »), s'arrêter et poser la question. Ne jamais décider seul sur ces points.
- Un seul développeur travaille sur ce dépôt, environ 15 h par semaine. Les PR doivent être petites et fusionnables indépendamment.

## 1. Contexte produit

ARI prépare des médecins non germanophones à la Fachsprachprüfung (FSP) allemande. Trois épreuves : conversation médecin-patient (anamnèse, orale), documentation écrite (Arztbrief), présentation du cas à un confrère (Arzt-Arzt, orale).

État du dépôt : monolithe Python (FastAPI) d'environ 20 000 lignes sous `backend/src/ari/`, couches `domain → application → infrastructure → api`, deux frontends sans framework (`web/`, `backoffice-web/`), deux bases PostgreSQL logiques (`platform` pour les apprenants et le registre clinique, `content` pour le pipeline documentaire), un worker, Docker Compose, Caddy. Migrations Alembic en deux chaînes (`backend/migrations/platform`, `backend/migrations/content`). Triggers d'immutabilité générés par `infrastructure/persistence/ddl.py`.

Principe fondateur à préserver partout : **le LLM propose, le code décide**. En mode entraînement, le LLM choisit des références de faits (`source_refs`) et le code rend des phrases rédigées et validées (`application/services/patient.py`). Le LLM ne publie jamais de contenu médical, ne met jamais à jour une maîtrise, ne choisit jamais un exercice.

Tous les chemins ci-dessous sont relatifs à `backend/src/ari/` sauf mention contraire.

## 2. Invariants non négociables

1. `ari.learning` n'importe aucun autre module `ari`, aucun SDK, aucune I/O. Vérifié par un contrat import-linter ajouté en action 6.
2. Toute sortie LLM utilisée par le métier passe par un schéma strict (`extra="forbid"`, `strict=True`) et le port `LLMProvider.generate_structured`. Pas de texte libre parsé par regex.
3. Les tables de preuves sont append-only : `evaluation_runs`, `learning_observations`, `recommendation_log`. Jamais de `merge` ou d'`UPDATE` dessus.
4. Une erreur technique (timeout, STT en échec, refus du fournisseur) ne produit **jamais** une observation négative sur l'apprenant.
5. Toute modification de prompt, de modèle ou de politique de sélection exige un rapport `ari.evals` sans nouvelle erreur critique avant promotion.
6. Aucun changement clinique par génération : âge, durée, symptôme, valeur, négation, traitement sont figés par le gold. Le LLM ne reformule que.
7. Les sessions épinglées sur une version exacte de scénario continuent de fonctionner après retrait de ce scénario.
8. Aucune donnée patient réelle ni aucun secret dans les tests, les fixtures ou les datasets d'évals.
9. Chaque appel IA enregistre un `ExecutionRecord` complet avec coût calculé. Usage manquant = inconnu visible, jamais zéro.
10. Le code existant qui fonctionne est conservé : `assessment-weighted-v1`, `practice-exact-answer-v1`, `srs-sm2-v1`, `placement-staircase-v1`, `program-rules-v2` ne sont jamais supprimés, seulement étendus ou versionnés.
11. **Le coût se minimise sous contrainte de qualité, jamais par coupure.** Aucune limite d'usage ne dégrade l'expérience d'un apprenant normal. Les limites sont structurelles (une session vocale à la fois, durée de l'épreuve, quota d'examens de l'offre) ou des coupe-circuits d'abus désactivés par défaut. Le coût baisse par routage de modèle, cache et pré-génération, validés par `ari.evals` : on retient la configuration la moins chère parmi celles qui passent les seuils de qualité. Aucun plafond en euros n'est fixé a priori.

## 3. Constantes et valeurs figées

| Clé | Valeur | Statut |
|---|---|---|
| Quota examens inclus par mois | 2 | Décidé |
| Prix abonnement | 35 € TTC | Décidé |
| Durée max d'une session vocale | durée du scénario (`educational_target.duration_minutes`) + 5 min de marge technique | Dérivé du contenu, marge en paramètre |
| Plafond de coût par apprenant | **aucun fixé a priori** (décision du 18 octobre 2026) ; coupe-circuit d'abus optionnel, désactivé par défaut ; l'écart à la médiane des apprenants est affiché, sans seuil | Décidé |
| TTS examen | ElevenLabs `eleven_v4_turbo` | Décidé |
| TTS phrases pré-générées | ElevenLabs `eleven_v4` | Décidé |
| Taille taxonomie FSP v1 | environ 30 compétences | Décidé |
| Relations entre compétences | `prerequisite`, `part_of` uniquement | Décidé |
| Modèle de maîtrise | `mastery-beta-v1` (Beta, oubli, statut sur borne basse) | Décidé |
| Seuil `mastered` | borne basse 80 % ≥ 0,75 + diversité (voir action 6) | Hypothèse, paramètre |
| Seuil `uncertain` | largeur d'intervalle > 0,35 | Hypothèse, paramètre |
| Demi-vies d'oubli | 30 j langue, 45 j structure | Hypothèse, paramètre |
| Fiabilités initiales | déterministe 1,0 ; LLM 0,5 ; auto-déclaration 0,2 ; placement 0,6 | Hypothèse, à remplacer par la mesure des évals |
| Mix de sélection | 55 remédiation / 25 consolidation / 20 découverte | Hypothèse, paramètre versionné |
| Latence examen, fin de parole → début audio | P95 ≤ 2,5 s objectif, 4 s plafond | Décidé |
| Coût cible session examen | ≤ 0,70 € | Hypothèse |

Toute valeur marquée « Hypothèse, paramètre » vit dans la configuration ou un fichier YAML versionné, jamais en dur dans le code.

## 4. Calendrier et jalons

Phase 1 (actions 1 à 8) : octobre 2026 à mi-février 2027. Phase 2a (actions 9 à 12) : mi-février à juin 2027. L'action 13 (UI/UX) se fait en deux passes : l'application avant J3, la landing avant J4.

| Jalon | Cible | Condition |
|---|---|---|
| J1 Coûts et quotas en production | fin novembre 2026 | actions 1 et 2 fusionnées |
| J2 Examen ElevenLabs v1 | mi-décembre 2026 | action 3 fusionnée, avant la dépréciation OpenAI Realtime (janvier 2027) |
| J3 Bêta fermée gratuite | mi-février 2027 | actions 1 à 8 fusionnées, passe « application » de l'action 13 faite |
| J4 Pilote payant | juin 2027 | actions 9 à 12 fusionnées, passe « landing » de l'action 13 faite |

---

## Phase 0 : cadrage (1 semaine)

### P0.1 Matrice capacité / promesse
Produire `docs/CAPABILITIES.md` : pour chaque format (anamnèse entraînement, anamnèse examen, Arzt-Arzt, Fachbegriffe, Arztbrief, test de niveau, carnet), indiquer : implémenté (oui/non), évaluateur (déterministe/LLM/aucun), modèle configuré, contenu publié disponible, limites connues. Lancer la suite de tests existante et noter les versions de référence (prompts, schémas, scorers).

### P0.2 Grille tarifaire réelle
Demander au propriétaire les tarifs du compte pour les modèles configurés dans `config.py` (notamment les identifiants `gpt-5.6-luna` et `gpt-5.6-terra`, dont le tarif n'est pas public) et le compte ElevenLabs (voix choisies, plan). Ne rien inventer : les valeurs inconnues restent `null` dans `pricing-v1.yaml` et apparaissent comme « coût inconnu ».

### P0.3 Inventaire des dépréciations
Documenter dans `docs/DEPENDENCIES.md` les dates annoncées par OpenAI pour `gpt-4o-mini-tts`, `gpt-realtime-mini` et `gpt-4o-mini-transcribe` (début 2027) et le plan de remplacement (actions 3 et 11).

---

## Phase 1 : fondations

### Action 1 : grille tarifaire et coût par exécution

**Objectif.** Chaque exécution IA porte un coût en micro-euros calculé à partir d'une grille versionnée.

**Lire d'abord.** `domain/models.py` (`ExecutionRecord`), `infrastructure/providers/openai/llm.py` (`_model_and_timeout`, usage), `infrastructure/persistence/platform/repository.py` (`record_execution`), `infrastructure/persistence/content/rows.py` (`ai_runs`, `add_ai_run`), `config.py`.

**Faire.**
1. `pricing/pricing-v1.yaml` : par fournisseur et modèle, prix par unité (`input_token`, `cached_input_token`, `output_token`, `audio_input_token`, `audio_output_token`, `stt_minute`, `tts_character`, `tts_minute`, `realtime_minute`), devise, date de relevé, minimum facturé (Groq : 10 s par requête STT). Valeurs inconnues = `null`.
2. `application/services/costing.py::cost_of(execution: ExecutionRecord, pricing: PricingTable) -> CostResult` : fonction pure, retourne `cost_micro_eur: int | None`, `pricing_version`, `unknown_components: list[str]`. Séparer jetons cachés et non cachés.
3. Migration additive `platform` : `executions.cost_micro_eur`, `executions.pricing_version`, `executions.attempt_ref` (nullable). Migration additive `content` : `ai_runs.cost_micro_eur`, `ai_runs.pricing_version`.
4. Calcul au moment de `record_execution` et `add_ai_run`. Rattacher les exécutions du test de niveau à l'apprenant (vérifier `application/services/placement.py`).
5. Vue back-office `backoffice_api/routes_admin.py` : coût par apprenant, par session, par opération, par jour ; coûts inconnus affichés séparément.

**Hors périmètre.** Réconciliation facture, batch API, quotas (action 2).

**Acceptation.** Tests unitaires par type d'usage (LLM avec et sans cache, STT avec minimum facturé, TTS, realtime, fake). Un usage sans tarif donne `cost_micro_eur = None` et une composante inconnue listée. `alembic check` propre sur les deux chaînes. Tous les tests existants verts.

---

### Action 2 : limites structurelles et coupe-circuit d'abus

**Objectif.** Empêcher les usages qui coûtent sans servir l'apprentissage (sessions simultanées, connexions oubliées ouvertes, scripts), sans jamais plafonner un apprenant normal. Pas de budget en euros fixé à l'avance : la minimisation du coût sous contrainte de qualité relève du routage, du cache et de la pré-génération (action 8 pour la mesure, action 11 et phase 3 pour l'optimisation).

**Lire d'abord.** `api/pause.py` (`ServicePauseMiddleware`), `api/realtime_socket.py`, `api/voice_socket.py`, `application/services/conversation.py`, `api/auth.py`, `api/ownership.py`, `infrastructure/persistence/costs.py` (action 1).

**Faire.**
1. Port `application/ports/usage.py::UsageLedger` : `exam_sessions(learner_id, month) -> int`, `active_sessions(learner_id) -> int`, `month_cost_micro_eur(learner_id, month) -> int | None` (coût inconnu = `None`, jamais zéro). Implémentation SQL sur `executions` et `sessions`.
2. Garde `application/services/usage_guard.py::UsageGuard` appliquée aux mêmes points d'entrée que `ServicePauseMiddleware` et au démarrage de session vocale :
   - une seule session vocale active par compte (409) ;
   - quota d'examens de l'offre, 2 par mois, valeur décidée (402, message avec la date de réinitialisation) ;
   - **coupe-circuit d'abus optionnel** `ARI_LEARNER_COST_CEILING_EUR`, **non défini par défaut** : s'il est défini, refus 429 au démarrage d'une nouvelle activité au-delà de ce coût mensuel. Aucune valeur par défaut.
3. Durée maximale d'une session vocale **dérivée du scénario** : `educational_target.duration_minutes` + `ARI_SESSION_GRACE_MINUTES` (5 par défaut, marge technique). Appliquée aux deux sockets avec message de fin propre ; l'évaluation finale se déclenche normalement.
4. Arrêt d'une session vocale sans aucune activité (parole de l'apprenant, réponse du patient, message de contrôle) après `ARI_VOICE_IDLE_TIMEOUT_SECONDS`. Une connexion temps réel oubliée ouverte est facturée en continu : c'est une économie sans effet sur la qualité.
5. Page Coûts du back-office : afficher pour chaque apprenant le **rapport à la médiane** des apprenants actifs de la période. Pas de seuil d'alerte codé en dur.
6. Paramètres : `ARI_EXAM_SESSIONS_PER_MONTH`, `ARI_LEARNER_COST_CEILING_EUR` (optionnel), `ARI_SESSION_GRACE_MINUTES`, `ARI_VOICE_IDLE_TIMEOUT_SECONDS`.

**Hors périmètre.** Réservation de budget au démarrage (sans plafond par défaut, inutile), tout choix de modèle ou de cache (actions 8, 11, phase 3).

**Acceptation.** Tests d'API : refus avec message pour la deuxième session simultanée et pour le troisième examen du mois, réinitialisation au changement de mois, coupe-circuit actif seulement s'il est configuré. Test de socket coupé à la durée dérivée du scénario avec évaluation finale produite, et coupé après inactivité. Test de deux sessions concurrentes sur un même compte. Rapport à la médiane visible dans la page Coûts.

---

### Action 3 : mode examen sur ElevenLabs (option A, chaîne contrôlée)

**Objectif.** Remplacer OpenAI Realtime par une chaîne STT → LLM patient (sélection de faits) → TTS ElevenLabs `eleven_v4_turbo` en streaming, en conservant la garantie « phrases cliniques authored ». OpenAI Realtime reste derrière le port comme repli jusqu'à sa dépréciation.

**Lire d'abord.** `application/services/patient.py` (`PatientSimulator`), `application/services/realtime_patient.py` (`FactAttributor`), `infrastructure/providers/openai/realtime.py`, `application/ports/` (ports TTS et Realtime), `web/voice.html`, `web/app.js`, `application/schemas.py` (`PatientResponseSchema`), prompts `patient-v3` et `patient-realtime-v1`.

**Étape 3a, spike (1 semaine, branche jetable).** Prototype minimal : VAD serveur ou client, STT (Whisper Groq ; comparer ElevenLabs Scribe), `PatientSimulator` existant, nouveau adaptateur `infrastructure/providers/elevenlabs/tts.py` implémentant `StreamingTTSProvider` en WebSocket streaming, lecture côté navigateur avec interruption (barge-in : arrêt de la lecture dès détection de parole). Mesurer sur réseau réel : latence fin de parole → premier octet audio (P50, P95), coût par session de 20 minutes, qualité perçue par 5 personnes en aveugle contre l'actuel. **Livrer un rapport `docs/spikes/exam-elevenlabs.md` et s'arrêter pour validation avant 3b.**

**Constat du 19 octobre 2026 (documentation ElevenLabs, à confirmer par le spike).** `eleven_v4_turbo` n'est servi que par le **WebSocket Text to Dialogue** (`/v1/text-to-dialogue/stream-input`) ; le WebSocket TTS classique le refuse. **Une seule voix par connexion**, pas d'annulation en cours de synthèse, texte retenu jusqu'à environ 40 caractères sauf `flush`, `keep_alive` toutes les 10 s. L'adaptateur est donc un client WebSocket avec un petit pool de connexions (une par voix), pas un simple appel HTTP. Décision du propriétaire le même jour : on reste sur ElevenLabs, modèle `eleven_v4_turbo`. Le protocole et les seuils sont dans `docs/spikes/exam-elevenlabs.md`, l'outil de mesure dans `scripts/spikes/`.

**Étape 3b, implémentation.**
1. Adaptateur ElevenLabs derrière `StreamingTTSProvider`, configuration par opération (`exam_tts_model`, `voice_id` par persona), timeout, retries bornés, coût enregistré via l'action 1 (unité `tts_character`).
2. Nouveau moteur d'examen `application/services/exam_session.py` : même schéma `PatientResponseSchema`, phrases cliniques authored, formulation libre autorisée **uniquement** pour un type de tour non clinique explicite (`small_talk`, `clarification_request`, `hesitation`), produite par le LLM dans un champ dédié et passée par une liste d'interdits (aucun chiffre, aucune unité, aucun terme du lexique médical du cas hors faits sélectionnés).
3. Crédit d'un fait « entendu » uniquement après accusé de lecture audio, comme en entraînement (`confirm_audio_delivered`).
4. Persona (standard, anxious, talkative, terse) portée par la voix et les consignes de formulation, jamais par les faits.
5. Fin de session : évaluation finale existante, preuves par tour, rapport d'examen.
6. `api/realtime_socket.py` conservé, désactivable par flag `ARI_EXAM_ENGINE=chain|openai_realtime`.

**Hors périmètre.** ElevenAgents hébergé (option B, uniquement si 3a échoue sur la latence perçue). Pré-génération TTS (action 11). Arzt-Arzt oral.

**Acceptation.** Les cinq propriétés : (1) suite `patient-leakage@1` de l'action 8 exécutée sur le mode examen, zéro fuite de fait non demandé, zéro invention ; (2) test en aveugle avec 5 utilisateurs, préférence majoritaire ou égalité ; (3) latence P95 ≤ 2,5 s mesurée, 4 s plafond ; (4) interruption fonctionnelle ; (5) rapport d'examen complet. Coût mesuré ≤ 0,70 € par session de 20 minutes. Tests avec fake ElevenLabs déterministe.

---

### Action 4 : corrections du cycle de vie du contenu

**Objectif.** Corriger trois défauts de sécurité éditoriale connus.

**Lire d'abord.** `infrastructure/cases/clinical_store.py` (`publish`, lignes du retrait automatique), `content/services/bundle_generator.py` (incrément de `case_version`), `content/services/bundle_drafting.py` (`BundleDrafting.run`), `content/domain/pii.py`, `content/services/workflow.py`, `content/registry_bridge.py`.

**Faire.**
1. Dans `ClinicalStore.publish`, retirer automatiquement tout scénario publié de la même lignée `(case_id, phase)` quelle que soit la `case_version` antérieure. Aujourd'hui le retrait ne vise que la même `case_version`, or le générateur l'incrémente à chaque révision.
2. Appliquer `pii.scan` aux brouillons générés dans `BundleDrafting.run` ; exposer les résultats comme bloqueurs à l'import dans le registre.
3. Interdire qu'un même compte prenne la décision médecin et la décision owner sur une même version de protocole (le bridge interdit déjà les deux approbations clinique et linguistique par un même compte ; étendre le principe au workflow protocole).

**Acceptation.** Test : révision 1 publiée, révision 2 publiée → révision 1 retirée, sessions épinglées sur la révision 1 toujours lisibles. Test : brouillon contenant un numéro de téléphone → bloqué à l'import. Test : même compte médecin et owner → refus. Tests existants de `test_clinical_registry.py`, `test_content_e2e.py`, `test_backoffice_api.py` verts.

---

### Action 5 : évaluations immuables

**Objectif.** Conserver toutes les analyses d'une session au lieu d'écraser la précédente.

**Lire d'abord.** `infrastructure/persistence/platform/repository.py` (`save_analysis`, usage de `merge`), `application/services/conversation.py` (ré-analyse), `api/` route `/api/sessions/{id}`, `infrastructure/persistence/ddl.py`.

**Faire.**
1. Table `evaluation_runs` append-only : `id`, `session_id`, `run_no`, `payload` JSONB, `schema_version`, `prompt_version`, `prompt_hash`, `model`, `model_returned`, `rubric_version`, `execution_id`, `created_at`. Trigger d'immutabilité via `ddl.py`.
2. `evaluations` devient un pointeur vers le run courant (`current_run_id`) ; champs existants conservés pour la compatibilité de lecture.
3. `save_analysis` insère un nouveau run et met à jour le pointeur, dans une transaction.
4. Migration de backfill : chaque évaluation existante devient `run_no = 1`.
5. Les observations de vocabulaire (actuellement supprimées puis réinsérées) : ne pas toucher dans cette action, noter pour l'action 7.

**Acceptation.** Test : deux analyses successives → deux runs conservés, pointeur sur le second, API `/api/sessions/{id}` inchangée. Backfill idempotent (rejouable sans doublon). Triggers vérifiés par test.

---

### Action 6 : noyau `ari.learning`

**Objectif.** Package pur, indépendant du domaine, testé par propriétés, avec un domaine jouet.

**Lire d'abord.** `backend/pyproject.toml` (contrats import-linter existants), `domain/srs.py` (style des algorithmes versionnés), `application/services/learner_model.py`, `application/services/program.py` (pour comprendre ce que la politique devra reproduire en action 9, sans rien copier de médical dans le noyau).

**Faire.**

`learning/model.py` :
```python
ObservationKind = Literal["success", "failure", "required_omission", "not_elicited",
                          "uninterpretable", "stt_suspect", "interrupted", "assisted"]

@dataclass(frozen=True, slots=True)
class Observation:
    id: str; learner_id: str; skill_id: str
    item_ref: str; attempt_ref: str
    kind: ObservationKind
    outcome: float | None        # 0..1 ; None si not_elicited / uninterpretable
    reliability: float           # 0..1
    source: Literal["deterministic", "llm", "self_report", "placement"]
    producer: str                # ex. "assessment-weighted-v1", "evaluation-v6@<model>@fsp-anamnesis@1"
    evidence: Mapping[str, Any]
    observed_at: datetime

@dataclass(frozen=True, slots=True)
class MasteryEstimate:
    skill_id: str; mean: float; lower: float; upper: float
    effective_n: float; distinct_items: int; distinct_contexts: int
    autonomous_n: float; last_observed_at: datetime | None
    status: Literal["unknown", "weak", "developing", "mastered", "recheck", "uncertain"]
    model_version: str
```
Plus `Skill`, `SkillEdge(kind: Literal["prerequisite", "part_of"], threshold: float)`, `SkillFramework`, `Candidate` (item_ref, skills visées avec poids, durée, difficulté, modalité, coût prévu), `Constraints` (temps disponible, modalités autorisées, items récents, budget), `Decision` (item choisi, alternatives, scores, raisons, policy_version, seed).

`learning/mastery.py` (`mastery-beta-v1`) :
- `alpha, beta` initialisés à `alpha_0 = beta_0 = 1` ou par un a priori fourni.
- Avant chaque mise à jour et à chaque lecture, oubli : `(alpha - alpha_0) * 2^(-Δt / H)`, idem pour `beta`, `H` par famille fourni par la politique.
- Mise à jour : `alpha += w * o`, `beta += w * (1 - o)`, `w = reliability * independence_weight`.
- `independence_weight` : plafond de poids par `attempt_ref` et par compétence (paramètre), poids réduit si même `item_ref` déjà observé (paramètre), `kind = "assisted"` compte dans `effective_n` mais pas dans `autonomous_n`.
- `kind in {"not_elicited", "uninterpretable", "interrupted"}` : aucune mise à jour. `stt_suspect` : mise à jour avec `reliability` divisée par 2 (paramètre).
- Une observation portant le même `attempt_ref` et le même `producer` qu'une observation antérieure **remplace** sa contribution (réévaluation), elle ne s'ajoute pas. Implémenter par recalcul depuis la liste complète, pas par soustraction.
- Intervalle crédible à 80 % (quantiles de la Beta ; `scipy` interdit dans le noyau, utiliser une approximation documentée ou une implémentation pure).
- Statuts : `mastered` si `lower >= 0.75` et `distinct_contexts >= 2` et `effective_n` couvre au moins 3 `attempt_ref` distincts et `autonomous_n >= 1` et aucune observation `failure` sur une compétence marquée critique dans les 14 derniers jours ; `uncertain` si `upper - lower > 0.35` ; `recheck` si `mastered` mais `now - last_observed_at > H` ; `weak` si `mean < 0.5` avec `effective_n >= 2` ; `developing` sinon ; `unknown` si aucune observation.

`learning/framework.py` : chargement d'une taxonomie (dict ou YAML parsé en amont, le noyau ne lit pas de fichier), validation : identifiants uniques, arêtes vers des compétences connues, graphe `prerequisite` acyclique, `part_of` sans cycle, hash de la taxonomie.

`learning/replay.py` : `replay(observations, framework, model_version, params, now) -> dict[skill_id, MasteryEstimate]`, déterministe.

`learning/clock.py` : horloge et générateur aléatoire injectés ; aucune lecture de `datetime.now()` ni de `random` global dans le package.

Contrat import-linter : `ari.learning` ne peut importer que la bibliothèque standard et `typing_extensions`. Ajouter au `pyproject.toml`.

Tests : propriétés (monotonie : une réussite n'abaisse jamais `mean` ; bornes `0 ≤ lower ≤ mean ≤ upper ≤ 1` ; oubli : `effective_n` décroît avec le temps sans observation ; idempotence : replay deux fois = même résultat ; remplacement : réévaluer le même `attempt_ref` ne double pas `effective_n`) ; domaine jouet de 5 compétences avec 2 prérequis dans `backend/tests/learning/`.

**Hors périmètre.** Politique de sélection (action 9), stockage (action 7), tout import FSP.

**Acceptation.** `pytest backend/tests/learning`, `mypy --strict` sur le package, `lint-imports` vert, aucune I/O (grep `open(`, `datetime.now`, `random.` vide dans le package).

---

### Action 7 : taxonomie FSP v1 et observations

**Objectif.** Rendre les compétences FSP explicites et convertir chaque résultat existant en observations append-only.

**Préalable humain.** Atelier de 4 h avec le médecin : liste d'environ 30 compétences, définition d'une erreur critique, prérequis stricts (peu nombreux). Sans ce livrable, ne pas commencer ; demander.

**Lire d'abord.** `domain/clinical.py` (`ANAMNESIS_SECTION_IDS`, `ClinicalAssessmentItem`, rubriques), `application/services/assessment.py` (`weighted_assessment`), `application/schemas.py` (`LanguageErrorSchema`, catégories fermées, `EvaluationOutputSchema`), `application/services/evaluation.py` (gardes de preuves), `domain/practice.py` (`assess_practice`, `not_matched`, `provisional`), `application/services/lexicon.py`, `application/services/placement.py`, `application/services/progress_axes.py`, `application/services/conversation.py` (`end_session`, `_build_metrics`).

**Faire.**
1. `skills/fsp/fsp-skills.v1.yaml` : familles `anamnese`, `communication`, `langue`, `lexique`, `comprehension_orale`, `arzt_arzt`, `arztbrief` ; par compétence : `id`, `label`, `family`, `importance` par épreuve, `critical: bool`, `half_life_days` ; arêtes `prerequisite` et `part_of`.
2. Migration `platform` (schéma `learning`) : `skill_frameworks`, `skills`, `skill_edges`, `learning_items` (`item_ref`, `domain`, `exercise_type`, `content_hash`, `difficulty`), `item_skills` (`item_ref`, `skill_id`, `evidence_key`, `weight`, `role: trained|assessed|prerequisite`), `learning_observations` append-only (colonnes de `Observation` + `framework_version`).
3. Commande de chargement idempotente par hash : `python -m ari.learning_cli load-framework skills/fsp/fsp-skills.v1.yaml`.
4. Génération déterministe de `item_skills` depuis les items de rubrique (`ClinicalAssessmentItem.dimension`), les sections d'anamnèse et les catégories d'erreurs des scénarios publiés. Distinguer `trained` et `assessed` : entraîner n'est pas mesurer.
5. `application/services/fsp_observations.py::FspLearningAdapter.observations_for(result) -> Sequence[Observation]` couvrant : `weighted_assessment` (item satisfait → `success`, item exigible manqué → `required_omission`, item non sollicité → `not_elicited`), couverture des sections, `language_errors` par catégorie (`llm`, fiabilité 0,5, preuve = numéros de tours et extrait cité, vérifier que le tour cité existe), `empathy`, `PracticeFeedback` (`not_matched` en exercice libre → `uninterpretable`, jamais `failure` ; en format fermé → `failure`), révisions du carnet (agrégées par compétence lexicale, jamais mot par mot), test de niveau (`placement`, portée limitée, fiabilité 0,6). Toute observation issue d'un `ExecutionRecord` en erreur → `uninterpretable`.
6. Appel en fin de `end_session`, `PracticeService.finish`, `PlacementService.finish`, dans la même transaction que le résultat.
7. Commande de backfill depuis `evaluation_runs` (action 5), idempotente (clé naturelle `attempt_ref + producer + skill_id + evidence_key`).

**Acceptation.** Taxonomie chargée et validée en CI. Chaque scénario publié du jeu de démonstration a au moins une compétence `assessed`. Tests de correspondance sur des évaluations fixtures : chaque observation a une preuve, aucune observation `failure` pour `not_matched` en exercice libre, aucune pour une exécution en erreur. Backfill rejouable sans doublon.

---

### Action 8 : cadre d'évaluation v0 (`ari.evals`)

**Objectif.** Mesurer la qualité des sorties LLM de façon reproductible, avec holdout scellé et gate de promotion.

**Lire d'abord.** `infrastructure/providers/` (fakes déterministes), `.github/workflows/promote.yml`, `application/prompting.py` (prompts versionnés et hashés), `tests/` (fixtures existantes, `debug.transcript` en mode fake).

**Faire.**
1. Module `evals/` dans le dépôt : `runner.py` (`python -m ari.evals run --suite <name>@<version> --config <yaml> --split dev|holdout`), `manifest.py` (hash par fichier, source, annotateurs, date, consentement, split), `scorers/` (déterministes ; juge LLM uniquement là où un scorer déterministe est impossible, avec sa propre suite de calibration), `report.py` (JSON : métriques globales et par segment, erreurs critiques listées, coût et durée du run, versions exactes, modèle retourné), `compare.py` (baseline vs candidat, cas qui changent de verdict, intervalles par bootstrap sur 3 répétitions).
2. Dossier `evals/datasets/<suite>/<version>/` avec `manifest.json`, JSONL, schéma Pydantic des annotations. Partition par **familles de cas**, jamais par lignes aléatoires. Holdout : jamais lu hors du runner en mode gate, jamais utilisé en few-shot (vérification par hash dans un test).
3. Suites initiales :
   - `patient-leakage@1` : faits révélés sans question correspondante, refus `out_of_scope` et `wrong_language`, invention. Exécutée sur le mode entraînement **et** le mode examen (action 3).
   - `evaluation-language@1` : 100 à 150 transcripts synthétiques à erreurs injectées (genre, cas, verbe, ordre des mots, section oubliée, mot français, erreur STT simulée), vérité connue par construction.
   - `evaluation-medical@1` : 30 à 50 transcripts réels annotés par le médecin (avec consentement), erreurs médicales et omissions exigibles.
   - `voice-sim@1` (apprenant simulé, ajout du 22 octobre 2026) : un agent joue l'apprenant de bout en bout sur la vraie chaîne vocale, sans humain. Il suit un script de questions en allemand par cas publié (questions attendues, questions hors sujet, une question dans une autre langue, interruptions volontaires du patient), synthétise chaque question en audio et l'envoie au moteur comme un micro réel, en entraînement (push-to-talk) et en examen (micro ouvert, VAD). Mesures par tour : latence fin de parole → premier octet audio (P50, P95, contre les seuils de la section 3), erreur de transcription (texte envoyé connu par construction), faits révélés contre faits demandés, invention, arrêt de lecture à l'interruption, crédit d'un fait seulement après accusé de lecture. Mesures par session : coût total depuis les `ExecutionRecord` (action 1), durée, nombre de tours. Deux cibles : en processus avec fakes (CI, à chaque commit) et contre l'environnement de staging sur réseau réel (workflow manuel, vrais modèles, 20 sessions par configuration). Compte d'évaluation dédié, sessions marquées comme évaluation et exclues des métriques et du programme des apprenants ; la façon de ne pas consommer le quota d'examens est à trancher dans l'action, jamais en le désactivant pour les apprenants. Aucune donnée réelle (invariant 8). Le rapport alimente la frontière coût / qualité (point 5bis) pour comparer les piles vocales (OpenAI Realtime, chaîne ElevenLabs, voix et modèles STT).
4. Le rapport calcule la **fiabilité mesurée par source et par producteur** (précision des observations `llm` contre l'annotation) : c'est la valeur qui remplacera le 0,5 de l'action 6.
5. `evals/gates.yaml` : seuils ; une seule nouvelle erreur critique bloque quelle que soit la moyenne.
5bis. **Frontière coût / qualité.** Le rapport donne, pour chaque configuration testée (modèle, prompt, routage), la qualité mesurée **et** le coût moyen par cas (donnée par l'action 1). `compare.py` classe les configurations qui passent tous les seuils par coût croissant et recommande la moins chère. C'est le mécanisme de « minimiser le coût sous contrainte de qualité » : un modèle moins cher n'est adopté que s'il passe les seuils, et le retour arrière est une variable d'environnement.
6. Workflow GitHub manuel sur vrais modèles ; exécution en mode fake à chaque commit ; gate dans `promote.yml` dès qu'un prompt, un modèle ou une politique change (détection par hash).

**Seuils initiaux de `gates.yaml` (à recalibrer après la première mesure).** Erreurs de langue : précision ≥ 0,85, rappel ≥ 0,70 par catégorie. Fausses erreurs dues au STT ≤ 5 %. Erreurs médicales critiques : rappel ≥ 0,90, cible 0,98. Hallucination de preuve (tour cité ne contenant pas l'erreur) ≤ 2 %. Fuite de faits ≤ 2 % des tours. Stabilité : ≤ 10 % de verdicts changeants sur 3 répétitions. Structure exploitable après un retry ≥ 99,5 %.

**Acceptation.** Rapport baseline produit sur `patient-v3`, `evaluation-v6` et le moteur d'examen de l'action 3, avec les vrais modèles. Rapport `voice-sim@1` produit sur staging pour les deux modes et les deux moteurs d'examen (`ARI_EXAM_ENGINE=chain|openai_realtime`). Rapport fake en CI. Le holdout n'apparaît dans aucun prompt (test par hash).

---

## Phase 2a : MVP vendable

### Action 9 : projection de maîtrise, API et politique de sélection v1

**Lire d'abord.** `application/services/learner_model.py`, `application/services/program.py` (`recommend_case`, `recommend_practice`, phases, créneaux), `application/services/next_actions.py`, `application/services/progress_axes.py`, `tests/test_program.py`, `web/` (page Progression).

**Faire.**
1. Table `mastery_state` (cache recalculable : `learner_id`, `skill_id`, `alpha`, `beta`, `model_version`, `framework_version`, `computed_at`), service de projection incrémentale, commande de recalcul complet, `GET /api/mastery` (moyenne, intervalle, statut, nombre de preuves, dernière observation, couverture des contextes).
2. Bloc `mastery` ajouté à la sortie de `learner_model.py` sans retirer les champs actuels.
3. `learning/policy.py` (`policy-v1`) : étape 1 éligibilité (publié et non invalidé, moteur exécutable, durée et modalité compatibles, prérequis stricts, droits et budget), étape 2 score :
   `score = Σ_s importance(s) × [a·(1 − mean(s)) + b·(upper − lower)(s) + c·recheck(s)] × ready(item) × difficulty_fit(item) − recency_penalty − cost_penalty`, `difficulty_fit` maximal pour un taux de réussite prédit entre 70 et 85 %. Mix 55/25/20 versionné. Jamais le même cas deux fois de suite, diversité des formats, fallback explicite si aucun candidat.
4. `learning/simulation.py` : apprenants synthétiques (vecteur de compétences vraies, modèle de réponse bruité, apprentissage sur exercice ciblé, oubli) pour des tests en CI sans LLM.
5. Table `recommendation_log` append-only : candidats, scores, choix, raisons, `policy_version`, `framework_version`, seed.
6. Branchement dans `recommend_case` et `recommend_practice` de `program.py` (devient `program-rules-v3`) en conservant les phases, les créneaux et le budget temps. Feature flag `ARI_POLICY=rules-v2|policy-v1` avec retour arrière.
7. Affichage des raisons dans l'interface apprenant (texte généré depuis les raisons structurées ; un LLM peut reformuler, jamais inventer une raison).

**Acceptation.** Simulations en CI : fausse maîtrise ≤ 5 %, lacune injectée détectée (statut `weak`) en ≤ 4 observations pertinentes, ≥ 70 % des recommandations de remédiation visent une lacune vraie, zéro violation de prérequis, budget temps respecté. Recalcul complet identique au calcul incrémental. `test_program.py` adapté sans perte de comportement visible. Page Progression inchangée.

---

### Action 10 : Arztbrief

**Préalable humain.** Rubrique `fsp-arztbrief@1` validée avec le médecin ; 30 lettres annotées pour la suite d'évals.

**Lire d'abord.** `content/services/bundle_generator.py` (`available_phases`), `domain/clinical.py` (phases, `arztbrief` déclaré), `content/domain/protocol.py` (`ProtocolRecord`, champs examen clinique, diagnostic, traitement), `domain/practice.py`.

**Faire.**
1. `QualificationReport` pour le format `arztbrief` : éligible seulement si le gold contient la matière nécessaire (anamnèse, examen clinique ou résultats, diagnostic de travail, traitement ou plan) ; sinon `eligible: with_enrichment` avec la liste des informations manquantes. Aucune invention.
2. Moteur guidé minimal : structure imposée (en-tête, anamnèse, examen, diagnostic, traitement, plan), champs attendus dérivés du gold, saisie texte.
3. Évaluateur LLM structuré contre la rubrique : verdict par champ (`present`, `incomplete`, `incorrect`, `missing`), preuve = extrait cité et champ du gold attendu, erreurs de langue par catégorie. Gardes : un extrait cité doit exister dans la lettre.
4. Mapping vers 3 à 5 compétences `arztbrief.*` via `FspLearningAdapter`.
5. Suite `arztbrief@1` dans `ari.evals`.

**Acceptation.** Refus explicite quand le gold est insuffisant. Seuils de la suite atteints sur le holdout. Coût mesuré ≈ 0,05 € par évaluation.

---

### Action 11 : qualification, critique, gold versionné et TTS pré-généré

**Lire d'abord.** `content/services/workflow.py` (`GOLD` dans `CLOSED`), `infrastructure/persistence/content/rows.py` (`gold_protocols`), `content/services/diff.py`, `content/services/bundle_drafting.py`, `infrastructure/cases/clinical_store.py`, `infrastructure/cases/clinical_catalog.py`.

**Faire.**
1. `QualificationReport` généralisé à tous les formats (anamnèse, Arzt-Arzt, Fachbegriffe, Arztbrief, simulation complète) : formats possibles, éligibilité (oui / non / avec enrichissement), faits sources, compétences cibles, informations manquantes, limites, variations autorisées, difficulté estimée, version de qualification. Calcul par règles sur les champs du `ProtocolRecord` ; le LLM peut proposer, le code applique.
2. Validateurs déterministes supplémentaires sur les brouillons : chaque nombre et unité d'une phrase patient figure dans la valeur du fait source ; aucun terme médical hors protocole (liste dérivée du gold).
3. Critique LLM distinct du générateur (prompt différent, modèle différent si disponible) : verdict par phrase `supported | contradicts | adds_information | unclear`, niveau CEFR, évaluabilité des critères. Le critique route vers revue allégée ou complète ; il ne publie jamais. Au lancement, revue complète systématique ; l'accord critique/relecteurs est mesuré et stocké.
4. Gold versionné : clé `(protocol_id, protocol_version)`, correction qui rouvre le cycle médecin + owner avec diff champ par champ, statut `superseded`. Job de propagation : brouillons et scénarios dérivés marqués `source_superseded` ; retrait automatique si un fait critique ou une valeur clinique change ; sinon file « à régénérer ». Sessions actives : interruption avec explication si erreur grave, terminables si correction mineure. Pins préservés.
5. Champ `derived_from` explicite sur chaque fait d'exercice (identifiant de l'item du protocole).
6. TTS pré-généré à la publication : pour chaque scénario publié, chaque phrase authored est synthétisée en `eleven_v4` et cachée par hash (texte, voix, modèle, version de configuration) ; le mode entraînement et le mode examen servent le cache quand il existe, le streaming sinon. Invalidation du cache sur changement de phrase ou de voix.

**Acceptation.** Correction d'un gold → dérivés marqués, scénarios retirés selon la gravité, pins préservés, sessions épinglées terminables. Accord critique/relecteurs mesuré sur 30 exercices. Taux de cache TTS mesuré sur une session d'entraînement ≥ 80 %. Coût de session d'entraînement mesuré ≤ 0,20 €.

---

### Action 12 : offre payante, RGPD, diagnostic intégré, pilote

**Points réservés avant de commencer.** Prestataire de paiement choisi ; conditions générales et politique de confidentialité validées par un juriste ; formulation du consentement à l'usage des transcripts ; prix des packs d'examens.

**Lire d'abord.** `application/services/accounts.py`, `api/auth.py`, `infrastructure/persistence/ddl.py` (triggers), `infrastructure/providers/email.py`, `application/services/placement.py`, `web/`.

**Faire.**
1. Paiement : webhooks idempotents (clé d'événement unique, traitement exactement une fois), droits et quotas reliés à l'action 2, packs d'examens, état d'abonnement visible, échec de paiement → dégradation propre, jamais de perte de données.
2. RGPD : consentements horodatés (produit, amélioration via transcripts), export des données, effacement par anonymisation (fonction privilégiée contournant les triggers append-only pour les champs texte libres, apprenant pseudonymisé, audio jamais conservé), registre des sous-traitants (OpenAI, Groq, ElevenLabs, hébergeur, paiement), `LogEmailSender` remplacé par un envoi réel.
3. Observabilité : suivi d'erreurs (Sentry ou équivalent), métriques `executions` (échecs des gardes, latence, coût P50/P95), bouton « cette correction est fausse » côté apprenant alimentant une file de tri.
4. Diagnostic d'entrée : test de niveau existant + mini-consultation, produisant des observations `placement` de portée limitée avec incertitude visible ; écart de plus d'un niveau entre oral et écrit signalé, pas tranché.
5. Parcours : onboarding → diagnostic → programme → exercice → feedback → suite, chaque recommandation avec ses raisons.
6. Protocole de mesure de l'apprentissage réel écrit avant le pilote : prétest et post-test sur cas inédits, contrôle différé à 30 jours, notation en aveugle.
7. Pilote de 20 à 30 apprenants.

**Acceptation.** Un nouvel utilisateur obtient diagnostic et programme en moins de 20 minutes. Effacement d'un compte vérifié par test (aucune donnée identifiante résiduelle, observations conservées pseudonymisées). Webhook rejoué → aucun double traitement. Aucune nouvelle erreur critique sur le holdout. Marge brute mesurée ≥ 75 % sur les 50 premiers payants. Rapport de cohorte produit.

---

### Action 13 : tests et audit UI/UX (application et landing)

**Objectif.** Vérifier que l'apprenant comprend et termine les parcours clés sans aide, et que la landing convertit un médecin visiteur en inscrit, avec des constats priorisés et des corrections en petites PR. Deux passes indépendantes : **application** (avant J3, la bêta) et **landing** (avant J4, le pilote payant).

**Dépend de.** Rien pour démarrer. La passe application se refait sur les écrans modifiés par les actions 3, 9, 10 et 12 quand ils arrivent.

**Outils.** Skills Claude Code à installer dans la session avant de commencer (ils ne sont pas dans le dépôt) :
- `cro-optimization` (dépôt founder-skills) pour la landing (`web/landing.html`, `web/login.html`) ;
- `ux-audit` ou `design-audit` pour l'application (`web/`) et, en second, le back-office (`backoffice-web/`).
Si un skill est introuvable, le dire et appliquer la même grille à la main, sans inventer son contenu.

**Lire d'abord.** `AGENTS.md` (section Frontend), `web/*.html` et `web/*.mjs`, `web/tests/` (dont `practice-e2e.mjs`, Playwright), `.github/workflows/ci.yml` (job e2e), `README.md` (ce qu'ARI promet et ne promet pas : pas de niveau CECRL certifié, pas de certification), `docs/CAPABILITIES.md`.

**Faire.**
1. Passe application (`ux-audit` ou `design-audit`), en mode fake puis sur un vrai compte de test : inscription, onboarding et profil, test de positionnement, programme de la semaine, consultation vocale en entraînement, examen, feedback et prochaines actions, lexique et révisions, progression. Sur mobile et sur ordinateur. Couvrir les états vides et d'erreur (catalogue vide, micro refusé, STT en échec, coupure réseau, limite d'une session vocale atteinte, quota d'examens épuisé), l'accessibilité (contraste, navigation clavier, libellés, taille des cibles tactiles) et la clarté des textes en français.
2. Passe landing (`cro-optimization`) : proposition de valeur en une phrase pour un médecin qui prépare la FSP, appel à l'action, preuves, objections (prix, temps, niveau d'allemand requis), friction jusqu'à la première consultation. Aucune promesse de réussite, de certification ou d'information réglementaire non sourcée.
3. Rapports : `docs/reviews/ux-app-audit.md` et `docs/reviews/ux-landing-cro.md`, chaque constat avec écran, capture, gravité (bloquant, majeur, mineur), correction proposée et effort. Les constats bloquants et majeurs deviennent des PR ; le reste est listé.
4. Tests : étendre `web/tests/practice-e2e.mjs` aux parcours de l'étape 1 en mode fake, avec captures en largeur mobile et ordinateur, exécutés dans le job e2e existant. Ajouter un contrôle d'accessibilité automatique sur les pages principales si une dépendance légère le permet, sinon le noter.
5. Test avec de vrais utilisateurs : 3 à 5 médecins de la cible, à voix haute, sur le parcours inscription → première consultation, sans aide. Noter où ils bloquent et le temps jusqu'à la première consultation.

**Points réservés au propriétaire.** Textes marketing et promesses de la landing, prix affichés et offres, tout retrait ou masquage d'un élément visible (préférence : réorganiser ou replier, jamais retirer ; section 6, point 6), changement d'identité visuelle.

**Acceptation.** Les deux rapports existent avec constats priorisés. Aucun constat bloquant ouvert sur les parcours de l'étape 1 avant J3. Les parcours de l'étape 1 sont couverts par l'e2e en mode fake, sur mobile et ordinateur, en CI. Le temps médian jusqu'à la première consultation est mesuré sur le test utilisateur.

---

## 5. Après la phase 2a (ne pas commencer sans demande explicite)

- Arzt-Arzt oral : réutiliser le moteur de l'action 3 avec une persona de médecin senior et une rubrique de présentation.
- Phase 3 : routage de modèles validé par les évals, cache de prompt, batch API pour le contenu, calibration des fiabilités et demi-vies sur données réelles, Elo ou IRT pour la difficulté des cas, BKT seulement si plusieurs milliers d'observations par compétence, pgvector pour le back-office, revue allégée si le critique est fiable.
- Phase 4 : extraction de `ari.learning` en package distribuable et second pack de domaine, seulement après validation commerciale d'ARI.

## 6. Points réservés au propriétaire (s'arrêter et demander)

1. Toute valeur de tarif, de prix de vente ou de quota non présente en section 3. Aucun plafond de coût par apprenant ne doit être introduit sans accord explicite.
2. Choix des voix ElevenLabs et nombre de voix par persona.
3. Contenu de la taxonomie (action 7), définition d'une erreur critique, rubriques (actions 7 et 10).
4. Passage de l'étape 3a à 3b (rapport de spike à valider).
5. Prestataire de paiement, textes juridiques, formulation des consentements.
6. Toute décision qui retire un comportement visible par l'apprenant ou le back-office.
7. Toute modification d'un prompt ou d'un modèle en production (exige un rapport `ari.evals`).
8. Format exact des épreuves FSP par Land (à documenter depuis les sources officielles des Ärztekammern avant de coder une simulation complète).

## 7. Modèle de note de fin d'action

```
## Action N : <titre>
Fait : ...
Découvert dans le code (écarts avec ce plan) : ...
Décisions prises dans mon périmètre : ...
Points remontés au propriétaire : ...
Mesures : (coût, latence, métriques d'évals, couverture)
Reste ouvert : ...
Commandes de validation exécutées : ...
```
