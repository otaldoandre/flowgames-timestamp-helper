"""
Avalia o pipeline de fase 2 (perfil por programa + few-shot, em
gerar_metadata_capitulo.py) contra o fase2_teste.json — o conjunto que
NUNCA foi usado nem como exemplo few-shot, nem pra escrever os perfis
de PERFIS_PROGRAMA. Mede se a "probabilidade_corte" que o Gemini estima
realmente separa capítulo que virou corte de capítulo que não virou.

Isso é o que falta pra saber se vale a pena continuar refinando o texto
dos perfis por programa — sem essa baseline, qualquer ajuste de redação
é tiro no escuro.

Cada capítulo avaliado é uma chamada real à API do Gemini (custa cota).
Por isso dá pra rodar num pedaço pequeno primeiro:
    python avaliar_fase2.py --limite 20

Resumível: salva cada avaliação em avaliacao_fase2_teste.json conforme
processa, e PULA quem já foi avaliado numa rodada anterior — interromper
no meio (Ctrl+C, queda de rede, rate limit) não perde nada nem gasta
cota de novo com o mesmo capítulo. Rodar de novo sem --limite completa
o que faltar.

Uso:
    python avaliar_fase2.py              # avalia todo mundo que ainda falta
    python avaliar_fase2.py --limite 20  # só os 20 primeiros (teste barato)
"""

import os
import sys
import json
import time
import argparse

from dotenv import load_dotenv

load_dotenv()  # GEMINI_API_KEY vem do .env na raiz do projeto

# gerar_metadata_capitulo.py mora na raiz do projeto, um nível acima de scripts/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gerar_metadata_capitulo import (
    carregar_json,
    selecionar_exemplos_few_shot,
    avaliar_capitulo,
)

PASTA_CORTES       = "dados_cortes_flow_games"
ARQUIVO_TREINO     = os.path.join(PASTA_CORTES, "fase2_treino.json")
ARQUIVO_TESTE      = os.path.join(PASTA_CORTES, "fase2_teste.json")
ARQUIVO_RESULTADO  = os.path.join(PASTA_CORTES, "avaliacao_fase2_teste.json")

LIMIAR_POSITIVO       = 50  # probabilidade_corte >= isso conta como "modelo disse que vira corte"
PAUSA_ENTRE_CHAMADAS  = 4.0  # segundos — folga pra não estourar limite por minuto da API (1s ainda deu rate limit no teste com 20)


def chave(capitulo):
    return f"{capitulo['episodio_id']}::{capitulo['capitulo_inicio_segundos']}"


def calcular_auc(pares):
    """
    AUC (área sob a curva ROC), calculada via estatística de Mann-Whitney
    — sem precisar de scikit-learn: fração de pares (positivo, negativo)
    em que o score do positivo é maior que o do negativo (empate conta
    0.5). 0.5 = não separa nada (aleatório/chute), 1.0 = separa perfeito.
    """
    positivos = [s for s, y in pares if y]
    negativos = [s for s, y in pares if not y]
    if not positivos or not negativos:
        return None

    total = 0.0
    for sp in positivos:
        for sn in negativos:
            if sp > sn:
                total += 1
            elif sp == sn:
                total += 0.5
    return total / (len(positivos) * len(negativos))


def calcular_metricas(pares):
    total = len(pares)
    if total == 0:
        return None

    vp = sum(1 for s, y in pares if y and s >= LIMIAR_POSITIVO)
    fn = sum(1 for s, y in pares if y and s < LIMIAR_POSITIVO)
    fp = sum(1 for s, y in pares if not y and s >= LIMIAR_POSITIVO)
    vn = sum(1 for s, y in pares if not y and s < LIMIAR_POSITIVO)

    acuracia = (vp + vn) / total
    precisao = vp / (vp + fp) if (vp + fp) else 0.0
    recall = vp / (vp + fn) if (vp + fn) else 0.0
    f1 = 2 * precisao * recall / (precisao + recall) if (precisao + recall) else 0.0

    n_pos = sum(1 for _, y in pares if y)
    n_neg = total - n_pos
    media_pos = sum(s for s, y in pares if y) / n_pos if n_pos else None
    media_neg = sum(s for s, y in pares if not y) / n_neg if n_neg else None

    return {
        "n": total,
        "auc": calcular_auc(pares),
        "acuracia": acuracia,
        "precisao": precisao,
        "recall": recall,
        "f1": f1,
        "media_prob_positivos": media_pos,
        "media_prob_negativos": media_neg,
    }


def imprimir_metricas(nome, metricas):
    if metricas is None:
        print(f"  {nome}: sem dado suficiente pra calcular métrica")
        return
    auc_str = f"{metricas['auc']:.3f}" if metricas["auc"] is not None else "N/A (só uma classe presente)"
    print(f"  {nome} (n={metricas['n']})")
    print(f"    AUC (separa positivo de negativo)     : {auc_str}")
    print(f"    Acurácia (limiar {LIMIAR_POSITIVO})                    : {metricas['acuracia']:.1%}")
    print(f"    Precisão / Recall / F1                : {metricas['precisao']:.1%} / {metricas['recall']:.1%} / {metricas['f1']:.1%}")
    if metricas["media_prob_positivos"] is not None:
        print(f"    Probabilidade média — virou corte      : {metricas['media_prob_positivos']:.1f}")
    if metricas["media_prob_negativos"] is not None:
        print(f"    Probabilidade média — não virou corte  : {metricas['media_prob_negativos']:.1f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--limite", type=int, default=None,
        help="Avalia só os N primeiros capítulos do teste (pra gastar pouca cota testando antes de rodar tudo)"
    )
    args = parser.parse_args()

    treino = carregar_json(ARQUIVO_TREINO)
    teste = carregar_json(ARQUIVO_TESTE)

    if args.limite:
        teste = teste[:args.limite]

    resultados = {}
    if os.path.exists(ARQUIVO_RESULTADO):
        with open(ARQUIVO_RESULTADO, "r", encoding="utf-8") as f:
            resultados = json.load(f)
        print(f"  → {len(resultados)} capítulos já avaliados numa rodada anterior (pulando esses)")

    faltam = [c for c in teste if chave(c) not in resultados]
    print(f"{len(teste)} capítulos no teste (desse conjunto), {len(faltam)} ainda faltam avaliar\n")

    falhas = 0
    for i, capitulo in enumerate(faltam):
        exemplos = selecionar_exemplos_few_shot(treino, programa=capitulo.get("programa"))
        avaliacao = avaliar_capitulo(capitulo, exemplos)

        if avaliacao is None or avaliacao.get("probabilidade_corte") is None:
            falhas += 1
            print(f"  [{i + 1}/{len(faltam)}] FALHOU ao avaliar ({capitulo['capitulo_titulo'][:50]!r})")
        else:
            resultados[chave(capitulo)] = {
                "episodio_id": capitulo["episodio_id"],
                "programa": capitulo.get("programa"),
                "capitulo_titulo": capitulo["capitulo_titulo"],
                "virou_corte_real": capitulo["virou_corte"],
                "probabilidade_corte": avaliacao["probabilidade_corte"],
            }

            # Salva a CADA capítulo — interromper no meio não perde nem
            # gasta cota de novo com quem já foi avaliado.
            with open(ARQUIVO_RESULTADO, "w", encoding="utf-8") as f:
                json.dump(resultados, f, ensure_ascii=False, indent=2)

        if (i + 1) % 10 == 0 or (i + 1) == len(faltam):
            print(f"  ... {i + 1}/{len(faltam)} processados")

        if i < len(faltam) - 1:
            time.sleep(PAUSA_ENTRE_CHAMADAS)

    if falhas:
        print(f"\n[AVISO] {falhas} capítulos falharam na avaliação (ver mensagens acima) — não entram nas métricas.")

    # --- métricas ---
    avaliados = list(resultados.values())
    if not avaliados:
        print("\nNenhum resultado pra calcular métrica ainda.")
        return

    pares_gerais = [(r["probabilidade_corte"], r["virou_corte_real"]) for r in avaliados]

    print(f"\n{'=' * 60}")
    print(f"MÉTRICAS — AVALIAÇÃO FASE 2")
    imprimir_metricas("GERAL", calcular_metricas(pares_gerais))

    print("\nPor programa:")
    programas = sorted(set(r.get("programa") for r in avaliados), key=lambda p: str(p))
    for programa in programas:
        pares_prog = [
            (r["probabilidade_corte"], r["virou_corte_real"])
            for r in avaliados if r.get("programa") == programa
        ]
        imprimir_metricas(str(programa), calcular_metricas(pares_prog))
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
