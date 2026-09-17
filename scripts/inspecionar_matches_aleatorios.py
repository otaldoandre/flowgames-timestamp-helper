"""
Amostra aleatória de capítulos rotulados como "virou_corte: true" pra
conferência manual.

O salto de ~121 pra ~694 capítulos marcados como "virou corte" (depois do
fix que soma todos os blocos de match em vez de só o maior) é bem grande
pra confiar só no número. A checagem de duração (intervalo vs. duração
real do corte) já filtra os casos mais óbvios de intervalo inflado, mas
não garante que o CONTEÚDO bate — só que o TAMANHO bate. Esse script só
te dá uma amostra pra olhar na mão: o título do corte listado realmente
tem a ver com o título do capítulo apontado?

Uso:
    python inspecionar_matches_aleatorios.py [N]
    (N = quantos capítulos amostrar, default 15)
"""

import os
import sys
import json
import random

PASTA_CORTES = "dados_cortes_flow_games"
ARQUIVO_ROTULADOS = os.path.join(PASTA_CORTES, "capitulos_rotulados.json")

N = int(sys.argv[1]) if len(sys.argv) > 1 else 15

if not os.path.exists(ARQUIVO_ROTULADOS):
    raise RuntimeError(f"Não achei {ARQUIVO_ROTULADOS} — rode cruzar_cortes_capitulos.py primeiro.")

with open(ARQUIVO_ROTULADOS, "r", encoding="utf-8") as f:
    capitulos = json.load(f)

positivos = [c for c in capitulos if c["virou_corte"]]
if not positivos:
    raise RuntimeError("Nenhum capítulo marcado como virou_corte — nada pra conferir.")

amostra = random.sample(positivos, min(N, len(positivos)))

for i, cap in enumerate(amostra, 1):
    print(f"\n{'-' * 70}")
    print(f"[{i}/{len(amostra)}] Programa: {cap.get('programa')}  |  Episódio: {cap['episodio_id']}")
    print(f"  Capítulo: \"{cap['capitulo_titulo']}\"  (início {cap['capitulo_inicio_segundos']}s)")
    for corte in cap["cortes_relacionados"]:
        print(
            f"    -> Corte: \"{corte['titulo_corte']}\"  "
            f"(confiança {corte['confianca_match']}, "
            f"intervalo no episódio {corte['inicio_episodio_segundos']}-{corte['fim_episodio_segundos']}s, "
            f"duração real do corte {corte.get('duracao_real_corte_segundos')}s, "
            f"video_id {corte['video_id']})"
        )

print(f"\n{'=' * 70}")
print(f"{len(amostra)} capítulos amostrados de {len(positivos)} marcados como virou_corte.")
print("Pra cada um: o título do corte listado faz sentido pro título do capítulo apontado?")
print("Se a maioria bater (mesmo que o título do corte seja mais \"chamativo\"/editado,")
print("o assunto deve ser o mesmo do capítulo), o dataset tá confiável.")
print(f"{'=' * 70}")
