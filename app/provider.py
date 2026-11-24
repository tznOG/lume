"""Store Leads documented REST schema: https://storeleads.app/api ."""
import time
from urllib.parse import urljoin

import httpx

from .network import hostname
from .qualification import valid_contact, drop_signals

ENDPOINT = "https://storeleads.app/json/api/v1/all/domain"


class ProviderError(ValueError):
    pass


def list_page(key, config, cursor=None):
    params = {"f:p": "shopify", "f:evmin": config["min_visits"], "f:ds": "Active", "f:ismy": "false", "page_size": 50}
    if config["countries"]:
        params.update({"f:cc": config["countries"], "f:cc:op": "or"})
    if cursor:
        params["cursor"] = cursor
    with httpx.Client(timeout=40, follow_redirects=False, trust_env=False) as client:
        for attempt in range(3):
            try:
                response = client.get(ENDPOINT, params=params, headers={"Authorization": f"Bearer {key}"})
            except httpx.HTTPError as exc:
                raise ProviderError("Não foi possível conectar ao Store Leads. Tente novamente.") from exc
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == 2:
                    raise ProviderError("Store Leads indisponível ou com limite de requisições. Tente mais tarde.")
                try:
                    delay = min(max(float(response.headers.get("Retry-After", 2 ** attempt)), 1), 30)
                except ValueError:
                    delay = 2 ** attempt
                time.sleep(delay)
                continue
            if response.status_code in (401, 403):
                raise ProviderError("Chave inválida ou plano sem acesso à API do Store Leads.")
            if response.status_code != 200:
                raise ProviderError(f"Store Leads retornou HTTP {response.status_code}. Confira a integração.")
            try:
                payload = response.json()
            except ValueError as exc:
                raise ProviderError("Resposta inválida do Store Leads.") from exc
            # Documented list response uses 'domains'; tolerate legacy 'domain'.
            domains = payload.get("domains", payload.get("domain"))
            if not isinstance(domains, list):
                raise ProviderError("Formato do Store Leads mudou: lista de domínios ausente.")
            return domains, payload.get("next_cursor")
    raise ProviderError("Falha ao consultar fonte.")


def normalize(item, checked_day):
    domain = hostname(item.get("name", ""))
    base = "https://" + domain
    contacts = []
    for contact in item.get("contact_info", []):
        if valid_contact(contact.get("type"), contact.get("value", "")):
            contacts.append({"type": contact["type"], "value": contact["value"], "source": urljoin(base, contact.get("source") or "/"), "via": "Store Leads"})
    apps = [a.get("name", a.get("token", "")) if isinstance(a, dict) else str(a) for a in item.get("apps", []) if not isinstance(a, dict) or not a.get("removed_at")]
    visits = item.get("estimated_visits")
    try:
        visits = int(visits) if visits is not None else None
    except (ValueError, TypeError):
        visits = None
    aliases = item.get("aliases", []) + [item.get("cluster_best_ranked", "")]
    return {
        "domain": domain, "name": item.get("merchant_name") or item.get("title") or domain,
        "country": item.get("country_code", ""), "category": ", ".join(item.get("categories", [])),
        "shopify_verified": item.get("platform", "").lower() == "shopify", "platform_source": "Store Leads",
        "visits": visits, "traffic_source": "Store Leads", "traffic_metric": "monthly_visits",
        "traffic_checked_at": (item.get("last_updated_at") or checked_day)[:10],
        "traffic_collected_at": checked_day, "traffic_period": "Estimativa mensal do fornecedor; mês de referência não informado",
        "contacts": contacts, "apps": apps, "drop_signals": drop_signals(apps), "stock_signals": [],
        "evidence": [], "notes": "", "source": "Store Leads", "aliases": [a for a in aliases if isinstance(a, str) and a],
    }
