from dataclasses import dataclass
import re


@dataclass
class ParsedPhone:
    telefono_normalizado: str | None
    tipo_telefono: str  # whatsapp_verificado | celular | fijo | sin_telefono
    whatsapp_url: str | None


def _wa_url(digits_with_cc: str) -> str:
    return f"https://wa.me/{digits_with_cc}"


def parse_cordoba_phone(raw: str | None) -> ParsedPhone:
    if not raw or not raw.strip():
        return ParsedPhone(None, "sin_telefono", None)

    digits = re.sub(r"\D", "", raw)
    if len(digits) < 7:
        return ParsedPhone(None, "sin_telefono", None)

    # 1. Líneas 0800 / 0810 -> fijo
    if digits.startswith(("0800", "0810", "800", "810")):
        norm = digits if digits.startswith("0") else f"0{digits}"
        return ParsedPhone(norm, "fijo", None)

    # 2. Formato internacional móvil explícito: +54 9 ... (13 dígitos)
    if digits.startswith("549") and len(digits) >= 12:
        # Quitar un posible "15" mal pegado después de 549351
        if digits.startswith("54935115") and len(digits) == 15:
            digits = "549351" + digits[8:]
        return ParsedPhone(f"+{digits}", "celular", _wa_url(digits))

    # 3. Formato internacional sin "9": +54 351 ...
    if digits.startswith("54") and not digits.startswith("549"):
        national = digits[2:]
        if national.startswith("0"):
            national = national[1:]
        return _classify_national(national)

    # 4. Formato nacional con "0" inicial: 0351 ...
    if digits.startswith("0"):
        return _classify_national(digits[1:])

    # 5. Formato local sin 0: 351 ... o solo 7 dígitos de Córdoba Capital
    if len(digits) == 7:
        return _classify_cordoba_local(digits, had_mobile_hint=False)

    if digits.startswith("15") and len(digits) == 9:
        return _classify_cordoba_local(digits[2:], had_mobile_hint=True)

    return _classify_national(digits)


def _classify_national(national: str) -> ParsedPhone:
    # Córdoba Capital: 351 + 15 + 7 dígitos -> celular seguro
    if national.startswith("35115") and len(national) == 12:
        local = national[5:]
        return _classify_cordoba_local(local, had_mobile_hint=True)

    # Córdoba Capital: 351 + 7 dígitos
    if national.startswith("351") and len(national) == 10:
        local = national[3:]
        return _classify_cordoba_local(local, had_mobile_hint=False)

    # Otros códigos de área argentinos con "15" intermedio (ej: 3541 15 xxxxxx)
    m_mobile = re.match(r"^(\d{2,4})15(\d{6,8})$", national)
    if m_mobile and len(m_mobile.group(1) + m_mobile.group(2)) == 10:
        full = f"549{m_mobile.group(1)}{m_mobile.group(2)}"
        return ParsedPhone(f"+{full}", "celular", _wa_url(full))

    # Resto de números nacionales de 10 dígitos sin pista móvil -> fijo por defecto
    if len(national) == 10:
        return ParsedPhone(f"+54{national}", "fijo", None)

    return ParsedPhone(f"+{national}", "fijo", None)


def _classify_cordoba_local(local_7: str, had_mobile_hint: bool) -> ParsedPhone:
    if had_mobile_hint:
        full = f"549351{local_7}"
        return ParsedPhone(f"+{full}", "celular", _wa_url(full))

    # Regla Córdoba Capital: sin "9" ni "15", los 4xx-xxxx y 5xx-xxxx se tratan como fijos
    if local_7.startswith(("4", "5")):
        return ParsedPhone(f"+54351{local_7}", "fijo", None)

    # Resto de prefijos (2xx, 3xx, 6xx, 7xx, 8xx) -> celulares
    full = f"549351{local_7}"
    return ParsedPhone(f"+{full}", "celular", _wa_url(full))
