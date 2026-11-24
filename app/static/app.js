'use strict';
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const e = escapeHTML;
const number = value => Number(value || 0).toLocaleString('pt-BR');
const statusLabels = {queued:'Na fila',review:'Revisar',approved:'Aprovada',ready:'Pronta para entregar',qualified:'Qualificada',rejected:'Descartada',blocked:'Não contatar',duplicate:'Duplicada',running:'Em andamento',completed:'Meta atingida',partial:'Rodada encerrada',failed:'Falha',cancelled:'Interrompida',interrupted:'Reiniciada'};
const stages = {new:'Novo contato',contacted:'Contato feito',replied:'Respondeu',meeting:'Reunião marcada',won:'Cliente conquistado',lost:'Sem interesse'};
const icons = {
 grid:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
 store:'<path d="M3 10h18l-2-6H5l-2 6ZM5 14v6h14v-6M10 20v-6h4v6M3 10v2a3 3 0 0 0 5 2 3 3 0 0 0 4 0 3 3 0 0 0 4 0 3 3 0 0 0 5-2v-2"/>',
 check:'<path d="m8 12 3 3 5-6"/><rect x="3" y="3" width="18" height="18" rx="5"/>',
 upload:'<path d="M12 16V3m-5 5 5-5 5 5M4 15v5h16v-5"/>',
 download:'<path d="M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4"/>',
 clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v6l4 2"/>',
 settings:'<path d="m9 3-1 3-3 1-2 4 2 2v4l4 3 3-1 3 1 4-3v-4l2-2-2-4-3-1-1-3H9Z"/><circle cx="12" cy="12" r="3"/>',
 logout:'<path d="M9 4H4v16h5m6-13 5 5-5 5M9 12h11"/>',
 arrow:'<path d="M4 12h16m-6-6 6 6-6 6"/>',
 play:'<path d="m8 5 11 7-11 7V5Z"/>',
 target:'<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
 contact:'<rect x="3" y="5" width="18" height="14" rx="3"/><path d="m3 7 9 6 9-6"/>',
 info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-11v1"/>',
 search:'<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6"/>',
 shield:'<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/>'
};
const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${icons[name] || icons.store}</svg>`;
function hydrate(root = document) {
  $$('[data-icon]', root).forEach(el => el.replaceWith(Object.assign(document.createElement('span'), {innerHTML:icon(el.dataset.icon)})));
  $$('[data-progress]', root).forEach(el => el.style.setProperty('--progress', Math.min(100, Number(el.dataset.progress)) + '%'));
  $$('[data-height]', root).forEach(el => el.style.setProperty('--height', Number(el.dataset.height) + 'px'));
}
function safeURL(value) { try { const u = new URL(value); return ['https:','http:'].includes(u.protocol) ? e(u.href) : '#'; } catch {return '#';} }
function dateLabel(value, withTime = false) {
  if (!value) return '—';
  const d = new Date(value.length === 10 ? value + 'T12:00:00' : value);
  return Number.isNaN(d.valueOf()) ? e(value) : d.toLocaleString('pt-BR', withTime ? {day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'} : {day:'2-digit',month:'short'});
}
const badge = status => `<span class="badge ${e(status)}">${e(statusLabels[status] || status)}</span>`;
let view = 'dashboard', summary = null, cfg = null, currentLead = null, needsSetup = false;
let filters = {status:'all',q:'',stage:'all',today:false,page:1,include_unverified:false,sort:'priority',focus:'all'};
let importMode = 'domains';
let toastTimer, renderTicket = 0;

async function api(path, options = {}) {
  const headers = {...options.headers};
  if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const response = await fetch('/api' + path, {...options,headers});
  let body;
  try {body = await response.json();} catch {throw new Error('O servidor não respondeu como esperado. Tente novamente.');}
  if (!response.ok) {
    if (response.status === 401 && path !== '/login') showLogin(false);
    let detail = body.detail;
    if (Array.isArray(detail)) detail = detail.map(x => `${x.loc?.slice(1).join('.') || 'Campo'}: ${x.msg}`).join('; ');
    throw new Error(detail || 'Não foi possível concluir esta ação.');
  }
  return body;
}
function toast(message, error = false) {
  const el = $('#toast'); el.textContent = message; el.className = 'toast' + (error ? ' error' : '');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.add('hidden'), error ? 10000 : 6500);
}
function showLogin(setup,register=false) {
  authMode=setup?'setup':register?'register':'login';needsSetup=setup;
  renderTicket++;currentLead=null;signedUser=null;summary=null;cfg=null;
  if($('#lead-dialog').open)$('#lead-dialog').close();
  $('#lead-dialog-content').innerHTML='';$('#content').innerHTML='';
  $('#app').classList.add('hidden');$('#login-screen').classList.remove('hidden');
  $('#setup-token-label').classList.toggle('hidden',!setup);
  $('#display-name-label').classList.toggle('hidden',!register);
  $('#registration-info').classList.toggle('hidden',!register);
  $('#register-toggle').classList.toggle('hidden',setup||!registrationOpen);
  $('#register-toggle').textContent=register?'Já tenho conta · entrar':'Criar minha conta';
  $('#login-title').textContent=setup?'Vamos preparar seu espaço.':register?'Seu espaço de prospecção.':'Que bom ter você aqui.';
  $('#login-description').textContent=setup?'Use o código de instalação para criar sua conta de administrador.':register?'Crie seu usuário e uma senha com pelo menos 10 caracteres.':'Entre com seu usuário e sua senha.';
  $('#login-submit').textContent=setup?'Criar meu acesso →':register?'Criar conta →':'Entrar no Lume →';
  $('#login-form [name=password]').autocomplete=setup||register?'new-password':'current-password';
  $('#login-form [name=password]').value='';$('#login-error').textContent='';
}
$('#login-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = $('#login-submit'); button.disabled = true; $('#login-error').textContent = '';
  try {
    const data = Object.fromEntries(new FormData(event.target));
    await api('/'+authMode, {method:'POST',body:JSON.stringify(data)});
    event.target.reset();history.replaceState(null,'','#dashboard');view='dashboard';filters={status:'all',q:'',stage:'all',today:false,page:1,include_unverified:false,sort:'priority',focus:'all'};await boot();
  } catch (err) {$('#login-error').textContent = err.message;} finally {button.disabled = false;}
});
$('#logout').addEventListener('click', async () => {try {await api('/logout',{method:'POST'}); showLogin(false);} catch(err) {toast(err.message,true);}});
function heading(title, text, action = '') {
  return `<div class="page-heading"><div><h1>${title}</h1><p>${text}</p></div>${action || `<span class="date-pill">${dateLabel(summary?.date)} · Brasil</span>`}</div>`;
}
function empty(title, description, action = '', symbol = 'store') {
  return `<div class="empty-state"><div class="empty-icon">${icon(symbol)}</div><h3>${title}</h3><p>${description}</p>${action}</div>`;
}
function runBanner() {
  if (!summary?.running) return '';
  const run = summary.last_run?.data || {};
  return `<div class="running-banner"><span class="spinner"></span><div><strong>${run.search_waiting?'Aguardando confirmação no Google':'Busca em andamento'}</strong><br>${e(run.message || 'Preparando…')} · ${number(run.checked)} verificadas</div><button class="button small" data-action="stop">Interromper</button></div>`;
}
function table(items) {
  if (!items.length) return empty('Seus contatos vão aparecer aqui.', 'Pesquise um tema para encontrar lojas e contatos.', '<a class="button secondary" href="#search">' + icon('search') + 'Buscar lojas</a>');
  return `<div class="table-scroll"><table class="prospect-table"><thead><tr><th>LOJA / SITE</th><th>CNPJ</th><th>TELEFONE</th><th>E-MAIL</th><th>VISITAS / MÊS</th><th></th></tr></thead><tbody>${items.map(l => {
    const d=l.data, contacts=d.contacts || [], email=contacts.find(c=>c.type==='email'), phone=contacts.find(c=>c.type==='whatsapp') || contacts.find(c=>c.type==='phone');
    return `<tr><td><div class="store-cell"><div><button class="store-name" data-lead="${l.id}">${e(d.name || l.domain)}</button><a class="store-domain" href="${safeURL(d.storefront_url || "https://"+l.domain)}" target="_blank" rel="noopener noreferrer">${e(d.storefront_url ? new URL(d.storefront_url).hostname : l.domain)} ↗</a><span class="cell-sub">${badge(l.status)} · ${e(stages[l.stage])}</span>${d.prospecting?`<span class="next-step">${e(d.prospecting.label)}</span>`:''}</div></div></td><td class="nowrap" data-label="CNPJ">${e(d.cnpj || (d.cnpjs?.length>1?'Conferir no detalhe':'Não informado'))}</td><td class="nowrap" data-label="Telefone">${contactLink(phone)}${phone?.type==='whatsapp'?'<span class="cell-sub">WhatsApp</span>':''}</td><td class="email-cell" data-label="E-mail">${contactLink(email)}</td><td data-label="Visitas / mês"><span class="traffic-value">${d.visits!=null&&d.traffic_metric==='monthly_visits'?number(d.visits):'Não informado'}</span>${d.visits!=null&&d.traffic_metric==='monthly_visits'?`<span class="cell-sub" title="${e(d.traffic_source || 'Fonte não informada')}">Estimativa · ${e(d.traffic_period || 'Período não informado')}</span>`:''}${l.status==='qualified'&&d.current_problems?.length?'<span class="cell-sub">Revisar dados</span>':''}</td><td><button class="button small" data-lead="${l.id}">Abrir ↗</button></td></tr>`;
  }).join('')}</tbody></table></div>`;
}
function contactLink(contact) {
  if(!contact) return 'Não informado';
  const value=String(contact.value||''), digits=value.replace(/\D/g,'');
  let href='';
  if(contact.type==='email' && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) href='mailto:'+encodeURIComponent(value);
  if(contact.type==='phone' && digits.length>=10 && digits.length<=15) href='tel:'+digits;
  if(contact.type==='whatsapp' && digits.length>=10 && digits.length<=15) href='https://wa.me/'+((digits.length===10||digits.length===11)?'55':'')+digits;
  return href?`<a class="contact-link" href="${e(href)}" target="_blank" rel="noopener noreferrer">${e(value)}</a>`:e(value);
}
function similarwebURL(lead) {
  try {return 'https://www.similarweb.com/website/'+encodeURIComponent(new URL(lead.data.storefront_url || 'https://'+lead.domain).hostname)+'/';} catch {return '';}
}
function topicForm() {
  return `<form id="topic-form" class="panel topic-panel"><div class="form-section"><h2>O que você quer prospectar?</h2><p>Encontre lojas Shopify brasileiras para oferecer checkout e gateway. Digite o segmento e inicie a busca.</p><div class="topic-controls"><label>Tema das lojas<input name="topic" id="topic-input" value="${e(cfg.search_topic)}" placeholder="Ex.: perfumes, roupas, cosméticos" minlength="2" maxlength="80" required autocomplete="off"></label><button class="button primary" type="submit" ${summary.running?'disabled':''}>${icon('search')}Buscar lojas</button></div><div class="topic-suggestions">${['perfumes','roupas','cosméticos','calçados'].map(topic=>`<button type="button" class="topic-chip" data-topic="${topic}">${topic}</button>`).join('')}<span>Buscar novamente continua de onde parou.</span></div><details class="mt"><summary>Detalhes da pesquisa</summary><p class="field-help">A busca combina variações do tema, domínios próprios e myshopify.com. Cada consulta mantém sua posição; uma consulta indisponível não apaga os resultados das outras. A lista mostra lojas com evidências de origem brasileira, Shopify, catálogo ativo, tema compatível e contato público. Tráfego, estoque próprio e CRM continuam sujeitos à conferência. Esses sinais não certificam a idoneidade da empresa.</p><div class="inline-actions mt"><button class="button small" type="button" data-action="show-query">Ver consultas usadas</button><button class="button small" type="button" data-action="reset-search">Recomeçar este tema</button><a class="quiet-link" href="#import">Adicionar sites manualmente ↗</a></div><div id="query-preview"></div><p class="field-help">${cfg.search_backend==='searxng'?'Busca via SearXNG configurado na instalação.':cfg.search_backend==='browser'?'Busca automática pelo Google em uma janela própria do navegador.':'Busca automática em segundo plano, sem abrir janelas de pesquisa.'} Os mecanismos podem limitar consultas ou não retornar resultados. Nenhum CAPTCHA é contornado.</p></details></div></form>`;
}
async function searchPage() {
  const topic=cfg.search_topic;
  const results=topic?await api('/leads?'+new URLSearchParams({topic,page:filters.page,sort:'priority'})):null;
  const run=summary.last_run;
  return `${heading('Buscar lojas.', 'Escolha um tema e deixe o Lume procurar os sites e contatos.')}${crmNotice()}${topicForm()}${runBanner()}${run?.data.search_topic?`<div class="search-result-note ${run.status==='failed'?'search-failed':''}"><strong>${e(run.data.search_topic)} · ${number(run.data.brazilian)} brasileiras com evidências · ${number(run.data.checked)} verificadas · ${number(run.data.filtered_out)} fora dos critérios · ${number(run.data.crm_skipped)} ocorrências filtradas pelo CRM</strong><p>${e(run.data.message)}</p>${run.data.warnings?.length?`<details><summary>Ocorrências da busca (${run.data.warnings.length})</summary>${run.data.warnings.map(w=>'<p>'+e(w)+'</p>').join('')}</details>`:''}</div>`:''}${cfg.search_backend==='browser'?'<p class="field-help mt">O Lume abre uma janela própria do navegador, pesquisa e lê os resultados automaticamente. Se o Google pedir uma verificação, conclua nessa janela; a busca continua sozinha.</p>':''}${results?`<section class="panel mt"><div class="panel-head"><div><h3>Lojas encontradas: ${e(topic)}</h3><p>${number(results.total)} lojas brasileiras com evidências. Ordenadas para facilitar a revisão. Confira CRM, estoque próprio e tráfego antes do contato.</p></div></div>${table(results.items)}${results.hidden?`<details class="mt"><summary>${number(results.hidden)} registros aguardando verificação ou fora dos critérios</summary><p>Origem incerta, loja estrangeira, tema incompatível, falta de catálogo/contato ou leitura incompleta. Esses itens não contam como lojas brasileiras.</p><button class="button small" data-action="show-unverified">Inspecionar registros omitidos</button></details>`:''}${results.pages>1?`<div class="pagination"><span>${number(results.total)} candidatas</span><div><button class="button small" data-action="prev" ${results.page<=1?'disabled':''}>←</button><span>${results.page} de ${results.pages}</span><button class="button small" data-action="next" ${results.page>=results.pages?'disabled':''}>→</button></div></div>`:''}</section>`:''}`;
}
async function dashboardPage() {
  const s=summary;
  const queue=await api('/leads?focus=new&sort=priority');
  return `${heading('Seu dia de prospecção.', 'Encontre lojas, confira a disponibilidade e prepare sua abordagem.')}${crmNotice()}${topicForm()}${runBanner()}
    <div class="work-summary"><a href="#leads" data-queue="new"><strong>${number(s.prospect_count)}</strong><span>lojas na fila de avaliação</span></a><a href="#leads" data-queue="crm"><strong>${number(s.crm_count)}</strong><span>aguardam conferência no CRM</span></a><div><strong>${number(s.delivered_today)} <small>/ ${number(s.goal)}</small></strong><span>qualificadas novas hoje</span></div></div>
    <section class="panel"><div class="panel-head"><div><h3>Por onde começar</h3><p>Novas lojas com mais dados disponíveis aparecem primeiro. O próximo passo está indicado em cada linha.</p></div><a class="quiet-link" href="#leads">Ver todas ↗</a></div>${queue.items.length?table(queue.items.slice(0,8)):empty('Sua próxima oportunidade começa por um tema.','A busca reúne lojas brasileiras e seus contatos. As que atenderem aos critérios entram na meta do dia.')}<div class="panel-bottom"><span class="field-help">Uma loja encontrada ainda pode precisar de tráfego, estoque próprio e CRM. Cada loja qualificada conta uma vez.</span></div></section>
    <details class="disclosure mt"><summary>Meta e rotina diária</summary><div class="disclosure-body"><p class="field-help">Shopify · Brasil · ≥ ${number(cfg.min_visits)} visitas mensais estimadas · estoque próprio · contato com fonte · livre no CRM. A meta depende de dados e resultados suficientes.</p><p class="field-help">${s.schedule_enabled?`Rotina diária às ${s.schedule_hour}h, usando o último tema salvo.`:'Você pode ativar a rotina diária nas configurações.'}</p>${s.last_run?`<p class="field-help">Última execução: ${e(s.last_run.data.message || statusLabels[s.last_run.status])}</p>`:''}<div class="inline-actions"><a href="#activity" class="quiet-link">Ver histórico ↗</a><a class="quiet-link" href="/api/export?scope=today">Baixar qualificadas de hoje ↗</a>${s.total?'<button class="button small" data-action="start">Verificar fila existente</button>':''}</div></div></details>`;
}
async function leadsPage() {
  const review = view==='review';
  const params = new URLSearchParams({...filters,status:review?'review':filters.status});
  const result = await api('/leads?'+params);
  return `${heading(review?'Lojas para revisar.':'Lojas e contatos.', review?'Confira os contatos e aprove. Visitas e CRM são opcionais.':'O site e os contatos de cada loja, juntos.', review?'':'<a class="button" href="/api/export?scope=approved">'+icon('download')+'Baixar aprovadas</a>')}${runBanner()}
    <div class="queue-tabs" role="group" aria-label="Fila de prospecção">${Object.entries(review?{all:'Todas',new:'Novas para avaliar',crm:'Conferir CRM'}:{all:'Todas',new:'Novas para avaliar',crm:'Conferir CRM',approved:'Aprovadas',ready:'Critérios atendidos'}).map(([key,label])=>`<button type="button" data-focus="${key}" class="topic-chip ${filters.focus===key?'selected':''}" aria-pressed="${filters.focus===key}">${label}</button>`).join('')}</div><div class="toolbar compact-toolbar"><select id="sort-filter" class="filter" aria-label="Ordenar lojas"><option value="priority" ${filters.sort!=='recent'?'selected':''}>Prioridade de revisão</option><option value="recent" ${filters.sort==='recent'?'selected':''}>Mais recentes</option></select><input id="search" class="search-input" type="search" aria-label="Buscar loja ou domínio" placeholder="Buscar loja ou site…" value="${e(filters.q)}">${!review?`<select class="filter" id="status-filter" aria-label="Situação"><option value="all">Todas as situações</option>${Object.entries(statusLabels).filter(([k])=>['queued','review','approved','ready','qualified','rejected','blocked','duplicate'].includes(k)).map(([k,v])=>`<option value="${k}" ${filters.status===k?'selected':''}>${v}</option>`).join('')}</select>`:''}<details class="filter-details" ${filters.stage!=='all'||filters.today||filters.include_unverified?'open':''}><summary>Mais filtros</summary><div class="advanced-filters"><label class="check-label"><input type="checkbox" id="unverified-filter" ${filters.include_unverified?'checked':''}> Incluir registros fora dos critérios</label><select class="filter" id="stage-filter" aria-label="Etapa comercial"><option value="all">Todas as etapas</option>${Object.entries(stages).map(([k,v])=>`<option value="${k}" ${filters.stage===k?'selected':''}>${v}</option>`).join('')}</select><label class="check-label"><input type="checkbox" id="today-filter" ${filters.today?'checked':''}> Entregues hoje</label></div></details></div>
    <section class="panel">${result.items.length ? table(result.items) : empty(review?'Nenhuma loja para revisar.':'Nenhuma loja nesta seleção.',review?'As lojas com dados pendentes aparecerão aqui.':'Adicione lojas ou ajuste os filtros para encontrar seus contatos.','','check')}<div class="pagination"><span>${number(result.total)} loja${result.total!==1?'s':''} na seleção</span><div><button class="button small" data-action="prev" ${result.page<=1?'disabled':''}>←</button><span>${result.page} de ${result.pages}</span><button class="button small" data-action="next" ${result.page>=result.pages?'disabled':''}>→</button></div></div></section>`;
}
function importPage() {
  return `${heading('Adicionar lojas.', 'Escolha como trazer os sites que você quer prospectar.')}
  <div class="import-container"><div class="import-tabs" role="tablist" aria-label="Como adicionar lojas"><button type="button" role="tab" id="domains-tab" aria-controls="domains-form" aria-selected="${importMode==='domains'}" data-import-tab="domains" class="import-tab ${importMode==='domains'?'selected':''}">Colar sites</button><button type="button" role="tab" id="csv-tab" aria-controls="import-form" aria-selected="${importMode==='csv'}" data-import-tab="csv" class="import-tab ${importMode==='csv'?'selected':''}">Importar planilha</button></div>
  <form id="domains-form" role="tabpanel" aria-labelledby="domains-tab" class="panel ${importMode==='domains'?'':'hidden'}"><div class="form-section"><label>Sites das lojas brasileiras<textarea name="domains" rows="7" maxlength="100000" required placeholder="www.sualoja.com.br&#10;outraloja.com.br"></textarea><small>Um endereço por linha. As lojas repetidas são ignoradas.</small></label><div id="domains-result"></div></div><div class="form-actions"><button class="button primary" type="submit">Adicionar à fila ${icon('arrow')}</button></div></form>
  <form id="import-form" role="tabpanel" aria-labelledby="csv-tab" class="panel ${importMode==='csv'?'':'hidden'}"><div class="form-section"><label class="upload-zone">${icon('upload')}<b>Escolha sua planilha CSV</b><small>Até 5 MB e 2.000 lojas por arquivo</small><input name="file" type="file" accept=".csv,text/csv" required></label><div class="inline-actions mt"><a class="button small secondary" href="/api/import/template">${icon('download')}Baixar modelo</a></div><div id="import-result"></div></div><div class="form-actions"><button class="button primary" type="submit">Importar planilha ${icon('arrow')}</button></div></form>
  <p class="import-footnote">Depois de adicionar, clique em <strong>Verificar minha base</strong>. Lojas sem fonte de tráfego ou evidência de estoque ficam para revisão.</p>
  <details class="disclosure"><summary>Preciso de ajuda com a planilha</summary><div class="disclosure-body"><p class="field-help">Baixe o modelo e mantenha os cabeçalhos. Só <code>domain</code> é obrigatório para importar; os demais dados podem ser completados na revisão.</p><div class="table-scroll"><table class="help-table"><tbody><tr><td><code>domain · name · country</code></td><td>Site, nome da loja e país (BR para Brasil).</td></tr><tr><td><code>monthly_visits</code></td><td>Visitas mensais totais estimadas, como inteiro: 3500.</td></tr><tr><td><code>traffic_source · traffic_checked_at</code></td><td>Fonte da estimativa e data da consulta, no formato AAAA-MM-DD.</td></tr><tr><td><code>traffic_period</code></td><td>Mês de referência, se conhecido.</td></tr><tr><td><code>email · phone · whatsapp · contact_source</code></td><td>Contatos comerciais e URL da fonte. Telefone com DDD.</td></tr></tbody></table></div><p class="field-help">Sem assinatura, você fornece os sites e o dado de tráfego com fonte. O Lume não consegue deduzir o número de visitas pelo conteúdo do site.</p></div></details></div>`;
}
async function activityPage() {
  const runs=await api('/runs');
  return `${heading('Cada busca deixa um caminho.', 'Acompanhe as execuções, os resultados e o que precisa de atenção.')}${runBanner()}<section class="panel">${runs.length ? runs.map(r=>`<article class="activity-card"><span class="activity-icon">${icon('search')}</span><div><h3>Verificação de lojas <span class="subtle">#${r.id}</span></h3><p>${e(r.data.message || 'Execução interrompida ao reiniciar o servidor.')}</p><small>${dateLabel(r.started_at,true)} ${r.finished_at?'→ '+dateLabel(r.finished_at,true):''} · ${number(r.data.checked)} verificadas · ${number(r.data.discovered)} encontradas na pesquisa · ${number(r.data.duplicates)} repetidas ignoradas · ${number(r.data.crm_skipped)} ocorrências filtradas pelo CRM</small></div>${badge(r.status)}</article>`).join('') : empty('Seu histórico vai morar aqui.','Ao verificar uma base, o Lume registra o progresso e informa se a meta foi atingida.','<a href="#import" class="button secondary">Começar pela minha base</a>','clock')}</section>`;
}
async function settingsPage() {
  cfg=await api('/settings');
  return `${heading('Configurações.', 'Ajuste sua meta e o horário da rotina.')}<form id="settings-form" class="panel settings-container"><div class="form-section"><h3>Sua meta</h3><div class="form-grid"><label>Lojas novas por dia<input name="daily_goal" type="number" min="1" max="500" value="${cfg.daily_goal}" required></label><label>Mínimo de visitas mensais estimadas<input name="min_visits" type="number" min="3000" max="1000000000" value="${cfg.min_visits}" required><small>Visitas totais, diferentes de visitantes únicos.</small></label></div></div>
  <div class="form-section"><h3>Horário da verificação</h3><label class="check-label"><input name="schedule_enabled" type="checkbox" ${cfg.schedule_enabled?'checked':''}>Verificar a fila automaticamente todos os dias</label><div class="form-grid mt"><label>Horário<select name="schedule_hour">${Array.from({length:24},(_,i)=>`<option value="${i}" ${cfg.schedule_hour===i?'selected':''}>${String(i).padStart(2,'0')}:00</option>`).join('')}</select></label><label class="check-label"><input name="weekdays_only" type="checkbox" ${cfg.weekdays_only?'checked':''}>Somente de segunda a sexta</label></div><p class="field-help">O computador ou servidor precisa estar ligado. A rotina pesquisa o último tema salvo, quando houver, e verifica as lojas encontradas. Você pode fechar o navegador.</p></div>
  <div class="form-section"><details><summary>Configurações avançadas</summary><div class="form-grid mt"><label>Consultas por rodada<input name="search_query_limit" type="number" min="1" max="60" value="${cfg.search_query_limit}" required><small>Busca padrão. Continua da posição salva em cada tema.</small></label><label>Páginas por rodada nos modos alternativos<input name="search_page_limit" type="number" min="1" max="10" value="${cfg.search_page_limit}" required></label><label>Último tema usado na rotina<input name="search_topic" value="${e(cfg.search_topic)}" maxlength="80"></label><label>Países<input name="countries" value="${e(cfg.countries)}" placeholder="BR" maxlength="100"><small>BR para Brasil. Outros países: BR,PT,US.</small></label><label>Validade da consulta de tráfego (dias)<input name="traffic_max_age" type="number" min="1" max="180" value="${cfg.traffic_max_age}" required></label><label>Fuso horário<input name="timezone" value="${e(cfg.timezone)}" required></label><label>Máximo de lojas verificadas por execução<input name="max_candidates" type="number" min="1" max="500" value="${cfg.max_candidates}" required></label><label>Páginas por execução da integração opcional<input name="max_pages" type="number" min="1" max="20" value="${cfg.max_pages}" required></label></div></details></div>
  <div class="form-section"><details><summary>Integração opcional com Store Leads</summary><p class="field-help mt">O modo com sites e CSV funciona sem assinatura. Para descobrir lojas pela API, é necessário contratar acesso no fornecedor. <a href="https://storeleads.app/#pricing" target="_blank" rel="noopener noreferrer">Consultar preços ↗</a></p><label class="mt">Chave do Store Leads<input name="api_key" type="password" autocomplete="new-password" placeholder="${cfg.has_api_key?'Chave salva. Deixe vazio para manter.':'Opcional. Deixe vazio para usar sem assinatura.'}" ${cfg.api_key_from_environment?'disabled':''}><small>A chave fica criptografada na instalação.</small></label><div class="inline-actions mt"><button class="button small" type="button" data-action="test-provider">Testar chave salva</button><button class="button small" type="button" data-action="remove-key" ${cfg.api_key_from_environment?'disabled':''}>Remover chave</button><button class="button small" type="button" data-action="reset-provider">Reiniciar busca</button></div></details></div>
  <div class="form-actions"><button class="button primary" type="submit">Salvar configurações ${icon('check')}</button></div></form>`;
}
async function render() {
  const ticket = ++renderTicket;
  const titles={dashboard:'Início',search:'Buscar lojas',leads:'Lojas e contatos',review:'Revisar',import:'Adicionar lojas',activity:'Histórico',settings:'Configurações',account:'Minha conta',admin:'Administração',crm:'Planilha do CRM'};
  $('#page-label').textContent=titles[view];
  $$('[data-nav]').forEach(el=>el.classList.toggle('active',el.dataset.nav===view));
  try {
    const html = await ({dashboard:dashboardPage,search:searchPage,leads:leadsPage,review:leadsPage,import:importPage,activity:activityPage,settings:settingsPage,account:accountPage,admin:adminPage,crm:crmPage}[view])();
    if(ticket!==renderTicket) return;
    $('#content').innerHTML=html; hydrate($('#content')); bindPage();
  } catch(err) {if(ticket===renderTicket) {$('#content').innerHTML=empty('Não foi possível carregar.',e(err.message),'<button class="button" data-action="reload">Tentar novamente</button>','info');}}
}
function updateSummary(s) {
  summary=s; $('#review-count').textContent=number(s.counts.review);
  $('#connection-status').textContent=s.running?'● Verificando lojas':s.has_api_key?'● Integração conectada':'● Modo sem assinatura';
}
async function refreshSummary() {updateSummary(await api('/dashboard'));}
function importResult(result, target) {
  $(target).innerHTML=`<div class="import-result"><strong>${number(result.inserted)} loja${result.inserted===1?'':'s'} adicionada${result.inserted===1?'':'s'} à fila.</strong><br>${number(result.duplicates)} repetidas ignoradas · ${number(result.invalid)} linhas inválidas${result.errors.length?'<ul>'+result.errors.map(x=>'<li>'+e(x)+'</li>').join('')+'</ul>':''}<div class="inline-actions mt"><button class="button primary small" type="button" data-action="start">Verificar minha base ${icon('play')}</button><a class="button small" href="#leads">Ver minha base</a></div></div>`;
}
function bindPage() {
  bindCRM();
  $('#topic-form')?.addEventListener('submit',async event=>{
    event.preventDefault();const button=$('button[type=submit]',event.target);button.disabled=true;
    try {
      await api('/search',{method:'POST',body:JSON.stringify({topic:$('#topic-input').value})});
      filters.page=1;
      cfg=await api('/settings');await refreshSummary();
      if(view!=='search')location.hash='search';else await render();
      toast('Pesquisa iniciada. Os resultados vão aparecer com os critérios pendentes.');
    } catch(err){toast(err.message,true);}finally{button.disabled=false;}
  });
  function selectImportTab(mode) {
    importMode=mode;
    $$('[data-import-tab]').forEach(tab=>{
      const selected=tab.dataset.importTab===mode;
      tab.classList.toggle('selected',selected);
      tab.setAttribute('aria-selected',String(selected));
      tab.tabIndex=selected?0:-1;
    });
    $('#domains-form')?.classList.toggle('hidden',mode!=='domains');
    $('#import-form')?.classList.toggle('hidden',mode!=='csv');
  }
  $$('[data-import-tab]').forEach(button=>{
    button.tabIndex=button.dataset.importTab===importMode?0:-1;
    button.addEventListener('click',()=>selectImportTab(button.dataset.importTab));
    button.addEventListener('keydown',event=>{
      if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
      event.preventDefault();
      const mode=event.key==='Home'?'domains':event.key==='End'?'csv':importMode==='domains'?'csv':'domains';
      selectImportTab(mode);$(`[data-import-tab=${mode}]`).focus();
    });
  });
  const search=$('#search'); let searchTimer;
  search?.addEventListener('input',()=>{clearTimeout(searchTimer); searchTimer=setTimeout(async()=>{const pos=search.selectionStart; filters.q=search.value;filters.page=1;await render();$('#search')?.focus();try{$('#search')?.setSelectionRange(pos,pos);}catch{}},400);});
  $('#sort-filter')?.addEventListener('change',event=>{filters.sort=event.target.value;filters.page=1;render();});
  $('#status-filter')?.addEventListener('change',event=>{filters.status=event.target.value;filters.page=1;render();});
  $('#stage-filter')?.addEventListener('change',event=>{filters.stage=event.target.value;filters.page=1;render();});
  $('#today-filter')?.addEventListener('change',event=>{filters.today=event.target.checked;filters.page=1;render();});
  $('#unverified-filter')?.addEventListener('change',event=>{filters.include_unverified=event.target.checked;filters.page=1;render();});
  $('#import-form')?.addEventListener('submit',async event=>{
    event.preventDefault();const button=$('button[type=submit]',event.target);button.disabled=true;
    try{const result=await api('/import',{method:'POST',body:new FormData(event.target)});importResult(result,'#import-result');await refreshSummary();}catch(err){toast(err.message,true);}finally{button.disabled=false;}
  });
  $('#domains-form')?.addEventListener('submit',async event=>{
    event.preventDefault();const button=$('button[type=submit]',event.target);button.disabled=true;
    try{const result=await api('/import/domains',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(event.target)))});importResult(result,'#domains-result');await refreshSummary();}catch(err){toast(err.message,true);}finally{button.disabled=false;}
  });
  $('#settings-form')?.addEventListener('submit',async event=>{
    event.preventDefault();const button=$('button[type=submit]',event.target);button.disabled=true;
    const data={...cfg,...Object.fromEntries(new FormData(event.target))};
    for(const name of ['daily_goal','min_visits','traffic_max_age','schedule_hour','max_candidates','max_pages','search_page_limit','search_query_limit']) data[name]=Number(data[name]);
    for(const name of ['schedule_enabled','weekdays_only']) data[name]=$(`[name=${name}]`,event.target).checked;
    if(!data.api_key) delete data.api_key;
    try{cfg=await api('/settings',{method:'PUT',body:JSON.stringify(data)});toast('Configurações salvas.');await refreshSummary();await render();}catch(err){toast(err.message,true);}finally{button.disabled=false;}
  });
}
async function openLead(id) {
  try {
    const lead=await api('/leads/'+id);currentLead=lead; const d=lead.data;
    const reviewNeeded=d.current_problems.length>0;
    const approved=['approved','ready','qualified'].includes(lead.status);
    const contacts=d.contacts||[], phone=contacts.find(c=>c.type==='whatsapp')||contacts.find(c=>c.type==='phone'), email=contacts.find(c=>c.type==='email');
    $('#lead-dialog-content').innerHTML=`<div class="dialog-head"><div><h2>${e(d.name || lead.domain)}</h2><a href="${safeURL(d.storefront_url || "https://"+lead.domain)}" target="_blank" rel="noopener noreferrer">${e(d.storefront_url || lead.domain)} ↗</a></div><button class="icon-button" data-action="close" aria-label="Fechar">×</button></div><form id="lead-form"><div class="dialog-body review-simple">
    <div class="review-intro">${badge(lead.status)}<p>${approved?'Revisão concluída. Você pode completar os dados quando quiser.':'Confira os contatos e aprove quando quiser. Os dados opcionais podem ficar em branco.'}</p></div>
    <section aria-label="Dados da loja"><dl class="contact-overview"><div><dt>CNPJ</dt><dd>${e(d.cnpj || 'Não informado')}</dd></div><div><dt>${phone?.type==='whatsapp'?'WhatsApp':'Telefone'}</dt><dd>${phone?contactLink(phone):'Não informado'}</dd></div><div><dt>E-mail</dt><dd>${email?contactLink(email):'Não informado'}</dd></div></dl>
    <details class="dialog-section" data-section="contacts"><summary>Editar CNPJ, contatos e fontes</summary><div class="dialog-section-body"><div class="form-grid"><label>CNPJ publicado (opcional)<input name="cnpj" value="${e(d.cnpj)}" maxlength="30" placeholder="Não informado"></label><label>URL da fonte do CNPJ<input name="cnpj_source" value="${e(d.cnpj_source)}" maxlength="1000" placeholder="https://site-da-loja/…"></label></div>${d.cnpjs?.length>1?`<p class="field-help">Confira qual CNPJ pertence à loja:</p>${d.cnpjs.map(c=>`<p class="field-help">${e(c.value)} · <a href="${safeURL(c.source)}" target="_blank" rel="noopener noreferrer">Ver fonte ↗</a></p>`).join('')}`:''}<p class="field-help">O CNPJ foi encontrado no site. A situação cadastral e o vínculo com a loja não foram consultados na Receita Federal.</p><h3 class="mt">Contatos públicos</h3><div id="contact-rows">${contacts.map(contactRow).join('')}</div><button class="button small" type="button" data-action="add-contact">+ Adicionar contato</button></div></details></section>
    <section data-section="optional"><div class="form-grid review-optional"><label>Visitas mensais <span class="optional-label">opcional</span><input name="visits" type="number" min="0" max="1000000000000" value="${d.visits??''}" placeholder="Não informado"></label><label>Conferência no CRM <span class="optional-label">opcional</span><select name="crm_status"><option value="unknown" ${!d.crm_status||d.crm_status==='unknown'?'selected':''}>Não conferido</option><option value="free" ${d.crm_status==='free'?'selected':''}>Conferi: livre para prospectar</option><option value="assigned" ${d.crm_status==='assigned'?'selected':''}>Já está com outro vendedor</option></select></label></div>
    <details class="dialog-section traffic-details"><summary>Fonte das visitas e anotação do CRM</summary><div class="dialog-section-body"><a class="quiet-link" href="${safeURL(similarwebURL(lead))}" target="_blank" rel="noopener noreferrer">Consultar na Similarweb ↗</a><p class="field-help">Registre as visitas mensais totais. Se não houver estimativa, deixe em branco.</p><div class="form-grid"><label>Mês de referência (opcional)<input name="traffic_period" value="${e(d.traffic_period)}" maxlength="100" placeholder="Não informado"></label><label>Data da consulta<input name="traffic_checked_at" type="date" value="${e(d.traffic_checked_at?.slice(0,10))}"></label><label class="row-full">Fonte da estimativa<input name="traffic_source" value="${e(d.traffic_source)}" maxlength="500" placeholder="Nome ou URL da fonte"></label></div><button class="button small mt" type="button" data-action="similarweb-source">Conferi na Similarweb hoje</button><p class="field-help">Preenche apenas a fonte e a data. O número de visitas é informado por você.</p><label class="mt">Anotação da conferência no CRM (opcional)<input name="crm_note" value="${e(d.crm_note)}" maxlength="1000" placeholder="Não informado"></label><p class="field-help">O Lume não está conectado ao CRM. ${d.crm_checked_at?'Última confirmação: '+dateLabel(d.crm_checked_at,true)+'.':''}</p></div></details></section>
    <label>Suas anotações <span class="optional-label">opcional</span><textarea class="notes" name="notes" maxlength="5000" rows="2" placeholder="Algo que você queira lembrar depois…">${e(d.notes)}</textarea></label>
    ${d.approval_blockers?.length?`<div class="review-blockers" role="status"><b>Confira antes de aprovar</b><ul>${d.approval_blockers.map(item=>`<li>${e(item)}</li>`).join('')}</ul></div>`:''}
    <details class="dialog-section" data-section="qualification"><summary>Verificações adicionais e evidências</summary><div class="dialog-section-body"><p class="field-help">Aprovar salva sua escolha e retira a loja da revisão. A meta de qualificadas só conta lojas com todos os critérios comprovados, incluindo estoque e tráfego.</p>${d.prospecting?`<div class="checklist">${d.prospecting.checks.map(item=>`<span class="${item.ok?'confirmed':'pending'}">${item.ok?'✓':'○'} ${e(item.label)}</span>`).join('')}</div>`:''}<div class="criterion-list ${reviewNeeded?'':'ok'}">${reviewNeeded ? d.current_problems.map(p=>'• '+e(p)).join('<br>') : '✓ Critérios atendidos com as evidências registradas.'}</div>${d.crawl_error?'<p class="error mt">Leitura do site: '+e(d.crawl_error)+'</p>':''}
    ${d.search_topic?`<p class="field-help">${d.screening_ok?'Evidências de origem brasileira e catálogo ativo encontradas.':'Fora da lista principal: '+e((d.screening_reasons||[]).join('; '))}</p>${(d.country_evidence||[]).map(item=>`<p class="field-help">${e(item.text)} <a href="${safeURL(item.url)}" target="_blank" rel="noopener noreferrer">Fonte ↗</a></p>`).join('')}`:''}<label class="mt">País<input name="country" value="${e(d.country)}" maxlength="2" placeholder="BR"></label>
    <h3 class="mt">Plataforma Shopify</h3><p class="field-help">${d.shopify_verified?'Identificada. Fonte: '+e(d.platform_source):'Ainda não confirmada.'}</p><label class="check-label"><input type="checkbox" name="manual_shopify">Conferi a plataforma manualmente</label><label class="mt">URL da evidência de Shopify<input name="shopify_source" placeholder="https://…" value="${d.platform_source?.startsWith('http')?e(d.platform_source):''}"></label>
    <h3 class="mt">Estoque próprio</h3>${(d.stock_signals||[]).map(s=>`<div class="evidence-line">${e(s.text)}<a href="${safeURL(s.url)}" target="_blank" rel="noopener noreferrer">${e(s.url)} ↗</a></div>`).join('') || '<p class="field-help">Não confirmado. Você pode aprovar a loja e completar esta verificação depois.</p>'}${(d.drop_signals||[]).map(s=>'<div class="info-box warning">'+e(s)+'</div>').join('')}<label class="check-label mt"><input type="checkbox" name="manual_stock" ${d.manual_stock?'checked':''}>Conferi que a loja não opera por dropshipping</label><label class="mt">Evidência que você verificou<textarea name="stock_evidence" rows="2" maxlength="1000" placeholder="Descreva a confirmação de estoque ou fabricação própria…">${e(d.stock_evidence)}</textarea></label><label class="mt">URL da fonte de confirmação<input name="stock_source" value="${e(d.stock_source)}" placeholder="https://…"></label><p class="field-help">Preencha a evidência e a fonte se marcar a confirmação acima. Sem confirmar, pode deixar os campos em branco.</p>
    ${d.checkout_links?.length?`<h3 class="mt">Links de checkout publicados</h3>${d.checkout_links.map(item=>`<p class="field-help">${e(item.host)} · <a href="${safeURL(item.source)}" target="_blank" rel="noopener noreferrer">Ver fonte ↗</a></p>`).join('')}<p class="field-help">Esses links não confirmam qual gateway processa pagamentos.</p>`:''}<p class="field-help">Conferido em ${dateLabel(d.checked_at,true)}. Os sinais do site não certificam idoneidade.</p></div></details>
    <details class="dialog-section" data-section="followup"><summary>Acompanhamento e histórico</summary><div class="dialog-section-body"><label>Etapa da conversa<select name="stage">${Object.entries(stages).map(([k,v])=>`<option value="${k}" ${lead.stage===k?'selected':''}>${v}</option>`).join('')}</select></label><div class="inline-actions mt"><button class="button small" type="button" data-action="copy-message">Copiar rascunho de abordagem</button><button class="button small" type="button" data-action="copy-record">Copiar ficha para o CRM</button></div><ul class="event-list mt">${lead.events.map(ev=>`<li><b>${e({imported:'Importada',checked:'Verificada',delivered:'Entregue',save:'Atualizada',approve:'Aprovada manualmente',reject:'Descartada',block:'Bloqueada para contato',recheck:'Recolocada na fila',duplicate:'Duplicada'}[ev.action] || ev.action)}</b> · ${e(ev.detail)}<small>${dateLabel(ev.at,true)}</small></li>`).join('')}</ul></div></details>
    <p id="lead-error" class="error" role="alert"></p></div><div class="dialog-footer"><details><summary>Mais opções</summary><div class="more-actions"><button class="button small danger" type="button" data-review="block">Não contatar</button><button class="button small" type="button" data-review="reject">Descartar</button><button class="button small" type="button" data-review="recheck">Verificar de novo</button></div></details><div><button class="button small ${approved?'primary':''}" type="submit">${approved?'Salvar alterações':'Salvar para depois'}</button>${!approved?`<button class="button primary small" type="button" data-review="approve" ${['blocked','duplicate'].includes(lead.status)?'disabled':''}>Aprovar loja ✓</button>`:''}</div></div></form>`;
    $('#lead-form').addEventListener('submit',event=>{event.preventDefault();saveLead('save');});
    if(!$('#lead-dialog').open) $('#lead-dialog').showModal();
  } catch(err) {toast(err.message,true);}
}
function contactRow(c={type:'email',value:'',source:''}) {
  return `<div class="contact-row"><select name="contact_type" aria-label="Tipo de contato">${['email','phone','whatsapp'].map(k=>`<option value="${k}" ${k===c.type?'selected':''}>${{email:'E-mail',phone:'Telefone',whatsapp:'WhatsApp'}[k]}</option>`).join('')}</select><input name="contact_value" aria-label="Contato" value="${e(c.value)}" placeholder="Contato"><input name="contact_source" aria-label="Fonte do contato" value="${e(c.source)}" placeholder="https://fonte…"><button class="icon-button" data-action="remove-contact" type="button" aria-label="Remover contato">×</button></div>`;
}
async function saveLead(action) {
  if(action==='block' && !confirm('Marcar esta loja como “Não contatar”? Ela será retirada das exportações e não entrará novamente por importação.')) return;
  const form=$('#lead-form'); for(const input of $$(':invalid',form)){let parent=input.parentElement;while(parent&&parent!==form){if(parent.tagName==='DETAILS')parent.open=true;parent=parent.parentElement;}} if(!form.reportValidity()) return;
  const data=Object.fromEntries(new FormData(form));data.action=action;data.visits=data.visits===''?null:Number(data.visits);data.country=data.country.toUpperCase();
  for(const name of ['manual_stock','manual_shopify']) data[name]=$(`[name=${name}]`,form).checked;
  data.contacts=$$('.contact-row',form).map(row=>({type:$('[name=contact_type]',row).value,value:$('[name=contact_value]',row).value.trim(),source:$('[name=contact_source]',row).value.trim()})).filter(c=>c.value);
  for(const name of ['contact_type','contact_value','contact_source']) delete data[name];
  const buttons=$$('button',form);buttons.forEach(b=>b.disabled=true);$('#lead-error').textContent='';
  try {await api('/leads/'+currentLead.id,{method:'PATCH',body:JSON.stringify(data)});$('#lead-dialog').close();toast(action==='approve'?'Loja aprovada. Você pode completar os dados depois.':'Registro atualizado.');await refreshSummary();await render();}catch(err){$('#lead-error').textContent=err.message;$('#lead-error').scrollIntoView({behavior:'smooth',block:'center'});}finally{buttons.forEach(b=>b.disabled=false);}
}
document.addEventListener('click',async event=>{
  const queueLink=event.target.closest('[data-queue]');if(queueLink){event.preventDefault();filters={status:'all',q:'',stage:'all',today:false,page:1,sort:'priority',focus:queueLink.dataset.queue};view='leads';history.replaceState(null,'','#leads');await render();return;}
  const topicButton=event.target.closest('[data-topic]');if(topicButton){$('#topic-input').value=topicButton.dataset.topic;$('#topic-input').focus();return;}
  const focusButton=event.target.closest('[data-focus]');if(focusButton){filters.focus=focusButton.dataset.focus;filters.page=1;await render();return;}
  const leadButton=event.target.closest('[data-lead]');if(leadButton){await openLead(leadButton.dataset.lead);return;}
  const reviewButton=event.target.closest('[data-review]');if(reviewButton){await saveLead(reviewButton.dataset.review);return;}
  const button=event.target.closest('[data-action]');if(!button)return;
  const action=button.dataset.action;button.disabled=true;
  try {
    if(action==='start'){if(!summary.total&&!summary.has_api_key){location.hash='import';toast('Adicione os sites ou importe uma planilha para começar.');return;}await api('/runs',{method:'POST'});await refreshSummary();if(view==='import')location.hash='dashboard';else await render();toast('Verificação iniciada. Você pode continuar usando o painel.');}
    if(action==='stop'){await api('/runs/stop',{method:'POST'});toast('Interrompendo após a leitura atual. O progresso será salvo.');}
    if(action==='prev'||action==='next'){filters.page+=action==='next'?1:-1;await render();}
    if(action==='reload')await render();
    if(action==='close')$('#lead-dialog').close();
    if(action==='add-contact')$('#contact-rows').insertAdjacentHTML('beforeend',contactRow());
    if(action==='remove-contact')button.closest('.contact-row').remove();
    if(action==='similarweb-source'){
      $('#lead-form [name=traffic_source]').value=similarwebURL(currentLead);
      $('#lead-form [name=traffic_checked_at]').value=summary.date;
      $('#lead-form .traffic-details').open=true;
      toast('Fonte e data preenchidas. Informe o valor e o mês exibidos e salve.');
    }
    if(action==='copy-record'){const d=currentLead.data;await navigator.clipboard.writeText([d.name||currentLead.domain,d.storefront_url||'https://'+currentLead.domain,'CNPJ: '+(d.cnpj||'Não informado'),...(d.contacts||[]).map(c=>`${c.type}: ${c.value} | Fonte: ${c.source}`),'Visitas mensais: '+(d.visits??'Não informado'),'Fonte do tráfego: '+(d.traffic_source||'Não informado'),'Etapa: '+stages[currentLead.stage],'CRM: '+({free:'Conferido: livre para prospectar',assigned:'Já está com outro vendedor'}[d.crm_status]||'Não conferido')].join('\n'));toast('Ficha copiada. A atualização no CRM é feita por você.');}
    if(action==='copy-message'){await navigator.clipboard.writeText(`Olá, equipe da ${currentLead.data.name || currentLead.domain}! Tudo bem? Sou [seu nome], da [sua empresa]. Trabalho com soluções de checkout e gateway de pagamento para lojas virtuais. Quem cuida dessa parte na loja? Gostaria de entender o cenário de vocês e avaliar se faz sentido apresentar nosso serviço.`);toast('Rascunho copiado. Preencha os trechos entre colchetes antes de usar.');}
    if(action==='show-unverified'){filters.include_unverified=true;filters.focus='all';filters.status='all';filters.page=1;view='leads';history.replaceState(null,'','#leads');render();return;}
    if(action==='show-query'){
      const result=await api('/search?'+new URLSearchParams({topic:$('#topic-input').value}));
      $('#query-preview').innerHTML=`${(result.queries||[result.query]).map(query=>`<p class="query-text">${e(query)}</p>`).join('')}${result.google_url?`<a class="quiet-link" href="${safeURL(result.google_url)}" target="_blank" rel="noopener noreferrer">Comparar com a consulta original no Google ↗</a>`:''}`;
    }
    if(action==='reset-search'){
      await api('/search/reset',{method:'POST',body:JSON.stringify({topic:$('#topic-input').value})});toast('Próxima busca deste tema começa na primeira página. As duplicatas continuam bloqueadas.');
    }
    if(action==='test-provider'){const result=await api('/provider/test',{method:'POST'});toast(result.message);}
    if(action==='remove-key'){await api('/settings',{method:'PUT',body:JSON.stringify({...cfg,api_key:''})});await refreshSummary();await render();toast('Chave removida. Modo sem assinatura ativo.');}
    if(action==='reset-provider'){await api('/provider/reset',{method:'POST'});toast('Posição reiniciada. A deduplicação do histórico foi preservada.');}
  }catch(err){toast(err.message,true);}finally{button.disabled=false;}
});
window.addEventListener('hashchange',()=>{const next=location.hash.slice(1);view=['dashboard','search','leads','review','import','activity','settings','account','admin','crm'].includes(next)?next:'dashboard';filters={status:'all',q:'',stage:'all',today:false,page:1,sort:'priority',focus:'all'};if(!$('#app').classList.contains('hidden'))render();});
async function boot(){
  try{const session=await api('/session');registrationOpen=session.registration_open;if(!session.authenticated){showLogin(session.needs_setup);if(session.needs_setup)$('#login-form [name=username]').value=session.default_admin_username;return;}accountNavigation(session.user);$('#login-screen').classList.add('hidden');$('#app').classList.remove('hidden');cfg=await api('/settings');await refreshSummary();view=['dashboard','search','leads','review','import','activity','settings','account','admin','crm'].includes(location.hash.slice(1))?location.hash.slice(1):'dashboard';await render();}catch(err){toast(err.message,true);}
}
let lastPoll=0;
setInterval(async()=>{
  if(document.hidden||$('#app').classList.contains('hidden')||Date.now()-lastPoll<(summary?.running?4000:29000))return;
  lastPoll=Date.now();
  try{const previous=summary?.last_run;await refreshSummary();const changed=JSON.stringify(previous)!==JSON.stringify(summary.last_run);if(!$('#lead-dialog').open&&changed&&['dashboard','search','activity','leads','review'].includes(view)&&!document.activeElement?.closest('#topic-form'))await render();}catch{}
},5000);
hydrate();boot();
