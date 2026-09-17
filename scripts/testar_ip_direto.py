"""
Testa se sua IP direta (de casa, sem proxy) ainda está bloqueada pelo
YouTube pra baixar transcrição — ou se o bloqueio de antes já passou.

Bloqueios do YouTube por excesso de requisição costumam ser temporários
(geralmente somem depois de algumas horas a 1-2 dias sem mais tentativas
da mesma IP). Como o proxy do Webshare parece estar bloqueado de forma
mais ampla (ver diagnóstico anterior), vale confirmar se voltar a usar
sua IP direta — devagar, sem concorrência — é viável antes de investir
em outro provedor de proxy.

Pega alguns video_ids de dados_cortes_flow_games/cortes.json que ainda
não têm transcrição, e tenta baixar 1 por vez, direto (sem proxy),
com pausa generosa entre cada um.

Uso:
    python testar_ip_direto.py
"""

import os
import json
import time
from youtube_transcript_api import YouTubeTranscriptApi

N_TESTE = 8
PAUSA_ENTRE_TENTATIVAS = 8  # segundos — de propósito bem folgado
CAMINHO_CORTES = os.path.join("dados_cortes_flow_games", "cortes.json")

if not os.path.exists(CAMINHO_CORTES):
    raise RuntimeError(f"Não achei {CAMINHO_CORTES} — rode isso na raiz do projeto.")

with open(CAMINHO_CORTES, "r", encoding="utf-8") as f:
    cortes = json.load(f)

candidatos = [c["video_id"] for c in cortes if not c.get("tem_transcricao")]
video_ids = candidatos[:N_TESTE]

if not video_ids:
    raise RuntimeError("Não sobrou nenhum corte sem transcrição pra testar.")

print(f"Testando {len(video_ids)} vídeos, direto (sem proxy), 1 por vez, "
      f"com {PAUSA_ENTRE_TENTATIVAS}s de pausa entre cada...\n")

cliente = YouTubeTranscriptApi()
resultados = {"ok": 0, "bloqueado": 0, "outro_erro": 0}

for i, video_id in enumerate(video_ids):
    try:
        inicio = time.time()
        transcript = cliente.fetch(video_id, languages=["pt"])
        print(f"  [{i + 1}/{len(video_ids)}] {video_id} → OK "
              f"({len(transcript)} blocos, {time.time() - inicio:.1f}s)")
        resultados["ok"] += 1
    except Exception as e:
        tipo = type(e).__name__
        if tipo in ("IpBlocked", "RequestBlocked"):
            print(f"  [{i + 1}/{len(video_ids)}] {video_id} → BLOQUEADO ({tipo})")
            resultados["bloqueado"] += 1
        else:
            print(f"  [{i + 1}/{len(video_ids)}] {video_id} → outro erro ({tipo}: {str(e)[:100]})")
            resultados["outro_erro"] += 1

    if i < len(video_ids) - 1:
        time.sleep(PAUSA_ENTRE_TENTATIVAS)

print(f"\n{'=' * 50}")
print(f"OK: {resultados['ok']}  |  Bloqueado: {resultados['bloqueado']}  |  Outro erro: {resultados['outro_erro']}")
if resultados["ok"] > 0:
    print("[RESULTADO] Sua IP direta está liberada (pelo menos parcialmente) — "
          "vale rodar a coleta sem proxy, devagar e sem paralelismo por enquanto.")
elif resultados["bloqueado"] == len(video_ids):
    print("[RESULTADO] Ainda bloqueada. Ou espera mais (bloqueios costumam ser "
          "temporários) e testa de novo depois, ou parte pra outra estratégia "
          "(outro provedor de proxy, ou reduzir bem o escopo de vídeos necessários).")
print(f"{'=' * 50}")
