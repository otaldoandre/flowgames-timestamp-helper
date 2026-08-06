"""
Núcleo da fase 2: dado o texto de um capítulo, decide se ele "vale
cortar" e, se sim, gera título + quote de destaque + descrição +
sugestão de imagem — usando exemplos REAIS (capítulos que viraram ou
não viraram corte) como few-shot, em vez de julgamento genérico do LLM.

Uso como módulo (importado por avaliar_fase2.py) ou standalone pra
testar em um único capítulo:
    python gerar_metadata_capitulo.py
"""

import os
import re
import json
import random
import time

from google import genai

GEMINI_API_KEY = "AIzaSyAZqwyPaye4VZH9pZvDmQi5Rokywo6endQ"
MODEL_NAME     = "gemini-3.1-flash-lite"  # mesmo modelo usado no notebook

N_EXEMPLOS_POSITIVOS = 4
N_EXEMPLOS_NEGATIVOS = 4
SEED_EXEMPLOS = 42

MAX_TENTATIVAS = 3


def carregar_json(caminho):
    with open(caminho, "r", encoding="utf-8") as f:
        return json.load(f)


def selecionar_exemplos_few_shot(treino, n_positivos=N_EXEMPLOS_POSITIVOS, n_negativos=N_EXEMPLOS_NEGATIVOS, seed=SEED_EXEMPLOS):
    """
    Escolhe uma amostra fixa (mesma seed sempre) de capítulos que
    viraram corte e que não viraram, pra usar como few-shot. Fixo pra
    poder comparar resultados entre execuções sem o exemplo mudar.
    """
    positivos = [c for c in treino if c["virou_corte"]]
    negativos = [c for c in treino if not c["virou_corte"]]

    rng = random.Random(seed)
    exemplos_pos = rng.sample(positivos, min(n_positivos, len(positivos)))
    exemplos_neg = rng.sample(negativos, min(n_negativos, len(negativos)))

    exemplos = exemplos_pos + exemplos_neg
    rng.shuffle(exemplos)
    return exemplos


def montar_prompt(exemplos, texto_capitulo):
    blocos_exemplo = []
    for i, ex in enumerate(exemplos):
        if ex["virou_corte"]:
            titulo_real = ex["cortes_relacionados"][0]["titulo_corte"] if ex["cortes_relacionados"] else "?"
            blocos_exemplo.append(
                f"[Exemplo {i + 1} — VIROU CORTE]\n"
                f"Texto: {ex['texto'][:1500]}\n"
                f"Título real do corte: {titulo_real}\n"
            )
        else:
            blocos_exemplo.append(
                f"[Exemplo {i + 1} — NÃO VIROU CORTE]\n"
                f"Texto: {ex['texto'][:1500]}\n"
            )

    exemplos_texto = "\n".join(blocos_exemplo)

    return f"""Você é um editor de cortes do canal Flow Games. Sua tarefa é estimar
a PROBABILIDADE de um trecho de podcast virar um corte (clipe) publicado
no canal "Cortes do Flow Games", e gerar um rascunho de título, uma fala
de destaque do host, uma descrição curta, um texto curto pra thumbnail
e uma sugestão de imagem pra thumbnail — mesmo que a probabilidade seja
baixa, o rascunho ajuda o editor a decidir.

Sobre o texto_thumbnail: é DIFERENTE da quote_destaque. A quote_destaque
pode ser uma frase inteira; o texto_thumbnail precisa ser bem mais
curto — só cabe 3-5 palavras por linha, em 2 linhas, no template real
do canal. Use palavras REAIS ditas pelo host nesse trecho (pode cortar
uma frase maior, não precisa ser gramaticalmente completo), nunca
invente.

Não existe resposta "certa" binária aqui — vários fatores fora do
conteúdo em si (tempo de edição disponível no dia, se um assunto
parecido já foi coberto, execução de título/thumbnail) influenciam se
um trecho vira corte ou não. Por isso queremos uma estimativa de
probabilidade, não um sim/não — dê a nota que reflete o quanto esse
trecho parece com os exemplos que viraram corte, mesmo sabendo que
nem todo trecho bom historicamente virou corte.

Abaixo estão exemplos REAIS de capítulos que viraram corte e capítulos
que não viraram, com o texto de cada um. Use isso como referência do
que a equipe historicamente considerou "cortável" — não invente um
critério genérico de "viral", baseie-se no padrão desses exemplos.

--- EXEMPLOS ---

{exemplos_texto}
--- FIM DOS EXEMPLOS ---

Agora avalie o capítulo abaixo. Responda SOMENTE em JSON puro (sem
markdown, sem ```), no formato exato:
{{
  "probabilidade_corte": número inteiro de 0 a 100 (sua estimativa de quão provável é esse capítulo virar um corte publicado, baseado no padrão dos exemplos acima),
  "titulo": "string (sempre preencha, mesmo se a probabilidade for baixa — é um rascunho pro editor avaliar)",
  "quote_destaque": "frase curta do texto que resume o gancho",
  "texto_thumbnail": {{
    "linha1": "3-5 palavras REAIS do texto, ditas pelo host",
    "linha2": "3-5 palavras REAIS do texto, continuando a linha1"
  }},
  "descricao": "1-2 linhas",
  "sugestao_imagem": "descrição do que mostrar na thumbnail"
}}

Texto do capítulo a avaliar:
{texto_capitulo[:3000]}
"""


def chamar_gemini(prompt, api_key=GEMINI_API_KEY, model_name=MODEL_NAME, max_tentativas=MAX_TENTATIVAS):
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY não encontrada. Defina a variável de ambiente antes de rodar.")

    client = genai.Client(api_key=api_key)

    for tentativa in range(max_tentativas):
        try:
            response = client.models.generate_content(model=model_name, contents=prompt)
            texto_resposta = response.text.strip()

            if "```json" in texto_resposta:
                texto_resposta = texto_resposta.split("```json")[1].split("```")[0].strip()
            elif "```" in texto_resposta:
                texto_resposta = texto_resposta.split("```")[1].split("```")[0].strip()

            return json.loads(texto_resposta)

        except json.JSONDecodeError as e:
            print(f"  [AVISO] Resposta não veio em JSON válido (tentativa {tentativa + 1}/{max_tentativas}): {e}")
            time.sleep(1)
        except Exception as e:
            erro_str = str(e)
            if "429" in erro_str or "RESOURCE_EXHAUSTED" in erro_str:
                espera = 2 ** tentativa
                print(f"  [AVISO] Rate limit, esperando {espera}s...")
                time.sleep(espera)
            else:
                print(f"  [ERRO] {erro_str[:200]}")
                time.sleep(1)

    return None  # esgotou tentativas


def normalizar_para_comparacao(texto):
    texto = texto.lower()
    texto = re.sub(r'[^\wà-úÀ-Ú\s]', '', texto)
    texto = re.sub(r'\s+', ' ', texto).strip()
    return texto


def fracao_sequencial(palavras_quote, palavras_texto):
    """
    Fração das palavras da quote que aparecem no texto respeitando a
    ORDEM (maior subsequência comum) — pode pular palavras no meio
    (tipo um "né" ou "tipo" removido), mas não aceita palavras certas
    numa ordem embaralhada/combinada de partes aleatórias do texto.
    Isso é o que realmente distingue "quote real, só limpa" de "quote
    com as palavras certas mas inventada".
    """
    n, m = len(palavras_quote), len(palavras_texto)
    if n == 0:
        return 0.0

    anterior = [0] * (m + 1)
    for i in range(1, n + 1):
        atual = [0] * (m + 1)
        for j in range(1, m + 1):
            if palavras_quote[i - 1] == palavras_texto[j - 1]:
                atual[j] = anterior[j - 1] + 1
            else:
                atual[j] = max(anterior[j], atual[j - 1])
        anterior = atual

    return anterior[m] / n


def verificar_quote(quote, texto_capitulo):
    """
    Confere se a quote_destaque realmente aparece (ou é bem próxima) do
    texto original do capítulo — o Gemini pode parafrasear, embaralhar
    palavras de partes diferentes do texto, ou no pior caso gerar uma
    frase que soa real mas não foi dita.

    Retorna (encontrada_exata, fracao_sequencial):
    - encontrada_exata: a string normalizada da quote aparece literal
      dentro do texto normalizado
    - fracao_sequencial: fração das palavras que batem respeitando a
      ORDEM (subsequência comum mais longa) — não é só "a palavra
      existe em algum lugar do texto", tem que estar na sequência certa
    """
    if not quote:
        return False, 0.0

    quote_norm = normalizar_para_comparacao(quote)
    texto_norm = normalizar_para_comparacao(texto_capitulo)

    encontrada_exata = quote_norm in texto_norm

    palavras_quote = quote_norm.split()
    palavras_texto = texto_norm.split()
    if not palavras_quote:
        return encontrada_exata, 0.0

    fracao = fracao_sequencial(palavras_quote, palavras_texto)

    return encontrada_exata, fracao


def normalizar_probabilidade(valor):
    """Aceita int, float ou string tipo '70%' e devolve int entre 0 e 100, ou None."""
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return max(0, min(100, round(valor)))
    if isinstance(valor, str):
        limpo = re.sub(r'[^\d.]', '', valor)
        if not limpo:
            return None
        return max(0, min(100, round(float(limpo))))
    return None


def avaliar_capitulo(capitulo, exemplos, api_key=GEMINI_API_KEY):
    """Recebe um capítulo (dict com 'texto') e retorna a avaliação do Gemini, ou None se falhar."""
    prompt = montar_prompt(exemplos, capitulo["texto"])
    resultado = chamar_gemini(prompt, api_key=api_key)

    if resultado is None:
        return None

    resultado["probabilidade_corte"] = normalizar_probabilidade(resultado.get("probabilidade_corte"))

    if resultado.get("quote_destaque"):
        exata, fracao = verificar_quote(resultado["quote_destaque"], capitulo["texto"])
        resultado["quote_encontrada_exata"] = exata
        resultado["quote_fracao_sequencial"] = round(fracao, 2)
        if not exata and fracao < 0.7:
            print(f"  [AVISO] Quote não parece real (só {fracao:.0%} das palavras batem em ordem no texto original)")

    texto_thumb = resultado.get("texto_thumbnail")
    if isinstance(texto_thumb, dict) and (texto_thumb.get("linha1") or texto_thumb.get("linha2")):
        texto_completo = f"{texto_thumb.get('linha1', '')} {texto_thumb.get('linha2', '')}".strip()
        exata_thumb, fracao_thumb = verificar_quote(texto_completo, capitulo["texto"])
        resultado["texto_thumbnail_fracao_sequencial"] = round(fracao_thumb, 2)
        if not exata_thumb and fracao_thumb < 0.7:
            print(f"  [AVISO] Texto de thumbnail não parece real (só {fracao_thumb:.0%} das palavras batem em ordem)")

    return resultado


if __name__ == "__main__":
    # Teste rápido em UM capítulo de treino, só pra ver o formato da resposta
    treino = carregar_json(os.path.join("scripts/dados_cortes_flow_games", "fase2_treino.json"))
    exemplos = selecionar_exemplos_few_shot(treino)

    # Pega um capítulo qualquer que NÃO esteja nos exemplos, só pra teste
    ids_exemplos = {(e["episodio_id"], e["capitulo_inicio_segundos"]) for e in exemplos}
    candidatos = [c for c in treino if (c["episodio_id"], c["capitulo_inicio_segundos"]) not in ids_exemplos]
    alvo = candidatos[0]

    print(f"Testando com capítulo: {alvo['capitulo_titulo']}")
    print(f"Rótulo real: {'virou corte' if alvo['virou_corte'] else 'não virou corte'}\n")

    resultado = avaliar_capitulo(alvo, exemplos)
    print(json.dumps(resultado, ensure_ascii=False, indent=2))