import tkinter as tk
from tkinter import scrolledtext, messagebox
from datetime import datetime, timedelta
from tkinter import Tk, font, filedialog, simpledialog
from tkinter.ttk import *
import unicodedata
import re
import os
import json
import subprocess
import shutil
import sys


from dotenv import load_dotenv
caminho_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(caminho_env)

# Restringe o acesso a e-mails @flowgames.gg — checa ANTES de montar
# qualquer coisa da interface. Se negar (domínio errado ou login
# cancelado), fecha o programa aqui mesmo.
from auth_google import autenticar_usuario

_email_autenticado = autenticar_usuario()
if not _email_autenticado:
    _tela_login = tk.Tk()
    _tela_login.withdraw()
    messagebox.showerror(
        "Acesso negado",
        "Login necessário com uma conta @flowgames.gg.\n"
        "Feche e tente novamente com a conta certa."
    )
    _tela_login.destroy()
    sys.exit(1)

from detectar_capitulos import detectar_capitulos, carregar_transcricao as carregar_transcricao_ia
from gerar_metadata_capitulo import carregar_json, selecionar_exemplos_few_shot, avaliar_capitulo
from pipeline_thumbnail import montar_thumbnail_completa
from PIL import Image, ImageTk

LOGO_PADRAO_PATH = r"C:\Users\andre\Downloads\TEMPLATE\TEMPLATE\logo.png"

# Caminho do dataset de treino para referência de metadados
CAMINHO_TREINO_IA = r"C:\Users\andre\Downloads\projetos\flowgames-timestamp-helper\scripts\dados_cortes_flow_games\fase2_treino.json"

# Template de thumbnail e pasta de saída — ajusta esses dois caminhos
CAMINHO_TEMPLATE_THUMBNAIL = r"C:\Users\andre\Downloads\projetos\flowgames-timestamp-helper\thumb_corte_template.psd"
PASTA_SAIDA_THUMBNAILS = r"C:\Users\andre\Downloads\projetos\flowgames-timestamp-helper\thumbnails"

# Detecção automática de lado (esquerda/direita do host) ainda não é
# confiável nos frames reais — força um lado fixo aqui até isso
# melhorar. Ajusta manualmente se um corte específico precisar do outro.
LADO_THUMBNAIL_PADRAO = "direita"

capitulos_vars = {}

# Armazena os resultados de metadados gerados (score, quotes, textos de
# thumbnail) indexados por título de capítulo para referência futura
metadata_ia = {}

# Guarda a transcrição selecionada uma vez, pra não pedir de novo a
# cada capítulo — só reseta fechando e abrindo o programa (ou trocando
# na função reset se você adicionar isso depois)
caminho_transcricao_atual = None
blocos_transcricao_atual = None


LOGO_POSICAO_X = None
LOGO_POSICAO_Y = None

# Pasta do projeto da live atual — None até o usuário definir via
# "Criar Projeto". Cortes, previews e thumbnails passam a ir pra dentro
# dela (subpastas cortes/, preview/, thumbnail/) em vez de pastas
# soltas com data no nome. Fica None = comportamento antigo ainda
# funciona (compatibilidade, não trava quem não configurar isso).
PASTA_PROJETO_ATUAL = None

# Registro de todos os projetos já criados (nome, pasta, última vez
# aberto) — fica ao lado do app.py, não da pasta atual de trabalho, pra
# sempre ser encontrado independente de onde o programa é executado.
CAMINHO_REGISTRO_PROJETOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_projetos.json")


def carregar_registro_projetos():
    if not os.path.exists(CAMINHO_REGISTRO_PROJETOS):
        return []
    try:
        with open(CAMINHO_REGISTRO_PROJETOS, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def salvar_registro_projetos(projetos):
    with open(CAMINHO_REGISTRO_PROJETOS, "w", encoding="utf-8") as f:
        json.dump(projetos, f, ensure_ascii=False, indent=2)


def registrar_projeto(nome, pasta):
    """Adiciona (ou, se já existir, atualiza) um projeto no registro, marcando como o mais recente."""
    projetos = carregar_registro_projetos()
    agora = datetime.now().isoformat()

    for p in projetos:
        if p["pasta"] == pasta:
            p["ultima_abertura"] = agora
            salvar_registro_projetos(projetos)
            return

    projetos.append({"nome": nome, "pasta": pasta, "ultima_abertura": agora})
    salvar_registro_projetos(projetos)


def caminho_estado_projeto(pasta_projeto):
    return os.path.join(pasta_projeto, "estado.json")


def salvar_estado_projeto():
    """Salva o texto de timestamps colado, o estado das checkboxes, o vídeo e a transcrição do projeto atual."""
    if not PASTA_PROJETO_ATUAL:
        return

    estado_capitulos = {
        title: {"corte": info["corte"].get(), "preview": info["preview"].get()}
        for title, info in capitulos_vars.items()
    }
    estado = {
        "texto_timestamps": txt_entrada.get("1.0", tk.END),
        "capitulos": estado_capitulos,
        "video_path": video_path_var.get(),
        "caminho_transcricao": caminho_transcricao_atual or "",
    }

    try:
        with open(caminho_estado_projeto(PASTA_PROJETO_ATUAL), "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False, indent=2)
    except OSError:
        pass  # falha ao salvar não deve travar o fluxo normal do programa


def carregar_estado_projeto(pasta_projeto):
    caminho = caminho_estado_projeto(pasta_projeto)
    if not os.path.exists(caminho):
        return None
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

def get_clean_title(title):
    #Remove acentos
    title = unicodedata.normalize("NFD", title)
    title = title.encode("ascii", "ignore").decode("utf-8")

    #Troca espaços por _
    title = title.replace(" ", "_")

    #Remove caracteres inválidos
    title = re.sub(r'[^a-zA-Z0-9_]', '', title)

    return title


def get_timestamps():
    input = txt_entrada.get("1.0", tk.END).strip()
    timestamps = []
    lines = input.split("\n")
    try:
        for i, l in enumerate(lines):

            #Tratamento de erros adicionais
            if "-" not in l:
                messagebox.showerror("Erro", "Formato de timestamp inválido! Use: <timestamp> - <title>")
                return None
            elif i == 0 and "00:00:00" not in l:
                messagebox.showerror("Erro", "A primeira timestamp deve ser 00:00:00")
                return None
            split_l = l.split(" - ", 1)
            if len(split_l) == 1:
                messagebox.showerror("Erro", "Formato de timestamp inválido! Use: <timestamp> - <title>")
                return None

            timestamp = split_l[0]
            title = split_l[1]

            #Verificar se timestamp está no formato
            datetime.strptime(timestamp, "%H:%M:%S")
            timestamps.append((
            f"{i + 1}-" + get_clean_title(title),
            timestamp,
            ))
    except ValueError:
        messagebox.showerror("Erro", "Formato de timestamp inválido! Use HH:MM:SS")

    return timestamps

def get_clips_segments():
    timestamps = get_timestamps()
    if timestamps is None:
        return None
    clips = []

    for i, timestamp in enumerate(timestamps):
        if "nl" in timestamps[i][0]:
            continue
        if i != len(timestamps) -1:
            end = timestamps[i + 1][1]
        else:
            end = None

        clips.append({
            "title": timestamp[0],
            "start": timestamp[1],
            "end": end
        })
    return clips


def filtrar_segments_por_flag(segments, flag):
    """
    Filtra os segmentos com base nas checkboxes marcadas em carregar_capitulos().
    Se a lista de capítulos ainda não foi carregada (capitulos_vars vazio),
    não filtra nada — mantém o comportamento antigo de processar tudo.
    """
    if not capitulos_vars:
        return segments
    filtrados = []
    for seg in segments:
        info = capitulos_vars.get(seg["title"])
        if info is None or info[flag].get():
            filtrados.append(seg)
    return filtrados


## --- Download via YouTube (yt-dlp) --- ##

def check_ytdlp_installed():
    return shutil.which("yt-dlp") is not None


def download_youtube_video(url, output_dir):
    """Baixa o vídeo do YouTube (via yt-dlp) para output_dir e retorna o caminho do arquivo .mp4 baixado."""
    output_template = os.path.join(output_dir, "%(title)s.%(ext)s")

    txt_saida.insert(tk.END, "Baixando vídeo do YouTube, aguarde...\n")
    txt_saida.see(tk.END)
    tela.update_idletasks()

    cmd = [
        "yt-dlp",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--no-playlist",
        "-o", output_template,
        url,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        erro = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "Erro desconhecido"
        messagebox.showerror("Erro", f"Falha ao baixar o vídeo do YouTube:\n{erro}")
        return None

    # Pergunta ao yt-dlp qual seria o nome final do arquivo, para localiza-lo
    filename_cmd = [
        "yt-dlp",
        "--get-filename",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--no-playlist",
        "-o", output_template,
        url,
    ]
    filename_result = subprocess.run(filename_cmd, capture_output=True, text=True)
    filepath = filename_result.stdout.strip()

    if not filepath or not os.path.exists(filepath):
        # Fallback: pega o .mp4 mais recente na pasta de download
        mp4_files = [f for f in os.listdir(output_dir) if f.lower().endswith(".mp4")]
        if not mp4_files:
            messagebox.showerror("Erro", "O download terminou, mas o arquivo baixado não foi encontrado.")
            return None
        mp4_files.sort(key=lambda f: os.path.getmtime(os.path.join(output_dir, f)), reverse=True)
        filepath = os.path.join(output_dir, mp4_files[0])

    txt_saida.insert(tk.END, f"Download concluído: {os.path.basename(filepath)}\n")
    txt_saida.see(tk.END)
    tela.update_idletasks()

    return filepath


def criar_novo_projeto():
    """
    Pede o NOME do projeto da live (não uma pasta pra escolher) e cria
    a pasta com esse nome, na pasta atual — com a estrutura padrão já
    dentro dela:
        <projeto>/cortes/
        <projeto>/preview/
        <projeto>/thumbnail/            (os .psd finais ficam direto aqui)
        <projeto>/thumbnail/thumbs_feitas/   (os .png finais ficam aqui)
    Cortes, previews e thumbnails gerados depois disso passam a usar
    essa estrutura em vez de pastas soltas com data no nome. Registra
    o projeto na lista, e limpa a tela pra começar do zero (um projeto
    novo não carrega progresso de outro).
    """
    global PASTA_PROJETO_ATUAL, caminho_transcricao_atual, blocos_transcricao_atual

    nome_projeto = simpledialog.askstring(
        "Nome do projeto",
        "Nome da live/projeto (vira o nome da pasta):"
    )
    if not nome_projeto:
        return

    nome_pasta = get_clean_title(nome_projeto)
    pasta = os.path.join(os.getcwd(), nome_pasta)

    os.makedirs(os.path.join(pasta, "cortes"), exist_ok=True)
    os.makedirs(os.path.join(pasta, "preview"), exist_ok=True)
    os.makedirs(os.path.join(pasta, "thumbnail", "thumbs_feitas"), exist_ok=True)

    PASTA_PROJETO_ATUAL = pasta
    pasta_projeto_var.set(pasta)

    registrar_projeto(nome_projeto, pasta)
    atualizar_lista_projetos()
    combo_projetos.set(nome_projeto)

    # Projeto novo começa vazio — não herda o que estava em tela antes
    txt_entrada.delete("1.0", tk.END)
    for widget in frame_capitulos.winfo_children():
        widget.destroy()
    capitulos_vars.clear()
    metadata_ia.clear()
    video_path_var.set("")
    caminho_transcricao_var.set("Nenhuma transcrição selecionada")
    caminho_transcricao_atual = None
    blocos_transcricao_atual = None

    txt_saida.insert(tk.END, f"Projeto criado: {pasta}\n")
    txt_saida.see(tk.END)


def abrir_projeto_selecionado():
    """
    Abre o projeto escolhido no menu suspenso — restaura o texto de
    timestamps, o estado das checkboxes, o vídeo e a transcrição
    salvos da última vez (se houver), continuando de onde parou.
    """
    global PASTA_PROJETO_ATUAL, caminho_transcricao_atual, blocos_transcricao_atual

    nome_selecionado = combo_projetos.get()
    if not nome_selecionado:
        messagebox.showinfo("Aviso", "Escolhe um projeto na lista primeiro.")
        return

    projetos = carregar_registro_projetos()
    projeto = next((p for p in projetos if p["nome"] == nome_selecionado), None)
    if not projeto:
        messagebox.showerror("Erro", "Esse projeto não foi encontrado no registro.")
        return

    pasta = projeto["pasta"]
    if not os.path.isdir(pasta):
        messagebox.showerror("Erro", f"A pasta desse projeto não existe mais:\n{pasta}")
        return

    PASTA_PROJETO_ATUAL = pasta
    pasta_projeto_var.set(pasta)
    registrar_projeto(nome_selecionado, pasta)  # marca como o mais recente
    atualizar_lista_projetos()
    combo_projetos.set(nome_selecionado)

    metadata_ia.clear()
    for widget in frame_capitulos.winfo_children():
        widget.destroy()
    capitulos_vars.clear()

    estado = carregar_estado_projeto(pasta)
    if estado:
        txt_entrada.delete("1.0", tk.END)
        txt_entrada.insert("1.0", estado.get("texto_timestamps", "").strip())

        video_path_salvo = estado.get("video_path", "")
        if video_path_salvo:
            video_path_var.set(video_path_salvo)

        transcricao_salva = estado.get("caminho_transcricao", "")
        if transcricao_salva and os.path.exists(transcricao_salva):
            caminho_transcricao_atual = transcricao_salva
            blocos_transcricao_atual = carregar_transcricao_ia(transcricao_salva)
            caminho_transcricao_var.set(transcricao_salva)
        elif transcricao_salva:
            # Caminho salvo, mas o arquivo não existe mais nesse local —
            # avisa em vez de falhar silenciosamente depois, na hora de gerar
            txt_saida.insert(tk.END, f"[AVISO] Transcrição salva não encontrada: {transcricao_salva}\n")

        estado_capitulos = estado.get("capitulos", {})
        estado_por_titulo = {
            title: (info.get("corte", True), info.get("preview", True))
            for title, info in estado_capitulos.items()
        }
        if estado_por_titulo:
            carregar_capitulos(estado_inicial=estado_por_titulo)

        txt_saida.insert(tk.END, f"Projeto '{nome_selecionado}' reaberto — progresso restaurado.\n")
    else:
        txt_entrada.delete("1.0", tk.END)
        txt_saida.insert(tk.END, f"Projeto '{nome_selecionado}' aberto (ainda sem progresso salvo).\n")
    txt_saida.see(tk.END)


def atualizar_lista_projetos():
    """Atualiza os itens do menu suspenso de projetos, do mais recente pro mais antigo."""
    projetos = carregar_registro_projetos()
    projetos_ordenados = sorted(projetos, key=lambda p: p["ultima_abertura"], reverse=True)
    combo_projetos["values"] = [p["nome"] for p in projetos_ordenados]
    return projetos_ordenados


def resolver_video():
    """
    Botão único: baixa o vídeo do YouTube (se houver link) ou abre o diálogo
    de seleção de arquivo local, e guarda o resultado em video_path_var —
    tanto "Criar cortes" quanto "Criar preview" reaproveitam esse mesmo
    vídeo em vez de baixar/selecionar de novo a cada ação.

    Se um projeto estiver ativo, o download vai direto pra pasta dele —
    só pergunta a pasta se não tiver nenhum projeto aberto.
    """
    youtube_url = youtube_link_var.get().strip()

    if youtube_url:
        if not check_ytdlp_installed():
            messagebox.showerror(
                "Erro",
                "yt-dlp não foi encontrado no sistema.\n"
                "Instale com: pip install yt-dlp\n"
                "(e garanta que esteja no PATH)."
            )
            return

        if PASTA_PROJETO_ATUAL:
            # Projeto ativo já tem lugar certo pra isso — não precisa perguntar
            download_dir = PASTA_PROJETO_ATUAL
        else:
            download_dir = filedialog.askdirectory(
                title="Selecione a pasta onde o vídeo baixado será salvo"
            )
            if not download_dir:
                messagebox.showerror("Erro", "Nenhuma pasta selecionada! Tente novamente.")
                return

        filepath = download_youtube_video(youtube_url, download_dir)
        if filepath:
            video_path_var.set(filepath)
            salvar_estado_projeto()

    else:
        path = filedialog.askopenfilename(
            title="Selecione a live full",
            filetypes=[("MP4 files", "*.mp4")]
        )
        if path:
            video_path_var.set(path)
            salvar_estado_projeto()


def extrair_video_id_youtube(url):
    """Extrai o video_id de várias formas de link do YouTube (watch, youtu.be, live)."""
    padrao = re.search(r'(?:youtu\.be/|youtube\.com/(?:watch\?v=|live/|embed/))([a-zA-Z0-9_-]{11})', url)
    return padrao.group(1) if padrao else None


def baixar_transcricao_youtube():
    """
    Baixa a transcrição do vídeo do YouTube linkado na mesma caixa de
    link usada pra baixar o vídeo — reaproveita o mesmo campo (é a
    mesma live nos dois casos), mas só LÊ o valor, nunca escreve nada
    nele, então não interfere com "Baixar / Selecionar vídeo".

    Salva no mesmo formato [MM:SS] que o resto do pipeline já usa, e
    define como a transcrição atual do projeto (igual selecionar
    manualmente faria).
    """
    global caminho_transcricao_atual, blocos_transcricao_atual

    url = youtube_link_var.get().strip()
    if not url:
        messagebox.showerror(
            "Erro",
            "Cola o link do YouTube na caixa acima primeiro (mesma caixa usada pra baixar o vídeo)."
        )
        return

    video_id = extrair_video_id_youtube(url)
    if not video_id:
        messagebox.showerror("Erro", "Não consegui identificar o ID do vídeo nesse link.")
        return

    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        messagebox.showerror(
            "Erro",
            "A biblioteca youtube_transcript_api não está instalada.\n"
            "Instale com: pip install youtube-transcript-api"
        )
        return

    txt_saida.insert(tk.END, "Baixando transcrição do YouTube, aguarde...\n")
    txt_saida.see(tk.END)
    tela.update_idletasks()

    try:
        api = YouTubeTranscriptApi()
        transcricao = api.fetch(video_id, languages=["pt"])
    except Exception as e:
        # A causa mais comum, de longe: legenda automática ainda não foi
        # gerada — o YouTube demora (às vezes bastante) pra disponibilizar
        # a legenda de lives grandes logo depois que elas terminam
        messagebox.showwarning(
            "Transcrição indisponível",
            "Não consegui baixar a transcrição desse vídeo agora.\n\n"
            "Isso é comum logo depois de uma live grande terminar — o "
            "YouTube pode levar um tempo pra gerar a legenda automática. "
            "Tenta de novo daqui a pouco.\n\n"
            f"Detalhe técnico: {e}"
        )
        txt_saida.insert(tk.END, f"[ERRO] Falha ao baixar transcrição: {e}\n")
        txt_saida.see(tk.END)
        return

    pasta_destino = PASTA_PROJETO_ATUAL or os.path.dirname(os.path.abspath(__file__))
    caminho_arquivo = os.path.join(pasta_destino, f"transcricao_{video_id}.txt")

    with open(caminho_arquivo, "w", encoding="utf-8") as arquivo:
        for bloco in transcricao:
            minutos = int(bloco.start // 60)
            segundos = int(bloco.start % 60)
            texto_limpo = bloco.text.replace("\n", " ").strip()
            arquivo.write(f"[{minutos:02d}:{segundos:02d}] {texto_limpo}\n")

    caminho_transcricao_atual = caminho_arquivo
    blocos_transcricao_atual = carregar_transcricao_ia(caminho_arquivo)
    caminho_transcricao_var.set(caminho_arquivo)
    salvar_estado_projeto()

    txt_saida.insert(tk.END, f"Transcrição baixada e salva em: {caminho_arquivo}\n")
    txt_saida.see(tk.END)
    messagebox.showinfo("Transcrição", "Transcrição baixada com sucesso!")


def selecionar_transcricao_manual():
    """
    Seleciona a transcrição do episódio explicitamente (em vez de só
    ser perguntada de forma escondida na primeira vez que "Metadata"
    ou "Thumbnail" precisam dela). Reaproveitada tanto pelo botão
    quanto por obter_metadata_ia_do_capitulo() quando ainda não tem
    nenhuma selecionada.
    """
    global caminho_transcricao_atual, blocos_transcricao_atual

    caminho = filedialog.askopenfilename(
        title="Selecione a transcrição do episódio",
        filetypes=[("Arquivos de texto", "*.txt")]
    )
    if not caminho:
        return

    caminho_transcricao_atual = caminho
    blocos_transcricao_atual = carregar_transcricao_ia(caminho)
    caminho_transcricao_var.set(caminho)
    salvar_estado_projeto()


def get_video_source():
    """
    Retorna o vídeo já resolvido (baixado ou selecionado via o botão
    "Baixar / Selecionar vídeo"). Se ainda não tiver sido resolvido nessa
    sessão, aciona resolver_video() uma vez antes de seguir.
    """
    if not video_path_var.get():
        resolver_video()

    path = video_path_var.get()
    if not path:
        messagebox.showerror(
            "Erro",
            "Nenhum vídeo baixado ou selecionado! Use o botão \"Baixar / Selecionar vídeo\"."
        )
        return None

    return path


def generate_clips():
    # A função assume que o usuário informou o link do YouTube (baixa via yt-dlp)
    # ou selecionou o arquivo de live correto, onde todos timestamps contidos no arquivo
    path = get_video_source()
    if not path:
        return

    segments = get_clips_segments()
    if segments is None:
        return

    segments = filtrar_segments_por_flag(segments, "corte")
    if not segments:
        messagebox.showinfo("Aviso", "Nenhum capítulo marcado para gerar corte.")
        return

    ## Config ##
    enable_fade_in = fade_in_var.get()
    enable_fade_out = fade_out_var.get()

    enable_endslate = endslate_var.get()
    endslate_path = endslate_path_var.get()

    enable_logo = logo_var.get()
    logo_path = logo_path_var.get()

    if enable_logo and not logo_path:
        messagebox.showerror(
            "Erro",
            "Selecione uma logo!"
        )
        return

    if enable_endslate and not endslate_path:
        messagebox.showerror(
            "Erro",
            "Selecione um vídeo de endslate!"
        )

        return
    fade_duration = 1.4

    ## Pasta de Output ##
    pasta_original = os.getcwd()

    if PASTA_PROJETO_ATUAL:
        pasta_destino = os.path.join(PASTA_PROJETO_ATUAL, "cortes")
    else:
        date = datetime.now().strftime("%d/%m/%Y %H:%M")
        date = get_clean_title(date)
        pasta_destino = f"cortes - {date}"

    os.makedirs(pasta_destino, exist_ok=True)
    os.chdir(pasta_destino)

    ## Processamento dos segmentos ##
    total_segments = len(segments)
    for i, seg in enumerate(segments):
        start = seg["start"]
        end = seg["end"]
        title = seg["title"]

        # Barra de progresso
        percent_progress = ((i + 1) / total_segments) * 100
        progress.config(value=percent_progress)
        tela.update_idletasks()


        ## Calculo de duração dos segmentos
        total_seconds = None
        duration_str = None
        if end is not None:
            start_dt = datetime.strptime(start, "%H:%M:%S")
            end_dt = datetime.strptime(end, "%H:%M:%S")

            duration = (end_dt - start_dt)
            total_seconds = int(duration.total_seconds())

            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60

            duration_str = f"{hours:02}:{minutes:02}:{seconds:02}"
        needs_render = (
                enable_fade_in
                or enable_fade_out
                or enable_endslate
                or enable_logo
        )
        if not needs_render:
            if end:
                cmd = (
                    f'ffmpeg -ss {start} '
                    f'-i "{path}" '
                    f'-t {duration_str} '
                    f'-c copy '
                    f'"{title}.mp4"'
                )
            else:

                cmd = (
                    f'ffmpeg -ss {start} '
                    f'-i "{path}" '
                    f'-c copy '
                    f'"{title}.mp4"'
                )
        else:
            # Habilita o render do input p/ o input (uso do fade-in e fadeout):
            # Aviso: o fade-in e fade-out não funcionam no último clipe
            filters = []

            if enable_fade_out and total_seconds > fade_duration and end is not None:
                filters.append(
                    f"fade=t=out:"
                    f"st={total_seconds - fade_duration}:"
                    f"d={fade_duration}"
                )
            if enable_fade_in:
                filters.append(
                    f"fade=t=in:"
                    f"st=0:"
                    f"d={fade_duration}"
                )
            filter_complex = ""
            temp_output = f"temp_{title}.mp4"

            # Se tiver logo
            if enable_logo:

                if len(filters) > 0:
                    video_chain = ",".join(filters)
                else:
                    video_chain = "null"

                if LOGO_POSICAO_X is not None and LOGO_POSICAO_Y is not None:
                    posicao_overlay = f"{LOGO_POSICAO_X}:{LOGO_POSICAO_Y}"
                else:
                    posicao_overlay = "W-w-40:40"

                filter_complex = (
                    f'"[1:v]scale=227:227[logo];'
                    f'[0:v]scale=1920:1080,{video_chain}[base];'
                    f'[base][logo]overlay={posicao_overlay}[outv]"'
                )

            else:
                if len(filters) > 0:
                    filter_complex = (
                        f'"[0:v]{",".join(filters)}[outv]"'
                    )


            filter_complex_cmd = ""
            map_video = '-map 0:v'

            if filter_complex:
                filter_complex_cmd = (
                    f'-filter_complex {filter_complex}'
                )

                map_video = '-map "[outv]"'
            if enable_logo:

                input_logo = f'-i "{logo_path}"'

            else:
                input_logo = ""
            ## Render
            if end:
                cmd_render = (
                    f'ffmpeg '
                    f'-ss {start} '
                    f'-i "{path}" '
                    f'{input_logo} '
                    f'-t {duration_str} '
                    f'{filter_complex_cmd} '
                    f'{map_video} '
                    f'-map 0:a '
                    f'-c:v h264_nvenc '
                    f'-preset p5 '
                    f'-cq 23 '
                    f'-c:a copy '
                    f'"{temp_output}"'
                )

            else:
                cmd_render = (
                    f'ffmpeg '
                    f'-ss {start} '
                    f'-i "{path}" '
                    f'{input_logo} '
                    f'{filter_complex_cmd} '
                    f'{map_video} '
                    f'-map 0:a '
                    f'-c:v h264_nvenc '
                    f'-preset p5 '
                    f'-cq 23 '
                    f'-c:a copy '
                    f'"{temp_output}"'
                )
            if not enable_endslate:

                final_output = f"{title}.mp4"

                cmd_finalize = (
                    f'ffmpeg '
                    f'-i "{temp_output}" '
                    f'-c copy '
                    f'"{final_output}"'
                )

                cmd = (
                        cmd_render
                        + " && " +
                        cmd_finalize
                )
            # Se tiver endslate
            else:

                final_output = f"{title}.mp4"

                cmd_concat = (
                    f'ffmpeg '
                    f'-i "{temp_output}" '
                    f'-i "{endslate_path}" '
                    f'-filter_complex '
                    f'"[0:v][0:a][1:v][1:a]'
                    f'concat=n=2:v=1:a=1[v][a]" '
                    f'-map "[v]" '
                    f'-map "[a]" '
                    f'-c:v h264_nvenc '
                    f'-preset p5 '
                    f'-cq 23 '
                    f'"{final_output}"'
                )

                cmd = (
                        cmd_render
                        + " && " +
                        cmd_concat
                )

        txt_saida.insert(tk.END, f"Clipe {i + 1}: Completo - {i + 1}/{total_segments}\n")

        os.system(cmd)
        print(cmd)

    os.chdir(pasta_original)


def generate_clips_preview():
    # A função assume que o usuário informou o link do YouTube (baixa via yt-dlp)
    # ou selecionou o arquivo de live correto, onde todos timestamps contidos no arquivo
    path = get_video_source()
    if not path:
        return

    segments = get_clips_segments()
    if segments is None:
        return

    segments = filtrar_segments_por_flag(segments, "preview")
    if not segments:
        messagebox.showinfo("Aviso", "Nenhum capítulo marcado para gerar preview.")
        return

    pasta_original = os.getcwd()

    if PASTA_PROJETO_ATUAL:
        pasta_preview_atual = os.path.join(PASTA_PROJETO_ATUAL, "preview")
    else:
        #Formata para exibir apenas o dia (DD/MM/AAAA) e o horário (HH:MM)
        date = datetime.now().strftime("%d/%m/%Y %H:%M")
        date = get_clean_title(date)
        pasta_preview_atual = os.path.join(pasta_original, f"preview - {date}")

    os.makedirs(pasta_preview_atual, exist_ok=True)
    os.chdir(pasta_preview_atual)

    total_segments = len(segments)
    for i, seg in enumerate(segments):
        start = seg["start"]
        end = seg["end"]
        title = seg["title"]

        percent_progress = ((i + 1) / total_segments) * 100
        progress.config(value=percent_progress)
        tela.update_idletasks()

        # Time variables that I can work with
        start_dt = datetime.strptime(start, "%H:%M:%S")
        preview_duration = timedelta(seconds=5)

        valid_start = (start_dt + preview_duration)
        #print(f"Start: {start} | Valid start: {valid_start}")

        if end:

            end_dt = datetime.strptime(end, "%H:%M:%S")

            duration = (end_dt - valid_start)
            total_seconds = int(duration.total_seconds())

            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60

            duration_str = f"{hours:02}:{minutes:02}:{seconds:02}"
            #Start
            cmd_start_preview = (
                f'ffmpeg -ss {start} -i "{path}" '
                f'-t 5 -c copy "{title}_preview_start.mp4"'
            )

            #End
            end_dt = datetime.strptime(end, "%H:%M:%S")

            valid_end = end_dt - preview_duration
            valid_end_str = valid_end.strftime("%H:%M:%S")
            cmd_end_preview = (
                f'ffmpeg -ss {valid_end_str} -i "{path}" '
                f'-t 5 -c copy "{title}_preview_end.mp4"'
            )

        else:
            # Possible feature: add an end preview to the last clip with ffprobe (we need to find the duration of the
            # whole video to do that)
            cmd_start_preview = (f'ffmpeg -i {path} -ss {start} -to {valid_start}'
                                 f' -c copy "{title}_preview_start.mp4"')
            cmd_end_preview = None


        txt_saida.insert(tk.END, f"Clipe {i + 1}: Completo - {i + 1}/{total_segments}\n")

        if cmd_end_preview:
            cmd = cmd_start_preview + " && " + cmd_end_preview
        else:
            cmd = cmd_start_preview

        os.system(cmd)

    os.chdir(pasta_original)
    ultima_pasta_preview_var.set(pasta_preview_atual)

def select_endslate():

    file_path = filedialog.askopenfilename(
        title="Selecione o vídeo de endslate",
        filetypes=[("MP4 files", "*.mp4")]
    )

    if file_path:
        endslate_path_var.set(file_path)


def abrir_preview_capitulo(title, parte):
    """
    Abre o arquivo de preview de uma parte específica desse capítulo
    ("start" ou "end") no player padrão do Windows (os.startfile). Só
    funciona depois de rodar "Criar preview de cortes" pelo menos uma vez
    na sessão atual.
    """
    pasta = ultima_pasta_preview_var.get()
    if not pasta:
        messagebox.showinfo(
            "Preview",
            "Gere os previews primeiro, com o botão \"Criar preview de cortes\"."
        )
        return

    caminho = os.path.join(pasta, f"{title}_preview_{parte}.mp4")
    if os.path.exists(caminho):
        os.startfile(caminho)
    else:
        messagebox.showinfo("Preview", "O preview desse capítulo ainda não foi gerado.")


def obter_metadata_ia_do_capitulo(title, seg):
    """
    Devolve o metadata da IA (quote, texto de thumbnail, título) pra
    esse capítulo. Se já tiver sido gerado (via "Gerar capítulos
    automaticamente"), usa o que já tá guardado. Senão, pergunta a
    transcrição — só na PRIMEIRA vez da sessão, reaproveita depois — e
    gera na hora, assim funciona mesmo quando os timestamps foram
    colados manualmente.
    """
    dados_ia = metadata_ia.get(title)
    if dados_ia is not None:
        return dados_ia

    gerar_agora = messagebox.askyesno(
        "Sugestão de texto pela IA",
        f"Esse capítulo (\"{title}\") ainda não tem sugestão de texto da IA.\n\n"
        "Quer gerar agora?"
    )
    if not gerar_agora:
        return {}

    global caminho_transcricao_atual, blocos_transcricao_atual

    if not caminho_transcricao_atual:
        selecionar_transcricao_manual()
        if not caminho_transcricao_atual:
            return {}

    blocos = blocos_transcricao_atual

    inicio_dt = datetime.strptime(seg["start"], "%H:%M:%S")
    inicio_segundos = inicio_dt.hour * 3600 + inicio_dt.minute * 60 + inicio_dt.second

    if seg["end"]:
        fim_dt = datetime.strptime(seg["end"], "%H:%M:%S")
        fim_segundos = fim_dt.hour * 3600 + fim_dt.minute * 60 + fim_dt.second
    else:
        fim_segundos = max((b["seconds"] for b in blocos), default=inicio_segundos) + 1

    texto = " ".join(b["text"] for b in blocos if inicio_segundos <= b["seconds"] < fim_segundos)
    if not texto.strip():
        maior_timestamp = max((b["seconds"] for b in blocos), default=0)
        messagebox.showinfo(
            "Aviso",
            f"Não achei texto da transcrição entre {seg['start']} e {seg['end'] or '(fim)'}.\n\n"
            f"A transcrição carregada ({os.path.basename(caminho_transcricao_atual)}) vai até "
            f"{maior_timestamp // 3600:02d}:{(maior_timestamp % 3600) // 60:02d}:{maior_timestamp % 60:02d} "
            f"— confere se é a transcrição do episódio certo pra esse capítulo."
        )
        return {}

    txt_saida.insert(tk.END, f"Gerando sugestão de texto com IA pra \"{title}\"...\n")
    txt_saida.see(tk.END)
    tela.update_idletasks()

    treino = carregar_json(CAMINHO_TREINO_IA)
    exemplos = selecionar_exemplos_few_shot(treino)
    dados_ia = avaliar_capitulo({"texto": texto}, exemplos)

    if dados_ia:
        metadata_ia[title] = dados_ia
        return dados_ia

    return {}


def dividir_texto_thumbnail(texto):
    """
    Quebra um texto em duas linhas, dividindo as palavras ao meio —
    usado só quando a IA não devolveu texto_thumbnail já dividido em
    linha1/linha2. O template tem duas caixas de texto (uma branca em
    cima, uma amarela embaixo); jogar tudo numa linha só estoura o
    espaço reservado pra ela.
    """
    palavras = texto.split()
    if len(palavras) <= 1:
        return texto, ""
    meio = (len(palavras) + 1) // 2
    linha1 = " ".join(palavras[:meio])
    linha2 = " ".join(palavras[meio:])
    return linha1, linha2


def mostrar_metadata_capitulo(title, seg):
    """
    Abre uma janela mostrando o metadata gerado pela IA pra esse
    capítulo — título, probabilidade de virar corte, quote de destaque,
    descrição, sugestão de imagem, texto de thumbnail — sem precisar
    abrir JSON na mão. Se ainda não tiver metadata gerado, oferece
    gerar na hora (mesma lógica do botão de thumbnail).
    """
    dados_ia = obter_metadata_ia_do_capitulo(title, seg)
    if not dados_ia:
        messagebox.showinfo("Metadata", "Sem metadata disponível pra esse capítulo.")
        return

    janela = tk.Toplevel(tela)
    janela.title(f"Metadata — {title}")
    janela.geometry("520x480")
    janela.configure(bg="#7F14B7")

    def adicionar_linha(rotulo, valor):
        frame_linha = tk.Frame(janela, bg="#7F14B7")
        frame_linha.pack(fill="x", padx=12, pady=5, anchor="w")
        tk.Label(
            frame_linha, text=rotulo, bg="#7F14B7", fg="#FEF500",
            font=("Industry-Black", 9, "bold")
        ).pack(anchor="w")
        tk.Label(
            frame_linha, text=valor or "(vazio)", bg="#FFFFFF", fg="#000000",
            wraplength=480, justify="left", anchor="w"
        ).pack(anchor="w", fill="x")

    probabilidade = dados_ia.get("probabilidade_corte")
    adicionar_linha("Título sugerido:", dados_ia.get("titulo"))
    adicionar_linha(
        "Probabilidade de virar corte:",
        f"{probabilidade}%" if probabilidade is not None else None
    )
    adicionar_linha("Quote de destaque:", dados_ia.get("quote_destaque"))
    adicionar_linha("Descrição:", dados_ia.get("descricao"))
    adicionar_linha("Sugestão de imagem:", dados_ia.get("sugestao_imagem"))

    texto_thumb = dados_ia.get("texto_thumbnail")
    if texto_thumb:
        adicionar_linha("Texto de thumbnail (linha 1):", texto_thumb.get("linha1"))
        adicionar_linha("Texto de thumbnail (linha 2):", texto_thumb.get("linha2"))
    else:
        adicionar_linha(
            "Texto de thumbnail:",
            "Não disponível — esse metadata é de antes desse campo existir, "
            "ou veio de um JSON antigo. Gera de novo pra ter esse campo."
        )


def obter_duracao_video(path):
    """Duração total do vídeo em segundos, via ffprobe. None se não conseguir."""
    resultado = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True
    )
    try:
        return float(resultado.stdout.strip())
    except ValueError:
        return None


def obter_fps_video(path):
    """FPS do vídeo, via ffprobe. 30.0 como fallback se não conseguir detectar."""
    resultado = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=r_frame_rate", "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True
    )
    saida = resultado.stdout.strip()
    try:
        if "/" in saida:
            numerador, denominador = saida.split("/")
            return float(numerador) / float(denominador)
        return float(saida)
    except (ValueError, ZeroDivisionError):
        return 30.0


def _hhmmss_para_segundos(timestamp_str):
    dt = datetime.strptime(timestamp_str, "%H:%M:%S")
    return dt.hour * 3600 + dt.minute * 60 + dt.second


def abrir_scrubber_live(titulo_janela="Escolher frame da live", seg_atual=None):
    """
    Player simplificado com slider pra navegar por um trecho da live —
    arrasta e solta o slider pra ver o frame daquele momento, e
    confirma quando achar um bom. Não é vídeo rodando de verdade (isso
    exigiria decodificar vídeo dentro do Tkinter, bem mais pesado) —
    é "arrasta e vê o frame extraído na hora".

    Por padrão o slider fica limitado ao capítulo atual (bem mais
    sensível que cobrir a live inteira de uma vez, já que a faixa é bem
    menor) — mas dá pra trocar pra qualquer outro capítulo carregado, ou
    pra live inteira, no menu suspenso. Os botões de ±1s/±10s ajudam a
    refinar depois de chegar perto, sem depender só da precisão do mouse.
    """
    path = get_video_source()
    if not path:
        return None

    duracao_total = obter_duracao_video(path)
    if not duracao_total:
        messagebox.showerror("Erro", "Não consegui determinar a duração do vídeo.")
        return None

    fps_video = obter_fps_video(path)
    duracao_frame = 1.0 / fps_video

    # Monta as faixas disponíveis: cada capítulo carregado + a live inteira
    faixas = {}
    for titulo_cap, info in capitulos_vars.items():
        seg_cap = info["seg"]
        inicio_cap = _hhmmss_para_segundos(seg_cap["start"])
        fim_cap = _hhmmss_para_segundos(seg_cap["end"]) if seg_cap["end"] else duracao_total
        faixas[titulo_cap] = (inicio_cap, fim_cap)

    faixas["Toda a live"] = (0, duracao_total)

    nome_padrao = "Toda a live"
    if seg_atual is not None:
        inicio_atual = _hhmmss_para_segundos(seg_atual["start"])
        fim_atual = _hhmmss_para_segundos(seg_atual["end"]) if seg_atual["end"] else duracao_total
        nome_padrao = next(
            (nome for nome, (i, f) in faixas.items() if i == inicio_atual and f == fim_atual),
            "Toda a live"
        )

    resultado = {"caminho": None}
    caminho_preview = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_frame_scrubber_live.jpg")

    janela = tk.Toplevel(tela)
    janela.title(titulo_janela)
    janela.configure(bg="#7F14B7")
    janela.grab_set()

    tk.Label(
        janela, text="Faixa de busca:", bg="#7F14B7", fg="#FEF500", font=("Industry-Black", 9, "bold")
    ).pack(pady=(10, 0))

    var_faixa = tk.StringVar(value=nome_padrao)
    combo_faixa = Combobox(janela, textvariable=var_faixa, values=list(faixas.keys()), state="readonly", width=55)
    combo_faixa.pack(pady=5)

    lbl_imagem = tk.Label(janela, bg="#000000")
    lbl_imagem.pack(padx=10, pady=10)

    lbl_timestamp = tk.Label(
        janela, text="00:00:00", bg="#7F14B7", fg="#FEF500", font=("Industry-Black", 10, "bold")
    )
    lbl_timestamp.pack()

    imagem_ref = {"img": None}
    posicao_atual = {"segundos": 0.0}

    def formatar_ts_exibicao(segundos):
        segundos_int = int(segundos)
        h, resto = divmod(segundos_int, 3600)
        m, s = divmod(resto, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def formatar_ts_ffmpeg(segundos):
        # Com fração de segundo (milissegundo) — precisão de segundo
        # inteiro não é suficiente pra navegar frame a frame
        h = int(segundos // 3600)
        m = int((segundos % 3600) // 60)
        s = segundos % 60
        return f"{h:02d}:{m:02d}:{s:06.3f}"

    def atualizar_frame(segundos):
        segundos = float(segundos)
        posicao_atual["segundos"] = segundos
        lbl_timestamp.config(text=formatar_ts_exibicao(segundos))

        resultado_ffmpeg = subprocess.run(
            ["ffmpeg", "-y", "-ss", formatar_ts_ffmpeg(segundos), "-i", path, "-frames:v", "1", caminho_preview],
            capture_output=True, text=True
        )
        if resultado_ffmpeg.returncode == 0 and os.path.exists(caminho_preview):
            img = Image.open(caminho_preview)
            img.thumbnail((640, 360))
            img_tk = ImageTk.PhotoImage(img)
            imagem_ref["img"] = img_tk  # evita a imagem ser coletada como lixo
            lbl_imagem.config(image=img_tk)

    def ao_soltar_slider(event):
        atualizar_frame(slider.get())

    inicio_padrao, fim_padrao = faixas[nome_padrao]

    slider = tk.Scale(
        janela, from_=inicio_padrao, to=fim_padrao, orient="horizontal",
        length=640, showvalue=False, bg="#7F14B7", fg="#FEF500", troughcolor="#FFFFFF"
    )
    slider.pack(padx=10, pady=5)
    slider.bind("<ButtonRelease-1>", ao_soltar_slider)

    def trocar_faixa(event=None):
        inicio, fim = faixas[var_faixa.get()]
        slider.config(from_=inicio, to=fim)
        slider.set(inicio)
        atualizar_frame(inicio)

    combo_faixa.bind("<<ComboboxSelected>>", trocar_faixa)

    def ajustar(delta):
        # Sempre parte de posicao_atual (fração de segundo), não do
        # slider (só tem precisão de segundo inteiro) — senão ajuste de
        # frame não tem efeito nenhum depois de arredondar
        novo_valor = max(slider.cget("from"), min(slider.cget("to"), posicao_atual["segundos"] + delta))
        slider.set(int(novo_valor))
        atualizar_frame(novo_valor)

    frame_ajuste_fino = tk.Frame(janela, bg="#7F14B7")
    frame_ajuste_fino.pack(pady=5)
    for rotulo, delta in [("-10s", -10), ("-1s", -1), ("-1 frame", -duracao_frame),
                           ("+1 frame", duracao_frame), ("+1s", 1), ("+10s", 10)]:
        tk.Button(
            frame_ajuste_fino, text=rotulo, command=lambda d=delta: ajustar(d),
            bg="#FEF500", fg="#7F14B7", font=("Industry-Black", 9, "bold")
        ).pack(side="left", padx=3)

    def confirmar():
        if imagem_ref["img"] is None:
            messagebox.showinfo("Aviso", "Mexe no slider primeiro pra escolher um frame.")
            return
        resultado["caminho"] = caminho_preview
        janela.destroy()

    tk.Button(
        janela, text="Confirmar esse frame", command=confirmar,
        bg="#FEF500", fg="#7F14B7", font=("Industry-Black", 10, "bold")
    ).pack(pady=10)

    atualizar_frame(inicio_padrao)

    janela.wait_window()
    return resultado["caminho"]


def selecionar_frame_host(seg, titulo_janela="Selecionar frame do host"):
    """
    Abre uma janela com miniaturas de frames candidatos espalhados pela
    duração do corte, pra escolher qual vira o frame do host na
    thumbnail. Também dá pra escolher um arquivo manual, ou buscar um
    timestamp específico em qualquer outra parte da live — pro caso do
    corte em si não ter um bom frame do host.

    Retorna o caminho do frame escolhido, ou None se cancelado.
    """
    path = get_video_source()
    if not path:
        return None

    inicio_dt = datetime.strptime(seg["start"], "%H:%M:%S")
    inicio_segundos = inicio_dt.hour * 3600 + inicio_dt.minute * 60 + inicio_dt.second

    if seg["end"]:
        fim_dt = datetime.strptime(seg["end"], "%H:%M:%S")
        fim_segundos = fim_dt.hour * 3600 + fim_dt.minute * 60 + fim_dt.second
    else:
        fim_segundos = inicio_segundos + 60  # sem fim definido, usa só 60s como faixa de candidatos

    duracao = max(1, fim_segundos - inicio_segundos)
    n_candidatos = min(6, max(2, duracao // 5))
    passo = duracao / n_candidatos

    pasta_temp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_frames_candidatos")
    os.makedirs(pasta_temp, exist_ok=True)

    caminhos_candidatos = []
    for i in range(n_candidatos):
        segundo_candidato = inicio_segundos + int(passo * i)
        h, resto = divmod(segundo_candidato, 3600)
        m, s = divmod(resto, 60)
        ts_str = f"{h:02d}:{m:02d}:{s:02d}"

        caminho_candidato = os.path.join(pasta_temp, f"candidato_{i}.jpg")
        subprocess.run(
            ["ffmpeg", "-y", "-ss", ts_str, "-i", path, "-frames:v", "1", caminho_candidato],
            capture_output=True, text=True
        )
        if os.path.exists(caminho_candidato):
            caminhos_candidatos.append((ts_str, caminho_candidato))

    resultado = {"caminho": None}

    janela = tk.Toplevel(tela)
    janela.title(titulo_janela)
    janela.configure(bg="#7F14B7")
    janela.grab_set()  # trava interação com a janela principal até escolher

    tk.Label(
        janela, text="Clique no frame que quer usar:",
        bg="#7F14B7", fg="#FEF500", font=("Industry-Black", 10, "bold")
    ).pack(pady=8)

    frame_grade = tk.Frame(janela, bg="#7F14B7")
    frame_grade.pack(padx=10, pady=5)

    imagens_ref = []  # evita as miniaturas serem coletadas como lixo antes da janela fechar

    def escolher(caminho):
        resultado["caminho"] = caminho
        janela.destroy()

    for i, (ts_str, caminho_candidato) in enumerate(caminhos_candidatos):
        img = Image.open(caminho_candidato)
        img.thumbnail((200, 200))
        img_tk = ImageTk.PhotoImage(img)
        imagens_ref.append(img_tk)

        frame_item = tk.Frame(frame_grade, bg="#FFFFFF")
        frame_item.grid(row=i // 3, column=i % 3, padx=5, pady=5)

        tk.Button(frame_item, image=img_tk, command=lambda c=caminho_candidato: escolher(c)).pack()
        tk.Label(frame_item, text=ts_str, bg="#FFFFFF").pack()

    def escolher_arquivo():
        caminho = filedialog.askopenfilename(
            title="Selecione a imagem do host",
            filetypes=[("Imagens", "*.jpg *.jpeg *.png")]
        )
        if caminho:
            resultado["caminho"] = caminho
            janela.destroy()

    def buscar_outro_timestamp():
        caminho_escolhido = abrir_scrubber_live(
            titulo_janela="Buscar frame em outro momento da live",
            seg_atual=seg
        )
        if caminho_escolhido:
            resultado["caminho"] = caminho_escolhido
            janela.destroy()

    frame_botoes_extra = tk.Frame(janela, bg="#7F14B7")
    frame_botoes_extra.pack(pady=10)

    tk.Button(
        frame_botoes_extra, text="Escolher arquivo manualmente", command=escolher_arquivo,
        bg="#FEF500", fg="#7F14B7", font=("Industry-Black", 9, "bold")
    ).pack(side="left", padx=5)

    tk.Button(
        frame_botoes_extra, text="Buscar outro momento da live", command=buscar_outro_timestamp,
        bg="#FEF500", fg="#7F14B7", font=("Industry-Black", 9, "bold")
    ).pack(side="left", padx=5)

    janela.wait_window()  # bloqueia até a janela fechar, aí devolve o resultado

    return resultado["caminho"]


def gerar_thumbnail_para_capitulo(title, seg):
    """
    Gera a thumbnail completa (fundo removido do host, layout, texto,
    .psd editável) pra UM capítulo específico do checklist. Reaproveita
    o vídeo já resolvido, abre o seletor visual de frame do host
    (miniaturas candidatas dentro da duração do corte, com opção de
    arquivo manual ou buscar outro momento da live), e pede a imagem
    sugerida (isso continua escolha manual sua — não dá pra confiar
    busca automática de imagem).

    Se esse capítulo tiver metadata gerado pela IA (veio de "Gerar
    capítulos automaticamente"), usa o texto de thumbnail que ela já
    sugeriu; senão, usa o próprio título como texto.
    """
    path = get_video_source()
    if not path:
        return

    caminho_imagem_sugerida = filedialog.askopenfilename(
        title=f"Selecione a imagem sugerida para \"{title}\"",
        filetypes=[("Imagens", "*.jpg *.jpeg *.png")]
    )
    if not caminho_imagem_sugerida:
        return

    dados_ia = obter_metadata_ia_do_capitulo(title, seg)
    texto_thumb = dados_ia.get("texto_thumbnail") or {}

    if texto_thumb.get("linha1") or texto_thumb.get("linha2"):
        linha1 = texto_thumb.get("linha1") or ""
        linha2 = texto_thumb.get("linha2") or ""
    else:
        # A IA não devolveu texto já dividido (ou não tem metadata
        # nenhuma) — divide o título/quote em duas linhas na força
        # bruta, pra nunca estourar a linha de cima sozinha e deixar a
        # de baixo vazia. O template é feito pra 2 linhas curtas, não 1 longa.
        texto_base = dados_ia.get("titulo") or title
        linha1, linha2 = dividir_texto_thumbnail(texto_base)

    caminho_frame_bruto = selecionar_frame_host(seg, titulo_janela=f"Escolher frame do host — {title}")
    if not caminho_frame_bruto:
        return

    txt_saida.insert(tk.END, f"Gerando thumbnail pra \"{title}\" (pode demorar, o Photoshop vai abrir)...\n")
    txt_saida.see(tk.END)
    tela.update_idletasks()

    pasta_thumbnail_raiz = None
    if PASTA_PROJETO_ATUAL:
        pasta_thumbnail_raiz = os.path.join(PASTA_PROJETO_ATUAL, "thumbnail")
        # Arquivos de trabalho (frame bruto, sem fundo) ficam numa
        # subpasta própria de cada corte — mais organizado. O .psd e o
        # .png finais são movidos pra fora dessa subpasta logo abaixo.
        pasta_saida_geracao = os.path.join(pasta_thumbnail_raiz, get_clean_title(title))
    else:
        pasta_saida_geracao = PASTA_SAIDA_THUMBNAILS

    try:
        resultado_thumb = montar_thumbnail_completa(
            CAMINHO_TEMPLATE_THUMBNAIL,
            caminho_frame_bruto,
            caminho_imagem_sugerida,
            linha1,
            linha2,
            pasta_saida_geracao,
            title,
            lado_forcado=LADO_THUMBNAIL_PADRAO,
        )
    except Exception as e:
        # Qualquer erro inesperado (Photoshop travado, arquivo em uso,
        # etc.) cai aqui em vez de um traceback cru — o programa
        # continua rodando normalmente, só essa thumbnail específica
        # falhou.
        txt_saida.insert(tk.END, f"[ERRO INESPERADO] Falha ao gerar thumbnail de \"{title}\": {e}\n")
        txt_saida.see(tk.END)
        messagebox.showerror(
            "Erro ao gerar thumbnail",
            f"Algo deu errado gerando a thumbnail de \"{title}\":\n\n{e}\n\n"
            "O programa continua funcionando normalmente — só essa thumbnail não foi gerada."
        )
        return

    if resultado_thumb and pasta_thumbnail_raiz:
        # Reorganiza: .psd fica direto na raiz de thumbnail/ (fácil
        # acesso), .png final vai pra thumbnail/thumbs_feitas/ — só os
        # arquivos de trabalho continuam na subpasta própria do corte
        pasta_thumbs_feitas = os.path.join(pasta_thumbnail_raiz, "thumbs_feitas")
        os.makedirs(pasta_thumbs_feitas, exist_ok=True)

        caminho_psd_gerado = re.sub(r"\.png$", ".psd", resultado_thumb, flags=re.IGNORECASE)
        destino_png = os.path.join(pasta_thumbs_feitas, os.path.basename(resultado_thumb))
        destino_psd = os.path.join(pasta_thumbnail_raiz, os.path.basename(caminho_psd_gerado))

        try:
            shutil.move(resultado_thumb, destino_png)
            resultado_thumb = destino_png
            if os.path.exists(caminho_psd_gerado):
                shutil.move(caminho_psd_gerado, destino_psd)
        except OSError as e:
            txt_saida.insert(tk.END, f"[AVISO] Thumbnail gerada, mas não consegui reorganizar os arquivos: {e}\n")

    if resultado_thumb:
        txt_saida.insert(tk.END, f"Thumbnail pronta: {resultado_thumb}\n")
        messagebox.showinfo("Thumbnail", f"Pronta! Revisa o .psd antes de publicar (posição/tamanho do host, sombra).")
    else:
        txt_saida.insert(tk.END, f"Falha ao gerar thumbnail de \"{title}\" — confere o log do Photoshop.\n")
    txt_saida.see(tk.END)


def carregar_capitulos(estado_inicial=None):
    """
    Lê as timestamps coladas em txt_entrada e monta, pra cada capítulo, uma
    linha com checkboxes de "gerar corte" / "gerar preview" e um botão pra
    abrir o preview já gerado. generate_clips()/generate_clips_preview()
    passam a respeitar essas checkboxes via filtrar_segments_por_flag().

    Se estado_inicial for passado (dict título -> (corte, preview)), usa
    ele em vez do estado atual das checkboxes em tela — é assim que um
    projeto salvo restaura o progresso ao ser reaberto.
    """
    segments = get_clips_segments()
    if segments is None:
        return

    # Guarda o que já estava marcado/desmarcado antes de recarregar, pra uma
    # pequena edição no texto não jogar fora a seleção que você já tinha feito.
    # Casa primeiro pelo título completo (com o número do capítulo); se não
    # achar (porque a posição mudou), tenta casar só pelo texto do título.
    if estado_inicial is not None:
        estado_por_titulo = estado_inicial
    else:
        estado_por_titulo = {
            title: (info["corte"].get(), info["preview"].get())
            for title, info in capitulos_vars.items()
        }
    estado_por_texto = {
        title.split("-", 1)[1] if "-" in title else title: valores
        for title, valores in estado_por_titulo.items()
    }

    for widget in frame_capitulos.winfo_children():
        widget.destroy()
    capitulos_vars.clear()

    for seg in segments:
        texto_sem_numero = seg["title"].split("-", 1)[1] if "-" in seg["title"] else seg["title"]
        corte_anterior, preview_anterior = estado_por_titulo.get(
            seg["title"],
            estado_por_texto.get(texto_sem_numero, (True, True))
        )

        row = tk.Frame(frame_capitulos, bg="#FFFFFF")
        row.pack(fill="x", pady=2, padx=4)

        fim = seg["end"] if seg["end"] else "fim do vídeo"
        texto_linha = f'{seg["start"]} → {fim}  |  {seg["title"]}'
        lbl = tk.Label(
            row, text=texto_linha, bg="#FFFFFF", fg="#000000",
            anchor="w", width=48, font=("Industry-Black", 9)
        )
        lbl.pack(side="left")

        var_corte = tk.BooleanVar(value=corte_anterior)
        var_corte.trace_add("write", lambda *args: salvar_estado_projeto())
        chk_corte = tk.Checkbutton(row, text="Corte", variable=var_corte, bg="#FFFFFF")
        chk_corte.pack(side="left", padx=4)

        var_preview = tk.BooleanVar(value=preview_anterior)
        var_preview.trace_add("write", lambda *args: salvar_estado_projeto())
        chk_preview = tk.Checkbutton(row, text="Preview", variable=var_preview, bg="#FFFFFF")
        chk_preview.pack(side="left", padx=4)

        btn_abrir_inicio = tk.Button(
            row,
            text="▶ Início",
            command=lambda t=seg["title"]: abrir_preview_capitulo(t, "start"),
            bg="#FEF500",
            fg="#7F14B7",
            font=("Industry-Black", 8, "bold")
        )
        btn_abrir_inicio.pack(side="left", padx=2)

        # O preview de "fim" só é gerado quando o capítulo tem um próximo
        # timestamp (não é o último da lista) — então nem mostra o botão
        # quando ele nunca vai existir.
        if seg["end"]:
            btn_abrir_fim = tk.Button(
                row,
                text="▶ Fim",
                command=lambda t=seg["title"]: abrir_preview_capitulo(t, "end"),
                bg="#FEF500",
                fg="#7F14B7",
                font=("Industry-Black", 8, "bold")
            )
            btn_abrir_fim.pack(side="left", padx=2)

        btn_thumbnail = tk.Button(
            row,
            text="🖼 Thumbnail",
            command=lambda t=seg["title"], s=seg: gerar_thumbnail_para_capitulo(t, s),
            bg="#FEF500",
            fg="#7F14B7",
            font=("Industry-Black", 8, "bold")
        )
        btn_thumbnail.pack(side="left", padx=2)

        btn_metadata = tk.Button(
            row,
            text="📋 Metadata",
            command=lambda t=seg["title"], s=seg: mostrar_metadata_capitulo(t, s),
            bg="#FEF500",
            fg="#7F14B7",
            font=("Industry-Black", 8, "bold")
        )
        btn_metadata.pack(side="left", padx=2)

        capitulos_vars[seg["title"]] = {"corte": var_corte, "preview": var_preview, "seg": seg}

    txt_saida.insert(tk.END, f"{len(segments)} capítulos carregados.\n")
    txt_saida.see(tk.END)
    salvar_estado_projeto()


def gerar_capitulos_automaticamente():
    """
    Analisa a transcrição de um episódio novo, detecta automaticamente
    onde os capítulos começam, e usa IA pra gerar um título/descrição
    pra cada um — preenche a caixa de timestamps sozinho, como
    alternativa a colar manualmente. Depois é só clicar em "Carregar
    capítulos" normal, igual sempre já fazia.
    """
    caminho_transcricao = filedialog.askopenfilename(
        title="Selecione a transcrição do episódio",
        filetypes=[("Arquivos de texto", "*.txt")]
    )
    if not caminho_transcricao:
        return

    txt_saida.insert(tk.END, "Detectando capítulos automaticamente...\n")
    txt_saida.see(tk.END)
    tela.update_idletasks()

    blocos = carregar_transcricao_ia(caminho_transcricao)
    fronteiras = sorted(detectar_capitulos(caminho_transcricao))

    if not fronteiras:
        messagebox.showinfo("Aviso", "Nenhum capítulo detectado nessa transcrição.")
        return

    fim_transcricao = max((b["seconds"] for b in blocos), default=0) + 1

    txt_saida.insert(tk.END, f"{len(fronteiras)} capítulos detectados. Gerando título com IA...\n")
    txt_saida.see(tk.END)
    tela.update_idletasks()

    treino = carregar_json(CAMINHO_TREINO_IA)
    exemplos = selecionar_exemplos_few_shot(treino)

    metadata_ia.clear()
    linhas_timestamp = []

    for i, inicio in enumerate(fronteiras):
        fim = fronteiras[i + 1] if i + 1 < len(fronteiras) else fim_transcricao
        texto = " ".join(b["text"] for b in blocos if inicio <= b["seconds"] < fim)

        h, resto = divmod(int(inicio), 3600)
        m, s = divmod(resto, 60)
        timestamp_str = f"{h:02d}:{m:02d}:{s:02d}"

        titulo = "Sem titulo"
        if texto.strip():
            avaliacao = avaliar_capitulo({"texto": texto}, exemplos)
            if avaliacao and avaliacao.get("titulo"):
                titulo = avaliacao["titulo"]
                chave_metadata = f"{i + 1}-" + get_clean_title(titulo)
                metadata_ia[chave_metadata] = avaliacao

        linhas_timestamp.append(f"{timestamp_str} - {titulo}")

        txt_saida.insert(tk.END, f"  [{i + 1}/{len(fronteiras)}] {timestamp_str} - {titulo}\n")
        txt_saida.see(tk.END)
        tela.update_idletasks()

    txt_entrada.delete("1.0", tk.END)
    txt_entrada.insert("1.0", "\n".join(linhas_timestamp))

    txt_saida.insert(
        tk.END,
        "\nCapítulos gerados! Clique em \"Carregar capítulos\" pra montar a lista de corte/preview.\n"
    )
    txt_saida.see(tk.END)


# Configuração da interface
tela = tk.Tk()
print(font.families())
tela.title("Crie cortes com base nas timestamps!")
tela.geometry("900x800")
tela.configure(bg="#7F14B7")

# Container rolável pra toda a interface — sem isso, qualquer seção nova
# empurra a de baixo pra fora da janela e ela some sem aviso nenhum.
canvas_principal = tk.Canvas(tela, bg="#7F14B7", highlightthickness=0)
scrollbar_principal = tk.Scrollbar(tela, orient="vertical", command=canvas_principal.yview)
frame_conteudo = tk.Frame(canvas_principal, bg="#7F14B7")

frame_conteudo.bind(
    "<Configure>",
    lambda e: canvas_principal.configure(scrollregion=canvas_principal.bbox("all"))
)

janela_conteudo = canvas_principal.create_window((0, 0), window=frame_conteudo, anchor="nw")
canvas_principal.configure(yscrollcommand=scrollbar_principal.set)

def _acompanhar_largura_canvas(event):
    canvas_principal.itemconfig(janela_conteudo, width=event.width)

canvas_principal.bind("<Configure>", _acompanhar_largura_canvas)

canvas_principal.pack(side="left", fill="both", expand=True)
scrollbar_principal.pack(side="right", fill="y")

def _rolar_com_mouse(event):
    canvas_principal.yview_scroll(int(-1 * (event.delta / 120)), "units")

canvas_principal.bind_all("<MouseWheel>", _rolar_com_mouse)

pasta_projeto_var = tk.StringVar(value="Nenhum projeto aberto")

frame_projeto = tk.Frame(frame_conteudo, bg="#7F14B7")
frame_projeto.pack(pady=8)

lbl_lista_projetos = tk.Label(
    frame_projeto, text="Projeto:", bg="#7F14B7", fg="#FEF500", font=("Industry-Black", 10, "bold")
)
lbl_lista_projetos.pack(side="left", padx=(0, 5))

combo_projetos = Combobox(frame_projeto, state="readonly", width=35)
combo_projetos.pack(side="left", padx=5)

btn_abrir_projeto = tk.Button(
    frame_projeto,
    text="Abrir Projeto Selecionado",
    command=abrir_projeto_selecionado,
    bg="#FEF500",
    fg="#7F14B7",
    font=("Industry-Black", 9, "bold")
)
btn_abrir_projeto.pack(side="left", padx=5)

btn_novo_projeto = tk.Button(
    frame_projeto,
    text="Criar Novo Projeto",
    command=criar_novo_projeto,
    bg="#FEF500",
    fg="#7F14B7",
    font=("Industry-Black", 9, "bold")
)
btn_novo_projeto.pack(side="left", padx=5)

frame_projeto_ativo = tk.Frame(frame_conteudo, bg="#7F14B7")
frame_projeto_ativo.pack(pady=(0, 8))

lbl_projeto = tk.Label(
    frame_projeto_ativo,
    textvariable=pasta_projeto_var,
    bg="#7F14B7",
    fg="#FFFFFF",
    wraplength=600,
    justify="left"
)
lbl_projeto.pack()

# Preenche a lista com os projetos já criados — a abertura automática
# do mais recente acontece só no final do arquivo, depois que todos os
# outros widgets (capítulos, saída, etc) já existirem
_projetos_existentes = atualizar_lista_projetos()
if _projetos_existentes:
    combo_projetos.set(_projetos_existentes[0]["nome"])

lbl_instrucao = tk.Label(frame_conteudo, text="Cole as timestamps aqui:", bg="#7F14B7", fg="#FEF500", font=("Industry-Black", 12, "bold"))
lbl_instrucao.pack(pady=10)

# Campo de texto para entrada de timestamps
txt_entrada = scrolledtext.ScrolledText(frame_conteudo, width=80, height=14, bg="#FFFFFF", fg="#000000")
txt_entrada.pack(pady=5)

# Frame para o link do YouTube (opcional - se preenchido, baixa via yt-dlp em vez de pedir arquivo local)
frame_youtube = tk.Frame(frame_conteudo, bg="#7F14B7")
frame_youtube.pack(pady=8)

lbl_youtube = tk.Label(
    frame_youtube,
    text="Link do YouTube (opcional, deixe vazio para usar arquivo local):",
    bg="#7F14B7",
    fg="#FEF500",
    font=("Industry-Black", 10, "bold")
)
lbl_youtube.pack(side="left", padx=5)

youtube_link_var = tk.StringVar()
entry_youtube = tk.Entry(frame_youtube, textvariable=youtube_link_var, width=45, bg="#FFFFFF", fg="#000000")
entry_youtube.pack(side="left", padx=5)

# Vídeo já resolvido (baixado ou selecionado), reaproveitado pelos cortes e previews
video_path_var = tk.StringVar()

frame_video = tk.Frame(frame_conteudo, bg="#7F14B7")
frame_video.pack(pady=5)

btn_resolver_video = tk.Button(
    frame_video,
    text="Baixar / Selecionar vídeo",
    command=resolver_video,
    bg="#FEF500",
    fg="#7F14B7",
    font=("Industry-Black", 10, "bold")
)
btn_resolver_video.pack(side="left", padx=5)

lbl_video_resolvido = tk.Label(
    frame_video,
    textvariable=video_path_var,
    bg="#7F14B7",
    fg="#FFFFFF",
    wraplength=500,
    justify="left"
)
lbl_video_resolvido.pack(side="left", padx=5)

caminho_transcricao_var = tk.StringVar(value="Nenhuma transcrição selecionada")

frame_transcricao = tk.Frame(frame_conteudo, bg="#7F14B7")
frame_transcricao.pack(pady=5)

btn_selecionar_transcricao = tk.Button(
    frame_transcricao,
    text="Selecionar Transcrição",
    command=selecionar_transcricao_manual,
    bg="#FEF500",
    fg="#7F14B7",
    font=("Industry-Black", 10, "bold")
)
btn_selecionar_transcricao.pack(side="left", padx=5)

btn_baixar_transcricao = tk.Button(
    frame_transcricao,
    text="Baixar Transcrição do YouTube",
    command=baixar_transcricao_youtube,
    bg="#FEF500",
    fg="#7F14B7",
    font=("Industry-Black", 10, "bold")
)
btn_baixar_transcricao.pack(side="left", padx=5)

lbl_transcricao = tk.Label(
    frame_transcricao,
    textvariable=caminho_transcricao_var,
    bg="#7F14B7",
    fg="#FFFFFF",
    wraplength=500,
    justify="left"
)
lbl_transcricao.pack(side="left", padx=5)

# Guarda a pasta do último preview gerado, pra dar pra abrir os arquivos depois
ultima_pasta_preview_var = tk.StringVar()

lbl_capitulos = tk.Label(
    frame_conteudo,
    text="Capítulos — cole manualmente acima OU gera automático, depois marque o que quer gerar:",
    bg="#7F14B7",
    fg="#FEF500",
    font=("Industry-Black", 11, "bold")
)
lbl_capitulos.pack(pady=(10, 2))

frame_botoes_capitulos = tk.Frame(frame_conteudo, bg="#7F14B7")
frame_botoes_capitulos.pack(pady=2)

btn_carregar_capitulos = tk.Button(
    frame_botoes_capitulos,
    text="Carregar capítulos",
    command=carregar_capitulos,
    bg="#FEF500",
    fg="#7F14B7",
    font=("Industry-Black", 10, "bold")
)
btn_carregar_capitulos.pack(side="left", padx=5)

btn_gerar_automatico = tk.Button(
    frame_botoes_capitulos,
    text="Gerar capítulos automaticamente (IA)",
    command=gerar_capitulos_automaticamente,
    bg="#FEF500",
    fg="#7F14B7",
    font=("Industry-Black", 10, "bold")
)
btn_gerar_automatico.pack(side="left", padx=5)

# Lista rolável de capítulos com checkboxes de corte/preview
frame_capitulos_container = tk.Frame(frame_conteudo, bg="#7F14B7")
frame_capitulos_container.pack(pady=5, padx=20, fill="x")

canvas_capitulos = tk.Canvas(frame_capitulos_container, bg="#FFFFFF", height=180, highlightthickness=0)
scrollbar_capitulos = tk.Scrollbar(frame_capitulos_container, orient="vertical", command=canvas_capitulos.yview)
frame_capitulos = tk.Frame(canvas_capitulos, bg="#FFFFFF")

frame_capitulos.bind(
    "<Configure>",
    lambda e: canvas_capitulos.configure(scrollregion=canvas_capitulos.bbox("all"))
)

canvas_capitulos.create_window((0, 0), window=frame_capitulos, anchor="nw")
canvas_capitulos.configure(yscrollcommand=scrollbar_capitulos.set)

canvas_capitulos.pack(side="left", fill="both", expand=True)
scrollbar_capitulos.pack(side="right", fill="y")

# Frame para agrupar os botões lado a lado
frame_botoes = tk.Frame(frame_conteudo, bg="#7F14B7")
frame_botoes.pack(pady=20) # Empacota o container verticalmente

btn_criar_cortes = tk.Button(frame_botoes, text="Criar cortes", command=generate_clips, bg="#FEF500", fg="#7F14B7", font=("Industry-Black", 10, "bold"))
btn_criar_cortes.pack(side="left", padx=15)

btn_criar_previews = tk.Button(frame_botoes, text="Criar preview de cortes", command=generate_clips_preview, bg="#FEF500", fg="#7F14B7", font=("Industry-Black", 10, "bold"))
btn_criar_previews.pack(side="left", padx=15)


lbl_saida = tk.Label(frame_conteudo, text="Progresso:", bg="#7F14B7", fg="#FEF500", font=("Industry-Black", 12, "bold"))
lbl_saida.pack(pady=10)

txt_saida = scrolledtext.ScrolledText(frame_conteudo, width=80, height=10, bg="#FFFFFF", fg="#000000")
txt_saida.pack(pady=5)

progress = Progressbar(frame_conteudo, orient="horizontal", length=200, maximum=100)
progress.pack(pady=15)


# Frame das opções
frame_opcoes = tk.Frame(frame_conteudo, bg="#7F14B7")
frame_opcoes.pack(pady=10)

frame_endslate = tk.Frame(frame_conteudo, bg="#7F14B7")
frame_endslate.pack(pady=5)

frame_logo = tk.Frame(frame_conteudo, bg="#7F14B7")
frame_logo.pack(pady=10)

# Vars
fade_in_var = tk.BooleanVar()
fade_out_var = tk.BooleanVar()
endslate_var = tk.BooleanVar()
endslate_path_var = tk.StringVar()
logo_var = tk.BooleanVar(value=True)
logo_path_var = tk.StringVar(value=LOGO_PADRAO_PATH)

check_fade_in = tk.Checkbutton(
    frame_opcoes,
    text="Fade In",
    variable=fade_in_var,
    bg="#7F14B7",
    fg="#FEF500",
    selectcolor="#7F14B7",
    font=("Industry-Black", 10, "bold")
)

check_fade_in.pack(side="left", padx=10)

check_fade_out = tk.Checkbutton(
    frame_opcoes,
    text="Fade Out",
    variable=fade_out_var,
    bg="#7F14B7",
    fg="#FEF500",
    selectcolor="#7F14B7",
    font=("Industry-Black", 10, "bold")
)

check_fade_out.pack(side="left", padx=10)

check_endslate = tk.Checkbutton(
    frame_opcoes,
    text="Usar Endslate",
    variable=endslate_var,
    bg="#7F14B7",
    fg="#FEF500",
    selectcolor="#7F14B7",
    font=("Industry-Black", 10, "bold")
)

check_endslate.pack(side="left", padx=10)

btn_select_endslate = tk.Button(
    frame_endslate,
    text="Selecionar Endslate",
    command=select_endslate,
    bg="#FEF500",
    fg="#7F14B7",
    font=("Industry-Black", 10, "bold")
)

btn_select_endslate.pack(side="left", padx=10)

lbl_endslate = tk.Label(
    frame_endslate,
    textvariable=endslate_path_var,
    bg="#7F14B7",
    fg="#FFFFFF",
    wraplength=500,
    justify="left"
)

lbl_endslate.pack(side="left")

lbl_logo = tk.Label(
    frame_logo,
    textvariable=logo_path_var,
    bg="#7F14B7",
    fg="#FFFFFF",
    wraplength=500
)

lbl_logo.pack(side="left")

def abrir_posicionador_logo():
    """
    Abre uma janela com um frame do vídeo já resolvido, com a logo por
    cima — arrasta ela pra posição desejada e clica em "Confirmar
    posição". Isso substitui ter que editar o código toda vez que quer
    mudar onde a logo aparece nos cortes.
    """
    path = get_video_source()
    if not path:
        return

    if not logo_path_var.get():
        messagebox.showerror("Erro", "Selecione uma logo primeiro (\"Selecionar Logo\").")
        return

    timestamp_frame = simpledialog.askstring(
        "Frame pra visualizar",
        "Em que ponto do vídeo pegar o frame de referência? (HH:MM:SS)\n"
        "Evita a tela de espera do início — usa um momento com conteúdo de verdade.",
        initialvalue="00:05:00"
    )
    if not timestamp_frame:
        return

    caminho_frame = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_frame_preview_logo.jpg")
    resultado_ffmpeg = subprocess.run(
        ["ffmpeg", "-y", "-ss", timestamp_frame, "-i", path, "-frames:v", "1", caminho_frame],
        capture_output=True, text=True
    )
    if resultado_ffmpeg.returncode != 0:
        messagebox.showerror("Erro", f"Falha ao extrair frame do vídeo:\n{resultado_ffmpeg.stderr[-300:]}")
        return

    # O ffmpeg SEMPRE redimensiona o vídeo pra 1920x1080 antes de aplicar
    # a logo (scale=1920:1080 no filtro), independente da resolução real
    # do arquivo baixado. Se a gente calculasse a escala com base na
    # resolução nativa do frame (que pode não ser 1920x1080), a
    # pré-visualização ficaria desproporcional ao render final de
    # verdade — por isso força esse resize aqui primeiro, igual o ffmpeg.
    LARGURA_RENDER = 1920
    ALTURA_RENDER = 1080
    img_frame = Image.open(caminho_frame).resize((LARGURA_RENDER, ALTURA_RENDER))

    # Escala a exibição pra caber numa janela razoável, mantendo a
    # proporção — as coordenadas finais são convertidas de volta pra
    # resolução real do vídeo (1920x1080) ao confirmar
    largura_exibida = 800
    escala = largura_exibida / LARGURA_RENDER
    altura_exibida = int(ALTURA_RENDER * escala)

    img_frame_tk = ImageTk.PhotoImage(img_frame.resize((largura_exibida, altura_exibida)))

    img_logo = Image.open(logo_path_var.get()).convert("RGBA")
    logo_lado_real = 227  # mesmo tamanho do scale=227:227 usado no ffmpeg
    logo_lado_exibido = max(1, int(logo_lado_real * escala))
    img_logo_tk = ImageTk.PhotoImage(img_logo.resize((logo_lado_exibido, logo_lado_exibido)))

    janela = tk.Toplevel(tela)
    janela.title("Posicionar logo — arraste pra ajustar")

    canvas = tk.Canvas(janela, width=largura_exibida, height=altura_exibida)
    canvas.pack()
    canvas.create_image(0, 0, anchor="nw", image=img_frame_tk)
    canvas.imagem_frame_ref = img_frame_tk  # evita a imagem ser coletada como lixo

    # Posição inicial: usa a já definida antes, ou o padrão antigo
    # (canto superior direito, 40px de margem) se ainda não tiver nenhuma
    if LOGO_POSICAO_X is not None and LOGO_POSICAO_Y is not None:
        x_inicial = int(LOGO_POSICAO_X * escala)
        y_inicial = int(LOGO_POSICAO_Y * escala)
    else:
        x_inicial = largura_exibida - logo_lado_exibido - int(40 * escala)
        y_inicial = int(40 * escala)

    item_logo = canvas.create_image(x_inicial, y_inicial, anchor="nw", image=img_logo_tk)
    canvas.imagem_logo_ref = img_logo_tk

    estado_arraste = {"x": 0, "y": 0}

    def iniciar_arraste(event):
        estado_arraste["x"] = event.x
        estado_arraste["y"] = event.y

    def arrastar(event):
        dx = event.x - estado_arraste["x"]
        dy = event.y - estado_arraste["y"]
        canvas.move(item_logo, dx, dy)
        estado_arraste["x"] = event.x
        estado_arraste["y"] = event.y

    canvas.tag_bind(item_logo, "<ButtonPress-1>", iniciar_arraste)
    canvas.tag_bind(item_logo, "<B1-Motion>", arrastar)

    lbl_instrucao_arraste = tk.Label(
        janela, text="Arraste a logo pra posição desejada",
        font=("Industry-Black", 9)
    )
    lbl_instrucao_arraste.pack(pady=4)

    def confirmar():
        global LOGO_POSICAO_X, LOGO_POSICAO_Y
        x_exibido, y_exibido = canvas.coords(item_logo)

        LOGO_POSICAO_X = int(x_exibido / escala)
        LOGO_POSICAO_Y = int(y_exibido / escala)

        txt_saida.insert(tk.END, f"Posição da logo definida: x={LOGO_POSICAO_X}, y={LOGO_POSICAO_Y}\n")
        txt_saida.see(tk.END)

        if os.path.exists(caminho_frame):
            os.remove(caminho_frame)
        janela.destroy()

    btn_confirmar = tk.Button(
        janela, text="Confirmar posição", command=confirmar,
        bg="#FEF500", fg="#7F14B7", font=("Industry-Black", 10, "bold")
    )
    btn_confirmar.pack(pady=10)


def select_logo():
    path = filedialog.askopenfilename(
        title="Selecione a logo",
        filetypes=[
            ("PNG files", "*.png"),
            ("Image files", "*.png *.jpg *.jpeg")
        ]
    )

    if path:
        logo_path_var.set(path)

check_logo = tk.Checkbutton(
    frame_opcoes,
    text="Adicionar Logo",
    variable=logo_var,
    bg="#7F14B7",
    fg="#FEF500",
    selectcolor="#7F14B7",
    font=("Industry-Black", 10, "bold")
)

check_logo.pack(side="left", padx=10)

btn_logo = tk.Button(
    frame_logo,
    text="Selecionar Logo",
    command=select_logo,
    bg="#FEF500",
    fg="#7F14B7",
    font=("Industry-Black", 10, "bold")
)

btn_logo.pack(side="left", padx=10)

btn_posicionar_logo = tk.Button(
    frame_logo,
    text="Posicionar Logo",
    command=abrir_posicionador_logo,
    bg="#FEF500",
    fg="#7F14B7",
    font=("Industry-Black", 10, "bold")
)
btn_posicionar_logo.pack(side="left", padx=10)

# Abre automaticamente o projeto mais recente (se algum já existir),
# pra retomar de onde parou sem precisar clicar em nada — feito aqui no
# final porque depende de widgets (frame_capitulos, txt_saida, etc.)
# que só existem depois de toda a interface montada
if _projetos_existentes:
    abrir_projeto_selecionado()

# Inicia a interface
tela.mainloop()