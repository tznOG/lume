"""Durable breadth-first discovery. Each query owns its page and retry state.

The caller commits returned candidates and the new state in one transaction.
No database access here: a failed commit cannot skip a page of stores.
"""
import copy
import hashlib
from datetime import datetime, timedelta, timezone

from . import discovery, yahoo_search

VERSION = 1


def queries(topic):
    topic = discovery.normalize_topic(topic)
    variants = {
        'roupas': ['moda feminina', 'moda masculina', 'moda fitness'],
        'roupa': ['moda feminina', 'moda masculina', 'moda fitness'],
        'perfumes': ['perfumaria', 'fragrâncias'],
        'perfume': ['perfumaria', 'fragrâncias'],
        'cosméticos': ['maquiagem', 'skincare'],
        'cosmeticos': ['maquiagem', 'skincare'],
        'calçados': ['sapatos', 'tênis'],
        'calcados': ['sapatos', 'tênis'],
    }.get(topic, [])
    # Explore closely related segments before broad international-domain queries.
    result = discovery.brazil_queries(topic)[:2]
    result.extend(f'site:com.br {variant} "Shopify" -agência -curso -template' for variant in variants)
    result += [
        f'site:com.br {topic} "Tecnologia da Shopify"',
        discovery.brazil_queries(topic)[2],
        f'{topic} "CNPJ" "Shopify" -site:shopify.com -agência -curso',
        f'{topic} "Brasil" "Powered by Shopify" -site:myshopify.com',
        f'{topic} "CNPJ" "Desenvolvido por Shopify"',
        f'site:myshopify.com {topic} "Brasil" "contato"',
    ]
    return list(dict.fromkeys(result))


def enabled():
    return discovery.backend() == 'direct' and 'yahoo' in discovery.direct_engines().split(',')


def key(query):
    return hashlib.sha256(query.encode()).hexdigest()[:16]


def step(topic, state, now=None):
    """One bounded request; partial errors never discard another query's results."""
    now = now or datetime.now(timezone.utc)
    state = copy.deepcopy(state) if state.get('version') == VERSION else {'version': VERSION, 'queries': {}, **({'retry_at':state['retry_at']} if state.get('retry_at') else {})}
    plan = queries(topic)
    progress = state.setdefault('queries', {})
    available = []
    for index, query in enumerate(plan):
        entry = progress.setdefault(key(query), {'page': 1})
        if entry.get('exhausted_at'):
            if now - datetime.fromisoformat(entry['exhausted_at']) < timedelta(days=7):
                continue
            entry.clear()
            entry['page'] = 1
        if entry.get('retry_at') and now < datetime.fromisoformat(entry['retry_at']):
            continue
        available.append((entry['page'], index, query, entry))
    meta = {'attempted': False, 'warnings': [], 'query_count': len(plan)}
    if state.get('retry_at') and now < datetime.fromisoformat(state['retry_at']):
        meta.update(done=True, paused=True, message='O buscador ainda está no intervalo de espera após limitar as consultas. O progresso está salvo; aguarde alguns minutos e tente novamente.')
        return [], state, meta
    if not available:
        paused = any(not progress[key(q)].get('exhausted_at') for q in plan)
        meta.update(done=True, paused=paused, message='Consultas temporariamente indisponíveis. O progresso está salvo; tente mais tarde.' if paused else 'Consultas disponíveis concluídas. Tente outro tema para ampliar a base.')
        return [], state, meta
    page, index, query, entry = min(available, key=lambda row: (row[0], row[1]))
    meta.update(attempted=True, done=False, query=query, page=page, query_index=index + 1)
    try:
        rows, more = yahoo_search.search(query, page)
    except discovery.DiscoveryError as exc:
        entry['failures'] = min(6, entry.get('failures', 0) + 1)
        entry['retry_at'] = (now + timedelta(minutes=5 * 2 ** (entry['failures'] - 1))).isoformat()
        entry['error'] = str(exc)[:350]
        meta['warnings'] = [f'Consulta {index + 1}, página {page}: {exc} A posição dessa consulta foi preservada.']
        if isinstance(exc, discovery.SearchLimited):
            state['retry_at'] = (now + timedelta(minutes=30)).isoformat()
            meta.update(done=True, paused=True, message='O buscador limitou as consultas. Resultados anteriores foram salvos. Tente novamente em 30 minutos.')
        return [], state, meta
    found = discovery.normalize_results([{**r, '_query': query} for r in rows], topic, page, brazil=True, custom_domains=True)
    for data in found:
        data['search_engine'] = 'yahoo-http'
    # Detect loops from raw URLs as well: a page containing only filtered URLs
    # must not consume a hundred requests when the engine ignores pagination.
    signature = hashlib.sha256('|'.join(sorted(r.get('url', '') for r in rows)).encode()).hexdigest()
    repeated = bool(rows) and signature in entry.get('signatures', [])
    entry.update(page=page + 1, signatures=(entry.get('signatures', []) + [signature])[-5:], last_success=now.isoformat())
    for name in ('retry_at', 'failures', 'error'):
        entry.pop(name, None)
    if not more or repeated or page >= 30:
        entry['exhausted_at'] = now.isoformat()
    if repeated:
        meta['warnings'] = [f'A consulta {index + 1} repetiu resultados; as outras consultas continuam.']
    meta['raw_results'] = len(rows)
    return found, state, meta
