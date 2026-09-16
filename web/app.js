import { PcmPlaybackTracker } from "./audio-delivery.mjs";
import { allowsInCallHelp, voiceLaunchIntent } from "./voice-presentation.mjs";

const launchIntent = voiceLaunchIntent(location.search, localStorage.getItem("ari.current_session_id"));

const state = {
  learnerId: localStorage.getItem("ari.learner_id"),
  storedSessionId: launchIntent.sessionId,
  requestedCaseId: launchIntent.caseId,
  requestedLearningMode: launchIntent.mode,
  cases: [],
  case: null,
  sessionId: null,
  socket: null,
  audioContext: null,
  stream: null,
  source: null,
  worklet: null,
  sending: false,
  ending: false,
  startedAt: null,
  timer: null,
  providerMode: "fake",
  patientDraft: "",
  completedTurns: 0,
  persistedTurns: 0,
  pcmRemainder: new Uint8Array(),
  playbackEndTime: 0,
  playbackSources: new Set(),
  voiceStackId: null,
  failedTtsTurnId: null,
  userSpeaking: false,
  lastPatientTurnNode: null,
  patientTurnNodes: new Map(),
  callConnected: false,
  learningMode: "training",
  examActive: false,
  deferredTurns: [],
  openMic: false,
};

const $ = (id) => document.getElementById(id);
const LANGUAGE_LABELS = { "de-DE": ["Deutsch", "allemand"], "fr-FR": ["Français", "français"], "en-US": ["English", "anglais"] };
const caseLanguage = () => (state.case?.language || "de-DE").split("-")[0];
const caseKey = item => `${item.id}@${item.version}@${item.training_snapshot?.scenario_id || ""}@${item.training_snapshot?.scenario_version || ""}`;

function sendVoiceControl(event) {
  if (!state.ending && state.socket?.readyState === WebSocket.OPEN)
    state.socket.send(JSON.stringify(event));
}
const pcmPlayback = new PcmPlaybackTracker(sendVoiceControl);

function cancelPipelinePlayback() {
  pcmPlayback.cancelAll();
  state.playbackSources.forEach((source) => {
    try { source.stop(); } catch (_) { /* The source may already have ended. */ }
  });
  state.playbackSources.clear();
  state.pcmRemainder = new Uint8Array();
  state.playbackEndTime = state.audioContext?.currentTime || 0;
}

function applySessionVoice(session) {
  state.voiceStackId = session.voice_stack_id;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    signal: AbortSignal.timeout(20000),
    ...options,
  });
  if (!response.ok) {
    const error = new Error((await response.json()).detail || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function setStatus(text, live = false) {
  $("status").textContent = text;
  $("audio-state").textContent = live ? "La conversation est en cours." : "";
  $("status-dot").classList.toggle("live", live);
}

function setPartial(text) {
  if (!state.examActive) $("partial").textContent = text;
}

function setExamPresentation(active) {
  document.body.classList.remove("captions-off");
  $("subtitles-toggle").setAttribute("aria-pressed", "true");
  $("subtitles-toggle").textContent = "Masquer les sous-titres";
  state.examActive = active;
  document.body.classList.toggle("exam-active", active);
  $("subtitles-toggle").hidden = active || state.learningMode !== "training";
  $("debug-form").classList.toggle("hidden", state.providerMode !== "fake" || !state.case);
  if (active) {
    $("transcript").replaceChildren();
    $("partial").replaceChildren();
  }
}

function syncLearningModeButtons() {
  document.querySelectorAll("[data-learning-mode]").forEach((button) => {
    const selected = button.dataset.learningMode === state.learningMode;
    button.setAttribute("aria-pressed", String(selected));
    button.disabled = Boolean(state.sessionId);
  });
  $("debug-form").classList.toggle("hidden", state.providerMode !== "fake" || !state.case);
}

function addTurn(role, text) {
  if (state.examActive) {
    state.deferredTurns.push({ role, text });
    return null;
  }
  $("transcript").querySelector(".empty")?.remove();
  const node = document.createElement("div");
  node.className = `turn ${role}`;
  node.lang = caseLanguage();
  const label = document.createElement("span");
  label.textContent = role === "user" ? "Vous" : "Patient";
  node.append(label, document.createTextNode(text));
  $("transcript").append(node);
  $("transcript").scrollTop = $("transcript").scrollHeight;
  return node;
}

function clearTranscript() {
  $("transcript").replaceChildren();
  state.deferredTurns = [];
  if (state.examActive) return;
  const empty = document.createElement("div");
  empty.className = "empty";
  empty.textContent = "Le transcript apparaîtra ici pendant la conversation.";
  $("transcript").append(empty);
  setPartial("");
  state.lastPatientTurnNode = null;
  state.patientTurnNodes.clear();
}

function renderPersistedTranscript(turns) {
  clearTranscript();
  turns.forEach((turn) => {
    addTurn("user", turn.user_text);
    const incomplete = turn.provider_response_status !== "completed";
    if (turn.patient_text) {
      addTurn(
        "patient",
        incomplete
          ? `${turn.patient_text} [réponse incomplète — exclue de l’évaluation]`
          : turn.patient_text,
      );
    } else if (incomplete) {
      addTurn("patient", "[Réponse interrompue ou indisponible]");
    }
  });
}

function startTimer() {
  state.startedAt = Date.now();
  state.timer = setInterval(() => {
    const total = Math.floor((Date.now() - state.startedAt) / 1000);
    $("timer").textContent = `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
  }, 1000);
}

async function ensureLearner() {
  const profile = await api("/api/profile").catch(error => { if (error.status !== 404) throw error; return null; });
  if (profile) { state.learnerId = profile.id; localStorage.setItem("ari.learner_id", profile.id); return; }
  if (state.learnerId) {
    try {
      await api(`/api/learners/${state.learnerId}/goal`);
      return;
    } catch (error) {
      if (error.status !== 404) throw error;
      state.learnerId = null;
      localStorage.removeItem("ari.learner_id");
    }
  }
  const learner = await api("/api/learners", {
    method: "POST",
    body: JSON.stringify({ target_cefr: "C1" }),
  });
  state.learnerId = learner.id;
  localStorage.setItem("ari.learner_id", learner.id);
}

function renderCase(selectedCase) {
  state.case = selectedCase;
  $("case-title").textContent = selectedCase.title;
  $("case-summary").textContent = selectedCase.public_summary;
  const [native, french] = LANGUAGE_LABELS[selectedCase.language] || [selectedCase.language, selectedCase.language];
  $("case-language").textContent = `${native} · FSP`;
  $("case-goal").textContent = "Entraînement · niveau non mesuré";
  $("simulation-eyebrow").textContent = `Simulation clinique en ${french}`;
  $("simulation-intro").textContent =
    "Parlez naturellement. À la fin, vous recevez un feedback FSP bref, mesurable et relié à votre transcript.";
  $("debug-input").placeholder = `Mode fake : simuler une phrase en ${french}`;
  const caseValue = caseKey(selectedCase);
  if ($("case-select").value !== caseValue) $("case-select").value = caseValue;
}

function showCallActions({ canStart, canEnd, canRetry, canCreateNew }) {
  $("start").classList.toggle("hidden", !canStart);
  $("start").disabled = !canStart;
  $("end").classList.toggle("hidden", canRetry || canCreateNew);
  $("end").disabled = !canEnd;
  $("retry-analysis").classList.toggle("hidden", !canRetry);
  $("new-session").classList.toggle("hidden", !canCreateNew);
  // Redoing the same case right after the feedback is where the correction sticks.
  const knownCase = state.cases.some((item) => item.id === state.case?.id && item.version === state.case?.version);
  $("retry-case").classList.toggle("hidden", !(canCreateNew && knownCase && ["training", "exam"].includes(state.learningMode)));
  if (canStart || canRetry || canCreateNew) $("talk").classList.add("hidden");
}

async function restoreSession() {
  if (!state.storedSessionId) return false;
  try {
    const session = await api(`/api/sessions/${state.storedSessionId}`);
    const selectedCase = state.cases.find(
      (item) => item.id === session.case_id && item.version === session.case_version &&
        (!session.training_snapshot?.scenario_id || item.training_snapshot?.scenario_id === session.training_snapshot.scenario_id),
    ) || {id: session.case_id, version: session.case_version, title: `${session.case_id}@${session.case_version}`,
      public_summary: "Session historique épinglée. Le scénario n’est plus proposé aux nouveaux exercices.",
      language: "de-DE", mode: "fsp"};
    state.sessionId = session.id;
    state.learnerId = session.learner_id;
    localStorage.setItem("ari.learner_id", session.learner_id);
    applySessionVoice(session);
    renderCase(selectedCase);
    state.learningMode = session.learning_mode || "unknown";
    $("learning-mode").value = state.learningMode;
    $("learning-mode").disabled = true;
    syncLearningModeButtons();
    if (state.learningMode === "exam" && ["created", "active"].includes(session.status)) setExamPresentation(true);
    else setExamPresentation(false);
    renderPersistedTranscript(session.turns);
    state.persistedTurns = session.turns.length;
    $("case-select").disabled = true;
    $("retry-audio").classList.add("hidden");
    if (["created", "active"].includes(session.status)) {
      $("start").textContent = "Reprendre l’appel";
      showCallActions({
        canStart: true,
        canEnd: session.turns.length > 0,
        canRetry: false,
        canCreateNew: false,
      });
      setStatus("Session sauvegardée — vous pouvez reprendre ou terminer");
    } else if (["analysis_pending", "analysis_failed"].includes(session.status)) {
      showCallActions({ canStart: false, canEnd: false, canRetry: true, canCreateNew: false });
      setStatus(
        session.status === "analysis_failed"
          ? "Analyse échouée — le transcript est sauvegardé"
          : "Analyse interrompue — vous pouvez la relancer",
      );
    } else if (session.status === "completed") {
      showFeedback(session);
      showCallActions({ canStart: false, canEnd: false, canRetry: false, canCreateNew: true });
      setStatus("Session terminée et sauvegardée");
    }
    return true;
  } catch (error) {
    if (error.status === 404) {
      localStorage.removeItem("ari.current_session_id");
      state.storedSessionId = null;
      state.sessionId = null;
      return false;
    }
    console.error(error);
    $("case-select").disabled = true;
    showCallActions({ canStart: false, canEnd: false, canRetry: false, canCreateNew: false });
    setStatus("Récupération temporairement impossible — rechargez la page pour réessayer");
    return true;
  }
}

async function initialize() {
  const [health, cases] = await Promise.all([api("/api/health"), api("/api/cases?approved_only=true")]);
  state.providerMode = health.provider_mode;
  state.cases = cases;

  const defaultCase = state.requestedCaseId
    ? cases.find((item) => item.id === state.requestedCaseId)
    : cases[0];
  if (defaultCase) renderCase(defaultCase);
  if (state.requestedLearningMode) state.learningMode = state.requestedLearningMode;
  $("learning-mode").value = state.learningMode;
  syncLearningModeButtons();
  setExamPresentation(state.learningMode === "exam");

  if (cases.length > 1 && defaultCase) {
    const selector = $("case-select");
    selector.replaceChildren(
      ...cases.map((item) => {
        const option = document.createElement("option");
        option.value = caseKey(item);
        option.textContent = `${item.language} — ${item.title}`;
        option.selected = item.id === defaultCase.id && item.version === defaultCase.version;
        return option;
      }),
    );
    $("case-picker").classList.remove("hidden");
  }

  if (state.providerMode === "fake" && defaultCase) $("debug-form").classList.remove("hidden");
  const restored = await restoreSession();
  if (!restored && !defaultCase) {
    $("case-title").textContent = "Aucun scénario vocal approuvé";
    $("case-summary").textContent = "Les brouillons ne sont pas disponibles à l’entraînement. Revenez à l’accueil pour les phases disponibles.";
    showCallActions({canStart: false, canEnd: false, canRetry: false, canCreateNew: false});
    $("debug-form").classList.add("hidden");
    $("subtitles-toggle").hidden = true;
    setStatus("Aucun contenu disponible");
    $("audio-state").textContent = "Retrouvez les exercices disponibles depuis l’accueil.";
  }
}

async function ensureAudioContext() {
  if (!state.audioContext || state.audioContext.state === "closed") {
    state.audioContext = new AudioContext({ latencyHint: "interactive" });
  }
  if (state.audioContext.state === "suspended") await state.audioContext.resume();
}

async function openMicrophone() {
  await ensureAudioContext();
  state.stream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  await state.audioContext.audioWorklet.addModule("/pcm-worklet.js");
  state.source = state.audioContext.createMediaStreamSource(state.stream);
  state.worklet = new AudioWorkletNode(state.audioContext, "pcm-capture");
  state.worklet.port.onmessage = ({ data }) => {
    if (state.sending && state.socket?.readyState === WebSocket.OPEN) {
      state.socket.send(data);
    }
  };
  const mute = state.audioContext.createGain();
  mute.gain.value = 0;
  state.source.connect(state.worklet).connect(mute).connect(state.audioContext.destination);
}

function queuePcmAudio(bytes) {
  if (!state.audioContext) return null;
  let combined = bytes;
  if (state.pcmRemainder.length) {
    combined = new Uint8Array(state.pcmRemainder.length + bytes.length);
    combined.set(state.pcmRemainder);
    combined.set(bytes, state.pcmRemainder.length);
  }
  const completeLength = combined.length - (combined.length % 2);
  state.pcmRemainder = combined.slice(completeLength);
  if (!completeLength) return null;

  const view = new DataView(combined.buffer, combined.byteOffset, completeLength);
  const samples = completeLength / 2;
  const buffer = state.audioContext.createBuffer(1, samples, 24000);
  const channel = buffer.getChannelData(0);
  for (let index = 0; index < samples; index += 1) {
    channel[index] = view.getInt16(index * 2, true) / 32768;
  }

  const source = state.audioContext.createBufferSource();
  source.buffer = buffer;
  source.connect(state.audioContext.destination);
  const startAt = Math.max(state.audioContext.currentTime + 0.01, state.playbackEndTime);
  source.start(startAt);
  state.playbackEndTime = startAt + buffer.duration;
  state.playbackSources.add(source);
  source.onended = () => state.playbackSources.delete(source);
  return {source, startAt};
}

async function stopVoiceMedia(closeSocket = true) {
  const socket = state.socket;
  if (closeSocket) state.socket = null;
  if (closeSocket) socket?.close();
  state.stream?.getTracks().forEach((track) => track.stop());
  cancelPipelinePlayback();
  await state.audioContext?.close();
  clearInterval(state.timer);
  state.audioContext = null;
  state.stream = null;
  state.callConnected = false;
  state.openMic = false;
}

async function resetFailedCall() {
  state.sending = false;
  await stopVoiceMedia();
  $("start").disabled = false;
  $("case-select").disabled = false;
}

function showTalkButton(ready) {
  const talk = $("talk");
  talk.classList.toggle("hidden", state.providerMode === "fake" || state.openMic || !state.callConnected || state.ending);
  talk.disabled = !ready;
  talk.textContent = state.sending ? "J’ai fini" : ready ? "Parler" : "Envoi…";
}

function toggleTalk() {
  if (state.socket?.readyState !== WebSocket.OPEN) return;
  if (!state.sending) {
    state.sending = true;
    showTalkButton(true);
    setStatus("Je vous écoute — prenez votre temps", true);
    return;
  }
  state.sending = false;
  showTalkButton(false);
  state.socket.send(JSON.stringify({ type: "user.turn.finish" }));
  setStatus("Transcription en cours…", true);
}

function handleTerminalVoiceFailure(message) {
  setStatus(`Erreur : ${message}`);
  state.sending = false;
  $("talk").classList.add("hidden");
  void stopVoiceMedia().then(() => {
    $("end").disabled = state.persistedTurns === 0;
    $("start").classList.remove("hidden");
    $("start").disabled = false;
    $("start").textContent = "Reprendre l’appel";
    $("case-select").disabled = true;
  });
}

function handleEvent(event) {
  const data = event.data || {};
  if (event.type === "call.started") {
    $("start").classList.add("hidden");
    $("end").disabled = false;
    state.callConnected = true;
    state.openMic = data.interaction === "open_microphone";
    if (state.openMic) {
      // Exam: the microphone streams continuously to the speech-to-speech model.
      state.sending = true;
      showTalkButton(false);
      setStatus("Micro ouvert — parlez naturellement, le patient vous répond", true);
    } else if (state.providerMode === "fake") setStatus("À vous de parler", true);
    else {
      showTalkButton(true);
      setStatus("Appuyez sur « Parler » quand vous êtes prêt", true);
    }
  }
  if (event.type === "user.turn.empty") {
    showTalkButton(true);
    setStatus("Je n’ai rien entendu — réessayez", true);
  }
  if (event.type === "user.turn.failed") {
    showTalkButton(true);
    setStatus(data.message || "Transcription indisponible — réessayez", true);
  }
  if (event.type === "user.speech_started") {
    state.userSpeaking = true;
    // Open microphone: the learner may interrupt the patient; stop the local playback too.
    if (state.openMic) cancelPipelinePlayback();
    setStatus("Je vous écoute…", true);
  }
  if (event.type === "user.transcript_delta") setPartial(data.text);
  if (event.type === "user.transcript_final") {
    state.userSpeaking = false;
    setPartial("");
    addTurn("user", data.text);
    if (!state.openMic) state.sending = false;
    setStatus("Le patient réfléchit…", true);
  }
  if (event.type === "patient.response_text") {
    state.patientDraft = "";
    setPartial("");
    state.lastPatientTurnNode = addTurn("patient", data.text);
    if (data.response_id && state.lastPatientTurnNode) {
      state.patientTurnNodes.set(data.response_id, state.lastPatientTurnNode);
    }
  }
  if (event.type === "turn.persisted") {
    state.persistedTurns += 1;
    $("end").disabled = false;
  }
  if (event.type === "patient.audio_streaming") {
    pcmPlayback.begin({turnId:data.turn_id,responseId:data.response_id,
      audioStreamId:data.audio_stream_id});
    setStatus("Réponse générée — audio en cours de réception", true);
  }
  if (event.type === "patient.audio_chunk") {
    if (data.mime_type !== "audio/pcm;rate=24000") {
      setStatus("Erreur : format audio inattendu");
      return;
    }
    const raw = atob(data.audio);
    const bytes = new Uint8Array(raw.length);
    for (let index = 0; index < raw.length; index += 1) bytes[index] = raw.charCodeAt(index);
    if (data.index === 0) setStatus("Le patient répond…", true);
    const correlation={turnId:data.turn_id,responseId:data.response_id,
      audioStreamId:data.audio_stream_id};
    const scheduled=queuePcmAudio(bytes);
    if (scheduled) {
      pcmPlayback.attachChunk(correlation,data.index,scheduled.source);
      const delayMs=Math.max(0,(scheduled.startAt-state.audioContext.currentTime)*1000);
      const observeStart=()=>{
        if (!state.playbackSources.has(scheduled.source)) return;
        const context=state.audioContext;
        if (!context || context.state === "closed") return;
        if (context.state === "running" && context.currentTime >= scheduled.startAt)
          pcmPlayback.started(correlation,data.index);
        else setTimeout(observeStart,10);
      };
      setTimeout(observeStart,delayMs);
    }
  }
  if (event.type === "patient.audio_sent") {
    pcmPlayback.sent({turnId:data.turn_id,responseId:data.response_id,
      audioStreamId:data.audio_stream_id},data.last_index);
    setStatus("Audio reçu — lecture en cours", true);
  }
  if (event.type === "turn.completed") {
    state.completedTurns += 1;
    const patientNode = data.turn?.provider_response_id
      ? state.patientTurnNodes.get(data.turn.provider_response_id)
      : state.lastPatientTurnNode;
    if (data.turn?.provider_response_status !== "completed" && patientNode) {
      const label = document.createElement("span");
      label.textContent = "Patient";
      patientNode.replaceChildren(
        label,
        document.createTextNode(
          "[Réponse incomplète — contenu exclu de l’évaluation clinique]",
        ),
      );
    }
  }
  if (event.type === "turn.delivered") {
    pcmPlayback.forget(data.turn.id);
    showTalkButton(true);
    setStatus("Audio entendu — à vous de parler", true);
  }
  if (event.type === "turn.tts_failed") {
    cancelPipelinePlayback();
    showTalkButton(true);
    state.failedTtsTurnId=data.turn?.id || null;
    $("retry-audio").classList.toggle("hidden",!state.failedTtsTurnId);
    setStatus("Réponse générée, mais synthèse audio échouée");
  }
  if (event.type === "turn.retry_completed" && data.turn?.response_state !== "tts_failed") {
    state.failedTtsTurnId = null;
    $("retry-audio").classList.add("hidden");
  }
  if (event.type === "call.ended" && state.ending) setStatus("Analyse en cours…", true);
  if (event.type === "voice.error") {
    console.error(data);
    if (data.retryable && data.turn_id) {
      showTalkButton(true);
      setStatus(data.message || "Erreur audio récupérable");
    } else {
      handleTerminalVoiceFailure(data.message || "voix");
    }
  }
}

async function startCall() {
  $("start").disabled = true;
  $("end").disabled = true;
  $("case-select").disabled = true;
  $("learning-mode").disabled = true;
  try {
    if (!state.sessionId) state.learningMode = $("learning-mode").value;
    setExamPresentation(state.learningMode === "exam");
    syncLearningModeButtons();
    await ensureLearner();
    await ensureAudioContext();
    if (!state.sessionId) {
      const requestKey = `ari.voice.start:${state.learnerId}:${caseKey(state.case)}:${state.learningMode}`;
      let requestId = sessionStorage.getItem(requestKey);
      if (!requestId) { requestId = crypto.randomUUID(); sessionStorage.setItem(requestKey, requestId); }
      const session = await api("/api/sessions", {
        method: "POST",
        body: JSON.stringify({
          learner_id: state.learnerId,
          request_id: requestId,
          case_id: state.case.id,
          case_version: state.case.version,
          learning_mode: state.learningMode,
          scenario_id: state.case.training_snapshot?.scenario_id || null,
          scenario_version: state.case.training_snapshot?.scenario_version || null,
        }),
      });
      sessionStorage.removeItem(requestKey);
      state.sessionId = session.id;
      history.replaceState(null, "", `/voice.html?session=${encodeURIComponent(session.id)}`);
      state.storedSessionId = session.id;
      localStorage.setItem("ari.current_session_id", session.id);
      syncLearningModeButtons();
      state.completedTurns = 0;
      state.persistedTurns = 0;
      applySessionVoice(session);
      clearTranscript();
    } else {
      const session = await api(`/api/sessions/${state.sessionId}`);
      if (!["created", "active"].includes(session.status)) {
        throw new Error("Cette session ne peut plus être reprise");
      }
      state.persistedTurns = session.turns.length;
      applySessionVoice(session);
    }
    state.ending = false;
    state.userSpeaking = false;
    state.callConnected = false;
    $("start").textContent = "Reprendre l’appel";
    $("retry-analysis").classList.add("hidden");
    $("new-session").classList.add("hidden");
    $("end").classList.remove("hidden");
    $("retry-audio").classList.add("hidden");
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    state.socket = new WebSocket(
      `${protocol}://${location.host}/ws/sessions/${state.sessionId}/voice`,
    );
    state.socket.binaryType = "arraybuffer";
    state.socket.onmessage = ({ data }) => handleEvent(JSON.parse(data));
    state.socket.onerror = () => setStatus("Connexion vocale interrompue");
    state.socket.onclose = () => {
      if (!state.ending && state.socket) {
        handleTerminalVoiceFailure("Connexion vocale interrompue");
      }
    };
    await new Promise((resolve, reject) => {
      state.socket.onopen = resolve;
      state.socket.onerror = () => reject(new Error("Connexion de contrôle interrompue"));
    });
    state.socket.onerror = () => setStatus("Connexion vocale interrompue");
    if (state.providerMode !== "fake") await openMicrophone();
    if (state.providerMode === "fake") {
      state.callConnected = true;
      $("end").disabled = false;
    }
    startTimer();
  } catch (error) {
    setStatus(error.message);
    if (state.socket || state.stream) await resetFailedCall();
    else {
      $("start").disabled = false;
      $("case-select").disabled = false;
    }
  }
}

function retryAudio() {
  if (state.failedTtsTurnId) {
    cancelPipelinePlayback();
    sendVoiceControl({ type: "turn.retry_tts", turn_id: state.failedTtsTurnId });
  }
}

async function endCall() {
  if (state.ending) return;
  state.ending = true;
  state.sending = false;
  $("talk").classList.add("hidden");
  $("end").disabled = true;
  setStatus("Finalisation et sauvegarde…", true);
  if (state.socket?.readyState === WebSocket.OPEN) {
    state.socket.send(JSON.stringify({ type: "call.end" }));
  }
  try {
    const session = await api(`/api/sessions/${state.sessionId}/end`, {
      method: "POST",
      body: "{}",
    });
    await stopVoiceMedia();
    setExamPresentation(false);
    renderPersistedTranscript(session.turns);
    showFeedback(session);
    showCallActions({ canStart: false, canEnd: false, canRetry: false, canCreateNew: true });
    setStatus("Session terminée et sauvegardée");
  } catch (error) {
    await stopVoiceMedia();
    let saved = null;
    try {
      saved = await api(`/api/sessions/${state.sessionId}`);
    } catch (_) {
      // Preserve the original analysis error when recovery lookup also fails.
    }
    if (saved && ["analysis_pending", "analysis_failed"].includes(saved.status)) {
      setExamPresentation(false);
      renderPersistedTranscript(saved.turns);
      showCallActions({ canStart: false, canEnd: false, canRetry: true, canCreateNew: false });
      setStatus("Analyse échouée — le transcript est sauvegardé");
    } else if (saved?.status === "completed") {
      setExamPresentation(false);
      renderPersistedTranscript(saved.turns);
      showFeedback(saved);
      showCallActions({ canStart: false, canEnd: false, canRetry: false, canCreateNew: true });
      setStatus("Session terminée et sauvegardée");
    } else {
      state.ending = false;
      if (saved && ["created", "active"].includes(saved.status)) {
        renderPersistedTranscript(saved.turns);
        state.persistedTurns = saved.turns.length;
      }
      $("start").textContent = "Reprendre l’appel";
      $("case-select").disabled = true;
      showCallActions({
        canStart: true,
        canEnd: state.persistedTurns > 0,
        canRetry: false,
        canCreateNew: false,
      });
      setStatus(
        state.persistedTurns === 0
          ? "Aucun transcript final reçu — reprenez l’appel pour continuer"
          : `Finalisation incomplète : ${error.message} — vous pouvez reprendre`,
      );
    }
  }
}

async function retryAnalysis() {
  $("retry-analysis").disabled = true;
  setStatus("Nouvelle tentative d’analyse…", true);
  try {
    const session = await api(`/api/sessions/${state.sessionId}/analysis/retry`, {
      method: "POST",
      body: "{}",
    });
    setExamPresentation(false);
    renderPersistedTranscript(session.turns);
    showFeedback(session);
    showCallActions({ canStart: false, canEnd: false, canRetry: false, canCreateNew: true });
    setStatus("Session terminée et sauvegardée");
  } catch (error) {
    $("retry-analysis").disabled = false;
    setStatus(`Analyse échouée : ${error.message}`);
  }
}

function resetForNewSession(mode, selectedCase) {
  localStorage.removeItem("ari.current_session_id");
  history.replaceState(null, "", "/voice.html");
  state.storedSessionId = null;
  state.sessionId = null;
  state.persistedTurns = 0;
  state.completedTurns = 0;
  state.ending = false;
  state.learningMode = mode;
  setExamPresentation(false);
  $("learning-mode").value = mode;
  syncLearningModeButtons();
  clearTranscript();
  $("feedback").classList.add("hidden");
  $("case-select").disabled = false;
  $("learning-mode").disabled = false;
  renderCase(selectedCase);
  $("start").textContent = "Commencer l’appel";
  showCallActions({ canStart: true, canEnd: false, canRetry: false, canCreateNew: false });
  setStatus("Prêt");
}

function newSession() {
  if (!state.cases.length) { location.href = "/"; return; }
  resetForNewSession("training", state.cases[0]);
}

async function retryCase() {
  // Same case, same mode, a fresh session: the feedback is still on screen to guide it.
  const current = state.cases.find((item) => item.id === state.case?.id && item.version === state.case?.version);
  if (!current) { newSession(); return; }
  resetForNewSession(state.learningMode, current);
  await startCall();
}

function fillList(id, items) {
  const node = $(id);
  node.replaceChildren(
    ...items.map((item) => {
      const li = document.createElement("li");
      li.textContent = item;
      return li;
    }),
  );
}

const COMPARABLE_EVALUATIONS = new Set(["session-evaluation-v2", "session-evaluation-v3"]);
const VOCABULARY_KIND_LABELS = { missing: "mot manquant", misused: "mal employé", well_used: "bien employé" };
const turnsLabel = (sequences) => `tour${sequences.length > 1 ? "s" : ""} ${sequences.join(", ")}`;

function showFeedback(session) {
  const evaluation = session.evaluation;
  if (!evaluation) return;
  $("score").textContent = "Provisoire";
  $("score-label").textContent = "analyse automatisée · aucun score global ni niveau certifié";
  $("voice-dimensions").replaceChildren();
  if (!COMPARABLE_EVALUATIONS.has(evaluation.schema_version)) {
    const notice = document.createElement("p");
    notice.textContent = "Évaluation historique non comparable : les anciennes notes ne sont pas utilisées dans la progression.";
    $("voice-dimensions").append(notice);
  } else for (const criterion of evaluation.criteria) {
    const line = document.createElement("p");
    line.textContent = `${criterion.criterion_id} : ${criterion.score} · ${criterion.feedback} · preuves aux tours ${criterion.evidence_turn_sequences.join(", ") || "aucun"}`;
    $("voice-dimensions").append(line);
  }
  $("summary").textContent = evaluation.summary;
  fillList("strengths", evaluation.strengths.map((item) => `${item.text} (${turnsLabel(item.evidence_turn_sequences)})`));
  fillList("priorities", evaluation.priorities.map((item) => `${item.text} (${turnsLabel(item.evidence_turn_sequences)})`));
  fillList(
    "vocabulary",
    session.vocabulary.map(
      (item) => `${item.lemma} — ${item.translation} · ${VOCABULARY_KIND_LABELS[item.kind] || "candidat"} (${turnsLabel(item.evidence_turn_sequences)})`,
    ),
  );
  const languageErrors = evaluation.language_errors || [];
  fillList("language-errors", languageErrors.length
    ? languageErrors.map((item) => `${item.text} (${turnsLabel(item.evidence_turn_sequences)})`)
    : ["Aucune erreur de langue significative relevée."]);
  const codeSwitches = evaluation.code_switches || [];
  fillList("code-switches", codeSwitches.length
    ? codeSwitches.map((item) => `Tour ${item.turn} : « ${item.fragment} »${item.intended_german ? ` → ${item.intended_german}` : ""}`)
    : ["Vous êtes resté dans la langue de la consultation."]);
  const lexicon = session.lexicon;
  if (lexicon) {
    const added = lexicon.added.length, promoted = lexicon.promoted.length, wrong = lexicon.wrong_language_turns.length;
    $("lexicon-summary").textContent = [
      added ? `${added} mot${added > 1 ? "s" : ""} ajouté${added > 1 ? "s" : ""} à votre carnet.` : "Aucun nouveau mot ajouté.",
      promoted ? `${promoted} mot${promoted > 1 ? "s" : ""} de votre carnet utilisé${promoted > 1 ? "s" : ""} en session.` : "",
      wrong ? `Le patient n’a pas compris ${wrong} question${wrong > 1 ? "s" : ""} posée${wrong > 1 ? "s" : ""} dans une autre langue.` : "",
    ].filter(Boolean).join(" ");
    fillList("lexicon-added", lexicon.added.map((item) => `${item.lemma}${item.translation ? ` — ${item.translation}` : ""}`));
    fillList("lexicon-promoted", lexicon.promoted.map((item) => `${item.lemma} · ${item.state === "mastered" ? "maîtrisé" : "utilisé"}`));
  } else {
    $("lexicon-summary").textContent = "Carnet indisponible pour cette session.";
    fillList("lexicon-added", []);
    fillList("lexicon-promoted", []);
  }
  $("feedback").classList.remove("hidden");
  $("feedback").scrollIntoView({ behavior: "smooth" });
}

$("case-select").addEventListener("change", (event) => {
  const selected = state.cases.find(
    (item) => caseKey(item) === event.target.value,
  );
  if (selected) renderCase(selected);
});
document.querySelectorAll("[data-learning-mode]").forEach((button) => {
  button.addEventListener("click", () => {
    if (state.sessionId) return;
    state.learningMode = button.dataset.learningMode;
    $("learning-mode").value = state.learningMode;
    syncLearningModeButtons();
    setExamPresentation(state.learningMode === "exam");
  });
});
$("subtitles-toggle").addEventListener("click", () => {
  if (!allowsInCallHelp(state.learningMode)) return;
  const off = document.body.classList.toggle("captions-off");
  $("subtitles-toggle").setAttribute("aria-pressed", String(!off));
  $("subtitles-toggle").textContent = off ? "Afficher les sous-titres" : "Masquer les sous-titres";
});
$("start").addEventListener("click", startCall);
$("talk").addEventListener("click", toggleTalk);
$("end").addEventListener("click", endCall);
$("retry-analysis").addEventListener("click", retryAnalysis);
$("retry-audio").addEventListener("click", retryAudio);
$("new-session").addEventListener("click", newSession);
$("retry-case").addEventListener("click", retryCase);
$("debug-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = $("debug-input");
  if (input.value.trim() && state.socket?.readyState === WebSocket.OPEN) {
    state.socket.send(
      JSON.stringify({ type: "debug.transcript", transcript: input.value.trim() }),
    );
    input.value = "";
  }
});


initialize().catch((error) => setStatus(error.message));
