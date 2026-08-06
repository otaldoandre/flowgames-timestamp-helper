"""
Fase 1 extraída do notebook: detecta os capítulos (fronteiras de
assunto) de um episódio NOVO, ainda sem capítulos manuais — usa
embeddings + similaridade de cosseno entre janelas deslizantes da
transcrição (mesmo método testado e validado no trabalho da
faculdade — o método de clusterização testado lá não performou melhor,
por isso esse script usa só o de cosseno).

Dependências:
    pip install sentence-transformers scikit-learn

Uso:
    python detectar_capitulos.py caminho/para/transcricao.txt

Se o LIMIAR (linha abaixo) que você achou no trabalho da faculdade foi
diferente de 0.30, ajusta aqui antes de rodar.
"""

import re
import sys

MODELO_EMBEDDING = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

TAMANHO_JANELA    = 60    # segundos por chunk
SOBREPOSICAO      = 20    # segundos de sobreposição entre chunks
LIMIAR            = 0.70  # confirmado por reavaliação no dataset grande (platô 0.70-0.85, MAE mediano ~2.3min)
DISTANCIA_MINIMA  = 180   # segundos mínimos entre duas fronteiras (evita over-segmentação)

PADRAO_LINHA_TRANSCRICAO = re.compile(r'^\[(\d{1,4}):(\d{2})\]\s*(.*)$')

ABREVIACOES = {
    r"\btô\b": "estou", r"\btá\b": "está", r"\bvc\b": "você",
    r"\bpra\b": "para", r"\bcê\b": "você", r"\bné\b": "não é",
    r"\bpro\b": "para o",
}


def carregar_transcricao(caminho):
    """Mesmo formato [MM:SS] texto usado nos outros scripts do pipeline."""
    blocos = []
    with open(caminho, "r", encoding="utf-8") as f:
        for linha in f:
            m = PADRAO_LINHA_TRANSCRICAO.match(linha.rstrip("\n"))
            if m:
                minutos, segundos, texto = m.groups()
                blocos.append({"seconds": int(minutos) * 60 + int(segundos), "text": texto})
    return blocos


def remover_texto_nao_latino(texto):
    return re.sub(r'[^\x00-\x7FáàâãéèêíïóôõöúçñÁÀÂÃÉÈÊÍÏÓÔÕÖÚÇÑ\s.,!?]', '', texto)


def substituir_abreviacoes(texto):
    for abreviacao, termo in ABREVIACOES.items():
        texto = re.sub(abreviacao, termo, texto)
    return texto


def normalizar_texto(texto):
    """Mesma limpeza do notebook: remove ruído, minúsculas, abreviações."""
    if not texto:
        return ""
    texto = re.sub(r'\[risadas\]|\[ __ \]|>>', '', texto)
    texto = texto.lower()
    texto = remover_texto_nao_latino(texto)
    texto = substituir_abreviacoes(texto)
    texto = re.sub(r'\s+', ' ', texto).strip()
    return texto


def criar_chunks(blocos, tamanho_janela=TAMANHO_JANELA, sobreposicao=SOBREPOSICAO):
    """Janelamento por tempo (sliding window with overlap), igual ao notebook."""
    if not blocos:
        return []

    chunks = []
    passo = tamanho_janela - sobreposicao
    max_tempo = max(b["seconds"] for b in blocos)
    inicio_primeiro = blocos[0]["seconds"]

    for inicio in range(inicio_primeiro, max_tempo, passo):
        fim = inicio + tamanho_janela
        bloco_atual = [b for b in blocos if inicio <= b["seconds"] < fim]
        if bloco_atual:
            texto_agrupado = " ".join(b["text"] for b in bloco_atual)
            chunks.append({"start_time": inicio, "end_time": fim, "text": texto_agrupado})

    return chunks


def filtrar_fronteiras_proximas(fronteiras, distancia_minima=DISTANCIA_MINIMA):
    """Remove fronteiras muito próximas, mantendo a primeira de cada grupo."""
    if not fronteiras:
        return []
    filtradas = [fronteiras[0]]
    for f in fronteiras[1:]:
        if f - filtradas[-1] >= distancia_minima:
            filtradas.append(f)
    return filtradas


def preparar_similaridades(caminho_transcricao, tamanho_janela=TAMANHO_JANELA,
                            sobreposicao=SOBREPOSICAO, modelo=None):
    """
    Parte cara do pipeline: carrega, limpa, faz chunking e calcula
    embeddings + similaridade de cosseno entre chunks consecutivos.
    Separado de detectar_capitulos() pra poder testar vários LIMIAR
    sem recalcular embeddings a cada vez (útil pra grid search).

    Retorna (chunks, similaridades) — similaridades[i] é a similaridade
    entre chunks[i] e chunks[i+1]. Retorna ([], []) se não der pra
    processar (transcrição vazia ou curta demais).
    """
    from sentence_transformers import SentenceTransformer
    from sklearn.metrics.pairwise import cosine_similarity

    blocos = carregar_transcricao(caminho_transcricao)
    if not blocos:
        return [], []

    for b in blocos:
        b["text"] = normalizar_texto(b["text"])
    blocos = [b for b in blocos if b["text"]]

    if not blocos:
        return [], []

    chunks = criar_chunks(blocos, tamanho_janela, sobreposicao)
    if len(chunks) < 2:
        return chunks, []

    if modelo is None:
        modelo = SentenceTransformer(MODELO_EMBEDDING)

    embeddings = modelo.encode([c["text"] for c in chunks], show_progress_bar=False)

    similaridades = [
        cosine_similarity([embeddings[i]], [embeddings[i + 1]])[0][0]
        for i in range(len(embeddings) - 1)
    ]

    return chunks, similaridades


def fronteiras_de_similaridades(chunks, similaridades, limiar=LIMIAR, distancia_minima=DISTANCIA_MINIMA):
    """Parte barata: aplica um limiar já calculado sobre similaridades pré-computadas."""
    if not chunks:
        return []

    fronteiras = [chunks[0]["start_time"]]
    for i, sim in enumerate(similaridades):
        if sim < limiar:
            fronteiras.append(chunks[i + 1]["start_time"])

    return filtrar_fronteiras_proximas(fronteiras, distancia_minima)


def detectar_capitulos(caminho_transcricao, limiar=LIMIAR, tamanho_janela=TAMANHO_JANELA,
                        sobreposicao=SOBREPOSICAO, distancia_minima=DISTANCIA_MINIMA,
                        modelo=None):
    """
    Pipeline completo: transcrição crua -> lista de fronteiras de
    capítulo (timestamps em segundos), usando similaridade de cosseno
    entre chunks consecutivos.

    Passa `modelo` (um SentenceTransformer já carregado) se for chamar
    isso várias vezes seguidas — carregar o modelo é a parte lenta,
    não precisa recarregar a cada episódio. Se for testar vários
    LIMIAR pro mesmo episódio, usa preparar_similaridades() +
    fronteiras_de_similaridades() direto, em vez desta função (evita
    recalcular embeddings à toa).
    """
    chunks, similaridades = preparar_similaridades(caminho_transcricao, tamanho_janela, sobreposicao, modelo)
    if not chunks:
        return []
    if not similaridades:
        return [chunks[0]["start_time"]]

    return fronteiras_de_similaridades(chunks, similaridades, limiar, distancia_minima)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python detectar_capitulos.py caminho/para/transcricao.txt")
        sys.exit(1)

    caminho = sys.argv[1]
    print("Carregando modelo de embeddings (pode demorar na primeira vez)...")
    fronteiras = detectar_capitulos(caminho)

    print(f"\n{len(fronteiras)} capítulos detectados:\n")
    for seg in fronteiras:
        h, resto = divmod(seg, 3600)
        m, s = divmod(resto, 60)
        print(f"  {h:02d}:{m:02d}:{s:02d}")