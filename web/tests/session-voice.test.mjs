import assert from "node:assert/strict";
import {acceptedFallbackRequest,persistedVoiceSelection} from "../session-voice.mjs";
const session=JSON.parse(JSON.stringify({voice_stack_id:"pipeline_low_latency",
  voice_transport:"pipeline",voice_stack_available:true}));
assert.deepEqual(persistedVoiceSelection(session),{stackId:"pipeline_low_latency",transport:"pipeline"});
assert.throws(()=>persistedVoiceSelection({...session,voice_stack_available:false}),/disponible/);
const offer={failed_voice_stack_id:"realtime_economy",target_voice_stack_id:"pipeline_economy",available:true};
assert.throws(()=>acceptedFallbackRequest(offer,false),/explicite/);
assert.equal(acceptedFallbackRequest(offer,true).target_voice_stack_id,"pipeline_economy");
