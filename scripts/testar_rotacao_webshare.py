"""
Testa se a rotação de IP do Webshare está de fato funcionando pra sua
conta — reproduzindo EXATAMENTE o que o youtube_transcript_api faz por
baixo dos panos (sufixo "-rotate" no username, sem manter conexão viva).

Diferente do testar_proxy_webshare.py (que testava só se o proxy CONECTA,
usando o username puro): esse aqui testa se, a cada chamada, você recebe
um IP DIFERENTE. Se o IP não mudar entre as chamadas, a rotação não está
ativa pra sua conta/plano — e isso explicaria o 0% de sucesso mesmo com
plano residencial.

Uso:
    python testar_rotacao_webshare.py
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

# Mesmo esquema que o youtube_transcript_api usa internamente (proxies.py,
# WebshareProxyConfig.url): remove um "-rotate" pré-existente e reaplica,
# pra garantir que ativa o mesmo endpoint rotativo.
username_base = USERNAME[:-len("-rotate")] if USERNAME.endswith("-rotate") else USERNAME
username_rotate = f"{username_base}-rotate"

PROXY_URL = f"http://{username_rotate}:{PASSWORD}@p.webshare.io:80/"
PROXIES = {"http": PROXY_URL, "https": PROXY_URL}

N_CHAMADAS = 6

print(f"Usuário base       : {username_base}")
print(f"Usuário c/ rotação  : {username_rotate}")
print(f"Endpoint            : p.webshare.io:80\n")
print(f"Fazendo {N_CHAMADAS} chamadas seguidas (sem manter conexão viva, igual a lib faz)...\n")

ips = []
for i in range(N_CHAMADAS):
    try:
        inicio = time.time()
        # headers={"Connection": "close"} força não reaproveitar a conexão
        # TCP — é o que "prevent_keeping_connections_alive" faz na lib.
        r = requests.get(
            "https://api.ipify.org?format=json",
            proxies=PROXIES,
            timeout=15,
            headers={"Connection": "close"},
        )
        ip = r.json().get("ip")
        ips.append(ip)
        print(f"  [{i + 1}/{N_CHAMADAS}] IP: {ip}  ({time.time() - inicio:.1f}s)")
    except Exception as e:
        ips.append(None)
        print(f"  [{i + 1}/{N_CHAMADAS}] FALHOU ({type(e).__name__}): {e}")
    time.sleep(1)

ips_validos = [ip for ip in ips if ip]
ips_unicos = set(ips_validos)

print(f"\n{'=' * 50}")
if not ips_validos:
    print("[RESULTADO] Nenhuma chamada teve sucesso — proxy/credenciais com problema.")
elif len(ips_unicos) == 1:
    print(
        f"[RESULTADO] Todas as {len(ips_validos)} chamadas bem-sucedidas voltaram "
        f"com o MESMO IP ({ips_unicos.pop()}).\n"
        "  → A rotação NÃO está funcionando pra essa conta/formato de username.\n"
        "  → Se esse IP já estiver bloqueado pelo YouTube, TODA chamada vai falhar\n"
        "    sempre, o que bate com o 0% de sucesso que você teve.\n"
        "  → Vale abrir um chamado com o suporte do Webshare perguntando por que\n"
        "    o sufixo '-rotate' não está trocando o IP de saída."
    )
else:
    print(
        f"[RESULTADO] {len(ips_unicos)} IPs diferentes em {len(ips_validos)} chamadas "
        "bem-sucedidas — a rotação ESTÁ funcionando.\n"
        "  → Se mesmo assim o YouTube bloqueia tudo, o problema é o pool inteiro\n"
        "    (ou uma fatia grande dele) já estar na blacklist do YouTube — nesse\n"
        "    caso vale contatar o suporte do Webshare citando isso, ou considerar\n"
        "    outro provedor."
    )
print(f"{'=' * 50}")
