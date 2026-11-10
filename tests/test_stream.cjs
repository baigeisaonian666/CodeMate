const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
vm.runInThisContext(fs.readFileSync(__dirname+'/../static/stream.js','utf8'));
function response(text){
 const bytes=new TextEncoder().encode(text); let i=0;
 return new Response(new ReadableStream({pull(controller){if(i<bytes.length)controller.enqueue(bytes.slice(i,i+=1));else controller.close()}}));
}
(async()=>{
 const text=': heartbeat\n\ndata: '+JSON.stringify({type:'delta',text:'中文\n代码😀'})+'\n\ndata: '+JSON.stringify({type:'done',answer:'中文\n代码😀'})+'\n\n';
 const events=[];
 const result=await consumeChatStream(response(text),e=>events.push(e));
 assert.equal(result.answer,'中文\n代码😀');assert.equal(events.length,2);
 await assert.rejects(()=>consumeChatStream(response('data: {"type":"delta","text":"partial"}\n\n'),()=>{}),/提前结束/);
 await assert.rejects(()=>consumeChatStream(response('data: {"type":"error","detail":"broken"}\n\n'),()=>{}),/broken/);
 console.log('SSE parser: UTF-8 boundaries, multiline text, heartbeat, EOF and errors passed');
})().catch(e=>{console.error(e);process.exitCode=1});
