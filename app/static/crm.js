'use strict';
function crmNotice() {
  const s=summary.crm||{};
  const stale=s.updated_at && new Date(s.updated_at).toLocaleDateString('en-CA',{timeZone:cfg.timezone})!==new Date().toLocaleDateString('en-CA',{timeZone:cfg.timezone});
  return `<div class="search-result-note"><strong>${s.updated_at?'Filtro do CRM ativo':'Antes de começar: envie a planilha do CRM'}</strong><p>${s.updated_at?`${number(s.sites)} sites · ${number(s.cnpjs)} CNPJs · atualização ${dateLabel(s.updated_at,true)}.${stale?' A lista é de outro dia. Envie a exportação de hoje para atualizar.':''}`:'O Lume pode ignorar as lojas que já estão no CRM da empresa.'} <a class="quiet-link" href="#crm">${s.updated_at?'Atualizar planilha':'Enviar planilha'} ↗</a></p></div>`;
}
function crmReport(s) {
  if(!s.updated_at) return '<p>Nenhuma planilha enviada nesta conta.</p>';
  return `<p><strong>Lista atualizada em ${dateLabel(s.updated_at,true)}</strong></p><p>${number(s.sites)} sites e ${number(s.cnpjs)} CNPJs únicos usados no filtro. ${number(s.excluded_leads)} lojas já encontradas estão ocultas por constarem no CRM.</p><p class="field-help">${number(s.valid_rows)} linhas aproveitadas · ${number(s.duplicate_rows)} repetidas · ${number(s.skipped_rows)} sem identificador válido · ${number(s.invalid_cells)} campos inválidos.</p>${s.skipped_rows||s.invalid_cells?'<p>Alguns registros não puderam ser identificados. Confira as linhas sem site ou CNPJ válido na exportação.</p>':''}${s.errors?.length?`<details><summary>Ver avisos da planilha</summary><ul>${s.errors.map(x=>`<li>${e(x)}</li>`).join('')}</ul></details>`:''}`;
}
async function crmPage() {
  const s=await api('/crm');
  return `${heading('Planilha do CRM.', 'Envie a exportação mais recente antes de buscar lojas.')}
  <section class="panel"><div class="form-section" id="crm-status">${crmReport(s)}</div></section>
  <form id="crm-form" class="panel mt"><div class="form-section"><h2>Atualizar lojas que devem ser ignoradas</h2><p>Cada envio substitui a lista anterior desta conta. Aprovar ou descartar uma loja no Lume não altera esta lista.</p>
  <label class="upload-zone">${icon('upload')}<b>Escolha a planilha exportada do CRM</b><small>Excel (.xlsx) ou CSV · até 20 MB e 50.000 linhas</small><input id="crm-file" name="file" type="file" accept=".xlsx,.csv" required></label>
  <div id="crm-mapping" class="mt" aria-live="polite"></div><p id="crm-feedback" role="status" aria-live="polite"></p>
  </div><div class="form-actions"><button class="button primary" id="crm-submit" type="submit" disabled>Atualizar lista do CRM</button></div></form>
  <details class="disclosure mt"><summary>Como funciona o filtro?</summary><div class="disclosure-body"><p>Comparamos o site e o CNPJ. Lojas presentes na lista ficam fora da busca, da revisão e das exportações. Se o CNPJ só aparecer depois de abrir o site, o filtro é aplicado após essa leitura. Leituras já em andamento podem terminar, mas seus resultados também passam pelo filtro.</p><p>Não adicionamos clientes ao CRM e não sincronizamos etapas. Não estar nesta planilha não garante que a loja esteja livre agora: ela representa o momento da exportação. Rejeitar uma loja no Lume continua sendo uma decisão separada.</p><p>Guardamos somente sites e CNPJs normalizados, com data e contagens da importação, no espaço privado da sua conta no homelab. O arquivo original e as demais colunas não são guardados. Não enviamos os dados do CRM aos buscadores.</p><p>Arquivos inválidos ou sem identificadores válidos mantêm a lista anterior. Use a primeira linha para os cabeçalhos. No Excel, envie os identificadores como valores, não como fórmulas.</p></div></details>`;
}
function bindCRM() {
  const form=$('#crm-form');if(!form)return;
  const input=$('#crm-file',form), button=$('#crm-submit',form), feedback=$('#crm-feedback',form), mapping=$('#crm-mapping',form);
  let version=0;
  async function inspect(sheet='') {
    const ticket=++version,file=input.files[0];button.disabled=true;mapping.innerHTML='';
    if(!file){feedback.textContent='';return;}
    if(file.size>20000000){feedback.textContent='Limite: 20 MB por arquivo.';return;}
    feedback.textContent='Conferindo a planilha…';
    const body=new FormData();body.append('file',file);body.append('sheet',sheet);
    try {
      const p=await api('/crm/preview',{method:'POST',body});
      if(ticket!==version||!form.isConnected)return;
      const options=selected=>'<option value="-1">Não usar esta coluna</option>'+p.headers.map((h,i)=>`<option value="${i}" ${i===selected?'selected':''}>${e(h||'Coluna '+(i+1))}</option>`).join('');
      mapping.innerHTML=`${p.sheets.length>1?`<label>Aba do Excel<select name="sheet">${p.sheets.map(s=>`<option ${s===p.sheet?'selected':''}>${e(s)}</option>`).join('')}</select></label>`:`<input type="hidden" name="sheet" value="${e(p.sheet)}">`}<p>${number(p.rows)} linhas encontradas. ${p.site_column>=0||p.cnpj_column>=0?'Colunas reconhecidas automaticamente.':'Escolha a coluna de site ou CNPJ abaixo.'}</p><details ${p.site_column<0&&p.cnpj_column<0?'open':''}><summary>Conferir colunas usadas</summary><div class="form-grid mt"><label>Site da loja<select name="site_column">${options(p.site_column)}</select></label><label>CNPJ<select name="cnpj_column">${options(p.cnpj_column)}</select></label></div></details>`;
      $('select[name=sheet]',form)?.addEventListener('change',event=>inspect(event.target.value));
      feedback.textContent='Pronta para atualizar. A nova lista substituirá a anterior.';button.disabled=false;
    } catch(err){if(ticket===version){feedback.textContent=err.message;}}
  }
  input.addEventListener('change',()=>inspect());
  form.addEventListener('submit',async event=>{
    event.preventDefault();button.disabled=true;input.disabled=true;feedback.textContent='Atualizando o filtro…';
    const body=new FormData(form);body.set('file',input.files[0]);
    try {
      const result=await api('/crm',{method:'POST',body});
      if(!form.isConnected)return;
      $('#crm-status').innerHTML=crmReport(result);feedback.textContent='Lista atualizada. Você já pode buscar lojas.';
      input.value='';mapping.innerHTML='<a class="button secondary" href="#search">Buscar lojas →</a>';
      await refreshSummary();
    } catch(err){feedback.textContent=err.message;button.disabled=false;}finally{input.disabled=false;}
  });
}
