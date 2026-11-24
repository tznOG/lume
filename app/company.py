"""CNPJ published by a store; checksum validation is not a registry lookup."""
import re


def normalize_cnpj(value):
    raw = re.sub(r"[.\s/\-]", "", str(value)).upper()
    if not re.fullmatch(r"[A-Z0-9]{12}[0-9]{2}", raw) or len(set(raw)) == 1:
        raise ValueError("CNPJ inválido. Confira os 14 caracteres e os dígitos verificadores.")
    # Receita Federal: ASCII minus 48, modulo 11 (numeric and alphanumeric).
    digits = raw[:12]
    for weights in ((5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2), (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)):
        remainder = sum((ord(c) - 48) * w for c, w in zip(digits, weights)) % 11
        digits += str(0 if remainder < 2 else 11 - remainder)
    if digits != raw:
        raise ValueError("CNPJ inválido. Os dígitos verificadores não conferem.")
    return f"{raw[:2]}.{raw[2:5]}.{raw[5:8]}/{raw[8:12]}-{raw[12:]}"


def find_cnpjs(text):
    # Formatted identifiers or an unformatted identifier explicitly labelled CNPJ.
    pattern = r"(?<![A-Z0-9])(?:[A-Z0-9]{2}\.[A-Z0-9]{3}\.[A-Z0-9]{3}/[A-Z0-9]{4}-[0-9]{2}|CNPJ\s*[:\-]?\s*[A-Z0-9]{12}[0-9]{2})(?![A-Z0-9])"
    found = []
    for match in re.finditer(pattern, text, re.I):
        try:
            value = normalize_cnpj(re.sub(r"^CNPJ\s*[:\-]?\s*", "", match[0], flags=re.I))
        except ValueError:
            continue
        if value not in found:
            found.append(value)
    return found
