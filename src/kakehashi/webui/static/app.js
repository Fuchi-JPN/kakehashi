let KEY = localStorage.getItem('kxh_key') || '';
document.getElementById('apiKey').value = KEY;
function saveKey(){ KEY = document.getElementById('apiKey').value; localStorage.setItem('kxh_key', KEY); alert('保存'); }
function h(){ return KEY ? {'X-API-Key': KEY} : {}; }
async function jget(p){ const r = await fetch(p, {headers: h()}); return r.json(); }
async function jpost(p, b){ const r = await fetch(p, {method:'POST', headers:{...h(), 'Content-Type':'application/json'}, body: JSON.stringify(b||{})}); return r.json(); }
async function jput(p, b){ const r = await fetch(p, {method:'PUT', headers:{...h(), 'Content-Type':'application/json'}, body: JSON.stringify(b||{})}); return r.json(); }
function show(t){ for (const s of document.querySelectorAll('main section')) s.hidden = true; document.getElementById('tab-'+t).hidden = false;
  if (t==='dash') loadDash(); if (t==='prov') loadProv(); if (t==='be') loadBe(); if (t==='pr') loadPrompts(); if (t==='log') loadLog(); if (t==='srv') loadSrv(); }
function fd(form){ const o={}; for (const [k,v] of new FormData(form).entries()) o[k]=v; return o; }
async function loadDash(){
  const d = await jget('/api/config/dashboard');
  if (d.error) { document.getElementById('dashCards').textContent = JSON.stringify(d); return; }
  const up = d.upstream || {};
  const upOk = up.status === 'ok';
  document.getElementById('dashCards').innerHTML = [
    card('稼働', `${d.requests} 件（直近${d.sample_size}件）`),
    card('上流到達性', upOk ? `OK ${up.latency_ms||''}ms` : `異常: ${(up.error||up.status||'').toString().slice(0,80)}`, upOk ? 'ok' : 'ng'),
    card('フォールバック', `${d.fallback_count}件（率${(d.fallback_rate*100).toFixed(1)}%）`, d.fallback_count ? 'warn' : 'ok'),
    card('ログ量', `${(d.log_size_bytes/1024).toFixed(1)} KB`),
    card('翻訳', d.translation_enabled ? '有効' : '無効', d.translation_enabled ? 'ok' : 'warn'),
  ].join('');
  const ap = d.active_provider || {};
  document.getElementById('dashEgress').innerHTML =
    `<table><tr><th>名前</th><td>${esc(ap.name||ap.id||'')}</td></tr>` +
    `<tr><th>ID</th><td>${esc(ap.id||'')}</td></tr>` +
    `<tr><th>プロトコル</th><td>${esc(ap.protocol||'')}</td></tr>` +
    `<tr><th>接続先</th><td>${esc(ap.base_url||'')}</td></tr>` +
    `<tr><th>使用モデル</th><td><b>${esc(ap.model||'')}</b></td></tr></table>`;
  document.getElementById('dashChain').innerHTML = (d.translation_chain||[]).map((b,i) =>
    `<div class="chain ${b.enabled?'':'off'}"><span class="rank">#${i+1}</span> <b>${esc(b.id)}</b> ${esc(b.name||'')} [${esc(b.protocol||'openai')}] ` +
    `${esc(b.base_url||'')} モデル=<b>${esc(b.model||'(未設定)')}</b> ${b.enabled?'有効':'<b>無効</b>'} ` +
    `${d.last_backend_used===b.id?'<span class="badge">最終使用</span>':''}</div>`).join('') || '(未登録)';
  const la = d.latency_avg_ms || {};
  const bar = (label, v, max) => `<div class="bar-row"><span>${label}</span><div class="bar"><div style="width:${max?Math.min(100, v/max*100):0}%"></div></div><b>${v??'-'} ms</b></div>`;
  const m = Math.max(la.translate_in||0, la.upstream||0, la.translate_out||0, 1);
  document.getElementById('dashLatency').innerHTML =
    bar('翻訳IN', la.translate_in, m) + bar('上流', la.upstream, m) + bar('翻訳OUT', la.translate_out, m) +
    `<div>合計平均: <b>${la.total??'-'} ms</b> / 入力形式内訳: ${esc(JSON.stringify(d.ingress_breakdown||{}))} / ` +
    `ストリーミング: ${d.stream_count}件・通常: ${d.non_stream_count}件 / プレースホルダ失敗計: ${d.placeholder_fail_total}</div>`;
  document.getElementById('dashRecent').innerHTML =
    `<table class="recent"><tr><th>時刻</th><th>経路</th><th>モデル</th><th>翻訳</th><th>時間</th></tr>` +
    (d.recent||[]).map(r => `<tr><td>${esc((r.ts||'').slice(11,19))}</td><td>${esc(r.route||'')}</td>` +
      `<td>${esc(r.model||'')}</td><td>${esc(r.backend||'-')}${r.fallbacks?` (FB${r.fallbacks})`:''}</td>` +
      `<td>${r.total_ms??'-'}ms</td></tr>`).join('') + `</table>`;
}
function card(t, v, cls){ return `<div class="card ${cls||''}"><div class="card-t">${t}</div><div class="card-v">${v}</div></div>`; }
function esc(s){ return String(s??'').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
async function loadProv(){
  const list = await jget('/api/config/providers');
  document.getElementById('provList').innerHTML = list.map(p =>
    `<div><b>${p.id}</b> ${p.name} [${p.protocol}] ${p.base_url} モデル=${p.model} ${p.active?'（使用中）':''}
    <button onclick="activate('${p.id}')">使用中に設定</button>
    <button onclick="testProv('${p.id}')">接続確認</button>
    <button onclick="delProv('${p.id}')">削除</button></div>`).join('');
}
async function activate(id){ await jpost('/api/config/providers/active', {id}); loadProv(); }
async function delProv(id){ if(!confirm(id+' を削除しますか?')) return; const r = await fetch('/api/config/providers/'+id, {method:'DELETE', headers:h()}); alert(JSON.stringify(await r.json())); loadProv(); }
async function testProv(id){ const r = await jpost('/api/config/providers/'+id+'/test', {}); document.getElementById('provMsg').textContent = JSON.stringify(r, null, 2); }
async function fetchModels(){
  const f = fd(document.getElementById('provForm'));
  const r = await jpost('/api/config/providers/fetch-models', {protocol: f.protocol, base_url: f.base_url, api_key: f.api_key, api_key_env: f.api_key_env});
  document.getElementById('provMsg').textContent = JSON.stringify(r, null, 2);
  const sel = document.getElementById('modelSel'); sel.innerHTML = (r.models||[]).map(m=>`<option>${m}</option>`).join('');
}
function pickModel(){ const v = document.getElementById('modelSel').value; if (v) document.querySelector('#provForm [name=model]').value = v; }
async function saveProvider(e){ e.preventDefault(); const f = fd(document.getElementById('provForm'));
  const manual = document.getElementById('modelManual').value; if (manual) f.model = manual;
  const body = {name:f.name, protocol:f.protocol, base_url:f.base_url, api_key:f.api_key||'', api_key_env:f.api_key_env||'', model:f.model, timeout_s:parseInt(f.timeout_s||'300')};
  if (f.id) body.id = f.id;
  const params = {merge_policy: f.merge_policy};
  if (f.temperature!=='') params.temperature = parseFloat(f.temperature);
  if (f.max_tokens!=='') params.max_tokens = parseInt(f.max_tokens);
  body.params = params;
  let r;
  if (f.id) { r = await fetch('/api/config/providers/'+f.id, {method:'PUT', headers:{...h(),'Content-Type':'application/json'}, body:JSON.stringify(body)}).then(x=>x.json()); }
  else { r = await jpost('/api/config/providers', body); }
  document.getElementById('provMsg').textContent = JSON.stringify(r, null, 2); loadProv(); return false; }
async function loadBe(){ const l = await jget('/api/config/backends');
  document.getElementById('beList').innerHTML = l.map(b=>`<div><b>${b.id}</b> ${b.name} [${b.protocol||'openai'}] ${b.base_url} モデル=${b.model} ${b.enabled?'有効':'無効'} <button onclick="editBe('${b.id}')">編集</button> <button onclick="testBe('${b.id}')">接続確認</button> <button onclick="delBe('${b.id}')">削除</button></div>`).join(''); }
async function fetchBackendModels(){
  const f = fd(document.getElementById('beForm'));
  const r = await jpost('/api/config/backends/fetch-models', {protocol: f.protocol, base_url: f.base_url, api_key: f.api_key||'', api_key_env: f.api_key_env||''});
  document.getElementById('beMsg').textContent = JSON.stringify(r, null, 2);
  const sel = document.getElementById('beModelSel'); sel.innerHTML = (r.models||[]).map(m=>`<option>${m}</option>`).join('');
}
function pickBackendModel(){ const v = document.getElementById('beModelSel').value; if (v) document.querySelector('#beForm [name=model]').value = v; }
async function editBe(id){ const l = await jget('/api/config/backends'); const b = l.find(x=>x.id===id); if(!b) return;
  const form = document.getElementById('beForm');
  form.id.value = b.id; form.name.value = b.name||''; form.protocol.value = b.protocol||'openai';
  form.base_url.value = b.base_url; form.model.value = b.model;
  form.api_key_env.value = b.api_key_env||''; form.timeout_s.value = b.timeout_s||30; form.enabled.checked = !!b.enabled;
  window.scrollTo(0, form.offsetTop); }
async function saveBackend(e){ e.preventDefault(); const f = fd(document.getElementById('beForm'));
  const manual = document.getElementById('beModelManual').value; if (manual) f.model = manual;
  const body = {name:f.name||'', protocol:f.protocol||'openai', base_url:f.base_url, model:f.model, api_key:f.api_key||'', api_key_env:f.api_key_env||'', timeout_s:parseInt(f.timeout_s||'30'), enabled:!!f.enabled};
  if (f.id) body.id = f.id;
  let r;
  if (f.id) { r = await fetch('/api/config/backends/'+f.id, {method:'PUT', headers:{...h(),'Content-Type':'application/json'}, body:JSON.stringify(body)}).then(x=>x.json()); }
  else { r = await jpost('/api/config/backends', body); }
  document.getElementById('beMsg').textContent = JSON.stringify(r, null, 2); loadBe(); return false; }
async function delBe(id){ await fetch('/api/config/backends/'+id, {method:'DELETE', headers:h()}); loadBe(); }
async function testBe(id){ const r = await jpost('/api/config/backends/'+id+'/test', {}); document.getElementById('beMsg').textContent = JSON.stringify(r, null, 2); }
async function loadLog(){ const c = await jget('/api/config/logging'); document.getElementById('logCfg').textContent = JSON.stringify(c, null, 2);
  document.getElementById('logEnabled').checked = !!c.translation_log_enabled;
  document.getElementById('logTail').textContent = JSON.stringify(await jget('/api/config/logging/tail?n=20'), null, 2); }
async function loadPrompts(){ const p = await jget('/api/config/prompts');
  document.getElementById('prJa2en').value = p.ja2en||''; document.getElementById('prEn2ja').value = p.en2ja||'';
  document.getElementById('prGuard').value = p.output_guard||'';
  document.getElementById('prMsg').textContent = '読込完了';
  const cs = await jget('/api/config/code-strings');
  document.getElementById('csEnabled').checked = !!cs.enabled;
  document.getElementById('csMinLen').value = cs.min_length||8; }
async function saveCodeStrings(){ const r = await jput('/api/config/code-strings',
  {enabled: document.getElementById('csEnabled').checked,
   min_length: parseInt(document.getElementById('csMinLen').value||'8')});
  document.getElementById('csMsg').textContent = JSON.stringify(r); }
async function savePrompts(){ const r = await jput('/api/config/prompts',
  {ja2en: document.getElementById('prJa2en').value, en2ja: document.getElementById('prEn2ja').value,
   output_guard: document.getElementById('prGuard').value});
  document.getElementById('prMsg').textContent = JSON.stringify(r, null, 2).slice(0, 500); }
async function resetPrompts(){ const r = await jpost('/api/config/prompts/reset', {});
  document.getElementById('prJa2en').value = r.ja2en||''; document.getElementById('prEn2ja').value = r.en2ja||'';
  document.getElementById('prGuard').value = r.output_guard||'';
  document.getElementById('prMsg').textContent = '既定に戻しました'; }
async function saveLog(){ const r = await jput('/api/config/logging', {translation_log_enabled: document.getElementById('logEnabled').checked}); alert(JSON.stringify(r).slice(0,200)); }
async function loadSrv(){ document.getElementById('srv').textContent = JSON.stringify((await jget('/api/config/full')).server, null, 2); }
async function saveServer(e){ e.preventDefault(); const f = fd(document.getElementById('srvForm')); const b={}; if(f.host)b.host=f.host; if(f.port)b.port=parseInt(f.port); if(f.api_key!==undefined)b.api_key=f.api_key; alert(JSON.stringify(await jput('/api/config/server', b)).slice(0,300)); return false; }
show('dash');
