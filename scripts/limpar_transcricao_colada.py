"""
Limpa uma transcrição copiada NA MÃO do painel de transcrição do
YouTube pro formato "[MM:SS] texto" que o resto do pipeline
(detectar_capitulos.py, cruzar_cortes_capitulos.py, etc.) espera.

O problema: quando você seleciona e copia o texto direto da caixa de
transcrição do YouTube, cada linha vem colada assim:

    55:4955 minutos e 49 segundosencontrar em Dislcan. que você pode...

Isso é o timestamp visível ("55:49") + o texto de acessibilidade que
soletra o mesmo tempo por extenso ("55 minutos e 49 segundos", pra
leitor de tela) + a legenda de verdade de fato — tudo colado sem
espaço, porque o YouTube monta isso com elementos escondidos no HTML
que o navegador ainda inclui quando você copia a seleção. Como nenhuma
linha começa com "[", o resto do pipeline não reconhece nada — é por
isso que "gerar capítulos automaticamente" dava "nenhum capítulo
encontrado" nesse tipo de arquivo.

Esse script identifica o timestamp no início de cada linha, remove o
trecho por extenso (ele sempre termina em "segundo(s)" — a menor
unidade, sempre falada por último — então tudo depois disso é a
legenda de verdade), e reescreve no formato "[MM:SS] texto".

Uso:
    python limpar_transcricao_colada.py caminho/para/colado.txt
    (gera caminho/para/colado_limpo.txt, no formato certo pro app)
"""

import re
import sys
import os

PADRAO_TIMESTAMP_INICIO = re.compile(r'^(?:(\d{1,2}):)?(\d{1,3}):(\d{2})')
PADRAO_SEGUNDOS_POR_EXTENSO = re.compile(r'\d{1,2}\s*segundos?')


def limpar_linha(linha):
    """
    Extrai (segundos_totais, texto) de uma linha colada do YouTube.
    Retorna None se a linha não começar com um timestamp reconhecível
    (linha em branco, cabeçalho, etc.).
    """
    m_timestamp = PADRAO_TIMESTAMP_INICIO.match(linha)
    if not m_timestamp:
        return None

    horas, minutos, segundos = m_timestamp.groups()
    total_segundos = int(minutos) * 60 + int(segundos)
    if horas:
        total_segundos += int(horas) * 3600

    resto = linha[m_timestamp.end():]

    # O texto por extenso sempre termina em "segundo(s)" — o que vier
    # DEPOIS disso é a legenda de verdade. Se não achar (arquivo já sem
    # esse texto duplicado, ou colado de outro jeito), usa o resto da
    # linha direto, sem cortar nada.
    m_extenso = PADRAO_SEGUNDOS_POR_EXTENSO.search(resto)
    texto = resto[m_extenso.end():] if m_extenso else resto

    return total_segundos, texto.strip()


def limpar_arquivo(caminho_entrada, caminho_saida):
    linhas_limpas = []
    linhas_ignoradas = 0

    with open(caminho_entrada, "r", encoding="utf-8") as f:
        for linha in f:
            linha = linha.rstrip("\n")
            if not linha.strip():
                continue
            resultado = limpar_linha(linha)
            if resultado is None:
                linhas_ignoradas += 1
                continue
            segundos, texto = resultado
            if not texto:
                continue
            minutos_totais, seg = divmod(segundos, 60)
            linhas_limpas.append(f"[{minutos_totais}:{seg:02d}] {texto}")

    with open(caminho_saida, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas_limpas) + "\n")

    return len(linhas_limpas), linhas_ignoradas


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python limpar_transcricao_colada.py caminho/para/colado.txt")
        sys.exit(1)

    caminho_entrada = sys.argv[1]
    if not os.path.exists(caminho_entrada):
        print(f"Não achei o arquivo: {caminho_entrada}")
        sys.exit(1)

    base, ext = os.path.splitext(caminho_entrada)
    caminho_saida = f"{base}_limpo{ext or '.txt'}"

    n_ok, n_ignoradas = limpar_arquivo(caminho_entrada, caminho_saida)

    print(f"{n_ok} linhas convertidas pro formato [MM:SS] texto.")
    if n_ignoradas:
        print(
            f"[AVISO] {n_ignoradas} linha(s) não reconhecida(s) e ignorada(s) "
            f"— confere se não é conteúdo de verdade que ficou de fora."
        )
    print(f"Salvo em: {caminho_saida}")
    print("\nAgora é só selecionar ESSE arquivo (o \"_limpo\") no app, em vez do original colado.")
