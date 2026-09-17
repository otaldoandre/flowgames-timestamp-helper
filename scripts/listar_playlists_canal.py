"""
Lista todas as playlists de um canal do YouTube (nome + ID) — útil pra
achar o PLAYLIST_ID de cada programa sem precisar caçar manualmente
na interface do YouTube.

Uso:
    1. Coloque YOUTUBE_API_KEY no .env do projeto (mesma chave usada em
       coletar_dados_episodios.py)
    2. Preencha CHANNEL_HANDLE abaixo (ex: "@FlowGames") OU CHANNEL_ID
       diretamente, se já souber
    3. Rode: python listar_playlists_canal.py
"""

import os
from dotenv import load_dotenv
from googleapiclient.discovery import build

load_dotenv()

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")

# Preencha um dos dois (o handle é mais fácil de achar, tipo "@FlowGames")
CHANNEL_HANDLE = "@FlowGames"   # <-- ajuste aqui
CHANNEL_ID = None                # <-- ou preencha o ID direto, se souber


def resolver_channel_id(youtube, handle):
    resposta = youtube.channels().list(part="id,snippet", forHandle=handle.lstrip("@")).execute()
    itens = resposta.get("items", [])
    if not itens:
        raise RuntimeError(f"Nenhum canal encontrado pro handle '{handle}'.")
    canal = itens[0]
    print(f"[OK] Canal resolvido: {canal['snippet']['title']} (ID: {canal['id']})")
    return canal["id"]


def listar_playlists(youtube, channel_id):
    playlists = []
    token = None
    while True:
        resposta = youtube.playlists().list(
            part="snippet,contentDetails",
            channelId=channel_id,
            maxResults=50,
            pageToken=token
        ).execute()

        for item in resposta["items"]:
            playlists.append({
                "titulo": item["snippet"]["title"],
                "playlist_id": item["id"],
                "n_videos": item["contentDetails"]["itemCount"]
            })

        token = resposta.get("nextPageToken")
        if not token:
            break

    return playlists


if __name__ == "__main__":
    if not YOUTUBE_API_KEY:
        raise RuntimeError("YOUTUBE_API_KEY não encontrada. Defina a variável de ambiente antes de rodar.")

    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)

    channel_id = CHANNEL_ID or resolver_channel_id(youtube, CHANNEL_HANDLE)
    playlists = listar_playlists(youtube, channel_id)

    print(f"\n{len(playlists)} playlists encontradas:\n")
    for p in playlists:
        print(f"  {p['titulo']:<40} {p['playlist_id']}   ({p['n_videos']} vídeos)")
