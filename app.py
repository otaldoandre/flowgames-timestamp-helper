import tkinter as tk
from tkinter import scrolledtext, messagebox
from datetime import datetime, timedelta
from tkinter import Tk, font, filedialog
from tkinter.ttk import *
import unicodedata
import re
import os

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

def generate_clips():
    # A função assume que o usuário selecionou o arquivo de live correto, onde todos timestamps contidos no arquivo
    path = filedialog.askopenfilename(
        title="Selecione a live full",
        filetypes=[("MP4 files", "*.mp4")]
    )
    if not path:
        messagebox.showerror("Erro", "O arquivo não foi selecionado! Tente novamente.")
        return

    segments = get_clips_segments()
    if segments is None:
        return

    #Formata para exibir apenas o dia (DD/MM/AAAA) e o horário (HH:MM)
    date = datetime.now().strftime("%d/%m/%Y %H:%M")
    date = get_clean_title(date)

    os.mkdir(f"cortes - {date}")
    os.chdir(f"./cortes - {date}")

    total_segments = len(segments)
    for i, seg in enumerate(segments):
        start = seg["start"]
        end = seg["end"]
        title = seg["title"]

        percent_progress = ((i + 1) / total_segments) * 100
        progress.config(value=percent_progress)
        tela.update_idletasks()

        if end:
            cmd = f'ffmpeg -i {path} -ss {start} -to {end} -c copy "{title}.mp4"'
        else:
            cmd = f'ffmpeg -i {path} -ss {start} -c copy "{title}.mp4"'

        txt_saida.insert(tk.END, f"Clipe {i + 1}: Completo - {i + 1}/{total_segments}\n")

        os.system(cmd)


# Configuração da interface
tela = tk.Tk()
print(font.families())
tela.title("Crie cortes com base nas timestamps!")
tela.geometry("900x700")
tela.configure(bg="#7F14B7")

lbl_instrucao = tk.Label(tela, text="Cole as timestamps aqui:", bg="#7F14B7", fg="#FEF500", font=("Industry-Black", 12, "bold"))
lbl_instrucao.pack(pady=10)

# Campo de texto para entrada de timestamps
txt_entrada = scrolledtext.ScrolledText(tela, width=80, height=14, bg="#FFFFFF", fg="#000000")
txt_entrada.pack(pady=5)

btn_ajustar = tk.Button(tela, text="Criar cortes", command=generate_clips, bg="#FEF500", fg="#7F14B7", font=("Industry-Black", 10, "bold"))
btn_ajustar.pack(pady=20)

lbl_saida = tk.Label(tela, text="Progresso:", bg="#7F14B7", fg="#FEF500", font=("Industry-Black", 12, "bold"))
lbl_saida.pack(pady=10)

# Campo de texto para saída de timestamps ajustadas
txt_saida = scrolledtext.ScrolledText(tela, width=80, height=10, bg="#FFFFFF", fg="#000000")
txt_saida.pack(pady=5)

progress = Progressbar(tela, orient="horizontal", length=200, maximum=100)
progress.pack(pady=6)

# Inicia a interface
tela.mainloop()