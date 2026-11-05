'use strict';
const assert=require('node:assert/strict');
// Contract-only fixture: Python custom-sound endpoints are owned by another session.
module.exports=async function customSounds({page,shot,check}){
 const wav=Buffer.alloc(44+1600);wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(8000,24);wav.writeUInt32LE(16000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(1600,40);for(let i=0;i<800;i++)wav.writeInt16LE(Math.sin(i*.35)*6000,44+i*2);
 await page.evaluate(bytes=>{
  const original=window.fetch;window.customFixture={presets:{},calls:[],uploadError:false,deleteError:false,fileError:false,broken:false,settings:null,decodes:0,starts:0,restore(){window.fetch=original;}};
  const f=window.customFixture,decode=AudioContext.prototype.decodeAudioData,create=AudioContext.prototype.createBufferSource;f.gains=[];
  AudioContext.prototype.decodeAudioData=function(...args){f.decodes++;return decode.apply(this,args);};
  AudioContext.prototype.createBufferSource=function(){const source=create.call(this),start=source.start.bind(source),connect=source.connect.bind(source);source.connect=(destination)=>{f.gains.push(destination.gain.value);return connect(destination);};source.start=(...args)=>{f.starts++;return start(...args);};return source;};
  const json=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
  window.fetch=async(url,options)=>{
   const name=new URL(url,location.href).pathname,body=options?.body?JSON.parse(options.body):null;
   if(name==='/api/sounds/custom'){
    f.calls.push({name,body});if(f.uploadError)return json({error:'音效内容损坏，请更换文件'},400);
    const id='custom:fixture',preset={id,name:body.name,url:'/api/sounds/custom/file?id=fixture',custom:true};f.presets[id]=preset;return json({preset});
   }
   if(name==='/api/sounds/custom/delete'){
    f.calls.push({name,body});if(f.deleteError)return json({error:'音效正在被任务交付使用，请先更换'},409);delete f.presets[body.id];return json({ok:true});
   }
   if(name==='/api/sounds/custom/file'){
    f.calls.push({name});if(f.fileError)return new Response('missing',{status:404,statusText:'Not Found'});return new Response(new Uint8Array(f.broken?[1,2,3]:bytes));
   }
   if(name==='/api/settings'&&body?.sounds){f.settings=body.sounds;const value=await(await original(url)).json();Object.assign(value.sound_catalog.presets,f.presets);return json({...value,sounds:f.settings,sound_revision:value.sound_revision+1});}
   const response=await original(url,options);
   if(name==='/api/settings'){const value=await response.json();Object.assign(value.sound_catalog.presets,f.presets);if(f.settings)value.sounds=f.settings;return json(value);}
   if(name==='/api/state'){const value=await response.json();Object.assign(value.sounds.catalog.presets,f.presets);if(f.settings){value.sounds.preferences=f.settings;value.sounds.revision++;}return json(value);}
   return response;
  };
 },[...wav]);
 const upload=async(name,buffer=wav)=>{await page.locator('[data-sound-name]').fill('我的提醒');await page.locator('[data-sound-file]').setInputFiles({name,mimeType:'audio/wav',buffer});await page.locator('[data-sound-upload]').click();};
 const callCount=()=>page.evaluate(()=>customFixture.calls.length);
 let before=await callCount();await upload('bad.txt');await page.locator('#toast-root').getByText('音效只接受 wav / mp3 / ogg 文件。',{exact:true}).waitFor();assert.equal(await callCount(),before);
 await upload('large.wav',Buffer.alloc(1024*1024+1));await page.locator('#toast-root').getByText('音效文件不能超过 1MB。',{exact:true}).waitFor();assert.equal(await callCount(),before);check('Invalid extension and >1MB are rejected before upload',true);
 await page.evaluate(()=>customFixture.uploadError=true);await upload('bad.wav');await page.locator('#toast-root').getByText('音效内容损坏，请更换文件',{exact:true}).waitFor();check('Upload 400 preserves backend Chinese reason in toast',true);
 await page.evaluate(()=>customFixture.uploadError=false);await upload('my.wav');await page.locator('[data-sound-custom-preview]').waitFor();
 for(const kind of ['leader','assistant','delivery'])assert.equal(await page.locator(`[data-sound-kind=${kind}] option[value="custom:fixture"]`).innerText(),'我的提醒 · 自定义');
 const sent=await page.evaluate(()=>customFixture.calls.find(call=>call.name==='/api/sounds/custom'&&call.body.filename==='my.wav').body);assert.equal(sent.filename,'my.wav');assert.equal(sent.name,'我的提醒');assert.equal(Buffer.from(sent.data,'base64').compare(wav),0);check('Successful upload sends exact base64 and adds labeled custom option for every event',true);
 await page.locator('[data-sound-custom-preview]').click();await page.waitForFunction(()=>customFixture.starts===1);await page.locator('[data-sound-kind=leader]').selectOption('custom:fixture');await page.locator('[data-sound-preview=leader]').click();await page.waitForFunction(()=>customFixture.starts===2);assert.equal(await page.evaluate(()=>customFixture.decodes),1);check('List and event previews use decoded audio, cache by ID and preserve synthesis path',true);
 await page.locator('[data-sound-save]').click();await page.locator('[data-sound-status]').filter({hasText:'音效已保存'}).waitFor();await page.evaluate(()=>CrewSound.play('leader','custom-play-event'));await page.waitForFunction(()=>customFixture.starts===3);assert.equal(await page.evaluate(()=>customFixture.decodes),1);assert((await page.evaluate(()=>customFixture.gains)).every(value=>Math.abs(value-.41)<.001));check('Saved custom preset plays normal events through the shared 41 percent volume gain',true);await page.locator('#toast-root .toast').waitFor({state:'detached',timeout:7000});await shot('custom-sound-settings');
 await page.evaluate(()=>customFixture.deleteError=true);await page.locator('[data-sound-delete]').click();await page.locator('#toast-root').getByText('音效正在被任务交付使用，请先更换',{exact:true}).waitFor();assert.equal(await page.locator('[data-sound-custom-preview]').count(),1);check('Delete 409 retains entry and exact backend reason',true);
 await page.evaluate(()=>{CrewSound.forget('custom:fixture');customFixture.broken=true;});before=await page.evaluate(()=>customFixture.starts);await page.locator('[data-sound-preview=leader]').click();await page.locator('[data-sound-status][role=alert]').waitFor();assert.equal(await page.evaluate(()=>customFixture.starts),before);check('Decode failure is visible and plays no built-in fallback',true);
 await page.evaluate(()=>{CrewSound.forget('custom:fixture');customFixture.broken=false;customFixture.fileError=true;});await page.locator('[data-sound-preview=leader]').click();await page.locator('#toast-root').getByText('自定义音效加载失败：404 Not Found',{exact:true}).waitFor();assert.equal(await page.evaluate(()=>customFixture.starts),before);check('Load failure reports actual HTTP reason and stays silent',true);
 await page.evaluate(()=>{customFixture.fileError=false;customFixture.deleteError=false;});await page.locator('[data-sound-kind=leader]').selectOption('off');await page.locator('[data-sound-save]').click();await page.locator('[data-sound-status]').filter({hasText:'音效已保存'}).waitFor();await page.locator('[data-sound-delete]').click();await page.locator('[data-sound-delete]').waitFor({state:'detached'});
 await upload('again.wav');await page.locator('[data-sound-custom-preview]').click();await page.waitForFunction(()=>customFixture.starts===4);assert.equal(await page.evaluate(()=>customFixture.decodes),3);check('Successful delete invalidates the decoded cache; the same ID is decoded again',true);
 await page.evaluate(()=>customFixture.restore());
};
