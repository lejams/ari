import assert from "node:assert/strict";
import {PcmPlaybackTracker} from "../audio-delivery.mjs";
class Source { constructor(){this.listeners=[];} addEventListener(_, fn){this.listeners.push(fn);}
  end(){for(const fn of this.listeners.splice(0)) fn();} }
const c={turnId:"t",responseId:"r",audioStreamId:"s"};
{ const events=[], tracker=new PcmPlaybackTracker((e)=>events.push(e)), a=new Source(), b=new Source();
  tracker.begin(c); tracker.attachChunk(c,0,a); tracker.attachChunk(c,1,b); tracker.sent(c,1);
  tracker.started(c,0); a.end(); assert.deepEqual(events.map(e=>e.type),["audio.playback_started"]); b.end();
  assert.deepEqual(events.map(e=>e.type),["audio.playback_started","audio.playback_completed"]);
  b.end(); assert.equal(events.length,2); assert.equal(events[1].audio_stream_id,"s"); }
{ const events=[]; let now=100; const tracker=new PcmPlaybackTracker((e)=>events.push(e),()=>now);
  const a=new Source(); tracker.begin(c); tracker.attachChunk(c,0,a); tracker.sent(c,0);
  now=145; tracker.started(c,0); assert.equal(events[0].audio_sent_to_playback_started_ms,45); }
{ const events=[], tracker=new PcmPlaybackTracker((e)=>events.push(e)), a=new Source();
  tracker.begin(c); tracker.attachChunk(c,0,a); tracker.sent(c,0); a.end();
  assert.equal(events.length,2);
  assert.equal("audio_sent_to_playback_started_ms" in events[0],false); }
{ const events=[], tracker=new PcmPlaybackTracker((e)=>events.push(e)), a=new Source();
  tracker.begin(c); tracker.attachChunk(c,0,a); tracker.sent(c,0); tracker.cancelAll(); a.end();
  assert.deepEqual(events,[]); }
