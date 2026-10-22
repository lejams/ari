## Action 3b : examen en chaîne ElevenLabs (items 2 à 6)

Rédigée le 22 octobre 2026. L'item 1 (adaptateur ElevenLabs) est fusionné (PR #15).

**Fait.**
- Item 2. Moteur `application/services/exam_session.py`. `ExamPatient` reprend la sélection de faits de
  l'entraînement (même requête, même garde des références, même rendu par le code des phrases authored ;
  `patient.py` en expose désormais les trois étapes sans changer la requête d'entraînement).
  `ExamPatientResponseSchema` étend `PatientResponseSchema` de trois types non cliniques (`small_talk`,
  `clarification_request`, `hesitation`) et d'un champ dédié `free_text` (160 caractères), interdit
  pour tout autre type. `screen_free_text` refuse tout chiffre, nombre écrit, unité ou marqueur de
  durée, racine de vocabulaire médical (allemand et français) et mot du lexique du cas (faits et
  terminologie) ; un texte refusé est remplacé par une phrase neutre fixe, et l'appel garde la trace
  (`usage.free_text`, `usage.free_text_rejection`). Prompt `patient-exam-v1`.
  Socket `api/exam_socket.py` : micro ouvert, `SpeechSegmenter` (détection de parole par énergie sur
  trames de 20 ms), un tour à la fois et dans l'ordre (Whisper, patient, voix en flux).
- Item 3. Crédit d'un fait seulement après `audio.playback_completed` (`confirm_audio_delivered`,
  inchangé). Si l'apprenant parle pendant la réponse, le reste de l'audio n'est plus envoyé
  (`patient.audio_interrupted`), le navigateur coupe déjà sa lecture sur `user.speech_started` : rien
  n'est crédité. La phrase est quand même lue jusqu'au bout côté serveur pour que l'appel ElevenLabs
  soit enregistré avec son coût (invariant 9). Une réponse interrompue n'est pas rejouable ; une
  synthèse en échec technique l'est.
- Item 4. Persona `standard`, `anxious`, `talkative`, `terse` : champ optionnel `persona_variant` du
  scénario (absent des anciens scénarios, sérialisation et empreintes inchangées), renseigné par le
  générateur de brouillons. Elle porte : la voix, ses réglages (stabilité, similarité, style, vitesse)
  et des balises audio entre crochets, par persona (`ARI_ELEVENLABS_EXAM_PERSONAS`, demande du
  propriétaire du 22 octobre 2026), plus le ton des seules répliques non cliniques. Jamais les faits.
  Les balises n'acceptent que `[mot]` : aucun mot n'est ajouté aux phrases authored.
- Item 5. Évaluation finale existante ; rapport `exam-report-v1` dans `GET /api/sessions/{id}` d'un
  examen terminé : par tour, type de réponse, entendu ou non, faits choisis et entendus (nombres, pas
  les identifiants), texte libre accepté ou refusé, temps serveur ; synthèse : tours entendus, non
  entendus, échecs audio et transcription, faits entendus et non entendus, P50 et P95 du temps serveur
  fin de parole → premier audio (STT + patient + premier audio ; hors silence de fin et lecture
  navigateur ; inconnu si un appel manque).
- Item 6. `ARI_EXAM_ENGINE=openai_realtime|chain`, **par défaut `openai_realtime`** : rien ne change en
  production. Pile `chain_exam` v1 épinglée sur chaque session. Une session en chaîne refuse de
  reprendre si la chaîne est désactivée (jamais de bascule silencieuse). `api/realtime_socket.py`
  conservé.

**Découvert dans le code.** L'entraînement est déjà une chaîne (Whisper, sélection, TTS, accusé de
lecture) : le moteur d'examen la réutilise au lieu de la dupliquer. La persona n'existait que comme
texte libre (`persona`) et comme paramètre de génération (`persona_variant`), sans trace dans le
scénario publié. Le frontend gérait déjà le micro ouvert et l'arrêt de lecture : aucun changement web.

**Décisions prises dans mon périmètre.** Détection de parole côté serveur, par énergie, paramètres en
configuration (`ARI_EXAM_VAD_*`, hypothèses). Énoncés neutres de repli en allemand et en français.
Rapport exposé par l'API, pas encore affiché dans la page de résultats.

**Points remontés au propriétaire.** Voix : `3nMIMZ7RlGwsq1WLgxY3` pour les quatre personas en
attendant d'autres choix (section 6, point 2). Idées notées pour après la 3b : choix de la voix par
l'apprenant, accents régionaux liés au Land du cas (allemand standard par défaut). Le passage de
`ARI_EXAM_ENGINE` à `chain` en production est un changement de prompt et de modèle (section 6,
point 7) : il exige un rapport `patient-leakage@1` (action 8) sur `patient-exam-v1`.

**Mesures.** Aucune sur le service réel (décision du 20 octobre 2026). Latence et coût seront lus
dans le rapport d'examen et la page Coûts après la bascule.

**Reste ouvert.**
- Premier essai avec la vraie clé : protocole, et prise en compte effective de `voice_settings` à
  l'enregistrement de la voix et des balises par `eleven_v4_turbo`, non vérifiées.
- Réglage du seuil de détection de parole sur de vrais micros ; risque d'écho (la voix du patient
  captée par le micro déclencherait une interruption), atténué par l'annulation d'écho du navigateur.
- Affichage du rapport d'examen dans la page de résultats ; acceptation (1) à (4) de l'action 3
  (fuites, latence P95, interruption sur réseau réel).

**Commandes de validation exécutées.** `.venv/bin/pytest backend` (suite complète verte, dont
`tests/test_exam_chain.py`), `ruff check backend`, `mypy backend/src`, `lint-imports`.

**Revue.** Action hors liste Opus obligatoire ; revue Codex sur la PR (boucle du projet).
