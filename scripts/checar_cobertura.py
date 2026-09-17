"""
Confere, entre os cortes já coletados com transcrição, se o episódio de
origem de cada um também já tem transcrição coletada — antes de partir
pro script de matching (corte x episódio).

Uso:
    python checar_cobertura.py
"""

import json
import os

PASTA_CORTES    = "dados_cortes_flow_games"
PASTA_EPISODIOS = "dados_flow_games"


def carregar_json(caminho):
    with open(caminho, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    caminho_cortes = os.path.join(PASTA_CORTES, "cortes.json")
    caminho_index_episodios = os.path.join(PASTA_EPISODIOS, "index.json")

    if not os.path.exists(caminho_cortes):
        print(f"[ERRO] Não achei {caminho_cortes}")
        return
    if not os.path.exists(caminho_index_episodios):
        print(f"[ERRO] Não achei {caminho_index_episodios}")
        return

    cortes = carregar_json(caminho_cortes)
    episodios = carregar_json(caminho_index_episodios)

    episodios_com_transcricao = {
        ep["video_id"] for ep in episodios if ep.get("tem_transcricao")
    }

    cortes_utilizaveis = [
        c for c in cortes
        if c.get("tem_transcricao") and c.get("episodio_origem_id")
    ]

    origens_necessarias = {c["episodio_origem_id"] for c in cortes_utilizaveis}
    origens_cobertas = origens_necessarias & episodios_com_transcricao
    origens_faltando = origens_necessarias - episodios_com_transcricao

    print(f"Cortes com transcrição + origem identificada : {len(cortes_utilizaveis)}")
    print(f"Episódios de origem distintos necessários     : {len(origens_necessarias)}")
    print(f"  → já com transcrição coletada               : {len(origens_cobertas)}")
    print(f"  → faltando coletar                          : {len(origens_faltando)}")

    if origens_faltando:
        caminho_lista = os.path.join(PASTA_CORTES, "episodios_faltando.json")
        with open(caminho_lista, "w", encoding="utf-8") as f:
            json.dump(sorted(origens_faltando), f, ensure_ascii=False, indent=2)
        print(f"\n[INFO] Lista de video_ids faltando salva em: {caminho_lista}")
        print("       (dá pra rodar o coletar_dados_episodios.py só com MAX_VIDEOS")
        print("        ajustado, ou conferir manualmente se vale a pena)")

        cortes_afetados = sum(
            1 for c in cortes_utilizaveis if c["episodio_origem_id"] in origens_faltando
        )
        print(f"\n[INFO] {cortes_afetados} cortes ficam sem poder fazer matching até isso ser resolvido.")
    else:
        print("\n[OK] Todos os episódios de origem necessários já têm transcrição. Pode seguir pro matching.")


if __name__ == "__main__":
    main()
