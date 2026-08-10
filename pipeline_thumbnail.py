"""
Pipeline completo da fase 4: recebe um frame bruto do host + a imagem
sugerida + os dois textos, e gera a thumbnail final — remove o fundo
do host, detecta de que lado ele parece estar (pra escolher o layout),
e chama a automação do Photoshop.

ATENÇÃO: o "lado" detectado é baseado em ONDE O ROSTO ESTÁ POSICIONADO
no frame, não pra que lado ele está OLHANDO — pode não ser a mesma
coisa dependendo de como o frame é enquadrado. Teste em 2-3 frames
reais (um que devia dar "esquerda", outro "direita") antes de confiar
cegamente — se não bater, dá pra passar LADO manualmente e ignorar a
detecção automática.

Dependência:
    pip install rembg pillow opencv-python pywin32

Uso:
    python pipeline_thumbnail.py
    (edite as constantes no topo)
"""

import os
import re
import unicodedata

from remover_fundo import remover_fundo
from detectar_lado_rosto import detectar_lado_rosto
from gerar_thumbnail import gerar_thumbnail

from detectar_lado_rosto import detectar_lado_rosto

CAMINHO_TEMPLATE = r"C:\Users\andre\Downloads\projetos\flowgames-timestamp-helper\scripts\thumb_corte_template.psd"
CAMINHO_FRAME_HOST_BRUTO = r"C:\Users\andre\Downloads\projetos\flowgames-timestamp-helper\scripts\davy-host.png"
CAMINHO_IMAGEM_SUGERIDA = r"C:\Users\andre\Downloads\projetos\flowgames-timestamp-helper\scripts\kratos.jpg"
TEXTO_LINHA1 = "VAMOS TRAZER"
TEXTO_LINHA2 = "O KRATOS AQUI"
PASTA_SAIDA = r"C:\Users\andre\Downloads\projetos\flowgames-timestamp-helper\scripts"
# Identifica esse corte especificamente — usado pra nomear os arquivos
# de saída (thumbnail, .psd, etc), pra não sobrescrever o corte
# anterior. Use o título do corte, o video_id, ou qualquer coisa única
# por corte.
NOME_CORTE = "1-O_NOVO_CONSOLE_anunciado"

# Usado só se a detecção automática não achar rosto nenhum
LADO_PADRAO = "direita"

# Se quiser pular a detecção automática e forçar um lado, define aqui
# (ex: "esquerda") — None = deixa a detecção decidir
LADO_FORCADO = None


def sanitizar_nome_arquivo(texto):
    """Remove acento/espaço/caractere inválido, pra virar nome de arquivo seguro."""
    texto = unicodedata.normalize("NFD", texto)
    texto = texto.encode("ascii", "ignore").decode("utf-8")
    texto = texto.replace(" ", "_")
    texto = re.sub(r"[^a-zA-Z0-9_-]", "", texto)
    return texto or "corte"


def montar_thumbnail_completa(
    caminho_template, caminho_frame_host_bruto, caminho_imagem_sugerida,
    linha1, linha2, pasta_saida, nome_corte, lado_forcado=None, lado_padrao=LADO_PADRAO,
):
    os.makedirs(pasta_saida, exist_ok=True)
    nome_base = sanitizar_nome_arquivo(nome_corte)

    print("[1/3] Removendo fundo do host...")
    caminho_host_sem_fundo = os.path.join(pasta_saida, f"{nome_base}_sem_fundo.png")
    caminho_sem_fundo, caminho_original = remover_fundo(caminho_frame_host_bruto, caminho_host_sem_fundo)

    if lado_forcado:
        lado = lado_forcado
        print(f"[2/3] Lado forçado manualmente: {lado}")
    else:
        print("[2/3] Detectando de que lado o rosto está posicionado...")
        lado_detectado = detectar_lado_rosto(caminho_frame_host_bruto)
        if lado_detectado is None:
            print(f"  Não detectei rosto — usando o lado padrão ({lado_padrao})")
            lado = lado_padrao
        else:
            print(f"  Detectado: {lado_detectado} (confirme se bate com a direção real do olhar)")
            lado = lado_detectado

    caminho_saida_png = os.path.join(pasta_saida, f"{nome_base}_thumbnail.png")

    print(f"[3/3] Gerando thumbnail (layout '{lado}')...")
    resultado = gerar_thumbnail(
        caminho_template,
        caminho_imagem_sugerida,
        caminho_sem_fundo,
        linha1,
        linha2,
        caminho_saida_png,
        deslocamento_texto_x=0,
        lado=lado,
    )

    if resultado != "OK":
        print(f"\n[ERRO] {resultado}")
        return None

    print(f"\nThumbnail pronta        : {caminho_saida_png}")
    print(f"Host sem fundo           : {caminho_sem_fundo}")
    print(f"Frame original (backup)  : {caminho_original}")
    return caminho_saida_png


if __name__ == "__main__":
    montar_thumbnail_completa(
        CAMINHO_TEMPLATE,
        CAMINHO_FRAME_HOST_BRUTO,
        CAMINHO_IMAGEM_SUGERIDA,
        TEXTO_LINHA1,
        TEXTO_LINHA2,
        PASTA_SAIDA,
        NOME_CORTE,
        lado_forcado=LADO_FORCADO,
        lado_padrao=LADO_PADRAO,
    )