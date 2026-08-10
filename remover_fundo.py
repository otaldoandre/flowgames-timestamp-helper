"""
Remove o fundo de um frame do host (foto com fundo do estúdio),
deixando só a pessoa, com transparência — pronto pra entrar no Smart
Object do template sem precisar recortar manualmente.

Usa o modelo u2net_human_seg (especializado em pessoa, não objeto
genérico) via a lib rembg.

Dependência:
    pip install rembg pillow

Uso:
    python remover_fundo.py caminho/para/frame_host.jpg caminho/para/frame_host_sem_fundo.png
"""

import os
import shutil
import sys

from rembg import remove, new_session

MODELO = "u2net_human_seg"

_sessao = None


def _obter_sessao():
    global _sessao
    if _sessao is None:
        _sessao = new_session(MODELO)
    return _sessao


def remover_fundo(caminho_entrada, caminho_saida, manter_original=True):
    """
    Lê a imagem de entrada, remove o fundo (deixa transparente), salva
    como PNG. Reaproveita a mesma sessão/modelo entre chamadas — se for
    processar vários frames seguidos, isso evita recarregar o modelo
    a cada vez.

    Se manter_original=True (padrão), salva também uma cópia do frame
    original ao lado do resultado — se o recorte automático não sair
    bom, o frame cru já tá ali pra cortar manualmente, sem precisar
    voltar no vídeo.
    """
    sessao = _obter_sessao()

    with open(caminho_entrada, "rb") as f:
        dados_entrada = f.read()

    dados_saida = remove(dados_entrada, session=sessao)

    with open(caminho_saida, "wb") as f:
        f.write(dados_saida)

    if manter_original:
        pasta_saida = os.path.dirname(caminho_saida) or "."
        nome_base, ext_original = os.path.splitext(os.path.basename(caminho_entrada))
        caminho_original_copia = os.path.join(pasta_saida, f"{nome_base}_original{ext_original}")
        shutil.copy2(caminho_entrada, caminho_original_copia)
        return caminho_saida, caminho_original_copia

    return caminho_saida, None


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Uso: python remover_fundo.py caminho/entrada.jpg caminho/saida.png")
        sys.exit(1)

    caminho_entrada = sys.argv[1]
    caminho_saida = sys.argv[2]

    print(f"Removendo fundo (modelo {MODELO}, pode demorar um pouco na primeira vez)...")
    caminho_resultado, caminho_original = remover_fundo(caminho_entrada, caminho_saida)
    print(f"Sem fundo salvo em : {caminho_resultado}")
    print(f"Original salvo em  : {caminho_original}")
