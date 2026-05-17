import tkinter as tk
from tkinter import scrolledtext, messagebox
from datetime import datetime, timedelta
from tkinter import Tk, font


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


btn_ajustar = tk.Button(tela, text="Criar cortes", bg="#FEF500", fg="#7F14B7", font=("Industry-Black", 10, "bold"))
btn_ajustar.pack(pady=20)

lbl_saida = tk.Label(tela, text="Timestamps ajustadas:", bg="#7F14B7", fg="#FEF500", font=("Industry-Black", 12, "bold"))
lbl_saida.pack(pady=10)

# Campo de texto para saída de timestamps ajustadas
txt_saida = scrolledtext.ScrolledText(tela, width=80, height=14, bg="#FFFFFF", fg="#000000")
txt_saida.pack(pady=5)

# Inicia a interface
tela.mainloop()