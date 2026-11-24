import csv
import hashlib
import io
import json
import logging
import os
import threading
import time
from contextvars import copy_context
from .identity import USER_ID, tenant
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import database as store
from . import provider, discovery, search_plan
from .crawler import inspect_store
from .network import canonical, hostname
from .qualification import evaluate, valid_contact, approval_blockers, reviewed_status
from .company import normalize_cnpj
from . import screening, crm
from .settings import config, api_key

log = logging.getLogger(__name__)
LOCK = threading.RLock()
STOP = threading.Event()
WORKER = None
JOBS = {}


def job_state():
    uid = USER_ID.get()
    if uid is None:
        return None
    with LOCK:
        return JOBS.setdefault((str(store.DATA_DIR), uid), {'worker': None, 'stop': threading.Event()})


def get_worker():
    state = job_state()
    return state['worker'] if state is not None else WORKER


def get_stop():
    state = job_state()
    return state['stop'] if state is not None else STOP


def stop_user(uid):
    with tenant(uid):
        get_stop().set()


def stop_all():
    STOP.set()
    with LOCK:
        for state in JOBS.values():
            state['stop'].set()


def scheduler_all():
    from . import accounts
    with accounts.connect() as db:
        users = [row[0] for row in db.execute('SELECT id FROM users WHERE active=1')]
    for uid in users:
        with tenant(uid):
            try:
                scheduler_tick()
            except ValueError:
                # Capacity can be occupied by another account; retry next tick.
                pass


def local_now(cfg=None):
    return datetime.now(ZoneInfo((cfg or config())["timezone"]))


def day(cfg=None):
    return local_now(cfg).date().isoformat()


def lead_aliases(data):
    aliases = {"domain:" + canonical(data["domain"])}
    for alias in data.get("aliases", []):
        try:
            aliases.add("domain:" + canonical(alias))
        except ValueError:
            pass
    if data.get("shop_id"):
        aliases.add("shop:" + hostname(data["shop_id"]))
    return aliases


def ingest(data, db=None):
    if db is None:
        with LOCK, store.connect() as conn:
            return ingest(data, conn)
    if crm.matches(db, data):
        return False
    # This flag is controlled only by the current account's snapshot.
    data.pop('crm_excluded', None)
    aliases = lead_aliases(data)
    for alias in aliases:
        existing = db.execute("SELECT leads.id,leads.data FROM aliases JOIN leads ON leads.id=aliases.lead_id WHERE aliases.alias=?", (alias,)).fetchone()
        if existing:
            # A store can sell both clothing and footwear. Reuse the same CRM
            # record when its already-read catalog supports the new topic.
            topic = data.get('search_topic')
            known = json.loads(existing['data'])
            if topic and known.get('screening_ok') and screening.topic_matches(topic, ' '.join(known.get('catalog_terms', []))):
                topics = list(dict.fromkeys(known.get('search_topics', []) + [known.get('search_topic'), topic]))
                known['search_topics'] = [t for t in topics if t]
                db.execute('UPDATE leads SET data=? WHERE id=?', (json.dumps(known, ensure_ascii=False), existing['id']))
            return False
    stamp = store.now()
    cursor = db.execute("INSERT OR IGNORE INTO leads(domain,data,created_at,updated_at) VALUES(?,?,?,?)", (data["domain"], json.dumps(data, ensure_ascii=False), stamp, stamp))
    if not cursor.rowcount:
        return False
    lead_id = cursor.lastrowid
    db.executemany("INSERT INTO aliases VALUES(?,?)", [(a, lead_id) for a in aliases])
    store.event(db, lead_id, "imported", data.get("source", "Importação"))
    return True


def get_lead(lead_id):
    with store.connect() as db:
        row = db.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not row:
            raise ValueError("Loja não encontrada.")
        lead = store.unpack(row)
        lead["events"] = [dict(r) for r in db.execute("SELECT at,action,detail FROM events WHERE lead_id=? ORDER BY id DESC LIMIT 30", (lead_id,))]
        return lead


def save_inspection(lead_id, data):
    with LOCK, store.connect() as db:
        row = db.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not row or row["status"] in ("blocked", "rejected", "duplicate"):
            return
        current = json.loads(row['data'])
        # A crawl may finish after an operator saved a note, CRM check or
        # traffic estimate. Never replace these with the worker's old snapshot.
        for field in ('notes', 'visits', 'traffic_source', 'traffic_metric', 'traffic_checked_at', 'traffic_period',
                      'crm_status', 'crm_note', 'crm_checked_at', 'manual_stock', 'stock_evidence', 'stock_source', 'approved_at'):
            if field in current:
                data[field] = current[field]
        for flag, fields in (('cnpj_manual', ('cnpj', 'cnpj_source')), ('country_manual', ('country', 'country_source', 'country_conflict')),
                             ('manual_shopify', ('shopify_verified', 'platform_source'))):
            if current.get(flag):
                data[flag] = True
                for field in fields:
                    if field in current:
                        data[field] = current[field]
        if current.get('contacts_manual'):
            data['contacts'] = current.get('contacts', [])
            data['contacts_manual'] = True
        data['search_topics'] = [topic for topic in current.get('search_topics', []) if screening.topic_matches(topic, ' '.join(data.get('catalog_terms', [])))]
        screening.stamp(data)
        crm.stamp(db, data)
        data['checked_at'] = store.now()
        for alias in lead_aliases(data):
            other = db.execute("SELECT lead_id FROM aliases WHERE alias=?", (alias,)).fetchone()
            if other and other[0] != lead_id:
                data["problems"] = [f"Mesma loja que o registro #{other[0]}"]
                if data.get('search_topic'):
                    data.update(screening_ok=False, screening_reasons=data['problems'])
                db.execute("UPDATE leads SET status='duplicate',data=?,updated_at=? WHERE id=?", (json.dumps(data), store.now(), lead_id))
                store.event(db, lead_id, "duplicate", f"Loja #{other[0]}")
                return
        problems = evaluate(data, config(), local_now().date())
        data["problems"] = problems
        status = reviewed_status(data, config(), row['delivered_day'], local_now().date())
        db.execute("UPDATE leads SET data=?,status=?,updated_at=?,attempts=attempts+1 WHERE id=?", (json.dumps(data, ensure_ascii=False), status, store.now(), lead_id))
        db.executemany("INSERT OR IGNORE INTO aliases VALUES(?,?)", [(a, lead_id) for a in lead_aliases(data)])
        store.event(db, lead_id, "checked", "; ".join(problems) if problems else "Critérios atendidos por evidências públicas; classificação automática.")


def allocate(cfg=None):
    cfg = cfg or config()
    today = day(cfg)
    with LOCK, store.connect() as db:
        # One transaction protects the quota, including manual review approvals.
        db.execute("BEGIN IMMEDIATE")
        count = db.execute("SELECT count(*) FROM leads WHERE delivered_day=? AND " + crm.AVAILABLE_SQL, (today,)).fetchone()[0]
        remaining = max(0, cfg["daily_goal"] - count)
        for row in db.execute("SELECT * FROM leads WHERE status='ready' AND delivered_day IS NULL AND " + crm.AVAILABLE_SQL + " ORDER BY created_at,id").fetchall():
            if remaining <= 0:
                break
            data = json.loads(row["data"])
            problems = evaluate(data, cfg, local_now(cfg).date())
            if problems:
                data["problems"] = problems
                status = reviewed_status(data, cfg, row['delivered_day'], local_now(cfg).date())
                db.execute("UPDATE leads SET status=?,data=? WHERE id=?", (status, json.dumps(data), row["id"]))
                continue
            db.execute("UPDATE leads SET status='qualified',delivered_day=?,updated_at=? WHERE id=?", (today, store.now(), row["id"]))
            store.event(db, row["id"], "delivered", today)
            remaining -= 1
        return cfg["daily_goal"] - remaining


def import_csv(content):
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("cp1252")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        try:
            dialect = csv.Sniffer().sniff(text.splitlines()[0] if text else "", delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames or "domain" not in reader.fieldnames:
        raise ValueError("A planilha precisa de uma coluna domain. Baixe o modelo no painel.")
    result = {"inserted": 0, "duplicates": 0, "invalid": 0, "errors": []}
    with LOCK, store.connect() as db:
        for number, row in enumerate(reader, 2):
            if number > 2001:
                raise ValueError("Importe no máximo 2.000 linhas por arquivo.")
            try:
                domain = hostname(row.get("domain", ""))
                visits = (row.get("monthly_visits") or "").strip()
                if visits and (not visits.isdigit() or int(visits) > 10**12):
                    raise ValueError("monthly_visits deve ser um inteiro, sem separador de milhar.")
                contacts = []
                for kind in ("email", "phone", "whatsapp"):
                    value = (row.get(kind) or "").strip()
                    if value:
                        if not valid_contact(kind, value):
                            raise ValueError(f"{kind} inválido.")
                        source = (row.get("contact_source") or "").strip()
                        if source:
                            hostname(source)
                        contacts.append({"type": kind, "value": value, "source": source, "via": "CSV"})
                data = {"domain": domain, "name": row.get("name") or domain, "country": (row.get("country") or "").upper().strip(), "category": row.get("category") or "",
                        "visits": int(visits) if visits else None, "traffic_source": (row.get("traffic_source") or "").strip(),
                        "traffic_metric": "monthly_visits" if visits else "", "traffic_checked_at": (row.get("traffic_checked_at") or "").strip(),
                        "traffic_period": row.get("traffic_period") or "Não informado", "contacts": contacts, "shopify_verified": False,
                        "apps": [], "stock_signals": [], "drop_signals": [], "notes": "", "source": "CSV", "aliases": []}
                if ingest(data, db):
                    result["inserted"] += 1
                else:
                    result["duplicates"] += 1
            except (ValueError, TypeError) as exc:
                result["invalid"] += 1
                if len(result["errors"]) < 20:
                    result["errors"].append(f"Linha {number}: {exc}")
    return result


def update_run(run_id, stats, status="running"):
    with store.connect() as db:
        db.execute("UPDATE runs SET data=?,status=?,finished_at=? WHERE id=?", (json.dumps(stats, ensure_ascii=False), status, None if status == "running" else store.now(), run_id))
    from . import accounts
    accounts.job(run_id, status, stats)


def inspect_candidate(lead):
    # Recheck after queue selection: an upload may have arrived in between.
    with LOCK, store.connect() as db:
        if crm.matches(db, lead):
            return None
    return inspect_store(lead)


def ingest_candidate(data, db, stats):
    if crm.matches(db, data):
        stats['crm_skipped'] += 1
    else:
        stats['discovered' if ingest(data, db) else 'duplicates'] += 1


def run_pipeline(run_id, cfg, topic=None, discover=True):
    stats = {"checked": 0, "discovered": 0, "duplicates": 0, "crm_skipped": 0, "pages": 0, "brazilian": 0, "filtered_out": 0, "queries": 0, "message": "Verificando fila de lojas…", "search_topic": topic, "warnings": []}
    browser_session = None
    pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix='lume-store')
    try:
        cfg = dict(cfg)
        allocate(cfg)
        fingerprint = hashlib.sha256(json.dumps({k: cfg[k] for k in ("countries", "min_visits")}, sort_keys=True).encode()).hexdigest()[:16]
        cursor_state = store.get_setting("cursor:" + fingerprint, {})
        key = api_key() if not topic and discover else ""
        search_key = discovery.state_key(topic) if topic and discover else None
        search_state = store.get_setting(search_key, {}) if search_key else {}
        planned = bool(topic and discover and search_plan.enabled())
        if planned:
            source_retry = store.get_setting('search_source:yahoo:retry_at', '')
            if source_retry and source_retry > search_state.get('retry_at', ''):
                search_state['retry_at'] = source_retry
        search_paused = False
        search_finished = False
        if topic:
            stats['search_query'] = discovery.build_query(topic)
            stats['search_queries'] = search_plan.queries(topic) if planned else [stats['search_query']]
        while not get_stop().is_set() and stats["checked"] < cfg["max_candidates"]:
            with store.connect() as db:
                delivered = db.execute("SELECT count(*) FROM leads WHERE delivered_day=? AND " + crm.AVAILABLE_SQL, (day(cfg),)).fetchone()[0]
                pending = db.execute("SELECT * FROM leads WHERE status='queued' AND " + crm.AVAILABLE_SQL + (" AND json_extract(data,'$.search_topic')=?" if topic else "") + " ORDER BY id LIMIT ?", ((topic,) if topic else ()) + (min(3, cfg['max_candidates'] - stats['checked']),)).fetchall()
            if delivered >= cfg["daily_goal"]:
                stats["message"] = "Meta de hoje atingida. As próximas lojas ficam para outro dia."
                break
            if pending:
                leads = [store.unpack(row) for row in pending]
                stats["message"] = "Verificando " + ', '.join(lead['domain'] for lead in leads)
                update_run(run_id, stats)
                futures = {pool.submit(copy_context().run, inspect_candidate, lead['data']): lead for lead in leads}
                for future in as_completed(futures):
                    lead = futures[future]
                    try:
                        inspected = future.result()
                    except Exception:
                        log.exception("Falha na leitura da loja %s", lead["id"])
                        inspected = {**lead["data"], "crawl_error": "Falha na leitura. Tente verificar novamente ou revise manualmente."}
                    if inspected is None:
                        stats['crm_skipped'] += 1
                        continue
                    save_inspection(lead["id"], inspected)
                    with store.connect() as db:
                        excluded = crm.matches(db, inspected)
                    if excluded:
                        stats['crm_skipped'] += 1
                    if topic:
                        stats['brazilian' if inspected.get('screening_ok') and not excluded else 'filtered_out'] += 1
                    allocate(cfg)
                    stats["checked"] += 1
                    update_run(run_id, stats)
                continue
            if not discover:
                stats['message'] = 'Verificação das lojas coladas concluída. Confira os dados pendentes em Revisar.'
                break
            if topic:
                if planned:
                    if search_finished:
                        break
                    if stats['queries'] >= cfg.get('search_query_limit', 24):
                        stats['message'] = 'Rodada concluída. Buscar mais continua as consultas de onde parou, sem repetir lojas.'
                        break
                    stats['message'] = f'Buscando {topic}: consulta {stats["queries"] + 1} de até {cfg.get("search_query_limit", 24)}…'
                    update_run(run_id, stats)
                    candidates, next_state, meta = search_plan.step(topic, search_state)
                    # Results and each independent query cursor are durable together.
                    with LOCK, store.connect() as db:
                        for data in candidates:
                            ingest_candidate(data, db, stats)
                        db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (search_key, json.dumps(next_state)))
                        if meta.get('paused') and next_state.get('retry_at'):
                            db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ('search_source:yahoo:retry_at', json.dumps(next_state['retry_at'])))
                    search_state = next_state
                    stats['queries'] += int(meta['attempted'])
                    stats['pages'] += int(meta['attempted'])
                    stats['warnings'] = list(dict.fromkeys(stats['warnings'] + meta['warnings']))[-6:]
                    stats['active_query'] = meta.get('query', '')
                    search_finished = meta.get('done', False)
                    search_paused = meta.get('paused', False)
                    if meta.get('message'):
                        stats['message'] = meta['message']
                    update_run(run_id, stats)
                    if not search_finished:
                        get_stop().wait(2)
                    continue
                if stats['pages'] >= cfg['search_page_limit']:
                    stats['message'] = 'Limite de páginas desta rodada atingido. Buscar novamente continua da próxima página; confira as lojas encontradas em Revisar.'
                    break
                exhausted_at = search_state.get('exhausted_at')
                if exhausted_at and datetime.now().astimezone() - datetime.fromisoformat(exhausted_at) < timedelta(days=7):
                    stats['message'] = 'Fim dos resultados disponíveis para este tema. Tente outro tema ou reinicie a busca; isso não significa que todas as lojas da internet foram encontradas.'
                    break
                if exhausted_at:
                    search_state = {}
                page = search_state.get('page', 1)
                if page > 100:
                    stats['message'] = 'Limite de paginação atingido. Refine o tema ou reinicie a busca.'
                    break
                stats['message'] = f'Buscando {topic}: página {page}…'
                update_run(run_id, stats)
                if discovery.backend() == 'browser':
                    if browser_session is None:
                        from .browser_search import BrowserSearch
                        def progress(message, waiting=False):
                            stats.update(message=message, search_waiting=waiting)
                            update_run(run_id, stats)
                        browser_session = BrowserSearch(progress=progress, stop=get_stop())
                        browser_session.__enter__()
                    candidates, has_more, warnings = browser_session.search(topic, page)
                else:
                    candidates, has_more, warnings = discovery.search_page(topic, page)
                stats['warnings'] = list(dict.fromkeys(stats['warnings'] + warnings))
                signature = hashlib.sha256('|'.join(sorted(d['domain'] for d in candidates)).encode()).hexdigest()
                repeated = bool(candidates) and signature == search_state.get('signature')
                # Page ingestion and progress commit together. A failed search never advances.
                with LOCK, store.connect() as db:
                    for data in candidates:
                        ingest_candidate(data, db, stats)
                    search_state = {'page': page + 1, 'signature': signature}
                    if not has_more or repeated:
                        search_state['exhausted_at'] = store.now()
                    db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (search_key, json.dumps(search_state)))
                stats['pages'] += 1
                update_run(run_id, stats)
                if repeated:
                    stats['warnings'].append('O buscador repetiu a mesma página de resultados; a paginação foi encerrada para evitar um ciclo.')
                get_stop().wait(2)
                continue
            if not key:
                stats["message"] = "Fila processada. Use a busca por tema para encontrar outras lojas ou confira os dados pendentes em Revisar."
                break
            if stats["pages"] >= cfg["max_pages"]:
                stats["message"] = "Limite de páginas desta execução atingido. A próxima execução continua da mesma posição."
                break
            if cursor_state.get("exhausted_at"):
                exhausted = datetime.fromisoformat(cursor_state["exhausted_at"])
                if datetime.now(exhausted.tzinfo) - exhausted < timedelta(days=7):
                    stats["message"] = "Base consultada até o fim. Atualização disponível após 7 dias ou ao reiniciar a busca nas configurações."
                    break
                cursor_state = {}
            items, next_cursor = provider.list_page(key, cfg, cursor_state.get("cursor"))
            # Persist the entire page and the cursor in one transaction so a crash cannot skip records.
            with LOCK, store.connect() as db:
                for item in items:
                    try:
                        data = provider.normalize(item, day(cfg))
                        ingest_candidate(data, db, stats)
                    except (ValueError, TypeError):
                        stats["duplicates"] += 1
                cursor_state = {"cursor": next_cursor} if next_cursor else {"exhausted_at": store.now()}
                db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("cursor:" + fingerprint, json.dumps(cursor_state)))
            stats["pages"] += 1
            update_run(run_id, stats)
            get_stop().wait(0.6)
        if get_stop().is_set():
            stats["message"] = "Execução interrompida. O progresso foi salvo."
        elif stats["checked"] >= cfg["max_candidates"]:
            stats["message"] = "Limite de lojas verificadas atingido. Você pode continuar em uma nova execução."
        with store.connect() as db:
            stats["delivered_today"] = db.execute("SELECT count(*) FROM leads WHERE delivered_day=? AND " + crm.AVAILABLE_SQL, (day(cfg),)).fetchone()[0]
        stats['search_paused'] = search_paused
        status = "cancelled" if get_stop().is_set() else ("completed" if stats["delivered_today"] >= cfg["daily_goal"] else "partial")
        update_run(run_id, stats, status)
    except Exception as exc:
        if isinstance(exc, (provider.ProviderError, discovery.DiscoveryError)):
            log.warning('Rotina SDR interrompida: %s', exc)
        else:
            log.exception("Falha na rotina SDR")
        stats['search_waiting'] = False
        stats["message"] = str(exc) if isinstance(exc, (provider.ProviderError, discovery.DiscoveryError)) else "Falha na execução. O progresso foi salvo; consulte o log e tente novamente."
        update_run(run_id, stats, 'cancelled' if get_stop().is_set() else 'failed')
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
        if browser_session is not None:
            browser_session.__exit__(None, None, None)


def start_run(topic=None, discover=True):
    global WORKER
    with LOCK:
        from . import accounts
        accounts.check_search_limit()
        if get_worker() and get_worker().is_alive():
            raise ValueError("Já existe uma busca em andamento.")
        if USER_ID.get() is not None and sum(bool(state['worker'] and state['worker'].is_alive()) for state in JOBS.values()) >= int(os.getenv('LUME_MAX_CONCURRENT_JOBS', '2')):
            raise ValueError('O servidor está processando outras buscas. Tente novamente em alguns minutos.')
        topic = discovery.normalize_topic(topic) if topic else None
        get_stop().clear()
        cfg = config()
        if topic:
            cfg['search_topic'] = topic
            store.set_setting('config', cfg)
            # Recheck old uncontacted search records once under the new rules.
            # Do not alter delivered, rejected, blocked or commercially handled leads.
            with store.connect() as db:
                db.execute("UPDATE leads SET status='queued' WHERE json_extract(data,'$.search_topic')=? AND COALESCE(json_extract(data,'$.screening_version'),0)<? AND status='review' AND stage='new' AND delivered_day IS NULL", (topic, screening.VERSION))
        with store.connect() as db:
            run_id = db.execute("INSERT INTO runs(started_at,status,data) VALUES(?, 'running', ?)", (store.now(), json.dumps({"message": "Preparando busca…"}))).lastrowid
        from . import accounts
        accounts.job(run_id, 'running', {})
        worker = threading.Thread(target=copy_context().run, args=(run_pipeline, run_id, cfg, topic, discover), daemon=True, name="lume-worker")
        state = job_state()
        if state is None:
            WORKER = worker
        else:
            state['worker'] = worker
        worker.start()
        return run_id


def scheduler_tick():
    cfg = config()
    current = local_now(cfg)
    if not cfg["schedule_enabled"] or current.hour < cfg["schedule_hour"] or (cfg["weekdays_only"] and current.weekday() > 4):
        return
    today = current.date().isoformat()
    if store.get_setting("last_scheduled_day") == today or (get_worker() and get_worker().is_alive()):
        return
    # A restart resumes the scheduler, never a second job for the same local date.
    if cfg.get('search_topic'):
        start_run(topic=cfg['search_topic'])
    else:
        start_run()
    from . import accounts
    accounts.usage('scheduled')
    store.set_setting("last_scheduled_day", today)


def review_lead(lead_id, changes):
    with LOCK, store.connect() as db:
        row = db.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not row:
            raise ValueError("Loja não encontrada.")
        if row["status"] in ("blocked", "duplicate"):
            raise ValueError("Esta loja está bloqueada ou é duplicada.")
        data = json.loads(row["data"])
        action = changes.pop("action", "save")
        if 'cnpj' in changes or 'cnpj_source' in changes:
            value = (changes.get('cnpj', data.get('cnpj', '')) or '').strip()
            source = (changes.get('cnpj_source', data.get('cnpj_source', '')) or '').strip()
            value = normalize_cnpj(value) if value else ''
            if value:
                if not source:
                    raise ValueError('Informe a URL da fonte do CNPJ.')
                hostname(source)
            if value != data.get('cnpj', '') or source != data.get('cnpj_source', ''):
                data.update(cnpj=value, cnpj_source=source if value else '', cnpj_manual=True)
        if "manual_stock" in changes and not changes["manual_stock"]:
            data["manual_stock"] = False
        reviewed_country = bool(changes.get('country')) and changes['country'] != data.get('country')
        if 'contacts' in changes and changes['contacts'] != data.get('contacts', []):
            data['contacts_manual'] = True
        for field in ("notes", "country", "visits", "traffic_source", "traffic_checked_at", "traffic_period", "contacts", "crm_status", "crm_note"):
            if field in changes:
                data[field] = changes[field]
        if reviewed_country:
            data.update(country_manual=True, country_conflict=False, country_source='Conferência manual no painel')
        if changes.get('crm_status') == 'free':
            data['crm_checked_at'] = store.now()
        elif 'crm_status' in changes:
            data.pop('crm_checked_at', None)
        if data.get("visits") is not None:
            data["traffic_metric"] = "monthly_visits"
        if changes.get("manual_stock"):
            evidence = changes.get("stock_evidence", "").strip()
            source = changes.get("stock_source", "").strip()
            if len(evidence) < 15 or not source:
                raise ValueError("Descreva a evidência de estoque próprio (15 caracteres) e informe a fonte.")
            hostname(source)
            data.update(manual_stock=True, stock_evidence=evidence, stock_source=source)
        if changes.get("manual_shopify"):
            source = changes.get("shopify_source", "").strip()
            hostname(source)
            data.update(shopify_verified=True, platform_source=source, manual_shopify=True)
        screening.stamp(data)
        crm.stamp(db, data)
        problems = evaluate(data, config(), local_now().date())
        data["problems"] = problems
        status = row["status"]
        if action == "approve":
            blockers = approval_blockers(data, config())
            if blockers:
                raise ValueError("Confira antes de aprovar: " + " ".join(blockers))
            data['approved_at'] = store.now()
            status = reviewed_status(data, config(), row['delivered_day'], local_now().date())
        elif action == "reject":
            status = "rejected"
            data.pop('approved_at', None)
        elif action == "block":
            status = "blocked"
            data.pop('approved_at', None)
        elif action == "recheck":
            if data.get('crm_excluded'):
                raise ValueError('Esta loja consta na planilha atual do CRM e não será pesquisada.')
            status = "queued"
        elif status in ("ready", "qualified", "approved"):
            data.setdefault('approved_at', store.now())
            status = reviewed_status(data, config(), row['delivered_day'], local_now().date())
        stage = changes.get("stage", row["stage"])
        db.execute("UPDATE leads SET data=?,status=?,stage=?,updated_at=? WHERE id=?", (json.dumps(data, ensure_ascii=False), status, stage, store.now(), lead_id))
        store.event(db, lead_id, action, changes.get("stock_evidence") or data.get("notes", "")[:500])
    allocate()
    return get_lead(lead_id)


def spreadsheet_safe(value):
    value = str(value or "")
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else value


def export_csv(today_only=True, include_approved=False):
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["loja", "dominio", "pais", "visitas_mensais_estimadas", "fonte_trafego", "data_trafego", "email", "telefone", "whatsapp", "fontes_contatos", "evidencia_estoque", "data_entrega", "etapa", "observacoes", "cnpj", "fonte_cnpj", "url_loja", "periodo_trafego", "situacao", "crm", "criterios_pendentes"])
    with store.connect() as db:
        condition = "status IN ('approved','ready','qualified')" if include_approved else "status='qualified'"
        rows = db.execute("SELECT * FROM leads WHERE " + condition + (" AND delivered_day=?" if today_only else "") + " ORDER BY id", (day(),) if today_only else ()).fetchall()
    for row in rows:
        data = json.loads(row["data"])
        problems = evaluate(data, config(), local_now().date())
        if (approval_blockers(data, config()) if include_approved else problems):
            continue
        grouped = {kind: " | ".join(c["value"] for c in data.get("contacts", []) if c["type"] == kind) for kind in ("email", "phone", "whatsapp")}
        evidence = data.get("stock_evidence") or " | ".join(s["text"] for s in data.get("stock_signals", []))
        values = [data["name"], row["domain"], data.get("country"), data.get("visits"), data.get("traffic_source"), data.get("traffic_checked_at"), grouped["email"], grouped["phone"], grouped["whatsapp"], " | ".join(c["source"] for c in data.get("contacts", [])), evidence, row["delivered_day"], row["stage"], data.get("notes")]
        values.extend([data.get('cnpj'), data.get('cnpj_source'), data.get('storefront_url') or 'https://' + row['domain'], data.get('traffic_period')])
        values.extend(['Aprovada' if problems else 'Critérios atendidos', {'free': 'Conferido: livre', 'assigned': 'Outro vendedor'}.get(data.get('crm_status'), 'Não conferido'), ' | '.join(problems)])
        if include_approved:
            values = ['Não informado' if value is None or value == '' else value for value in values]
        writer.writerow([spreadsheet_safe(v) for v in values])
    return "\ufeff" + output.getvalue()
