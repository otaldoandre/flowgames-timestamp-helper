import tkinter as tk
from tkinter import scrolledtext, messagebox
from datetime import datetime, timedelta
from tkinter import Tk, font, filedialog
from tkinter.ttk import *
import unicodedata
import re
import os
import subprocess
import shutil

from detectar_capitulos import detectar_capitulos, carregar_transcricao as carregar_transcricao_ia
from gerar_metadata_capitulo import carregar_json, selecionar_exemplos_few_shot, avaliar_capitulo

LOGO_PADRAO_PATH = r"C:\Users\andre\Downloads\TEMPLATE\TEMPLATE\logo.png"

# Caminho do dataset de treino para referência de metadados
CAMINHO_TREINO_IA = r"C:\Users\andre\Downloads\projetos\flowgames-timestamp-helper\scripts\dados_cortes_flow_games\fase2_treino.json"
capitulos_vars = {}

# Armazena os resultados de metadados gerados (score, quotes, textos de
# thumbnail) indexados por título de capítulo para referência futura
metadata_ia = {}

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


def resolver_video():
    """
    Botão único: baixa o vídeo do YouTube (se houver link) ou abre o diálogo
    de seleção de arquivo local, e guarda o resultado em video_path_var —
    tanto "Criar cortes" quanto "Criar preview" reaproveitam esse mesmo
    vídeo em vez de baixar/selecionar de novo a cada ação.
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

        download_dir = filedialog.askdirectory(
            title="Selecione a pasta onde o vídeo baixado será salvo"
        )
        if not download_dir:
            messagebox.showerror("Erro", "Nenhuma pasta selecionada! Tente novamente.")
            return

        filepath = download_youtube_video(youtube_url, download_dir)
        if filepath:
            video_path_var.set(filepath)

    else:
        path = filedialog.askopenfilename(
            title="Selecione a live full",
            filetypes=[("MP4 files", "*.mp4")]
        )
        if path:
            video_path_var.set(path)


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
    date = datetime.now().strftime("%d/%m/%Y %H:%M")
    date = get_clean_title(date)

    pasta_original = os.getcwd()
    os.makedirs(f"cortes - {date}", exist_ok=True)
    os.chdir(f"./cortes - {date}")

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

                filter_complex = (
                    f'"[1:v]scale=227:227[logo];'
                    f'[0:v]scale=1920:1080,{video_chain}[base];'
                    f'[base][logo]overlay=W-w-40:40[outv]"'
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

    #Formata para exibir apenas o dia (DD/MM/AAAA) e o horário (HH:MM)
    date = datetime.now().strftime("%d/%m/%Y %H:%M")
    date = get_clean_title(date)

    pasta_original = os.getcwd()
    pasta_preview_atual = os.path.join(pasta_original, f"preview - {date}")
    os.makedirs(f"preview - {date}", exist_ok=True)
    os.chdir(f"./preview - {date}")

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


def carregar_capitulos():
    """
    Lê as timestamps coladas em txt_entrada e monta, pra cada capítulo, uma
    linha com checkboxes de "gerar corte" / "gerar preview" e um botão pra
    abrir o preview já gerado. generate_clips()/generate_clips_preview()
    passam a respeitar essas checkboxes via filtrar_segments_por_flag().
    """
    segments = get_clips_segments()
    if segments is None:
        return

    # Guarda o que já estava marcado/desmarcado antes de recarregar, pra uma
    # pequena edição no texto não jogar fora a seleção que você já tinha feito.
    # Casa primeiro pelo título completo (com o número do capítulo); se não
    # achar (porque a posição mudou), tenta casar só pelo texto do título.
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
        chk_corte = tk.Checkbutton(row, text="Corte", variable=var_corte, bg="#FFFFFF")
        chk_corte.pack(side="left", padx=4)

        var_preview = tk.BooleanVar(value=preview_anterior)
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

        capitulos_vars[seg["title"]] = {"corte": var_corte, "preview": var_preview, "seg": seg}

    txt_saida.insert(tk.END, f"{len(segments)} capítulos carregados.\n")
    txt_saida.see(tk.END)


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

# Guarda a pasta do último preview gerado, pra dar pra abrir os arquivos depois
ultima_pasta_preview_var = tk.StringVar()

lbl_capitulos = tk.Label(
    frame_conteudo,
    text="Capítulos (marque o que quer gerar):",
    bg="#7F14B7",
    fg="#FEF500",
    font=("Industry-Black", 11, "bold")
)
lbl_capitulos.pack(pady=(10, 2))

btn_carregar_capitulos = tk.Button(
    frame_conteudo,
    text="Carregar capítulos",
    command=carregar_capitulos,
    bg="#FEF500",
    fg="#7F14B7",
    font=("Industry-Black", 10, "bold")
)
btn_carregar_capitulos.pack(pady=2)

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

# Inicia a interface
tela.mainloop()