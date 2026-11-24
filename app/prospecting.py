"""A review queue for an SDR, ordered by available evidence, never revenue guesses."""
import json
from datetime import date

from .qualification import evaluate, valid_contact


def traffic_confirmed(data, cfg, today=None):
    today = today or date.today()
    try:
        age = (today - date.fromisoformat(data.get('traffic_checked_at', '')[:10])).days
    except (ValueError, TypeError):
        return False
    return (isinstance(data.get('visits'), (int, float)) and data['visits'] >= cfg['min_visits']
            and data.get('traffic_metric') == 'monthly_visits' and bool(data.get('traffic_source'))
            and 0 <= age <= cfg['traffic_max_age'])


def brief(data, status, stage, cfg, today=None):
    contacts = [c for c in data.get('contacts', []) if c.get('source') and valid_contact(c.get('type'), c.get('value', ''))]
    stock = bool(data.get('manual_stock') or (data.get('stock_signals') and not data.get('drop_signals')))
    traffic = traffic_confirmed(data, cfg, today)
    crm = data.get('crm_status') == 'free'
    checks = [
        {'label': 'Shopify', 'ok': bool(data.get('shopify_verified'))},
        {'label': 'Brasil', 'ok': data.get('country') == 'BR'},
        {'label': 'Contato público', 'ok': bool(contacts)},
        {'label': 'Estoque próprio', 'ok': stock},
        {'label': 'Tráfego mínimo', 'ok': traffic},
        {'label': 'Livre no CRM', 'ok': crm},
    ]
    available = not evaluate(data, cfg, today)
    if status in ('blocked', 'rejected', 'duplicate') or data.get('crm_status') == 'assigned':
        step, label, rank = 'excluded', 'Fora da fila de prospecção', 0
    elif stage != 'new':
        step, label, rank = 'followup', 'Acompanhar conversa', 5
    elif data.get('search_topic') and not data.get('screening_ok'):
        step, label, rank = 'screening', 'Verificação pendente', 1
    elif status == 'approved':
        step, label, rank = 'approved', 'Revisão concluída · dados opcionais a completar', 15
    else:
        rank = 30 + (10 if data.get('cnpj') else 0) + (10 if any(c['type'] == 'whatsapp' for c in contacts) else 0) + (5 if any(c['type'] == 'email' for c in contacts) else 0) + (20 if stock else 0) + (20 if traffic else 0) + (5 if crm else 0)
        if not crm and cfg.get('require_crm_check', True):
            step, label = 'crm', 'Conferir disponibilidade no CRM'
        elif not stock:
            step, label = 'stock', 'Conferir estoque próprio'
        elif not traffic:
            step, label = 'traffic', 'Conferir visitas mensais'
        elif available:
            step, label = 'ready', 'Contato liberado pelos critérios' if status == 'qualified' else 'Aprovar os critérios'
        else:
            step, label = 'review', 'Revisar evidências pendentes'
    return {'step': step, 'label': label, 'rank': rank, 'checks': checks,
            'qualified': available and status == 'qualified',
            'priority_reason': 'Ordem de revisão: estoque, tráfego, CNPJ e contatos com evidência. Não estima faturamento nem probabilidade de venda.'}


def sql_rank(raw, status, stage, cfg, today):
    try:
        return brief(json.loads(raw), status, stage, cfg, today)['rank']
    except (TypeError, ValueError, KeyError):
        return 0
