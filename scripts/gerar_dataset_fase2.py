"""
Gera o dataset REAL da fase 2 (fase2_treino.json / fase2_teste.json) a
partir de capitulos_rotulados.json — substitui o placeholder sintético
de 10 entradas que existia até agora.

gerar_metadata_capitulo.py espera cada entrada com um campo "texto": o
texto real da transcrição do capítulo, não só o título. capitulos_rotulados.json
não guarda isso (só o início do capítulo) — esse script busca, pra cada
capítulo, o intervalo [início do capítulo, início do PRÓXIMO capítulo do
mesmo episódio) na transcrição do episódio, e concatena o texto ali
dentro. Isso reconstrói exatamente o mesmo recorte que cruzar_cortes_capitulos.py
usa internamente pra decidir os limites de cada capítulo, sem precisar
reabrir os arquivos de capítulos por episódio de novo.

Split treino/teste é por EPISÓDIO, não por capítulo solto — capítulos do
mesmo episódio compartilham tom/piadas internas/contexto, então deixar
capítulos do mesmo episódio caírem um no treino e outro no teste infla
artificialmente o quão bem o few-shot parece funcionar (o "teste" não
seria realmente inédito). O split é estratificado por programa, pra
garantir que programas menores (GAMEPLAY, EVENTOS) também apareçam nos
dois splits quando tiverem episódios suficientes pra isso.

Uso:
    python gerar_dataset_fase2.py
"""

import os
import re
import json
import random

PASTA_CORTES      = "dados_cortes_flow_games"
PASTA_EPISODIOS   = "dados_flow_games"
ARQUIVO_ROTULADOS = os.path.join(PASTA_CORTES, "capitulos_rotulados.json")
ARQUIVO_TREINO    = os.path.join(PASTA_CORTES, "fase2_treino.json")
ARQUIVO_TESTE     = os.path.join(PASTA_CORTES, "fase2_teste.json")

FRACAO_TESTE = 0.15  # ~15% dos EPISÓDIOS de cada programa vão pro teste (não 15% dos capítulos)
SEED_SPLIT   = 42    # fixo, pra dataset ser reproduzível entre execuções

PADRAO_LINHA_TRANSCRICAO = re.compile(r'^\[(\d{1,4}):(\d{2})\]\s*(.*)$')


def carregar_json(caminho):
    with open(caminho, "r", encoding="utf-8") as f:
        return json.load(f)


def carregar_transcricao(caminho):
    """Lê um arquivo [MM:SS] texto e retorna lista de (segundos, texto)."""
    blocos = []
    if not os.path.exists(caminho):
        return blocos
    with open(caminho, "r", encoding="utf-8") as f:
        for linha in f:
            m = PADRAO_LINHA_TRANSCRICAO.match(linha.rstrip("\n"))
            if m:
                minutos, segundos, texto = m.groups()
                total_segundos = int(minutos) * 60 + int(segundos)
                blocos.append((total_segundos, texto))
    return blocos


def extrair_texto_capitulo(blocos_episodio, inicio_segundos, fim_segundos):
    """Concatena o texto de todas as linhas de transcrição dentro de [inicio, fim)."""
    trechos = [
        texto for segundos, texto in blocos_episodio
        if inicio_segundos <= segundos < fim_segundos
    ]
    return " ".join(t for t in trechos if t).strip()


def montar_dataset(capitulos_rotulados):
    capitulos_por_episodio = {}
    for cap in capitulos_rotulados:
        capitulos_por_episodio.setdefault(cap["episodio_id"], []).append(cap)

    dataset = []
    capitulos_sem_texto = 0

    for episodio_id, capitulos_do_episodio in capitulos_por_episodio.items():
        caminho_transcricao = os.path.join(PASTA_EPISODIOS, "transcricoes", f"{episodio_id}.txt")
        blocos_episodio = carregar_transcricao(caminho_transcricao)

        # Reordena pelos MESMOS critérios que cruzar_cortes_capitulos.py usou
        # (capitulos_rotulados.json já tem TODOS os capítulos desse episódio,
        # não só os que viraram corte — então reconstrói a sequência inteira).
        capitulos_ordenados = sorted(capitulos_do_episodio, key=lambda c: c["capitulo_inicio_segundos"])

        for i, cap in enumerate(capitulos_ordenados):
            inicio = cap["capitulo_inicio_segundos"]
            fim = (
                capitulos_ordenados[i + 1]["capitulo_inicio_segundos"]
                if i + 1 < len(capitulos_ordenados)
                else float("inf")
            )

            texto = extrair_texto_capitulo(blocos_episodio, inicio, fim)
            if not texto:
                capitulos_sem_texto += 1
                continue

            dataset.append({
                "episodio_id": episodio_id,
                "programa": cap.get("programa"),
                "capitulo_titulo": cap["capitulo_titulo"],
                "capitulo_inicio_segundos": inicio,
                "capitulo_fim_segundos": None if fim == float("inf") else fim,
                "texto": texto,
                "virou_corte": cap["virou_corte"],
                "cortes_relacionados": [
                    {"titulo_corte": c["titulo_corte"]} for c in cap.get("cortes_relacionados", [])
                ],
            })

    if capitulos_sem_texto:
        print(
            f"[AVISO] {capitulos_sem_texto} capítulos ficaram sem texto "
            f"(nenhuma linha de transcrição caiu no intervalo) — excluídos do dataset."
        )

    return dataset


def dividir_treino_teste(dataset):
    episodios_por_programa = {}
    for cap in dataset:
        episodios_por_programa.setdefault(cap.get("programa"), set()).add(cap["episodio_id"])

    rng = random.Random(SEED_SPLIT)
    episodios_teste = set()

    for programa, episodios in episodios_por_programa.items():
        episodios_lista = sorted(episodios)  # ordem determinística antes de embaralhar
        rng.shuffle(episodios_lista)
        n_teste = max(1, round(len(episodios_lista) * FRACAO_TESTE)) if len(episodios_lista) >= 2 else 0
        episodios_teste.update(episodios_lista[:n_teste])

    treino = [c for c in dataset if c["episodio_id"] not in episodios_teste]
    teste = [c for c in dataset if c["episodio_id"] in episodios_teste]
    return treino, teste, episodios_por_programa


def main():
    capitulos_rotulados = carregar_json(ARQUIVO_ROTULADOS)
    dataset = montar_dataset(capitulos_rotulados)
    treino, teste, episodios_por_programa = dividir_treino_teste(dataset)

    with open(ARQUIVO_TREINO, "w", encoding="utf-8") as f:
        json.dump(treino, f, ensure_ascii=False, indent=2)
    with open(ARQUIVO_TESTE, "w", encoding="utf-8") as f:
        json.dump(teste, f, ensure_ascii=False, indent=2)

    def resumo(nome, lista):
        pos = sum(1 for c in lista if c["virou_corte"])
        eps = len(set(c["episodio_id"] for c in lista))
        print(f"  {nome:<8}: {len(lista):>5} capítulos ({pos} viraram corte), de {eps} episódios")

    print(f"\n{'=' * 60}")
    print("DATASET FASE 2 GERADO")
    resumo("Treino", treino)
    resumo("Teste", teste)
    print(f"  Arquivo treino: {ARQUIVO_TREINO}")
    print(f"  Arquivo teste : {ARQUIVO_TESTE}")

    print("\nPor programa (treino / teste):")
    programas = sorted(episodios_por_programa.keys(), key=lambda p: -len(episodios_por_programa[p]))
    for programa in programas:
        treino_p = [c for c in treino if c.get("programa") == programa]
        teste_p = [c for c in teste if c.get("programa") == programa]
        print(
            f"  {str(programa):<22} treino: {len(treino_p):>4} ({sum(1 for c in treino_p if c['virou_corte'])} pos)"
            f"   teste: {len(teste_p):>4} ({sum(1 for c in teste_p if c['virou_corte'])} pos)"
        )
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
