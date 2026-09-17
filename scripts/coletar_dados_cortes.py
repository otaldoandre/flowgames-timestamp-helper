"""
Coleta título, descrição, duração, data de publicação E transcrição de
todos os cortes publicados no canal Cortes do Flow Games.

Diferente do coletar_dados_episodios.py (que coleta episódios completos):
  - não extrai capítulos da descrição (cortes não têm)
  - extrai o episódio de origem (video_id) a partir do link que sempre
    aparece no início da descrição do corte — sem isso, não teria como
    saber de qual episódio cada corte veio
  - busca os detalhes em LOTES de até 50 vídeos por chamada, em vez de
    1 chamada por vídeo — é isso que evita estourar o limite por minuto
  - resolve a playlist de uploads sozinho a partir do @handle do canal
  - baixa a transcrição de cada corte (1 chamada por vídeo — a lib não
    tem lote pra isso), setando "tem_transcricao" — cruzar_cortes_capitulos.py
    depende desse campo + do arquivo em transcricoes/<video_id>.txt

Resumível: tanto a metadata quanto a transcrição são salvas a cada corte
processado em cortes.json — interromper e rodar de novo pula o que já
foi feito (por isso o cast do JSON pra dict por video_id no meio do
código, não é só estético).

Dependências:
    pip install youtube_transcript_api google-api-python-client python-dotenv

Uso:
    1. Coloque YOUTUBE_API_KEY no .env do projeto (e, se tiver, também
       WEBSHARE_PROXY_USERNAME/PASSWORD — usado como fallback se o IP
       direto for bloqueado durante o download das transcrições)
    2. Rode: python coletar_dados_cortes.py
    3. O resultado fica em dados_cortes_flow_games/cortes.json +
       dados_cortes_flow_games/transcricoes/<video_id>.txt
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

HANDLE_CANAL    = "CortesdoFlowGames"
PASTA_SAIDA     = "dados_cortes_flow_games"
MAX_VIDEOS      = None       # quantos cortes coletar (None = todos)

# Limites pra não estourar RPM/cota — ajuste se precisar, mas esses
# valores já deixam bem folgado
PAUSA_ENTRE_PAGINAS      = 0.5    # segundos, entre páginas de playlistItems
PAUSA_ENTRE_LOTES        = 1.0    # segundos, entre lotes de videos().list
TAMANHO_LOTE             = 50     # máximo permitido pela API por chamada
MAX_TENTATIVAS           = 5      # retries em caso de erro temporário (429, 5xx)

# Downloads de transcrição em paralelo. Teste confirmou: sequencial e
# direto (sem proxy) NÃO leva bloqueio; 5 workers simultâneos direto
# levaram bloqueio na primeira tentativa. Suspeita forte: é a RAJADA de
# várias requisições ao mesmo tempo da mesma IP que dispara o bloqueio do
# YouTube, não o volume total. Por isso um valor baixo aqui — o objetivo
# agora é não gastar banda do Webshare, não velocidade máxima.
MAX_WORKERS_TRANSCRICAO = 3


# ============================================================
# 1. Utilitários
# ============================================================

def criar_pasta(pasta):
    os.makedirs(pasta, exist_ok=True)
    print(f"[OK] Pasta de saída: '{pasta}/'")


def duracao_iso_para_segundos(duracao_iso):
    """Converte duração ISO 8601 (ex: 'PT4M13S') pra segundos inteiros."""
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duracao_iso)
    horas, minutos, segundos = (int(g) if g else 0 for g in match.groups())
    return horas * 3600 + minutos * 60 + segundos


def chamar_com_retry(request, max_tentativas=MAX_TENTATIVAS):
    """
    Executa uma chamada da API com backoff exponencial em caso de erro
    temporário (rate limit, erro de servidor). quotaExceeded (cota DIÁRIA
    estourada) não adianta tentar de novo, então já para na hora com uma
    mensagem clara em vez de ficar tentando à toa.
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
                    "Reseta à meia-noite horário do Pacífico — não adianta "
                    "tentar de novo agora."
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
# 2. Resolve a playlist de uploads a partir do @handle
# ============================================================

def buscar_uploads_playlist_id(youtube, handle):
    """Custa 1 unidade — bom teste rápido pra ver se a chave tá funcionando."""
    resposta = chamar_com_retry(
        youtube.channels().list(part="contentDetails", forHandle=handle)
    )
    if not resposta.get("items"):
        raise ValueError(f"Canal @{handle} não encontrado.")
    return resposta["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]


# ============================================================
# 3. Lista os IDs de vídeo da playlist (leve, paginado)
# ============================================================

def buscar_ids_da_playlist(youtube, playlist_id, max_videos=None):
    """Só os video_ids — 1 unidade por página de até 50 itens."""
    ids = []
    token = None

    while True:
        resposta = chamar_com_retry(
            youtube.playlistItems().list(
                part="contentDetails",
                playlistId=playlist_id,
                maxResults=50,
                pageToken=token
            )
        )

        for item in resposta["items"]:
            ids.append(item["contentDetails"]["videoId"])
            if max_videos and len(ids) >= max_videos:
                return ids

        token = resposta.get("nextPageToken")
        if not token:
            break

        time.sleep(PAUSA_ENTRE_PAGINAS)

    return ids


# ============================================================
# 3.5. Extrai o episódio de origem do link que sempre vem no início
#      da descrição do corte
# ============================================================

REGEX_LINK_EPISODIO = re.compile(
    r'(?:youtu\.be/|youtube\.com/(?:watch\?v=|live/))([a-zA-Z0-9_-]{11})'
)

# "Shorts" (título com #shorts...) são um produto diferente dos cortes
# que interessam pro dataset de treino — mesmo vindo do mesmo canal/
# playlist de uploads, não é o formato que a IA vai aprender a cortar.
# Filtra ANTES de baixar transcrição pra não gastar banda com eles.
REGEX_HASHTAG_SHORTS = re.compile(r'#shorts', re.IGNORECASE)


def eh_short(titulo):
    return bool(REGEX_HASHTAG_SHORTS.search(titulo or ""))


def extrair_episodio_de_origem(descricao):
    """
    Extrai o video_id do episódio completo a partir do link que sempre
    aparece nas primeiras linhas da descrição do corte. Retorna None se
    não achar nenhum link reconhecível (vale checar manualmente esses
    casos depois).
    """
    match = REGEX_LINK_EPISODIO.search(descricao or "")
    return match.group(1) if match else None


# ============================================================
# 4. Busca título/descrição/duração em LOTES (o pulo do gato)
# ============================================================

def buscar_detalhes_em_lotes(youtube, video_ids, tamanho_lote=TAMANHO_LOTE):
    """
    videos().list aceita até 50 IDs separados por vírgula numa chamada só.
    Pra 500 cortes isso é ~10 chamadas em vez de 500 — é isso que evita
    estourar o limite por minuto, não só o time.sleep.
    """
    detalhes = []
    total_lotes = (len(video_ids) - 1) // tamanho_lote + 1 if video_ids else 0

    for i in range(0, len(video_ids), tamanho_lote):
        lote = video_ids[i:i + tamanho_lote]
        print(f"  → Lote {i // tamanho_lote + 1}/{total_lotes} ({len(lote)} vídeos)")

        resposta = chamar_com_retry(
            youtube.videos().list(
                part="snippet,contentDetails",
                id=",".join(lote)
            )
        )

        for item in resposta["items"]:
            detalhes.append({
                "video_id":           item["id"],
                "titulo":             item["snippet"]["title"],
                "descricao":          item["snippet"]["description"],
                "publicado_em":       item["snippet"]["publishedAt"],
                "duracao_segundos":   duracao_iso_para_segundos(item["contentDetails"]["duration"]),
                "episodio_origem_id": extrair_episodio_de_origem(item["snippet"]["description"]),
                "tem_transcricao":    False,
            })

        time.sleep(PAUSA_ENTRE_LOTES)

    sem_episodio = sum(1 for d in detalhes if d["episodio_origem_id"] is None)
    if sem_episodio:
        print(
            f"  [AVISO] {sem_episodio}/{len(detalhes)} cortes sem link de "
            f"episódio reconhecível na descrição — vale checar manualmente."
        )

    return detalhes


# ============================================================
# 5. Baixa transcrição de cada corte (mesma estratégia direto→proxy
#    do coletar_dados_episodios.py: só liga o proxy Webshare se o IP
#    direto for bloqueado, pra economizar banda)
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

_usando_proxy = False

# A lib não tem timeout embutido — sem isso, um proxy que não responde
# trava o script pra sempre. Roda o fetch numa thread separada só pra
# poder impor um limite de tempo (funciona em qualquer SO, diferente de
# signal.alarm que não existe no Windows).
#
# Esse executor é DIFERENTE do que paraleliza os cortes (lá embaixo, em
# coletar_cortes) — só dá timeout em CADA fetch individual. Por isso tem
# mais workers que MAX_WORKERS_TRANSCRICAO: cada corte em paralelo ocupa
# 1 worker aqui enquanto espera, e a folga extra evita que um fetch
# realmente travado morra de fome os próximos.
#
# NOTA sobre o timeout: se voltar a dar erro (não só timeout) nesse
# valor, me manda o log completo — a mensagem "Erro sem proxy (Tipo:
# msg)" / "Falha ao baixar transcrição (...)" já mostra a causa exata.
_EXECUTOR_TRANSCRICAO = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS_TRANSCRICAO * 3)
TIMEOUT_TRANSCRICAO = 30  # segundos

# Protege o flag global (_usando_proxy) e a escrita do cortes.json — com
# vários cortes em paralelo, sem isso duas threads podem salvar o JSON
# ao mesmo tempo e corromper o arquivo.
_lock_estado = threading.Lock()

# Provedores de proxy costumam limitar quantas conexões simultâneas o seu
# plano aceita — passar disso não dá bloqueio do YouTube, dá 502 Bad
# Gateway do PRÓPRIO proxy (visto na prática: com 5 workers batendo no
# proxy ao mesmo tempo, a taxa de sucesso caiu de ~64% pra quase 0% assim
# que a avalanche de 502 começou). Isso limita só as conexões via proxy;
# conexão direta continua com a concorrência cheia de MAX_WORKERS_TRANSCRICAO.
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


def formatar_transcricao(blocos):
    """Converte blocos da API para o formato [MM:SS] texto."""
    linhas = []
    for bloco in blocos:
        minutos  = int(bloco.start // 60)
        segundos = int(bloco.start % 60)
        texto    = bloco.text.replace("\n", " ").strip()
        linhas.append(f"[{minutos:02d}:{segundos:02d}] {texto}")
    return "\n".join(linhas)


# Testado na prática (testar_ip_direto.py): direto e sequencial NÃO leva
# bloqueio. Um único IpBlocked isolado pode ser ruído passageiro — só
# depois de vários SEGUIDOS é que faz sentido acreditar que é bloqueio de
# verdade e vale gastar banda do proxy. Contador global: incrementa a
# cada bloqueio direto, zera a cada sucesso direto.
LIMIAR_BLOQUEIOS_CONSECUTIVOS = 3
_bloqueios_consecutivos = {"n": 0}


def baixar_transcricao(video_id, max_tentativas=2):
    """
    Tenta direto primeiro. Só conta como "sinal de bloqueio real" os
    erros IpBlocked/RequestBlocked — qualquer outro erro transitório
    (rede, 5xx, etc.) só faz retry, sem contar pro limiar. Depois de
    LIMIAR_BLOQUEIOS_CONSECUTIVOS bloqueios seguidos (não 1 só), passa a
    usar o proxy Webshare a partir daí (banda é limitada, então não vale
    trocar por um blip isolado).

    max_tentativas=2 (não 3): cada tentativa via proxy baixa a página
    inteira do YouTube, não só a legenda — com banda limitada (plano de
    1GB), cada retry extra custa caro e raramente muda o resultado pra um
    vídeo que já falhou 2x (geralmente é o mesmo bloqueio persistindo).
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

            if (
                "disabled" in msg or "no transcript" in msg or "not found" in msg
                or "age-restricted" in msg or "video is unavailable" in msg
                or "video is private" in msg
            ):
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


# ============================================================
# 6. Pipeline principal — resumível (metadata + transcrição salvas
#    incrementalmente em cortes.json)
# ============================================================

def coletar_cortes(api_key, handle_canal, pasta_saida, max_videos=None):
    if not api_key:
        raise RuntimeError(
            "YOUTUBE_API_KEY não encontrada. Coloque ela no .env do projeto "
            "(veja o topo do arquivo)."
        )

    criar_pasta(pasta_saida)
    criar_pasta(os.path.join(pasta_saida, "transcricoes"))
    youtube = build("youtube", "v3", developerKey=api_key)

    print(f"\n[1/4] Resolvendo playlist de uploads de @{handle_canal}...")
    playlist_id = buscar_uploads_playlist_id(youtube, handle_canal)
    print(f"  → playlist: {playlist_id}")

    print(f"\n[2/4] Listando vídeos da playlist...")
    video_ids = buscar_ids_da_playlist(youtube, playlist_id, max_videos)
    print(f"  → {len(video_ids)} cortes encontrados")

    caminho_saida = os.path.join(pasta_saida, "cortes.json")
    cortes_existentes = {}
    if os.path.exists(caminho_saida):
        with open(caminho_saida, "r", encoding="utf-8") as f:
            for c in json.load(f):
                cortes_existentes[c["video_id"]] = c
        print(f"  → {len(cortes_existentes)} cortes já processados antes (serão pulados se já tiverem metadata + transcrição)")

    ids_sem_metadata = [vid for vid in video_ids if vid not in cortes_existentes]

    print(f"\n[3/4] Buscando título/descrição/duração dos cortes novos em lotes...")
    if ids_sem_metadata:
        for detalhe in buscar_detalhes_em_lotes(youtube, ids_sem_metadata):
            cortes_existentes[detalhe["video_id"]] = detalhe
    else:
        print("  → Nenhum corte novo, todos já tinham metadata.")

    # Salva a metadata já, antes de começar a parte lenta (transcrição)
    with open(caminho_saida, "w", encoding="utf-8") as f:
        json.dump(list(cortes_existentes.values()), f, ensure_ascii=False, indent=2)

    print(f"\n[4/4] Baixando transcrição de cada corte em paralelo ({MAX_WORKERS_TRANSCRICAO} de cada vez)...")
    sem_transcricao = [vid for vid in video_ids if not cortes_existentes.get(vid, {}).get("tem_transcricao")]
    faltam_transcricao = [
        vid for vid in sem_transcricao
        if not eh_short(cortes_existentes.get(vid, {}).get("titulo"))
    ]
    pulados_shorts = len(sem_transcricao) - len(faltam_transcricao)

    print(f"  → {len(sem_transcricao)} cortes sem transcrição ainda")
    if pulados_shorts:
        print(
            f"  → {pulados_shorts} desses são #shorts (não é o corte vertical "
            f"que interessa pro dataset) — pulados, sem gastar banda com eles"
        )

    contador = {"feitos": 0, "ok": 0, "falhou": 0}
    inicio_transcricoes = time.time()

    def processar_transcricao(video_id):
        corte = cortes_existentes.get(video_id)
        if not corte:
            return  # não deveria acontecer, mas não trava se acontecer

        blocos = baixar_transcricao(video_id)

        if blocos:
            texto_formatado = formatar_transcricao(blocos)
            caminho_txt = os.path.join(pasta_saida, "transcricoes", f"{video_id}.txt")
            with open(caminho_txt, "w", encoding="utf-8") as f:
                f.write(texto_formatado)
            corte["tem_transcricao"] = True

        with _lock_estado:
            contador["feitos"] += 1
            if blocos:
                contador["ok"] += 1
            else:
                contador["falhou"] += 1

            # Salva a CADA corte — interromper no meio de 9380 não perde nada
            with open(caminho_saida, "w", encoding="utf-8") as f:
                json.dump(list(cortes_existentes.values()), f, ensure_ascii=False, indent=2)

            if contador["feitos"] % 25 == 0 or contador["feitos"] == len(faltam_transcricao):
                decorrido = time.time() - inicio_transcricoes
                taxa = contador["feitos"] / decorrido if decorrido > 0 else 0
                restantes = len(faltam_transcricao) - contador["feitos"]
                eta_min = (restantes / taxa / 60) if taxa > 0 else float("inf")
                pct_sucesso = 100 * contador["ok"] / contador["feitos"] if contador["feitos"] else 0
                print(
                    f"  ... {contador['feitos']}/{len(faltam_transcricao)} processados "
                    f"(OK: {contador['ok']} [{pct_sucesso:.0f}%], falhou: {contador['falhou']}) — "
                    f"~{taxa * 60:.1f}/min, ETA {eta_min:.0f}min"
                )

    if faltam_transcricao:
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS_TRANSCRICAO) as executor:
            list(executor.map(processar_transcricao, faltam_transcricao))

    detalhes_finais = list(cortes_existentes.values())
    com_transcricao = sum(1 for d in detalhes_finais if d.get("tem_transcricao"))

    print(f"\n{'=' * 50}")
    print(f"COLETA FINALIZADA")
    print(f"  Cortes no total     : {len(detalhes_finais)}")
    print(f"  Com transcrição     : {com_transcricao}")
    print(f"  Arquivo salvo       : {caminho_saida}")
    print(f"{'=' * 50}")

    return detalhes_finais


# ============================================================
# 7. Entrada
# ============================================================

if __name__ == "__main__":
    coletar_cortes(
        api_key      = YOUTUBE_API_KEY,
        handle_canal = HANDLE_CANAL,
        pasta_saida  = PASTA_SAIDA,
        max_videos   = MAX_VIDEOS
    )