"""
Login com Google, restrito a e-mails @flowgames.gg — usado pra travar
o acesso ao app.py quando ele for compartilhado como .exe.

Fluxo: primeira vez abre o navegador pra fazer login (a lib
InstalledAppFlow cuida disso sozinha — sobe um servidor local
temporário, captura o retorno, troca por um token). Depois disso, o
token fica salvo (token.json) e é reaproveitado — não abre navegador
de novo a cada execução, só quando o token expira de vez.

Dependência:
    pip install google-auth google-auth-oauthlib requests

Uso:
    from auth_google import autenticar_usuario
    email = autenticar_usuario()
    if not email:
        # login cancelado, ou domínio não autorizado
        ...
"""

import os

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

PASTA_ATUAL = os.path.dirname(os.path.abspath(__file__))

# Baixado do Google Cloud Console (Clientes > Cliente de computador 1 >
# baixar JSON) — precisa estar na mesma pasta que esse arquivo, com
# esse nome exato. NUNCA commita esse arquivo (mesma regra do .env).
CAMINHO_CREDENCIAIS = os.path.join(PASTA_ATUAL, "client_secret.json")

# Token da sessão, gerado depois do primeiro login — também não deve
# ser commitado (é credencial de acesso, ainda que de escopo mínimo).
CAMINHO_TOKEN = os.path.join(PASTA_ATUAL, "token.json")

DOMINIO_PERMITIDO = "flowgames.gg"

# Só precisamos confirmar identidade (e-mail) — não pedimos acesso a
# nenhum dado do Google Drive, Gmail, etc.
SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email"]


def email_permitido(email):
    """True se o e-mail termina em @flowgames.gg (case-insensitive)."""
    if not email:
        return False
    return email.strip().lower().endswith(f"@{DOMINIO_PERMITIDO}".lower())


def obter_credenciais():
    """
    Devolve credenciais válidas — reaproveita o token salvo se existir
    e ainda for válido (renovando sozinho se só estiver expirado mas
    tiver refresh token), ou faz o login interativo do zero. NÃO salva
    o token aqui — isso só acontece depois de confirmar o domínio, em
    autenticar_usuario().
    """
    creds = None

    if os.path.exists(CAMINHO_TOKEN):
        try:
            creds = Credentials.from_authorized_user_file(CAMINHO_TOKEN, SCOPES)
        except (ValueError, OSError):
            creds = None

    if creds and not creds.valid and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if not os.path.exists(CAMINHO_CREDENCIAIS):
            raise FileNotFoundError(
                f"Arquivo de credenciais não encontrado: {CAMINHO_CREDENCIAIS}\n"
                "Baixa o JSON do Google Cloud Console (Clientes > baixar) e coloca "
                "nessa pasta com o nome 'client_secret.json'."
            )
        flow = InstalledAppFlow.from_client_secrets_file(CAMINHO_CREDENCIAIS, SCOPES)
        creds = flow.run_local_server(port=0)

    return creds


def obter_email(creds):
    """Consulta o e-mail da conta logada, usando o access token."""
    resposta = requests.get(
        "https://www.googleapis.com/oauth2/v3/userinfo",
        headers={"Authorization": f"Bearer {creds.token}"},
        timeout=10,
    )
    resposta.raise_for_status()
    return resposta.json().get("email", "")


def autenticar_usuario():
    """
    Faz login com o Google e confere o domínio do e-mail.
    Devolve o e-mail se for @flowgames.gg, ou None se não for (ou se o
    login for cancelado no navegador).

    Só salva o token depois de confirmar o domínio — uma conta negada
    nunca fica persistida em disco, senão a próxima abertura do
    programa reaproveitaria esse token ruim silenciosamente e nunca
    mais ofereceria login de novo (foi exatamente esse bug que
    aconteceu na primeira versão).
    """
    creds = obter_credenciais()
    email = obter_email(creds)

    if not email_permitido(email):
        if os.path.exists(CAMINHO_TOKEN):
            os.remove(CAMINHO_TOKEN)
        return None

    with open(CAMINHO_TOKEN, "w", encoding="utf-8") as f:
        f.write(creds.to_json())

    return email


if __name__ == "__main__":
    resultado = autenticar_usuario()
    if resultado:
        print(f"Login autorizado: {resultado}")
    else:
        print("Login negado — e-mail fora do domínio @flowgames.gg (ou login cancelado).")