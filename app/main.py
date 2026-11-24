import asyncio
import csv
import hmac
import io
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, SignatureExpired
from pydantic import BaseModel, Field, field_validator

from . import database as store
from . import service, accounts, crm
from .identity import USER_ID, tenant
from .auth_routes import router as auth_router, current_user, ATTEMPTS
from . import provider, discovery
from .network import hostname
from .qualification import evaluate, valid_contact
from .security import bootstrap_token, password_hash, password_ok, sessions
from .settings import Config, api_key, config, save_key

ROOT = Path(__file__).parent



async def scheduler():
    while True:
        try:
            await asyncio.to_thread(service.scheduler_all)
        except Exception:
            logging.exception("Falha no agendador")
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app):
    store.initialize()
    accounts.initialize()
    with accounts.connect() as db:
        user_ids = [row[0] for row in db.execute('SELECT id FROM users')]
    for uid in user_ids:
        with tenant(uid):
            store.initialize()
    if not accounts.has_users():
        print("\nLUME — Primeiro acesso: use o código de instalação abaixo para criar sua senha.\nCódigo: " + bootstrap_token() + "\nUse o endereço do painel exibido pelo iniciador.\n", flush=True)
    task = asyncio.create_task(scheduler())
    yield
    task.cancel()
    service.stop_all()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Lume SDR", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.include_router(auth_router)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.middleware("http")
async def protect(request: Request, call_next):
    path = request.url.path
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin", "")
        parsed = urlsplit(origin)
        if not origin or parsed.netloc.lower() != request.headers.get("host", "").lower() or parsed.scheme not in ("http", "https"):
            return JSONResponse({"detail": "Origem da requisição inválida. Reabra o painel."}, status_code=403)
        try:
            content_length = int(request.headers.get("content-length", "0") or 0)
        except ValueError:
            return JSONResponse({"detail": "Tamanho de requisição inválido."}, status_code=400)
        size_limit = 21_000_000 if path in ('/api/crm', '/api/crm/preview') else 6_000_000
        if content_length > size_limit:
            return JSONResponse({"detail": "Arquivo muito grande. Limite: " + ('20 MB.' if size_limit == 21_000_000 else '5 MB.')}, status_code=413)
    allowed_hosts = {v.strip().lower() for v in os.getenv('LUME_ALLOWED_HOSTS', '').split(',') if v.strip()}
    host = request.headers.get('host', '').lower()
    if allowed_hosts and urlsplit('//'+host).hostname not in allowed_hosts:
        return JSONResponse({'detail': 'Endereço não autorizado.'}, status_code=400)
    public = path in ('/api/session', '/api/login', '/api/setup', '/api/register', '/api/health')
    user = current_user(request)
    request.state.user = user
    if path.startswith('/api/') and not public and not user:
        return JSONResponse({'detail': 'Entre para continuar.'}, status_code=401)
    context = USER_ID.set(user['id'] if user else None)
    try:
        response = await call_next(request)
        if user and path.startswith('/api/'):
            await asyncio.to_thread(accounts.touch, user['id'])
            if 200 <= response.status_code < 300:
                action = None
                if request.method == 'POST' and path in ('/api/search', '/api/runs'):
                    action = 'search'
                elif request.method == 'POST' and path.startswith('/api/import'):
                    action = 'import'
                elif request.method == 'GET' and path == '/api/export':
                    action = 'export'
                elif request.method == 'POST' and path == '/api/runs/stop':
                    action = 'stop'
                if action:
                    await asyncio.to_thread(accounts.usage, action, user['id'])
    finally:
        USER_ID.reset(context)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(ValueError)
async def value_error(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(csv.Error)
async def csv_error(request, exc):
    return JSONResponse({"detail": "CSV inválido ou com campo muito extenso. Confira o formato do modelo e tente novamente."}, status_code=400)


@app.get("/")
def index():
    return FileResponse(ROOT / "templates" / "index.html")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/settings")
def settings_get():
    return {**config(), "has_api_key": bool(api_key()), "api_key_from_environment": USER_ID.get() is None and bool(os.getenv("STORELEADS_API_KEY")), "search_backend": discovery.backend()}


class SettingsBody(Config):
    api_key: str | None = Field(None, max_length=500)


@app.put("/api/settings")
def settings_save(body: SettingsBody):
    with service.LOCK:
        if service.get_worker() and service.get_worker().is_alive():
            raise ValueError("Aguarde a busca terminar antes de alterar os filtros.")
        store.set_setting("config", body.model_dump(exclude={"api_key"}))
        if body.api_key is not None:
            save_key(body.api_key)
        # Existing deliveries retain their dates; no stale lead remains exportable.
        with store.connect() as db:
            for row in db.execute("SELECT * FROM leads WHERE status IN ('ready','qualified','approved')").fetchall():
                data = json.loads(row["data"])
                problems = evaluate(data, config(), service.local_now().date())
                data["problems"] = problems
                status = service.reviewed_status(data, config(), row['delivered_day'], service.local_now().date())
                db.execute("UPDATE leads SET status=?,data=? WHERE id=?", (status, json.dumps(data), row["id"]))
    return settings_get()


@app.post("/api/provider/test")
def provider_test():
    if not api_key():
        raise ValueError("Salve sua chave nas configurações primeiro.")
    items, cursor = provider.list_page(api_key(), config())
    return {"ok": True, "message": f"Conexão confirmada. {len(items)} lojas retornadas na primeira página."}


@app.post("/api/provider/reset")
def provider_reset():
    with service.LOCK:
        if service.get_worker() and service.get_worker().is_alive():
            raise ValueError("Aguarde a execução atual terminar.")
        with store.connect() as db:
            db.execute("DELETE FROM settings WHERE key LIKE 'cursor:%'")
    return {"ok": True}


@app.post("/api/runs")
def runs_start():
    return {"id": service.start_run()}


@app.post("/api/runs/stop")
def runs_stop():
    service.get_stop().set()
    return {"ok": True, "message": "Interrompendo após a leitura atual…"}


class SearchBody(BaseModel):
    topic: str = Field(min_length=2, max_length=80)


class PastedSearchBody(SearchBody):
    text: str = Field(min_length=1, max_length=100_000)


@app.post('/api/search/results')
def search_results_import(body: PastedSearchBody):
    topic = discovery.normalize_topic(body.topic)
    items = discovery.pasted_results(body.text, topic)
    result = {'inserted': 0, 'duplicates': 0, 'run_id': None}
    with service.LOCK:
        if service.get_worker() and service.get_worker().is_alive():
            raise ValueError('Aguarde a busca atual terminar antes de colar os resultados.')
        with store.connect() as db:
            for data in items:
                result['inserted' if service.ingest(data, db) else 'duplicates'] += 1
        if result['inserted']:
            result['run_id'] = service.start_run(topic=topic, discover=False)
    return result


@app.post('/api/search')
def search_start(body: SearchBody):
    topic = discovery.normalize_topic(body.topic)
    return {'id': service.start_run(topic=topic), 'topic': topic, 'query': discovery.build_query(topic)}


@app.get('/api/search')
def search_info(topic: str = ''):
    from . import search_plan
    topic = discovery.normalize_topic(topic) if topic.strip() else config().get('search_topic')
    return {'topic': topic or '', 'query': discovery.build_query(topic) if topic else '',
            'queries': search_plan.queries(topic) if topic and search_plan.enabled() else [discovery.build_query(topic)] if topic else [],
            'google_url': discovery.google_url(topic) if topic else '', 'provider': discovery.backend(),
            'requires_setup': discovery.backend() == 'searxng', 'page_limit': config()['search_page_limit']}


@app.post('/api/search/reset')
def search_reset(body: SearchBody):
    with service.LOCK:
        if service.get_worker() and service.get_worker().is_alive():
            raise ValueError('Aguarde a busca atual terminar.')
        topic = discovery.normalize_topic(body.topic)
        store.set_setting(discovery.state_key(topic), {})
    return {'ok': True}


@app.get("/api/runs")
def runs_list():
    with store.connect() as db:
        return [store.unpack(r) for r in db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 40")]


@app.post("/api/import")
async def csv_import(file: UploadFile):
    content = await file.read(5_000_001)
    if len(content) > 5_000_000:
        raise HTTPException(413, "Limite: 5 MB por arquivo.")
    return await asyncio.to_thread(service.import_csv, content)


@app.get('/api/crm')
def crm_status():
    return crm.status()


@app.post('/api/crm/preview')
async def crm_preview(file: UploadFile, sheet: str = Form('', max_length=200)):
    content = await file.read(crm.MAX_BYTES + 1)
    return await asyncio.to_thread(crm.preview, content, file.filename, sheet)


@app.post('/api/crm')
async def crm_replace(file: UploadFile, site_column: int = Form(-2), cnpj_column: int = Form(-2), sheet: str = Form('', max_length=200)):
    content = await file.read(crm.MAX_BYTES + 1)
    return await asyncio.to_thread(crm.replace, content, file.filename, site_column, cnpj_column, sheet)


@app.get("/api/import/template")
def csv_template():
    return Response("\ufeffdomain;name;country;monthly_visits;traffic_source;traffic_checked_at;traffic_period;email;phone;whatsapp;contact_source;category\r\n", media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="modelo-lojas.csv"'})


class DomainsBody(BaseModel):
    domains: str = Field(min_length=1, max_length=100_000)


@app.post("/api/import/domains")
def domains_import(body: DomainsBody):
    lines = [line.strip() for line in body.domains.splitlines() if line.strip()]
    if len(lines) > 2000:
        raise ValueError("Cole até 2.000 endereços por vez.")
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["domain", "country"])
    writer.writerows((line, "BR") for line in lines)
    return service.import_csv(output.getvalue().encode())


@app.get("/api/leads")
def leads_list(status: str = "all", q: str = "", today: bool = False, stage: str = "all", page: int = 1, topic: str = "", include_unverified: bool = False, sort: str = 'priority', focus: str = 'all'):
    from . import prospecting
    cfg, current_date = config(), service.local_now().date()
    conditions, args = [crm.AVAILABLE_SQL], []
    if focus == 'approved':
        conditions.append("status IN ('approved','ready','qualified')")
    if focus in ('new', 'crm', 'ready'):
        conditions.extend(["stage='new'", "status NOT IN ('queued','rejected','blocked','duplicate')", "COALESCE(json_extract(data,'$.crm_status'),'unknown')!='assigned'"])
        if focus == 'crm':
            conditions.append("COALESCE(json_extract(data,'$.crm_status'),'unknown')!='free'")
        if focus == 'ready':
            conditions.append("status IN ('ready','qualified') AND prospect_ready(data)=1")
    if topic:
        conditions.append("(json_extract(data,'$.search_topic')=? OR EXISTS(SELECT 1 FROM json_each(leads.data,'$.search_topics') WHERE value=?))")
        args.extend([topic[:80], topic[:80]])
    if status != "all":
        conditions.append("status=?")
        args.append(status)
    if stage != "all":
        conditions.append("stage=?")
        args.append(stage)
    if q:
        conditions.append("(domain LIKE ? ESCAPE '\\' OR json_extract(data,'$.name') LIKE ? ESCAPE '\\')")
        query = "%" + q[:150].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        args.extend([query, query])
    if today:
        conditions.append("delivered_day=?")
        args.append(service.day())
    from .screening import VISIBLE_SQL
    all_where = " WHERE " + " AND ".join(conditions) if conditions else ""
    if not include_unverified:
        conditions.append(VISIBLE_SQL)
        if topic:
            conditions.append("status NOT IN ('rejected','blocked','duplicate')")
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    page = max(page, 1)
    with store.connect() as db:
        db.create_function('prospect_rank', 3, lambda data, status, stage: prospecting.sql_rank(data, status, stage, cfg, current_date))
        db.create_function('prospect_ready', 1, lambda data: not evaluate(json.loads(data), cfg, current_date))
        all_count = db.execute("SELECT count(*) FROM leads" + all_where, args).fetchone()[0]
        count = db.execute("SELECT count(*) FROM leads" + where, args).fetchone()[0]
        order = 'id DESC' if sort == 'recent' else 'prospect_rank(data,status,stage) DESC,id DESC'
        rows = db.execute("SELECT * FROM leads" + where + " ORDER BY " + order + " LIMIT 30 OFFSET ?", args + [(page - 1) * 30]).fetchall()
    items = [store.unpack(row) for row in rows]
    for lead in items:
        lead["data"]["current_problems"] = evaluate(lead["data"], cfg, current_date)
        lead['data']['prospecting'] = prospecting.brief(lead['data'], lead['status'], lead['stage'], cfg, current_date)
        lead['data']['approval_blockers'] = service.approval_blockers(lead['data'], cfg)
        lead['data'].pop('catalog_terms', None)
    return {"items": items, "total": count, "hidden": all_count - count, "page": page, "pages": max(1, (count + 29) // 30)}


@app.get("/api/leads/{lead_id}")
def lead_get(lead_id: int):
    from . import prospecting
    lead = service.get_lead(lead_id)
    lead["data"]["current_problems"] = evaluate(lead["data"], config(), service.local_now().date())
    lead['data']['prospecting'] = prospecting.brief(lead['data'], lead['status'], lead['stage'], config(), service.local_now().date())
    lead['data']['approval_blockers'] = service.approval_blockers(lead['data'], config())
    lead['data'].pop('catalog_terms', None)
    return lead


class Contact(BaseModel):
    type: Literal["email", "phone", "whatsapp"]
    value: str = Field(max_length=250)
    source: str = Field(max_length=1000)
    via: str = Field("Revisão manual", max_length=100)

    @field_validator("source")
    @classmethod
    def source_valid(cls, value):
        if value:
            hostname(value)
        return value


class ReviewBody(BaseModel):
    action: Literal["save", "approve", "reject", "block", "recheck"] = "save"
    stage: Literal["new", "contacted", "replied", "meeting", "won", "lost"] | None = None
    notes: str | None = Field(None, max_length=5000)
    cnpj: str | None = Field(None, max_length=30)
    cnpj_source: str | None = Field(None, max_length=1000)
    country: str | None = Field(None, max_length=2)
    visits: int | None = Field(None, ge=0, le=10**12)
    traffic_source: str | None = Field(None, max_length=500)
    traffic_checked_at: str | None = Field(None, max_length=10)
    traffic_period: str | None = Field(None, max_length=100)
    contacts: list[Contact] | None = Field(None, max_length=20)
    manual_stock: bool = False
    stock_evidence: str = Field("", max_length=1000)
    stock_source: str = Field("", max_length=1000)
    manual_shopify: bool = False
    shopify_source: str = Field("", max_length=1000)
    crm_status: Literal['unknown', 'free', 'assigned'] | None = None
    crm_note: str | None = Field(None, max_length=1000)


@app.patch("/api/leads/{lead_id}")
def lead_review(lead_id: int, body: ReviewBody):
    changes = body.model_dump(exclude_unset=True)
    changes = {k: v for k, v in changes.items() if v is not None or k == "visits"}
    if changes.get("contacts"):
        for contact in changes["contacts"]:
            if not valid_contact(contact["type"], contact["value"]):
                raise ValueError("E-mail ou telefone inválido.")
    result = service.review_lead(lead_id, changes)
    accounts.usage('approve' if body.action == 'approve' else 'review')
    return result


@app.get("/api/export")
def csv_export(scope: Literal["today", "all", "approved"] = "today"):
    return Response(service.export_csv(scope == "today", include_approved=scope == 'approved'), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="lume-{service.day()}-{scope}.csv"'})


@app.get("/api/dashboard")
def dashboard():
    from .screening import VISIBLE_SQL
    cfg = config()
    today = service.day(cfg)
    with store.connect() as db:
        counts = {r["status"]: r["n"] for r in db.execute("SELECT status,count(*) n FROM leads WHERE " + VISIBLE_SQL + " GROUP BY status")}
        prospect_count = db.execute("SELECT count(*) FROM leads WHERE " + VISIBLE_SQL + " AND status NOT IN ('rejected','blocked','duplicate','queued') AND stage='new' AND COALESCE(json_extract(data,'$.crm_status'),'unknown')!='assigned'").fetchone()[0]
        crm_count = db.execute("SELECT count(*) FROM leads WHERE " + VISIBLE_SQL + " AND status NOT IN ('rejected','blocked','duplicate','queued') AND stage='new' AND COALESCE(json_extract(data,'$.crm_status'),'unknown') NOT IN ('free','assigned')").fetchone()[0]
        delivered = db.execute("SELECT count(*) FROM leads WHERE delivered_day=? AND " + crm.AVAILABLE_SQL, (today,)).fetchone()[0]
        recent = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        from datetime import timedelta
        dates = [(service.local_now(cfg).date() - timedelta(days=n)).isoformat() for n in range(6, -1, -1)]
        history = [{"day": d, "count": db.execute("SELECT count(*) FROM leads WHERE delivered_day=? AND " + crm.AVAILABLE_SQL, (d,)).fetchone()[0]} for d in dates]
    return {"counts": counts, "total": sum(counts.values()), "prospect_count": prospect_count, "crm_count": crm_count, "delivered_today": delivered, "goal": cfg["daily_goal"], "date": today, "history": history,
            "last_run": store.unpack(recent) if recent else None, "running": bool(service.get_worker() and service.get_worker().is_alive()),
            "has_api_key": bool(api_key()), "schedule_enabled": cfg["schedule_enabled"], "schedule_hour": cfg["schedule_hour"], "timezone": cfg["timezone"], "crm": crm.status()}


# Outermost ASGI wrappers protect errors, request bodies and transport.
from .http_security import SecurityEnvelope
from .transport_security import TransportSecurity
app = SecurityEnvelope(TransportSecurity(app))
