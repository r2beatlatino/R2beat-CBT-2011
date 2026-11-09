# -*- coding: utf-8 -*-
"""Cifrado del protocolo y hash de contrasenas.

El cliente cifra parte de lo que manda con un XOR simple (FF55). No es
seguridad real, es del juego original; se implementa igual porque el
cliente espera exactamente eso.

--
Parte del servidor abierto de R2Beat. Ver LEEME.md en la raiz del proyecto.
"""

import hashlib

from .ajustes import CLIENT_CODEPAGE


def hash_password(plain):
    return hashlib.sha256(plain.encode("utf-8")).hexdigest().upper()

def enc_text(s):
    if isinstance(s, (bytes, bytearray)):
        return bytes(s)
    return (s or "").encode(CLIENT_CODEPAGE, errors="replace")

# ============================================================
# Crypto FF55 + parsing de comandos (PLUMBING — protocolo, no parche)
# ============================================================
def encrypt_ff55(plain, pwd, edx):
    rv = edx & 0xFF
    return bytes(pwd[i % 256] ^ pwd[rv] ^ rv ^ plain[i] for i in range(len(plain)))

def decrypt_ff55(enc, pwd, edx):
    rv = edx & 0xFF
    return bytes(pwd[i % 256] ^ pwd[rv] ^ rv ^ enc[i] for i in range(len(enc)))
