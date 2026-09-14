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
{ const events=[], tracker=new PcmPlaybackTracker((e)=>events.push(e)), a=new Source();
  tracker.begin(c); tracker.attachChunk(c,0,a); tracker.sent(c,0); a.end();
  assert.equal(events.length,2); }
{ const events=[], tracker=new PcmPlaybackTracker((e)=>events.push(e)), a=new Source();
  tracker.begin(c); tracker.attachChunk(c,0,a); tracker.sent(c,0); tracker.cancelAll(); a.end();
  assert.deepEqual(events,[]); }
