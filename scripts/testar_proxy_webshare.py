"""
Teste isolado do proxy Webshare, sem depender do youtube_transcript_api.
Só bate num serviço que devolve seu IP público, direto e via proxy, pra
confirmar se as credenciais/formato do Webshare estão certos e se a
conexão via proxy sequer estabelece.

Uso:
    python testar_proxy_webshare.py
"""

import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

USERNAME = os.environ.get("WEBSHARE_PROXY_USERNAME")
PASSWORD = os.environ.get("WEBSHARE_PROXY_PASSWORD")

if not USERNAME or not PASSWORD:
    raise RuntimeError("WEBSHARE_PROXY_USERNAME/PASSWORD não encontrados no .env")

# Endpoint padrão do Webshare pra proxies rotativos (residencial). Se
# você pegou credenciais de um plano diferente (proxy list / dedicado),
# o endpoint pode ser outro — confere no dashboard do Webshare, aba
# "Proxy List", em "Endpoint".
PROXY_URL = f"http://{USERNAME}:{PASSWORD}@p.webshare.io:80"
PROXIES = {"http": PROXY_URL, "https": PROXY_URL}

print(f"Usuário do proxy: {USERNAME}")
print(f"Endpoint testado: p.webshare.io:80\n")

print("[1/2] Testando conexão DIRETA (sem proxy)...")
try:
    inicio = time.time()
    r = requests.get("https://api.ipify.org?format=json", timeout=10)
    print(f"  → OK: {r.json()} ({time.time() - inicio:.1f}s)")
except Exception as e:
    print(f"  → Falhou ({type(e).__name__}): {e}")

print("\n[2/2] Testando conexão VIA PROXY Webshare...")
try:
    inicio = time.time()
    r = requests.get("https://api.ipify.org?format=json", proxies=PROXIES, timeout=15)
    print(f"  → OK: {r.json()} ({time.time() - inicio:.1f}s)")
    print("\n[RESULTADO] Proxy está funcionando — o problema deve estar em outro lugar (talvez o próprio YouTube bloqueando o IP do proxy também).")
except Exception as e:
    print(f"  → Falhou ({type(e).__name__}): {e}")
    print("\n[RESULTADO] Proxy NÃO está conectando. Prováveis causas:")
    print("  - Usuário/senha errados, ou copiados de outro tipo de plano (dedicado vs rotativo)")
    print("  - Endpoint errado (confere no dashboard do Webshare qual é o endpoint certo pro seu plano)")
    print("  - Plano/conta com problema (créditos zerados, conta não ativada)")
