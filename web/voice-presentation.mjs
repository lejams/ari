export function voiceLaunchIntent(search, storedSessionId = null) {
  const params = new URLSearchParams(search);
  const sessionId = params.get("session");
  const caseId = params.get("case");
  const requestedMode = params.get("mode");
  const mode = ["training", "exam"].includes(requestedMode) ? requestedMode : null;
  const hasExplicitLaunch = Boolean(caseId || mode);
  return { sessionId: sessionId || (hasExplicitLaunch ? null : storedSessionId), caseId, mode, hasExplicitLaunch };
}

export function allowsInCallHelp(mode) { return mode === "training"; }
