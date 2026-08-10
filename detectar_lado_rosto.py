"""
Detecta de que lado do frame o rosto do host está, pra escolher a
variação certa do template de thumbnail (host precisa "olhar" pra
dentro da imagem, não pra fora). Não depende de saber de antemão onde
cada host senta — como a bancada varia, decide a partir do frame
extraído de cada corte, na hora.

Usa detecção de rosto Haar Cascade (builtin do OpenCV, roda em CPU,
sem precisar treinar nada) — não é a parte pesada de visão
computacional, é uma tarefa simples e madura.

Dependência:
    pip install opencv-python

Uso:
    python detectar_lado_rosto.py caminho/para/frame.jpg
"""

import sys

import cv2

CASCADE_ROSTO = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"


def detectar_lado_rosto(caminho_imagem):
    """
    Retorna 'esquerda', 'direita', ou None (se não achar rosto nenhum
    — nesse caso precisa de escolha manual, não force um lado).

    'esquerda'/'direita' é onde o CENTRO do rosto cai na imagem.
    """
    detector = cv2.CascadeClassifier(CASCADE_ROSTO)
    imagem = cv2.imread(caminho_imagem)
    if imagem is None:
        return None

    cinza = cv2.cvtColor(imagem, cv2.COLOR_BGR2GRAY)
    rostos = detector.detectMultiScale(cinza, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60))

    if len(rostos) == 0:
        return None

    # Se achar mais de um rosto (dois hosts no frame), usa o maior —
    # provavelmente é quem tá mais em foco/perto da câmera
    maior_rosto = max(rostos, key=lambda r: r[2] * r[3])  # r = (x, y, w, h)
    x, y, w, h = maior_rosto
    centro_x = x + w / 2

    largura_imagem = imagem.shape[1]
    return "esquerda" if centro_x < largura_imagem / 2 else "direita"


def escolher_variacao_template(lado_do_rosto):
    """
    Host na esquerda -> variação com a imagem/thumb na DIREITA (host
    "olha" pra dentro). Host na direita -> imagem na ESQUERDA.
    """
    if lado_do_rosto == "esquerda":
        return "imagem_na_direita"
    if lado_do_rosto == "direita":
        return "imagem_na_esquerda"
    return None  # não detectou nada -- precisa de fallback/revisão manual


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python detectar_lado_rosto.py caminho/para/frame.jpg")
        sys.exit(1)

    caminho = sys.argv[1]
    lado = detectar_lado_rosto(caminho)

    if lado is None:
        print("Não detectei rosto nenhum nesse frame — escolhe a variação manualmente.")
    else:
        variacao = escolher_variacao_template(lado)
        print(f"Rosto detectado do lado: {lado}")
        print(f"Variação de template a usar: {variacao}")
