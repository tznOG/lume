"""Private replacement snapshots, independent of the prospecting workflow.

Only normalized site/CNPJ identifiers and import counts are retained; uploaded
workbooks and unrelated CRM columns are never saved or sent to search engines.
"""
import csv
import io
import json
import re
import unicodedata
import zipfile
from pathlib import Path

from . import database as store
from .company import normalize_cnpj
from .network import canonical

MAX_BYTES = 20_000_000
MAX_ROWS = 50_000
MAX_COLUMNS = 200
AVAILABLE_SQL = "COALESCE(json_extract(data,'$.crm_excluded'),0)=0"


def text(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip() if value is not None else ''


def header(value):
    value = unicodedata.normalize('NFKD', value.casefold())
    return re.sub(r'[^a-z0-9]', '', value)


def read_table(content, filename, sheet=''):
    if len(content) > MAX_BYTES:
        raise ValueError('Limite: 20 MB por arquivo do CRM.')
    suffix = Path(filename or '').suffix.lower()
    workbook = None
    try:
        sheets = []
        if suffix == '.csv':
            try:
                decoded = content.decode('utf-8-sig')
            except UnicodeDecodeError:
                decoded = content.decode('cp1252')
            if '\x00' in decoded:
                raise ValueError('Exporte o CSV em UTF-8 ou envie um arquivo Excel .xlsx.')
            try:
                dialect = csv.Sniffer().sniff(decoded[:4096], delimiters=',;\t')
            except csv.Error:
                dialect = csv.excel
            source = csv.reader(io.StringIO(decoded), dialect=dialect)
        elif suffix == '.xlsx':
            from openpyxl import load_workbook
            # XLSX is a ZIP: bound decompression before passing it to the reader.
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries = archive.infolist()
                if len(entries) > 2000 or sum(e.file_size for e in entries) > 40_000_000:
                    raise ValueError('Excel muito grande após descompactação. Exporte uma aba como CSV.')
            workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=False, keep_links=False)
            sheets = workbook.sheetnames
            if not sheets or (sheet and sheet not in sheets):
                raise ValueError('Selecione uma aba válida do Excel.')
            sheet = sheet or sheets[0]
            worksheet = workbook[sheet]
            worksheet.reset_dimensions()
            source = ([cell.value if cell.data_type != 'f' else '' for cell in row]
                      for row in worksheet.iter_rows(max_row=MAX_ROWS + 2, max_col=MAX_COLUMNS + 1))
        else:
            raise ValueError('Envie um arquivo CSV ou Excel .xlsx. Para .xls, salve como .xlsx.')
        rows = []
        for number, row in enumerate(source):
            values = [text(v) for v in row]
            while values and not values[-1]:
                values.pop()
            if len(values) > MAX_COLUMNS:
                raise ValueError('Limite: 200 colunas por planilha.')
            if number > MAX_ROWS:
                raise ValueError('Limite: 50.000 linhas por planilha.')
            if any(len(v) > 10_000 for v in values):
                raise ValueError('A planilha contém uma célula muito longa.')
            rows.append(values)
        if not rows or not rows[0]:
            raise ValueError('A primeira linha precisa conter os nomes das colunas.')
        headers = rows.pop(0)
        return headers, rows, sheets, sheet
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError('Não foi possível ler a planilha. Exporte novamente em CSV ou Excel .xlsx.') from exc
    finally:
        if workbook:
            workbook.close()


def detect(headers, kind):
    aliases = {'site': {'site', 'website', 'url', 'dominio', 'domain', 'sitedaloja', 'sitedaempresa', 'urlsite', 'urldaloja', 'linkdaloja', 'companywebsite', 'sitedaorganizacao'},
               'cnpj': {'cnpj', 'cpfcnpj', 'cnpjcpf', 'cnpjdaempresa', 'documento', 'documentodaempresa'}}
    return next((i for i, h in enumerate(headers) if header(h) in aliases[kind]), -1)


def preview(content, filename, sheet=''):
    headers, rows, sheets, selected = read_table(content, filename, sheet)
    return {'headers': headers, 'rows': sum(bool(r) for r in rows), 'sheets': sheets,
            'sheet': selected, 'site_column': detect(headers, 'site'), 'cnpj_column': detect(headers, 'cnpj')}


def site_key(value):
    result = canonical(value.strip())
    # A social-network or shared-platform homepage does not identify a shop.
    if result in {'instagram.com', 'facebook.com', 'wa.me', 'whatsapp.com', 'linktr.ee', 'myshopify.com', 'shopify.com', 'gmail.com', 'hotmail.com', 'outlook.com'}:
        raise ValueError('Use o site da loja, não um endereço de plataforma compartilhada.')
    return result


def cnpj_key(value):
    if value.isdigit() and len(value) < 14:
        value = value.zfill(14)  # Excel can discard leading zeroes in numeric cells.
    return normalize_cnpj(value)


def identifiers(data):
    result = set()
    for value in [data.get('domain'), data.get('storefront_url'), data.get('shop_id'), *data.get('aliases', [])]:
        if value:
            try:
                result.add(('site', site_key(str(value))))
            except ValueError:
                pass
    if data.get('cnpj'):
        try:
            result.add(('cnpj', cnpj_key(str(data['cnpj']))))
        except ValueError:
            pass
    return result


def matches(db, data):
    return any(db.execute('SELECT 1 FROM crm_exclusions WHERE kind=? AND value=?', key).fetchone()
               for key in identifiers(data))


def stamp(db, data):
    data['crm_excluded'] = matches(db, data)


def status():
    result = store.get_setting('crm_snapshot', {})
    with store.connect() as db:
        result['excluded_leads'] = db.execute("SELECT count(*) FROM leads WHERE NOT (" + AVAILABLE_SQL + ")").fetchone()[0]
    return result


def replace(content, filename, site_column=-2, cnpj_column=-2, sheet=''):
    from .service import LOCK, local_now
    from .qualification import evaluate, reviewed_status
    from .settings import config
    headers, rows, _, _ = read_table(content, filename, sheet)
    site_column = detect(headers, 'site') if site_column == -2 else site_column
    cnpj_column = detect(headers, 'cnpj') if cnpj_column == -2 else cnpj_column
    if any(c < -1 or c >= len(headers) for c in (site_column, cnpj_column)):
        raise ValueError('Selecione colunas válidas.')
    if site_column == cnpj_column:
        raise ValueError('Selecione pelo menos uma coluna de site ou CNPJ, em colunas diferentes.')
    keys, valid, skipped, invalid_cells, duplicates, errors = set(), 0, 0, 0, 0, []
    for line, row in enumerate(rows, 2):
        if not row:
            continue
        found = set()
        for kind, col, normalize in (('site', site_column, site_key), ('cnpj', cnpj_column, cnpj_key)):
            value = row[col] if 0 <= col < len(row) else ''
            if value:
                try:
                    found.add((kind, normalize(value)))
                except ValueError:
                    invalid_cells += 1
                    if len(errors) < 10:
                        errors.append(f'Linha {line}: {kind} inválido; campo ignorado.')
        if found:
            valid += 1
            duplicates += int(found <= keys)
            keys.update(found)
        else:
            skipped += 1
    if not keys:
        raise ValueError('Nenhum site ou CNPJ válido encontrado. A lista anterior foi mantida. Confira as colunas selecionadas.')
    summary = {'updated_at': store.now(), 'rows': valid + skipped, 'valid_rows': valid,
               'skipped_rows': skipped, 'invalid_cells': invalid_cells, 'duplicate_rows': duplicates,
               'sites': sum(k == 'site' for k, _ in keys), 'cnpjs': sum(k == 'cnpj' for k, _ in keys), 'errors': errors}
    with LOCK, store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM crm_exclusions')
        db.executemany('INSERT INTO crm_exclusions VALUES(?,?)', sorted(keys))
        excluded = 0
        cfg, today = config(), local_now().date()
        for row in db.execute('SELECT id,data,status,delivered_day FROM leads').fetchall():
            data = json.loads(row['data'])
            matched = bool(identifiers(data) & keys)
            excluded += int(matched)
            if bool(data.get('crm_excluded')) != matched:
                data['crm_excluded'] = matched
                # An inspection or settings save while excluded may have moved
                # a previously accepted lead to review. Restore eligibility,
                # preserving explicit rejection, blocking and the queued state.
                status = row['status']
                if not matched and status not in ('queued', 'rejected', 'blocked', 'duplicate'):
                    data['problems'] = evaluate(data, cfg, today)
                    status = reviewed_status(data, cfg, row['delivered_day'], today)
                db.execute('UPDATE leads SET data=?,status=? WHERE id=?', (json.dumps(data, ensure_ascii=False), status, row['id']))
        db.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                   ('crm_snapshot', json.dumps(summary)))
    return {**summary, 'excluded_leads': excluded}
