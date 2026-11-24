'use strict';
let authMode='login', registrationOpen=false, signedUser=null;

function accountNavigation(user) {
  signedUser=user;
  $('#admin-nav').classList.toggle('hidden',user?.role!=='admin');
  $('#account-name').textContent=user?.display_name||user?.username||'Minha conta';
}

async function accountPage(){
  return `${heading('Minha conta.','Seu acesso e suas preferências de segurança.')}<section class="panel form-section"><h3>${e(signedUser.display_name)} · @${e(signedUser.username)}</h3><p>Seu histórico, lojas, anotações, exportações e configurações ficam separados dos demais usuários. O administrador acompanha contagens de uso e gerencia contas. Ele não recebe o conteúdo das suas pesquisas no painel administrativo.</p><p class="field-help">O servidor é administrado pelo proprietário do Lume. Pessoas com acesso administrativo ao servidor e aos backups podem acessar os arquivos armazenados. Não salve senhas de outros serviços nas anotações.</p><form id="account-password"><div class="form-grid"><label>Senha atual<input name="current_password" type="password" autocomplete="current-password" required maxlength="200"></label><label>Nova senha<input name="new_password" type="password" autocomplete="new-password" required minlength="10" maxlength="200"></label></div><div class="inline-actions mt"><button class="button primary" type="submit">Atualizar senha</button><span class="field-help">Encerra as outras sessões desta conta.</span></div><p class="error" id="account-error" role="alert"></p></form></section>`;
}

let adminDays=30;
async function adminPage(){
  if(signedUser?.role!=='admin') return empty('Acesso restrito.','Este painel é exclusivo do administrador.');
  const result=await api('/admin/stats?days='+adminDays);
  const daily=result.daily.filter(row=>row.action==='search');
  const max=Math.max(1,...daily.map(row=>row.count));
  return `${heading('Administração.','Contas e métricas de uso. O conteúdo privado das pesquisas não aparece aqui.')}<div class="toolbar"><label>Período<select id="admin-period">${[7,30,90].map(days=>`<option value="${days}" ${days===adminDays?'selected':''}>Últimos ${days} dias</option>`).join('')}</select></label><span class="field-help">Horário de Brasília · ativo agora = atividade nos últimos 5 minutos.</span></div><div class="work-summary admin-summary"><div><strong>${number(result.total_users)}</strong><span>contas cadastradas</span></div><div><strong>${number(result.online_users)}</strong><span>ativos agora</span></div><div><strong>${number(result.active_users)}</strong><span>usuários ativos no período</span></div><div><strong>${number(result.searches)}</strong><span>buscas iniciadas no período</span></div></div><section class="panel form-section"><div class="panel-head"><h3>Pesquisas por dia</h3><button class="button small" data-action="reload">Atualizar</button></div><div class="usage-chart" aria-label="Pesquisas iniciadas por dia">${daily.length?daily.map(row=>`<div class="usage-bar" title="${e(row.day)}: ${number(row.count)} pesquisas"><strong>${number(row.count)}</strong><span data-height="${Math.max(3,row.count/max*100)}"></span><small>${dateLabel(row.day)}</small></div>`).join(''):'<p class="field-help">Ainda não há pesquisas neste período.</p>'}</div></section><section class="panel mt"><div class="panel-head"><div><h3>Usuários</h3><p>Logins, pesquisas, revisões e exportações são contagens do período.</p></div><label class="check-label"><input id="registration-toggle" type="checkbox" ${result.registration_open?'checked':''}>Permitir novos cadastros</label></div><div class="table-scroll"><table><thead><tr><th>Conta</th><th>Último acesso</th><th>Logins</th><th>Pesquisas</th><th>Lojas verificadas</th><th>Aprovações</th><th>Exportações</th><th>Acesso</th></tr></thead><tbody>${result.users.map(user=>`<tr><td><b>${e(user.display_name)}</b><span class="cell-sub">@${e(user.username)} · ${user.role==='admin'?'Administrador':'Usuário'}${user.online?' · ativo agora':''}</span></td><td>${dateLabel(user.last_seen,true)}</td><td>${number(user.usage.login)}</td><td>${number(user.usage.search)}</td><td>${number(user.jobs.checked)}</td><td>${number(user.usage.approve)}</td><td>${number(user.usage.export)}</td><td>${user.id===signedUser.id?'<span class="field-help">Sua conta</span>':`<button class="button small" data-account-id="${user.id}" data-account-active="${user.active?'false':'true'}">${user.active?'Desativar':'Reativar'}</button>`}</td></tr>`).join('')}</tbody></table></div><div class="form-section"><p class="field-help">Desativar encerra as sessões e pede a interrupção das tarefas em andamento. Os dados são preservados. Consultas automáticas agendadas são contabilizadas separadamente dos cliques em buscar. Não são coletadas teclas, gravações da tela ou textos das anotações.</p></div></section>`;
}

document.addEventListener('submit',async event=>{
  if(event.target.id!=='account-password')return;
  event.preventDefault();const button=$('button',event.target);button.disabled=true;
  try{await api('/account/password',{method:'PUT',body:JSON.stringify(Object.fromEntries(new FormData(event.target)))});event.target.reset();toast('Senha atualizada. As outras sessões foram encerradas.');}
  catch(err){$('#account-error').textContent=err.message;}finally{button.disabled=false;}
});
document.addEventListener('change',async event=>{
  if(event.target.id==='admin-period'){adminDays=Number(event.target.value);await render();}
  if(event.target.id==='registration-toggle'){
    try{await api('/admin/registration',{method:'PUT',body:JSON.stringify({enabled:event.target.checked})});toast('Configuração de cadastro atualizada.');}
    catch(err){toast(err.message,true);event.target.checked=!event.target.checked;}
  }
});
document.addEventListener('click',async event=>{
  if(event.target.closest('#register-toggle')){showLogin(false,authMode!=='register');return;}
  const button=event.target.closest('[data-account-id]');if(!button)return;
  const active=button.dataset.accountActive==='true';
  if(!active&&!confirm('Desativar esta conta e encerrar suas sessões? Os dados serão preservados.'))return;
  button.disabled=true;
  try{await api('/admin/users/'+button.dataset.accountId,{method:'PATCH',body:JSON.stringify({active})});await render();toast(active?'Conta reativada.':'Conta desativada.');}
  catch(err){toast(err.message,true);button.disabled=false;}
});
