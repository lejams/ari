import assert from "node:assert/strict";
import { allowsInCallHelp, voiceLaunchIntent } from "../voice-presentation.mjs";
assert.deepEqual(voiceLaunchIntent("?case=ARI-FSP-001&mode=exam", "old-session"), {sessionId:null,caseId:"ARI-FSP-001",mode:"exam",hasExplicitLaunch:true});
assert.deepEqual(voiceLaunchIntent("?session=known&mode=exam", "old-session"), {sessionId:"known",caseId:null,mode:"exam",hasExplicitLaunch:true});
assert.equal(voiceLaunchIntent("", "old-session").sessionId, "old-session");
assert.equal(allowsInCallHelp("training"), true);
assert.equal(allowsInCallHelp("exam"), false);
