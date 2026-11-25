"""
Codes à usage unique (TOTP, RFC 6238) : ceux qu'affichent les applications d'authentification
(Proton Pass, Aegis, FreeOTP, Google Authenticator…). 6 chiffres, renouvelés toutes les 30 secondes.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

STEP = 30
DIGITS = 6
DRIFT = (
    1  # tolérance : le code précédent et le suivant sont acceptés (horloge du téléphone décalée)
)


def new_secret():
    """Secret partagé de 160 bits, en base 32 (format attendu par les applications)."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def code_at(secret, counter):
    key = base64.b32decode(secret, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def counter_now(now=None):
    return int((time.time() if now is None else now) // STEP)


def match(secret, code, after=-1, now=None):
    """
    Compteur du code s'il est valide, sinon None. Un code déjà utilisé (compteur ≤ `after`) est
    refusé :
    un code intercepté ne sert pas une seconde fois.
    """
    code = "".join(c for c in str(code) if c.isdigit())
    if len(code) != DIGITS:
        return None
    current = counter_now(now)
    for counter in range(current - DRIFT, current + DRIFT + 1):
        if counter > after and hmac.compare_digest(code_at(secret, counter), code):
            return counter
    return None


def provisioning_uri(secret, account, issuer="CyberLearning"):
    """Adresse « otpauth:// » contenue dans le QR code."""
    label = quote(f"{issuer}:{account}")
    return f"otpauth://totp/{label}?" + urlencode(
        {"secret": secret, "issuer": issuer, "digits": DIGITS, "period": STEP}
    )


def new_recovery_codes(count=10):
    """Codes de secours (un seul usage chacun), au format « abcd-efgh-ijkl »."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return [
        "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(3))
        for _ in range(count)
    ]


def normalize_recovery(code):
    return "".join(c for c in str(code).lower() if c.isalnum())
