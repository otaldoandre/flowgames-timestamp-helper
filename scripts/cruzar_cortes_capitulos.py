"""
Cruza cada corte (que já tem transcrição + episódio de origem
identificado) com os capítulos do episódio de origem, pra rotular quais
capítulos viraram corte e quais não — o dado de treino que faltava pra
fase 2.

Método: como o corte é literalmente um pedaço do mesmo áudio/transcrição
do episódio, acha os trechos de palavras em comum entre a transcrição do
corte e a do episódio (difflib.SequenceMatcher), estima o INTERVALO
(início e fim) que o corte ocupa dentro do episódio, e marca todos os
capítulos que esse intervalo atravessa — não só o de onde ele começa.

Cada capítulo rotulado carrega também o "programa" do episódio de origem
(igual ao index.json de coletar_dados_episodios.py), pra dar pra analisar
e treinar por programa depois.

Checagem de sanidade: o corte já tem sua PRÓPRIA duração real (pelos
timestamps da própria transcrição dele). Se o intervalo estimado dele
dentro do episódio for bem maior que essa duração real
(FATOR_MAX_EXPANSAO_INTERVALO), é sinal de que algum bloco de match
espúrio (frase comum repetida em outro trecho do episódio, mesmo dentro
da janela de coerência) inflou o intervalo — esse corte não é usado pra
rotular capítulo e vai pro arquivo de suspeitos, em vez de contaminar o
dataset com um falso "virou_corte: true".

Performance: o processamento é por episódio e cada episódio é 100%
independente dos outros, então roda em paralelo entre processos
(ProcessPoolExecutor) — é trabalho de CPU puro (difflib), então
paralelizar com threads não ajudaria (GIL). Isso só muda a VELOCIDADE,
não o resultado: a ordem final de saída é preservada e o método de
matching é exatamente o mesmo de antes.

Uso:
    python cruzar_cortes_capitulos.py
"""

import os
import re
import json
import difflib
import concurrent.futures
from collections import defaultdict

PASTA_CORTES    = "dados_cortes_flow_games"
PASTA_EPISODIOS = "dados_flow_games"
ARQUIVO_SAIDA   = os.path.join(PASTA_CORTES, "capitulos_rotulados.json")
ARQUIVO_SUSPEITOS = os.path.join(PASTA_CORTES, "cortes_intervalo_suspeito.json")

MIN_PALAVRAS_MATCH     = 8     # trecho mínimo em comum pra considerar válido
TAXA_MINIMA_CONFIANCA  = 0.25  # fração mínima das palavras do corte que precisa bater

PADRAO_LINHA_TRANSCRICAO = re.compile(r'^\[(\d{1,4}):(\d{2})\]\s*(.*)$')

# "Shorts" (título com #shorts...) não são o corte vertical que interessa
# pro dataset de treino, mesmo já tendo transcrição baixada de antes desse
# filtro existir em coletar_dados_cortes.py — exclui aqui também.
REGEX_HASHTAG_SHORTS = re.compile(r'#shorts', re.IGNORECASE)


def eh_short(titulo):
    return bool(REGEX_HASHTAG_SHORTS.search(titulo or ""))


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


def normalizar_palavra(p):
    p = p.lower()
    p = re.sub(r'[^\wà-úÀ-Ú]', '', p)
    return p


def palavras_com_tempo(blocos):
    """[(segundos, texto), ...] -> [(palavra, segundos_do_bloco), ...]"""
    resultado = []
    for segundos, texto in blocos:
        for palavra in texto.split():
            p = normalizar_palavra(palavra)
            if p:
                resultado.append((p, segundos))
    return resultado


def apenas_palavras(blocos):
    resultado = []
    for _, texto in blocos:
        for palavra in texto.split():
            p = normalizar_palavra(palavra)
            if p:
                resultado.append(p)
    return resultado


TAMANHO_MIN_BLOCO_RUIDO = 3  # blocos menores que isso quase sempre são coincidência de palavra comum (é, não, que...), não sinal real de match
JANELA_COERENCIA_SEGUNDOS = 600  # um bloco de match a mais de 10min da âncora provável é sinal de frase comum repetida em outro ponto do episódio, não parte do mesmo corte — não conta pra confiança nem pro fim estimado
FATOR_MAX_EXPANSAO_INTERVALO = 2.5  # o corte JÁ tem sua própria duração real (pelos timestamps da própria transcrição dele) — se o intervalo estimado dentro do episódio for muito maior que isso, é sinal de que blocos de match espúrios (frase comum repetida em outro trecho do episódio, mesmo dentro da janela de coerência) inflaram o intervalo. Acima desse fator, o corte não conta como match confiável.


def encontrar_intervalo(palavras_episodio_com_tempo, apenas_ep, palavras_corte):
    """
    Corte e episódio são o MESMO áudio, mas cada um foi transcrito
    SEPARADAMENTE pelo YouTube — pequenas divergências pontuais (uma
    palavra ouvida diferente, vinheta/música no início do corte, uma
    pausa cortada) são esperadas e quebram um único "maior trecho
    contínuo" em vários pedaços menores, mesmo quando a maior parte do
    texto realmente bate. Por isso, em vez de usar só find_longest_match
    (um trecho só), olha TODOS os trechos que baterem via
    get_matching_blocks.

    Dois cuidados a mais em cima disso (uma frase comum tipo "e aí quando
    a gente começou a jogar" pode se repetir em outro ponto do episódio e
    enganar um match ingênuo):
      1. Só blocos numa vizinhança coerente da âncora (JANELA_COERENCIA_SEGUNDOS)
         contam pra confiança e pro fim estimado — um bloco isolado longe
         dali é descartado como coincidência, não empilhado.
      2. Retorna o INTERVALO inteiro (início e fim) que o corte ocupa no
         episódio, não só onde ele começa — importante pra detectar corte
         que atravessa mais de um capítulo.

    `apenas_ep` é só as palavras de palavras_episodio_com_tempo (sem o
    tempo), pré-calculado UMA VEZ por episódio (não por corte) por quem
    chama — é sempre a mesma lista pra todos os cortes de um mesmo
    episódio, então recalcular a cada chamada seria desperdício puro.

    Retorna (inicio_segundos, fim_segundos, confianca, tamanho_maior_bloco).
    inicio/fim vêm None se não achar nada confiável o suficiente (mas
    confianca sempre vem preenchida, pra dar pra diagnosticar depois).
    """
    sm = difflib.SequenceMatcher(None, apenas_ep, palavras_corte, autojunk=False)
    blocos = [b for b in sm.get_matching_blocks() if b.size > 0]

    if not blocos:
        return None, None, 0.0, 0

    maior_bloco = max(blocos, key=lambda b: b.size)

    if maior_bloco.size < MIN_PALAVRAS_MATCH:
        return None, None, 0.0, maior_bloco.size

    ancora = palavras_episodio_com_tempo[maior_bloco.a][1]

    blocos_coerentes = [
        b for b in blocos
        if b.size >= TAMANHO_MIN_BLOCO_RUIDO
        and abs(palavras_episodio_com_tempo[b.a][1] - ancora) <= JANELA_COERENCIA_SEGUNDOS
    ]
    total_match = sum(b.size for b in blocos_coerentes)
    confianca = total_match / max(len(palavras_corte), 1)

    if confianca < TAXA_MINIMA_CONFIANCA:
        return None, None, confianca, maior_bloco.size

    inicio_segundos = min(palavras_episodio_com_tempo[b.a][1] for b in blocos_coerentes)
    fim_segundos = max(
        palavras_episodio_com_tempo[min(b.a + b.size - 1, len(palavras_episodio_com_tempo) - 1)][1]
        for b in blocos_coerentes
    )

    return inicio_segundos, fim_segundos, confianca, maior_bloco.size


def achar_capitulos_no_intervalo(capitulos_ordenados, inicio_segundos, fim_segundos):
    """
    capitulos_ordenados já ordenado por 'segundos' crescente. Retorna
    TODOS os capítulos cujo intervalo [início, próximo capítulo) se
    sobrepõe a [inicio_segundos, fim_segundos] do corte — cobre o caso
    do corte atravessar mais de um capítulo, em vez de só marcar o
    capítulo de onde ele começa.
    """
    resultado = []
    for i, cap in enumerate(capitulos_ordenados):
        cap_inicio = cap["segundos"]
        cap_fim = capitulos_ordenados[i + 1]["segundos"] if i + 1 < len(capitulos_ordenados) else float("inf")
        if cap_inicio <= fim_segundos and cap_fim >= inicio_segundos:
            resultado.append(cap)
    return resultado


def processar_episodio(episodio_id, cortes_do_episodio, info_episodio):
    """
    Faz todo o trabalho de UM episódio: carrega a transcrição/capítulos
    dele uma vez só, cruza com cada um dos cortes que vieram dele, e
    devolve os capítulos rotulados + as listas de cortes sem match /
    sem dados de episódio / com intervalo suspeito.

    É uma função de topo (não um closure) de propósito: precisa ser
    "picklable" pra rodar em outro processo via ProcessPoolExecutor.
    Cada episódio é independente dos outros, então isso paraleliza sem
    mudar nenhum resultado — só a ordem em que os episódios terminam de
    processar, e essa ordem não afeta a saída final (main() remonta tudo
    na ordem original antes de salvar).

    Retorna (capitulos_rotulados_do_episodio, cortes_sem_match,
    cortes_sem_dados_episodio, cortes_intervalo_suspeito).
    """
    caminho_transcricao_ep = os.path.join(PASTA_EPISODIOS, "transcricoes", f"{episodio_id}.txt")
    caminho_capitulos_ep = os.path.join(PASTA_EPISODIOS, "capitulos", f"{episodio_id}.json")

    if not info_episodio or not info_episodio.get("tem_transcricao") or not os.path.exists(caminho_capitulos_ep):
        return [], [], [c["video_id"] for c in cortes_do_episodio], []

    blocos_episodio = carregar_transcricao(caminho_transcricao_ep)
    palavras_episodio = palavras_com_tempo(blocos_episodio)
    # Calculado 1x por episódio (não 1x por corte, como antes) — é sempre
    # a mesma lista de palavras do episódio pra qualquer corte que vier dele.
    apenas_ep = [p for p, _ in palavras_episodio]
    capitulos = sorted(carregar_json(caminho_capitulos_ep), key=lambda c: c["segundos"])
    programa = info_episodio.get("programa")

    # Todos os capítulos desse episódio começam marcados como "não virou corte"
    mapa_capitulos = {
        cap["segundos"]: {
            "episodio_id": episodio_id,
            "programa": programa,
            "capitulo_titulo": cap["titulo"],
            "capitulo_inicio_segundos": cap["segundos"],
            "virou_corte": False,
            "cortes_relacionados": []
        }
        for cap in capitulos
    }

    cortes_sem_match_ep = []
    cortes_intervalo_suspeito_ep = []

    for corte in cortes_do_episodio:
        caminho_transcricao_corte = os.path.join(PASTA_CORTES, "transcricoes", f"{corte['video_id']}.txt")
        blocos_corte = carregar_transcricao(caminho_transcricao_corte)
        palavras_corte = apenas_palavras(blocos_corte)

        if not palavras_corte:
            cortes_sem_match_ep.append(corte["video_id"])
            continue

        inicio_segundos, fim_segundos, confianca, tamanho_maior_bloco = encontrar_intervalo(
            palavras_episodio, apenas_ep, palavras_corte
        )

        if inicio_segundos is None:
            cortes_sem_match_ep.append(corte["video_id"])
            continue

        # Checagem de sanidade: o corte já tem sua própria duração real,
        # pelos timestamps da própria transcrição dele — não precisa
        # confiar cegamente no intervalo estimado dentro do episódio. Se
        # esse intervalo for muito maior que a duração real do corte, é
        # sinal de que algum bloco de match espúrio (frase comum repetida
        # em outro trecho do episódio) inflou o intervalo, e não dá pra
        # confiar nesse corte pra rotular capítulo.
        duracao_real_corte = (blocos_corte[-1][0] - blocos_corte[0][0]) if len(blocos_corte) >= 2 else None
        duracao_estimada_episodio = fim_segundos - inicio_segundos

        if (
            duracao_real_corte is not None
            and duracao_real_corte > 0
            and duracao_estimada_episodio > duracao_real_corte * FATOR_MAX_EXPANSAO_INTERVALO
        ):
            cortes_intervalo_suspeito_ep.append({
                "video_id": corte["video_id"],
                "duracao_real_corte_segundos": duracao_real_corte,
                "intervalo_estimado_segundos": duracao_estimada_episodio,
            })
            continue

        capitulos_atingidos = achar_capitulos_no_intervalo(capitulos, inicio_segundos, fim_segundos)
        if not capitulos_atingidos:
            cortes_sem_match_ep.append(corte["video_id"])
            continue

        for capitulo in capitulos_atingidos:
            entrada = mapa_capitulos[capitulo["segundos"]]
            entrada["virou_corte"] = True
            entrada["cortes_relacionados"].append({
                "video_id": corte["video_id"],
                "titulo_corte": corte["titulo"],
                "confianca_match": round(confianca, 3),
                "inicio_episodio_segundos": inicio_segundos,
                "fim_episodio_segundos": fim_segundos,
                "tamanho_maior_bloco": tamanho_maior_bloco,
                "duracao_real_corte_segundos": duracao_real_corte,
            })

    return list(mapa_capitulos.values()), cortes_sem_match_ep, [], cortes_intervalo_suspeito_ep


def main():
    caminho_cortes = os.path.join(PASTA_CORTES, "cortes.json")
    caminho_index_episodios = os.path.join(PASTA_EPISODIOS, "index.json")

    cortes = carregar_json(caminho_cortes)
    episodios_index = {ep["video_id"]: ep for ep in carregar_json(caminho_index_episodios)}

    cortes_utilizaveis = [
        c for c in cortes
        if c.get("tem_transcricao") and c.get("episodio_origem_id") and not eh_short(c.get("titulo"))
    ]
    n_shorts_excluidos = sum(
        1 for c in cortes
        if c.get("tem_transcricao") and c.get("episodio_origem_id") and eh_short(c.get("titulo"))
    )
    if n_shorts_excluidos:
        print(f"{n_shorts_excluidos} #shorts excluídos (não contam como corte pro dataset)")

    # Agrupa por episódio de origem — carrega a transcrição do episódio
    # só uma vez, mesmo que vários cortes venham dele
    cortes_por_episodio = {}
    for c in cortes_utilizaveis:
        cortes_por_episodio.setdefault(c["episodio_origem_id"], []).append(c)

    print(f"{len(cortes_utilizaveis)} cortes utilizáveis, de {len(cortes_por_episodio)} episódios diferentes\n")

    tarefas = [
        (episodio_id, cortes_do_episodio, episodios_index.get(episodio_id))
        for episodio_id, cortes_do_episodio in cortes_por_episodio.items()
    ]

    capitulos_rotulados = []
    cortes_sem_match = []
    cortes_sem_dados_episodio = []
    cortes_intervalo_suspeito = []

    if tarefas:
        n_processos = min(len(tarefas), os.cpu_count() or 1)
        print(f"Processando {len(tarefas)} episódios em paralelo ({n_processos} processos)...\n")

        concluidos = 0
        with concurrent.futures.ProcessPoolExecutor(max_workers=n_processos) as executor:
            # executor.map preserva a ordem de 'tarefas' nos resultados,
            # mesmo que os episódios terminem de processar fora de ordem
            # — a saída final fica idêntica à versão sequencial.
            episodio_ids, listas_cortes, infos = zip(*tarefas)
            resultados = executor.map(processar_episodio, episodio_ids, listas_cortes, infos)

            for capitulos_do_ep, sem_match_do_ep, sem_dados_do_ep, suspeito_do_ep in resultados:
                capitulos_rotulados.extend(capitulos_do_ep)
                cortes_sem_match.extend(sem_match_do_ep)
                cortes_sem_dados_episodio.extend(sem_dados_do_ep)
                cortes_intervalo_suspeito.extend(suspeito_do_ep)

                concluidos += 1
                if concluidos % 10 == 0 or concluidos == len(tarefas):
                    print(f"  ... {concluidos}/{len(tarefas)} episódios processados")

    with open(ARQUIVO_SAIDA, "w", encoding="utf-8") as f:
        json.dump(capitulos_rotulados, f, ensure_ascii=False, indent=2)

    with open(ARQUIVO_SUSPEITOS, "w", encoding="utf-8") as f:
        json.dump(cortes_intervalo_suspeito, f, ensure_ascii=False, indent=2)

    virou_corte = sum(1 for c in capitulos_rotulados if c["virou_corte"])

    print(f"\n{'=' * 50}")
    print("CRUZAMENTO FINALIZADO")
    print(f"  Capítulos rotulados          : {len(capitulos_rotulados)}")
    print(f"  Capítulos que viraram corte   : {virou_corte}")
    print(f"  Capítulos que não viraram     : {len(capitulos_rotulados) - virou_corte}")
    print(f"  Cortes sem match confiável    : {len(cortes_sem_match)}")
    print(f"  Cortes c/ intervalo suspeito  : {len(cortes_intervalo_suspeito)}  (span estimado >> duração real do corte — não contou como match)")
    print(f"  Cortes sem dados do episódio  : {len(cortes_sem_dados_episodio)}")
    print(f"  Arquivo salvo                 : {ARQUIVO_SAIDA}")
    print(f"  Suspeitos salvos em           : {ARQUIVO_SUSPEITOS}")

    por_programa = defaultdict(lambda: {"total": 0, "viraram_corte": 0})
    for cap in capitulos_rotulados:
        p = cap.get("programa") or "(desconhecido)"
        por_programa[p]["total"] += 1
        if cap["virou_corte"]:
            por_programa[p]["viraram_corte"] += 1

    print("\nCapítulos rotulados por programa:")
    for programa, stats in sorted(por_programa.items(), key=lambda item: -item[1]["total"]):
        print(f"  {programa:<22} {stats['total']:>4} total   ({stats['viraram_corte']} viraram corte)")

    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
