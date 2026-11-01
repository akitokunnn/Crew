'use strict';
const {test} = require('node:test'), assert = require('node:assert/strict');
const {ReportPreviewClient} = require('../src/report-preview.cjs');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(predicate) {
  const end = Date.now() + 1500;
  while (!predicate() && Date.now() < end) await pause(5);
  assert(predicate(), 'preview transport should recover without restarting the desktop');
}
function waitForClose(signal) {
  return new Promise((_resolve, reject) => {
    if (signal.aborted) reject(Error('closed'));
    else signal.addEventListener('abort', () => reject(Error('closed')), {once:true});
  });
}

test('preview host recovers an unavailable initial connection without recapturing a delivered report',async t => {
  let registrations=0, claimed=false, closed=false;
  const results=[], errors=[];
  const client = new ReportPreviewClient({base:'http://fixture',retryMs:5,
    renderer:{async render(job){return {png_base64:job.id,console_errors:[]};},close(){closed=true;}},
    onError:error => errors.push(error.message),
    request:async(_base,route,body,_timeout,signal)=>{
      if(route.endsWith('/register')){
        if(++registrations===1)throw Error('fixture backend temporarily unavailable');
        return {worker_id:'worker',token:'token'};
      }
      if(route.endsWith('/next')){
        if(!claimed){claimed=true;return {job:{id:'report'}};}
        return waitForClose(signal);
      }
      if(route.endsWith('/result')){results.push(body);return {ok:true};}
      if(route.endsWith('/close'))return {ok:true};
      throw Error('Unexpected transport route');
    }});
  t.after(()=>client.close());await client.start();
  await until(()=>results.length===1);await client.close();
  assert.equal(results[0].job_id,'report');assert.equal(results[0].png_base64,'report');
  assert.deepEqual(errors,['fixture backend temporarily unavailable']);assert(closed);
});

test('renewing a lost lease keeps in-flight captures bound to their original worker and never resends a result',async t => {
  let registrations=0, reads=0, finishOld;
  const results=[], images=[];
  const client = new ReportPreviewClient({base:'http://fixture',retryMs:5,onError(){},
    renderer:{render(job){images.push(job.id);return job.id==='old'?new Promise(resolve=>{finishOld=resolve;}):Promise.resolve({png_base64:'new'});},close(){}},
    request:async(_base,route,body,_timeout,signal)=>{
      if(route.endsWith('/register'))return {worker_id:'worker-'+(++registrations),token:'token-'+registrations};
      if(route.endsWith('/next')){
        const step=reads++;
        if(step===0)return {job:{id:'old'}};
        if(step===1)throw Object.assign(Error('fixture backend restarted'),{status:409});
        if(step===2)return {job:{id:'new'}};
        return waitForClose(signal);
      }
      if(route.endsWith('/result')){
        results.push(body);
        if(body.job_id==='old')throw Object.assign(Error('old capture no longer belongs to this backend'),{status:409});
        return {ok:true};
      }
      if(route.endsWith('/close'))return {ok:true};
      throw Error('Unexpected transport route');
    }});
  t.after(()=>client.close());await client.start();
  await until(()=>results.some(item=>item.job_id==='new'));
  finishOld({png_base64:'old'});await until(()=>results.length===2);await client.close();
  assert.deepEqual(images,['old','new']);
  assert.equal(results.find(item=>item.job_id==='old').worker_id,'worker-1');
  assert.equal(results.find(item=>item.job_id==='new').worker_id,'worker-2');
  assert.equal(results.filter(item=>item.job_id==='old').length,1);
});
