import assert from "node:assert/strict";
import {PcmPlaybackTracker, RealtimePlaybackObserver} from "../audio-delivery.mjs";
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
{ const events=[], observer=new RealtimePlaybackObserver((e)=>events.push(e));
  observer.responseStarted("r"); observer.mediaAdvanced(); observer.bindTurn("r","t","r");
  observer.mediaAdvanced(); assert.equal(events.length,1); assert.equal(events[0].type,"audio.playback_started"); }
{ const events=[]; let now=100; const observer=new RealtimePlaybackObserver((e)=>events.push(e),()=>now);
  observer.speechEnded(); now=345; observer.responseStarted("r"); observer.bindTurn("r","t","r");
  observer.mediaAdvanced(); assert.equal(events[0].speech_end_to_audio_started_ms,245); }
{ const events=[]; let now=100; const observer=new RealtimePlaybackObserver((e)=>events.push(e),()=>now);
  observer.speechEnded(); observer.responseStarted("r1"); now=200; observer.bindTurn("r1","t1","r1");
  observer.mediaAdvanced(); observer.responseStarted("r2"); now=300;
  observer.bindTurn("r2","t2","r2"); observer.mediaAdvanced();
  assert.equal(events[0].speech_end_to_audio_started_ms,100);
  assert.equal("speech_end_to_audio_started_ms" in events[1],false); }
{ const events=[], observer=new RealtimePlaybackObserver((e)=>events.push(e));
  observer.responseStarted("r"); observer.cancel("r"); observer.mediaAdvanced(); observer.bindTurn("r","t","r");
  assert.deepEqual(events,[]); }
