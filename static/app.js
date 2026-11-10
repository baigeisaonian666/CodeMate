const $=s=>document.querySelector(s);let history=[],busy=false;
const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n};
async function api(path,options={}){const r=await fetch(path,options);let d;try{d=await r.json()}catch{throw Error('服务未返回有效响应')}if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'请求参数不正确');return d}
async function refresh(){const s=await api('/api/status');$('#model').textContent=s.model+(s.configured?'':' · 待配置');$('#fileCount').textContent=s.files.length;$('#chunkCount').textContent=s.chunks;$('#fileList').replaceChildren();for(const f of s.files){const row=el('div',undefined,'file');row.append(el('span',f.name));const b=el('button','×');b.title='删除 '+f.name;b.onclick=async()=>{if(busy)return;try{await api('/api/files?name='+encodeURIComponent(f.name),{method:'DELETE'});await refresh()}catch(e){$('#error').textContent=e.message}};row.append(b);$('#fileList').append(row)}}
async function upload(e){if(!e.target.files.length)return;if(e.target.files.length>5000){$('#uploadStatus').textContent='每次最多上传5000个文件，请分批选择。';e.target.value='';return;}const body=new FormData();for(const f of e.target.files)body.append('files',f,f.webkitRelativePath||f.name);$('#uploadStatus').textContent='正在切分并建立索引…';try{const d=await api('/api/files',{method:'POST',body});$('#uploadStatus').textContent='完成 '+d.results.filter(x=>x.ok).length+' / '+d.results.length+' 个文件。'+d.results.filter(x=>!x.ok).map(x=>x.name+'：'+x.error).join('；');await refresh()}catch(err){$('#uploadStatus').textContent=err.message}e.target.value=''}
$('#files').onchange=upload;$('#folder').onchange=upload;
function message(role,text){const box=el('article',undefined,'message '+role);const copy=el('button','复制','copy');copy.onclick=async()=>{try{await navigator.clipboard.writeText(box.dataset.text ?? text);copy.textContent='已复制'}catch{copy.textContent='请手动复制'}};box.append(copy,el('div',role==='user'?'YOU':'CODEMATE','message-label'));const content=el('div',undefined,'message-content');if(role==='assistant'){text.split('```').forEach((part,i)=>{if(i%2)content.append(el('pre',part.replace(/^[\w+-]*\n/,'')));else part.split('\n').forEach(line=>content.append(/^#{1,4} /.test(line)?el('h3',line.replace(/^#+ /,'')):el('div',line||' ')))})}else content.textContent=text;box.append(content);$('#messages').append(box);return box}
function inspect(d){$('#trace').replaceChildren();for(const t of d.trace||[]){const li=el('li',t.node,'done');li.append(el('small',Array.isArray(t.detail)?t.detail.join(' → '):t.detail));$('#trace').append(li)}$('#sources').replaceChildren();$('#sourceCount').textContent=(d.sources||[]).length;for(const s of d.sources||[]){const n=el('details');n.append(el('summary',s.name+':'+s.start+'–'+s.end+' · '+s.score),el('pre',s.excerpt));$('#sources').append(n)}$('#usage').textContent='引用与工具上下文：'+d.context_cost+'（保守估算）'}
$('#chatForm').onsubmit=async e=>{
 e.preventDefault(); if(busy)return;
 const question=$('#question').value.trim(); if(!question)return;
 busy=true; $('#send').disabled=true; $('#newChat').disabled=true;
 $('#send').textContent='生成中…'; $('#error').textContent='';
 $('#welcome').hidden=true; $('#runState').textContent='准备中';
 inspect({trace:[],sources:[],context_cost:0});
 message('user',question); $('#question').value='';
 const pending=message('assistant','正在准备工作流…');
 const content=pending.querySelector('.message-content');
 let received='';
 try {
   const response=await fetch('/api/chat/stream',{
     method:'POST',headers:{'Content-Type':'application/json'},
     body:JSON.stringify({question,history:history.slice(-6),top_k:Number($('#topK').value),
       budget:Number($('#budget').value),use_tools:$('#useTools').checked})
   });
   const result=await consumeChatStream(response,event=>{
     if(event.type==='status'){
       $('#runState').textContent=event.detail;
       if(!received)content.textContent=event.detail;
     }else if(event.type==='metadata'){
       inspect(event);
     }else if(event.type==='delta'){
       if(!received)content.replaceChildren();
       received+=event.text;
       content.append(document.createTextNode(event.text));
       pending.dataset.text=received;
       $('#runState').textContent='正在输出…';
     }
   });
   pending.remove(); message('assistant',result.answer);
   history.push({role:'user',content:question},{role:'assistant',content:result.answer.slice(0,20000)});
   inspect(result); $('#runState').textContent='已完成';
 }catch(err){
   if(!received)pending.remove();
   else pending.querySelector('.message-label').textContent='CODEMATE · 回答未完成';
   $('#error').textContent=err.message; $('#question').value=question; $('#runState').textContent='已中断';
 }finally{
   busy=false; $('#send').disabled=false; $('#newChat').disabled=false; $('#send').textContent='发送 ↑';
 }
};
$('#question').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('#chatForm').requestSubmit()}};
document.querySelectorAll('[data-question]').forEach(b=>b.onclick=()=>{$('#question').value=b.dataset.question;$('#question').focus()});
$('#topK').oninput=e=>$('#topValue').textContent=e.target.value;$('#budget').oninput=e=>$('#budgetValue').textContent=e.target.value;
$('#newChat').onclick=()=>{history=[];$('#messages').replaceChildren();$('#welcome').hidden=false;$('#error').textContent='';$('#question').value='';$('#trace').replaceChildren();$('#sources').replaceChildren();$('#sourceCount').textContent='0';$('#runState').textContent='就绪';$('#usage').textContent='等待首次运行'};
refresh().catch(e=>$('#error').textContent=e.message);
