import re
import unicodedata
from datetime import date, datetime

DROP_APPS = ("dsers", "oberlo", "zendrop", "spocket", "cjdropshipping", "cj dropshipping", "aliexpress", "auto-ds", "autods", "dropified", "modalyst", "syncee")
STOCK_PHRASES = ("estoque proprio", "fabricacao propria", "producao propria", "own inventory", "own warehouse", "we manufacture", "nao trabalhamos com dropshipping", "we do not dropship")
EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)


def fold(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def stock_signals(text):
    """Only explicit statements suggest stock. Negated claims never qualify."""
    normalized = fold(text)
    signals = []
    for phrase in STOCK_PHRASES:
        for match in re.finditer(re.escape(phrase), normalized):
            prefix = normalized[max(0, match.start() - 70):match.start()]
            if phrase not in STOCK_PHRASES[-2:] and re.search(r"\b(nao|sem|not|no|never|possui|possui[r]?\?|tem\?|temos\?)\b[^.!?;]{0,55}$", prefix):
                continue
            signals.append(normalized[max(0, match.start() - 40):match.end() + 70].strip())
    return signals[:5]


def drop_signals(apps, text=""):
    results = [f"Aplicativo: {app}" for app in apps if any(term in fold(app) for term in DROP_APPS)]
    normalized = fold(text)
    for phrase in ("trabalhamos com dropshipping", "we dropship", "enviado diretamente da china", "direto do fornecedor internacional"):
        for match in re.finditer(re.escape(phrase), normalized):
            prefix = normalized[max(0, match.start() - 25):match.start()]
            if not re.search(r"\b(nao|not|never|no)\b[^.!?;]{0,20}$", prefix):
                results.append(normalized[max(0, match.start() - 25):match.end() + 60])
    return results[:10]


def valid_contact(kind, value):
    value = str(value).strip()
    if kind == "email":
        return bool(EMAIL_RE.fullmatch(value)) and not value.lower().endswith((".png", ".jpg", ".webp", ".svg"))
    if kind in ("phone", "whatsapp"):
        return 10 <= len(re.sub(r"\D", "", value)) <= 15
    return False


def evaluate(data, config, today=None):
    today = today or date.today()
    problems = []
    if data.get('crm_excluded'):
        problems.append('Loja presente na planilha atual do CRM')
    if data.get('search_topic'):
        from .screening import reasons
        problems.extend(reasons(data))
    if not data.get("shopify_verified"):
        problems.append("Shopify ainda não confirmado")
    countries = [c.strip() for c in config["countries"].split(",") if c.strip()]
    if countries and data.get("country") not in countries:
        problems.append("País fora do filtro ou não informado")
    visits = data.get("visits")
    if visits is None or visits < config["min_visits"]:
        problems.append("Tráfego abaixo do mínimo ou não informado")
    if data.get("traffic_metric") != "monthly_visits":
        problems.append("Métrica deve ser visitas mensais totais")
    if not data.get("traffic_source"):
        problems.append("Fonte do tráfego não informada")
    try:
        checked = date.fromisoformat(data.get("traffic_checked_at", "")[:10])
        age = (today - checked).days
        if age < 0 or age > config["traffic_max_age"]:
            problems.append("Estimativa de tráfego vencida ou com data futura")
    except (ValueError, TypeError):
        problems.append("Data da estimativa de tráfego não informada")
    if not any(valid_contact(c["type"], c["value"]) and c.get("source") for c in data.get("contacts", [])):
        problems.append("Sem e-mail ou telefone comercial com fonte")
    if data.get("drop_signals") and not data.get("manual_stock"):
        problems.append("Há indícios de dropshipping: revisar")
    if not data.get("stock_signals") and not data.get("manual_stock"):
        problems.append("Operação com estoque próprio não confirmada")
    if data.get("crawl_error") and not data.get("manual_stock"):
        problems.append("Leitura do site incompleta: revisar")
    if data.get("crm_status") == "assigned":
        problems.append("CRM: loja já atribuída ou atendida por outro vendedor")
    elif config.get("require_crm_check", True) and data.get("crm_status") != "free":
        problems.append("Conferir no CRM se a loja está livre para prospecção")
    return problems


def approval_blockers(data, config):
    """Manual acceptance allows missing enrichment, but not known exclusions."""
    problems = []
    if data.get('crm_excluded'):
        problems.append('Loja presente na planilha atual do CRM.')
    if data.get('search_topic'):
        from .screening import reasons
        problems.extend(reasons(data))
    if not data.get('shopify_verified'):
        problems.append('Confirme que a loja usa Shopify.')
    if data.get('country') != 'BR':
        problems.append('Confirme que a loja é brasileira.')
    if not any(valid_contact(c.get('type'), c.get('value', '')) and c.get('source') for c in data.get('contacts', [])):
        problems.append('Adicione pelo menos um contato público com sua fonte.')
    if data.get('drop_signals') and not data.get('manual_stock'):
        problems.append('Revise os indícios de dropshipping nas verificações adicionais.')
    if data.get('crm_status') == 'assigned':
        problems.append('A loja está marcada como atendida por outro vendedor no CRM.')
    if data.get('visits') is not None and data['visits'] < config['min_visits']:
        problems.append(f"O tráfego informado está abaixo do mínimo de {config['min_visits']:,} visitas mensais.".replace(',', '.'))
    return list(dict.fromkeys(problems))


def reviewed_status(data, config, delivered_day=None, today=None):
    """Acceptance and full qualification are separate; unknown data stays unknown."""
    if not evaluate(data, config, today):
        return 'qualified' if delivered_day else 'ready'
    if data.get('approved_at') and not approval_blockers(data, config):
        return 'approved'
    return 'review'
