"""
Coleta transcrições e capítulos de TODOS os vídeos de várias playlists
(uma por programa) no YouTube — versão com descrição buscada em lote,
retry com backoff, suporte a retomar uma coleta interrompida, e tag de
programa em cada episódio coletado.

Dependências:
    pip install youtube_transcript_api google-api-python-client python-dotenv

Uso:
    1. Coloque YOUTUBE_API_KEY no .env do projeto (mesmo arquivo que já
       tem GEMINI_API_KEY etc.) — não precisa mais setar variável de
       ambiente do sistema toda sessão.
    2. Ajuste PASTA_SAIDA pro MESMO caminho que você já usou antes, se
       já tiver coletado alguns episódios — é isso que faz o script
       pular quem já foi feito em vez de rebaixar tudo de novo.
    3. Rode: python coletar_dados_episodios.py
       (roda TODAS as playlists de PLAYLISTS, uma atrás da outra, salvando
       tudo no mesmo index.json com o campo "programa" preenchido)
"""

import os
import re
import json
import time
import threading
import concurrent.futures
from dotenv import load_dotenv
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import WebshareProxyConfig

load_dotenv()

# ============================================================
# CONFIGURAÇÕES — edite aqui
# ============================================================

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")

WEBSHARE_PROXY_USERNAME = os.environ.get("WEBSHARE_PROXY_USERNAME")
WEBSHARE_PROXY_PASSWORD = os.environ.get("WEBSHARE_PROXY_PASSWORD")

# Um programa por entrada — rode listar_playlists_canal.py pra achar os
# IDs certos. NÃO inclua MD3/MD3 News aqui (programa encerrado, público
# diferente).
PLAYLISTS = {
    "FLOW GAMES NEWS":     "PLkKsdR0a6X5QkINeX7GnNFMJRTI9GaW79",
    "flow_games":          "PLkKsdR0a6X5Qz26LZ20WcukTWbNUVXAVN",
    "EVENTOS":             "PLkKsdR0a6X5S-IL8yAE7S0kVnr5wYz4mM",
    "GAMEPLAY":            "PLkKsdR0a6X5RwD0LW0tSmK1r4OExRCrdE",
    "RESET":               "PLkKsdR0a6X5R0LIBlaUzi02QHJrooCg9b",
    "RANKING FLOW GAMES":  "PLkKsdR0a6X5R8lREa-wOFrLKeeRopeJoN",
    "TOP AO FLOP":         "PLkKsdR0a6X5TssBuQ_329qd1mEZ31PsoR",
    "FLOW GAMES AWARDS":   "PLkKsdR0a6X5TlYOX2Ys9KqHhcr-DadaVB",
}

MAX_VIDEOS  = None                 # quantos episódios coletar por playlist (None = todos)
PASTA_SAIDA = "dados_flow_games"   # use o MESMO caminho da coleta anterior

# Limites pra não estourar RPM/cota
PAUSA_ENTRE_PAGINAS      = 0.5   # segundos, entre páginas de playlistItems
PAUSA_ENTRE_LOTES        = 1.0   # segundos, entre lotes de videos().list
TAMANHO_LOTE              = 50   # máximo permitido pela API por chamada
MAX_TENTATIVAS            = 5    # retries em erro temporário da API

# Downloads de transcrição em paralelo. Teste confirmou (coletar_dados_cortes.py):
# sequencial e direto (sem proxy) NÃO leva bloqueio; 5 workers simultâneos
# direto levaram bloqueio na primeira tentativa — suspeita forte é a
# RAJADA de requisições simultâneas da mesma IP, não o volume total. Por
# isso um valor baixo aqui, priorizando não gastar banda do proxy.
MAX_WORKERS_TRANSCRICAO = 3


# ============================================================
# 1. Utilitários
# ============================================================

def criar_pasta(pasta):
    os.makedirs(pasta, exist_ok=True)
    print(f"[OK] Pasta de saída: '{pasta}/'")


def timestamp_para_segundos(ts):
    """Converte timestamp HH:MM:SS ou MM:SS para segundos inteiros."""
    partes = list(map(int, ts.split(":")))
    if len(partes) == 3:
        return partes[0] * 3600 + partes[1] * 60 + partes[2]
    return partes[0] * 60 + partes[1]


def chamar_com_retry(request, max_tentativas=MAX_TENTATIVAS):
    """
    Executa uma chamada da API com backoff exponencial em erro temporário.
    quotaExceeded (cota DIÁRIA) não adianta tentar de novo, já para com
    uma mensagem clara.
    """
    for tentativa in range(max_tentativas):
        try:
            return request.execute()
        except HttpError as e:
            motivo = ""
            try:
                motivo = e.error_details[0].get("reason", "") if e.error_details else ""
            except Exception:
                pass

            if motivo == "quotaExceeded":
                print(
                    "[ERRO] Cota DIÁRIA da API excedida (10.000 unidades). "
                    "Reseta à meia-noite horário do Pacífico."
                )
                raise

            espera = 2 ** tentativa
            print(
                f"  [AVISO] Erro temporário ({getattr(e.resp, 'status', '?')} "
                f"{motivo or e}), tentativa {tentativa + 1}/{max_tentativas}. "
                f"Esperando {espera}s..."
            )
            time.sleep(espera)

    raise RuntimeError(f"Excedeu {max_tentativas} tentativas sem sucesso.")


# ============================================================
# 2. Busca IDs e títulos da playlist (leve, paginado)
# ============================================================

def buscar_videos_da_playlist(youtube, playlist_id, max_videos=None):
    """Retorna lista de {'video_id': str, 'titulo': str}."""
    videos = []
    token = None

    while True:
        resposta = chamar_com_retry(
            youtube.playlistItems().list(
                part="snippet",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=token
            )
        )

        for item in resposta["items"]:
            video_id = item["snippet"]["resourceId"]["videoId"]
            titulo   = item["snippet"]["title"]
            videos.append({"video_id": video_id, "titulo": titulo})

            if max_videos and len(videos) >= max_videos:
                return videos

        token = resposta.get("nextPageToken")
        if not token:
            break

        time.sleep(PAUSA_ENTRE_PAGINAS)

    return videos


# ============================================================
# 3. Busca descrição de TODOS os vídeos em lotes (pra extrair capítulos)
# ============================================================

def buscar_descricoes_em_lotes(youtube, video_ids, tamanho_lote=TAMANHO_LOTE):
    """
    Antes: 1 chamada de videos().list por vídeo, só pra pegar a
    descrição. Agora: até 50 IDs por chamada.
    """
    descricoes = {}
    total_lotes = (len(video_ids) - 1) // tamanho_lote + 1 if video_ids else 0

    for i in range(0, len(video_ids), tamanho_lote):
        lote = video_ids[i:i + tamanho_lote]
        print(f"  → Lote {i // tamanho_lote + 1}/{total_lotes} ({len(lote)} vídeos)")

        resposta = chamar_com_retry(
            youtube.videos().list(part="snippet", id=",".join(lote))
        )

        for item in resposta["items"]:
            descricoes[item["id"]] = item["snippet"]["description"]

        time.sleep(PAUSA_ENTRE_LOTES)

    return descricoes


def extrair_capitulos_da_descricao(descricao):
    """
    Extrai capítulos no formato HH:MM:SS - Título a partir da descrição
    do YouTube. Suporta timestamps com ou sem link markdown.
    """
    capitulos = []
    pattern = r'\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?(?:\([^)]*\))?\s*[-–]\s*(.+)'

    idx = descricao.find("Capítulos")
    if idx == -1:
        idx = 0

    trecho = descricao[idx:]

    for match in re.finditer(pattern, trecho):
        ts     = match.group(1).strip()
        titulo = match.group(2).strip()
        titulo = re.sub(r'https?://\S+', '', titulo).strip()

        if titulo:
            capitulos.append({
                "timestamp": ts,
                "segundos":  timestamp_para_segundos(ts),
                "titulo":    titulo
            })

    return capitulos


# ============================================================
# 4. Baixa transcrição (um vídeo por vez — a lib não tem lote)
# ============================================================

_CLIENTE_DIRETO = YouTubeTranscriptApi()
_CLIENTE_PROXY = None

if WEBSHARE_PROXY_USERNAME and WEBSHARE_PROXY_PASSWORD:
    _CLIENTE_PROXY = YouTubeTranscriptApi(proxy_config=WebshareProxyConfig(
        proxy_username=WEBSHARE_PROXY_USERNAME,
        proxy_password=WEBSHARE_PROXY_PASSWORD,
    ))
    print("[OK] Proxy Webshare disponível como fallback (só é usado se o IP direto for bloqueado).")
else:
    print(
        "[AVISO] WEBSHARE_PROXY_USERNAME/PASSWORD não configurados no .env — "
        "sem fallback de proxy se o IP direto for bloqueado."
    )

# Começa tentando SEM proxy — só liga o proxy se de fato tomar bloqueio.
# Isso economiza a banda do Webshare (plano de 1GB), usando ele só quando
# for realmente necessário, não em toda chamada.
_usando_proxy = False

# A lib não tem timeout embutido — sem isso, um proxy que não responde
# trava o script pra sempre. Roda o fetch numa thread separada só pra
# poder impor um limite de tempo (funciona em qualquer SO, diferente de
# signal.alarm que não existe no Windows).
#
# Esse executor é DIFERENTE do que paraleliza os vídeos (lá embaixo, em
# coletar_dataset) — ele só existe pra dar timeout em CADA fetch
# individual. Por isso o número de workers aqui é maior que
# MAX_WORKERS_TRANSCRICAO: cada vídeo em paralelo ocupa 1 worker aqui
# enquanto espera o fetch, e se algum fetch travar de verdade (não dar
# timeout, travar mesmo) esse worker fica preso — a folga extra evita
# que isso morra de fome os vídeos seguintes.
#
# NOTA sobre o timeout: 20s já causou erro pra você antes (não só
# demora) e reduzir pra 5s piorou (5s é curto demais pro fetch completar
# via proxy). Se voltar a dar erro (não timeout) nesse valor, me manda o
# log completo da mensagem — o "Erro sem proxy (TipoDoErro: mensagem)"
# ou "Falha ao baixar transcrição (...)" que aparece no console diz
# exatamente o que está acontecendo.
_EXECUTOR_TRANSCRICAO = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS_TRANSCRICAO * 3)
TIMEOUT_TRANSCRICAO = 30  # segundos

# Protege o flag global (_usando_proxy) e a escrita do index.json — com
# vários vídeos em paralelo, sem isso duas threads podem tentar salvar o
# JSON ao mesmo tempo e corromper o arquivo.
_lock_estado = threading.Lock()

# Provedores de proxy costumam limitar quantas conexões simultâneas o seu
# plano aceita — passar disso não dá bloqueio do YouTube, dá 502 Bad
# Gateway do PRÓPRIO proxy (visto na prática no coletar_dados_cortes.py:
# com 5 workers batendo no proxy ao mesmo tempo, a taxa de sucesso caiu
# de ~64% pra quase 0% assim que a avalanche de 502 começou). Isso limita
# só as conexões via proxy; direto continua com a concorrência cheia.
LIMITE_CONEXOES_PROXY = 3
_semaforo_proxy = threading.Semaphore(LIMITE_CONEXOES_PROXY)


def _cliente_atual():
    return _CLIENTE_PROXY if (_usando_proxy and _CLIENTE_PROXY) else _CLIENTE_DIRETO


def _fetch_com_timeout(video_id, timeout=TIMEOUT_TRANSCRICAO):
    usando_proxy_agora = _usando_proxy and _CLIENTE_PROXY is not None
    cliente = _cliente_atual()

    if usando_proxy_agora:
        with _semaforo_proxy:
            future = _EXECUTOR_TRANSCRICAO.submit(cliente.fetch, video_id, languages=["pt"])
            return future.result(timeout=timeout)

    future = _EXECUTOR_TRANSCRICAO.submit(cliente.fetch, video_id, languages=["pt"])
    return future.result(timeout=timeout)


# Testado na prática (testar_ip_direto.py): direto e sequencial NÃO leva
# bloqueio. Um único IpBlocked isolado pode ser ruído passageiro — só
# depois de vários SEGUIDOS é que faz sentido acreditar que é bloqueio de
# verdade e vale gastar banda do proxy. Contador global: incrementa a
# cada bloqueio direto, zera a cada sucesso direto.
LIMIAR_BLOQUEIOS_CONSECUTIVOS = 3
_bloqueios_consecutivos = {"n": 0}


def baixar_transcricao(video_id, max_tentativas=2):
    """
    youtube_transcript_api não usa a cota oficial da Data API, mas ainda
    pode te bloquear temporariamente se bater rápido demais — por isso
    tem retry aqui também, separado do chamar_com_retry (que é só pra
    erros da googleapiclient). Tenta sem proxy primeiro. Só conta como
    "sinal de bloqueio real" os erros IpBlocked/RequestBlocked; qualquer
    outro erro transitório só faz retry, sem contar pro limiar. Depois de
    LIMIAR_BLOQUEIOS_CONSECUTIVOS bloqueios seguidos (não 1 só), passa a
    usar o proxy Webshare a partir daí (banda é limitada, então não vale
    trocar por um blip isolado).
    """
    global _usando_proxy

    for tentativa in range(max_tentativas):
        usando_proxy_agora = _usando_proxy and _CLIENTE_PROXY is not None

        try:
            resultado = _fetch_com_timeout(video_id)
            if not usando_proxy_agora:
                with _lock_estado:
                    _bloqueios_consecutivos["n"] = 0
            return resultado
        except concurrent.futures.TimeoutError:
            print(f"  [AVISO] Timeout ({TIMEOUT_TRANSCRICAO}s) baixando transcrição.")
            espera = 2 ** tentativa
            print(f"  [AVISO] Tentativa {tentativa + 1}/{max_tentativas}. Esperando {espera}s...")
            time.sleep(espera)
        except Exception as e:
            msg = str(e).lower()
            tipo = type(e).__name__
            # Casos permanentes (vídeo sem transcrição/legendas desativadas):
            # não adianta tentar de novo, com ou sem proxy
            if (
                "disabled" in msg or "no transcript" in msg or "not found" in msg
                or "age-restricted" in msg or "video is unavailable" in msg
                or "video is private" in msg
            ):
                print(f"  [AVISO] Sem transcrição disponível ({tipo}): não vale tentar de novo.")
                return None

            eh_bloqueio = tipo in ("IpBlocked", "RequestBlocked")

            if eh_bloqueio and not usando_proxy_agora:
                with _lock_estado:
                    _bloqueios_consecutivos["n"] += 1
                    n = _bloqueios_consecutivos["n"]
                    if n >= LIMIAR_BLOQUEIOS_CONSECUTIVOS and not _usando_proxy and _CLIENTE_PROXY:
                        print(
                            f"  [AVISO] {n} bloqueios seguidos sem proxy — mudando "
                            "pro proxy Webshare a partir de agora."
                        )
                        _usando_proxy = True
                    else:
                        print(f"  [AVISO] Bloqueio sem proxy ({n}/{LIMIAR_BLOQUEIOS_CONSECUTIVOS}), tentando de novo...")
                continue  # tenta de novo sem gastar a espera de backoff

            espera = 2 ** tentativa
            print(
                f"  [AVISO] Falha ao baixar transcrição ({tipo}: {e}), tentativa "
                f"{tentativa + 1}/{max_tentativas}. Esperando {espera}s..."
            )
            time.sleep(espera)

    print(f"  [AVISO] Desistindo da transcrição de {video_id} após {max_tentativas} tentativas.")
    return None


def formatar_transcricao(blocos):
    """Converte blocos da API para o formato [MM:SS] texto."""
    linhas = []
    for bloco in blocos:
        minutos  = int(bloco.start // 60)
        segundos = int(bloco.start % 60)
        texto    = bloco.text.replace("\n", " ").strip()
        linhas.append(f"[{minutos:02d}:{segundos:02d}] {texto}")
    return "\n".join(linhas)


# ============================================================
# 5. Pipeline principal — com suporte a retomar e múltiplos programas
# ============================================================

def coletar_dataset(api_key, playlist_id, pasta_saida, programa, max_videos=None):
    """
    Estrutura de saída (COMPARTILHADA entre programas, pra ficar
    compatível com cruzar_cortes_capitulos.py / checar_cobertura.py, que
    esperam um único index.json indexado por video_id):
        <pasta_saida>/
            transcricoes/<video_id>.txt
            capitulos/<video_id>.json
            index.json   ← salvo a CADA episódio, não só no final

    Cada registro do index.json agora carrega também "programa", pra
    dar pra usar isso depois no perfil de programa (gerar_metadata_capitulo.py)
    e no cruzamento (cruzar_cortes_capitulos.py).

    Se um video_id já estiver no index.json (de QUALQUER programa
    processado antes, nessa chamada ou numa anterior), ele é pulado — dá
    pra interromper e rodar de novo sem perder nem reprocessar nada.
    """
    if not api_key:
        raise RuntimeError(
            "YOUTUBE_API_KEY não encontrada. Coloque ela no .env do projeto "
            "(veja o topo do arquivo)."
        )

    criar_pasta(pasta_saida)
    criar_pasta(f"{pasta_saida}/transcricoes")
    criar_pasta(f"{pasta_saida}/capitulos")

    youtube = build("youtube", "v3", developerKey=api_key)

    print(f"\n[1/5] Buscando vídeos da playlist ({programa})...")
    videos = buscar_videos_da_playlist(youtube, playlist_id, max_videos)
    print(f"  → {len(videos)} vídeos encontrados")

    print(f"\n[2/5] Buscando descrições em lote (pra extrair capítulos)...")
    descricoes = buscar_descricoes_em_lotes(youtube, [v["video_id"] for v in videos])

    caminho_index = os.path.join(pasta_saida, "index.json")
    index_existente = {}
    if os.path.exists(caminho_index):
        with open(caminho_index, "r", encoding="utf-8") as f:
            for registro in json.load(f):
                index_existente[registro["video_id"]] = registro
        print(f"  → {len(index_existente)} episódios já coletados antes, no total (serão pulados)")

    print(f"\n[3/5] Processando capítulos (rápido, sequencial — sem rede)...")
    index = list(index_existente.values())
    ids_ja_feitos = set(index_existente.keys())
    pendentes = []

    for video in videos:
        video_id = video["video_id"]
        titulo   = video["titulo"]

        if video_id in ids_ja_feitos:
            continue

        registro = {
            "video_id": video_id,
            "titulo": titulo,
            "programa": programa,
            "tem_transcricao": False,
            "tem_capitulos": False,
            "n_capitulos": 0
        }

        descricao = descricoes.get(video_id, "")
        capitulos = extrair_capitulos_da_descricao(descricao)

        if capitulos:
            caminho_json = os.path.join(pasta_saida, "capitulos", f"{video_id}.json")
            with open(caminho_json, "w", encoding="utf-8") as f:
                json.dump(capitulos, f, ensure_ascii=False, indent=2)
            registro["tem_capitulos"] = True
            registro["n_capitulos"] = len(capitulos)

        index.append(registro)
        ids_ja_feitos.add(video_id)
        pendentes.append(registro)

    with open(caminho_index, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    print(f"  → {len(pendentes)} vídeos novos ({programa})")

    print(f"\n[4/5] Baixando transcrições em paralelo ({MAX_WORKERS_TRANSCRICAO} de cada vez)...")

    novos_deste_programa = len(pendentes)
    contador = {"feitos": 0}

    def processar_transcricao(registro):
        video_id = registro["video_id"]
        blocos = baixar_transcricao(video_id)

        if blocos:
            texto_formatado = formatar_transcricao(blocos)
            caminho_txt = os.path.join(pasta_saida, "transcricoes", f"{video_id}.txt")
            with open(caminho_txt, "w", encoding="utf-8") as f:
                f.write(texto_formatado)
            registro["tem_transcricao"] = True

        with _lock_estado:
            contador["feitos"] += 1
            with open(caminho_index, "w", encoding="utf-8") as f:
                json.dump(index, f, ensure_ascii=False, indent=2)
            status = "OK" if registro["tem_transcricao"] else "sem transcrição"
            print(f"  [{contador['feitos']}/{len(pendentes)}] {registro['titulo']} — {status}")

    if pendentes:
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS_TRANSCRICAO) as executor:
            list(executor.map(processar_transcricao, pendentes))

    print(f"\n{'=' * 50}")
    print(f"[5/5] COLETA FINALIZADA — {programa}")
    print(f"  Vídeos novos processados : {novos_deste_programa}")
    print(f"  Total no índice (geral)  : {len(index)}")
    print(f"  Pasta de saída           : {pasta_saida}/")
    print(f"{'=' * 50}")

    return index


# ============================================================
# 6. Entrada — roda todas as playlists de PLAYLISTS, em sequência
# ============================================================

if __name__ == "__main__":
    resumo = {}

    for programa, playlist_id in PLAYLISTS.items():
        index_atualizado = coletar_dataset(
            api_key      = YOUTUBE_API_KEY,
            playlist_id  = playlist_id,
            pasta_saida  = PASTA_SAIDA,
            programa     = programa,
            max_videos   = MAX_VIDEOS
        )
        resumo[programa] = sum(1 for r in index_atualizado if r.get("programa") == programa)

    print(f"\n{'#' * 50}")
    print("RESUMO GERAL (episódios por programa, no index.json inteiro)")
    for programa, n in resumo.items():
        print(f"  {programa:<22} {n}")
    print(f"{'#' * 50}")