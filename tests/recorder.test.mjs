import test from 'node:test';
import assert from 'node:assert/strict';
import {BrowserRecorder, audioExtension} from '../frontend/recorder.js';

function fixture({getMedia, constructorFailure=false, denied=false}={}) {
  let stopped=0;
  const track={stop(){stopped++;},getSettings(){return {sampleRate:48000,channelCount:1};}};
  const stream={getTracks(){return [track];},getAudioTracks(){return [track];}};
  class FakeRecorder {
    static isTypeSupported(type){return type==='audio/mp4';}
    constructor(input, options={}) {
      if (constructorFailure && options.mimeType) throw new Error('codec unavailable');
      this.mimeType=options.mimeType || 'audio/webm';this.state='inactive';
    }
    start(){this.state='recording';}
    stop(){this.state='inactive';setTimeout(()=>{
      this.ondataavailable?.({data:new Blob(['first'],{type:this.mimeType})});
      this.ondataavailable?.({data:new Blob(['last'],{type:this.mimeType})});
      this.onstop?.();
    },0);}
  }
  const platform={isSecureContext:true,MediaRecorder:FakeRecorder,navigator:{mediaDevices:{
    getUserMedia:getMedia || (()=> denied?Promise.reject(Object.assign(new Error(),{name:'NotAllowedError'})):Promise.resolve(stream)),
  }}};
  return {stream,platform,stopped:()=>stopped};
}

test('stop waits for final chunks and releases microphone; duplicate stop is idempotent',async()=>{
  const f=fixture();const recorder=new BrowserRecorder({platform:f.platform});
  await recorder.start();recorder._started-=500;
  const first=recorder.stop();assert.equal(first,recorder.stop());
  const result=await first;
  assert.equal(await result.blob.text(),'firstlast');
  assert.equal(result.type,'audio/mp4');assert.equal(audioExtension(result.type),'m4a');
  assert.equal(f.stopped(),1);assert.equal(recorder.state,'ready');
});

test('late permission approval after cancellation cannot retain a microphone',async()=>{
  let resolveMedia;const f=fixture({getMedia:()=>new Promise(resolve=>{resolveMedia=resolve;})});
  const recorder=new BrowserRecorder({platform:f.platform});const pending=recorder.start();
  recorder.cancelPermission();resolveMedia(f.stream);await pending;
  assert.equal(f.stopped(),1);assert.equal(recorder.state,'idle');
});

test('codec constructor failure falls back to browser default',async()=>{
  const f=fixture({constructorFailure:true});const recorder=new BrowserRecorder({platform:f.platform});
  await recorder.start();recorder._started-=500;const result=await recorder.stop();
  assert.equal(result.type,'audio/webm');assert.equal(f.stopped(),1);
});

test('permission rejection leaves a usable error state',async()=>{
  const f=fixture({denied:true});const recorder=new BrowserRecorder({platform:f.platform});
  await recorder.start();assert.equal(recorder.state,'error');assert.match(recorder.message,/未获授权/);
});

test('background interruption retains completed audio with notice',async()=>{
  const f=fixture();const recorder=new BrowserRecorder({platform:f.platform});
  await recorder.start();recorder._started-=500;recorder.interrupt();await recorder.stop();
  assert.equal(recorder.state,'ready');assert.match(recorder.message,/后台/);assert.equal(f.stopped(),1);
});
