"""
Autorização OAuth do Google Calendar — rodar UMA VEZ, na máquina com navegador.

Sem `google-auth-oauthlib`: o fluxo de loopback é HTTP puro e a biblioteca
padrão dá conta. Uma dependência a menos para instalar e auditar.

Uso:
    python scripts/autorizar_google.py

Gera `token.json`, que depois é copiado para o aparelho. O `credentials.json`
NÃO precisa ir para o celular — só o token.

Detalhes que costumam dar errado e que este script trata:

  access_type=offline + prompt=consent
      Sem os dois, o Google devolve apenas access_token (1 hora) e nenhum
      refresh_token — o assistente pararia de agendar depois de uma hora.
      O `prompt=consent` é necessário porque, numa segunda autorização, o
      Google omite o refresh_token se você já consentiu antes.

  PKCE
      Recomendado pelo Google para apps instalados. O client_secret de um
      "Desktop app" não é realmente secreto (vai distribuído no binário),
      então o PKCE é o que de fato protege a troca do código.

  redirect_uri de loopback
      Porta efêmera em 127.0.0.1. O Google descontinuou o fluxo OOB
      ("copie e cole o código"), então precisa ser servidor local mesmo.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CREDENCIAIS = RAIZ / "credentials.json"
TOKEN = RAIZ / "token.json"

# Só eventos. Não pedimos "calendar" completo (que permite criar/apagar
# calendários inteiros) porque o assistente não precisa — menor privilégio.
ESCOPO = "https://www.googleapis.com/auth/calendar.events"

# Janela generosa: é fluxo interativo, feito uma vez, e depende de a pessoa
# estar na frente do navegador. Cinco minutos expira antes de dar tempo.
ESPERA_S = 1800

_codigo: dict[str, str] = {}


class Captura(http.server.BaseHTTPRequestHandler):
    """Recebe o redirect do Google e extrai o `code`."""

    def do_GET(self):  # noqa: N802
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _codigo.update({k: v[0] for k, v in params.items()})

        ok = "code" in _codigo
        corpo = (
            "<h2>Caipora autorizado.</h2><p>Pode fechar esta aba.</p>"
            if ok
            else f"<h2>Falhou</h2><pre>{_codigo.get('error', 'sem codigo')}</pre>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(corpo.encode("utf-8"))

    def log_message(self, *a):
        pass  # silencia o log do http.server


def main() -> int:
    if not CREDENCIAIS.exists():
        print(f"ERRO: {CREDENCIAIS} nao encontrado.")
        print("Baixe em: Google Cloud Console > Credenciais > ID do cliente OAuth")
        print("Tipo: 'App para computador'. Renomeie para credentials.json")
        return 1

    conf = json.loads(CREDENCIAIS.read_text(encoding="utf-8"))
    conf = conf.get("installed") or conf.get("web")
    if not conf:
        print("ERRO: credentials.json nao parece ser de um cliente OAuth.")
        return 1

    # PKCE
    verificador = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    desafio = (
        base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    estado = secrets.token_urlsafe(16)

    servidor = http.server.HTTPServer(("127.0.0.1", 0), Captura)
    porta = servidor.server_address[1]

    # Usamos o MESMO host registrado no credentials.json. O Google compara o
    # redirect_uri como string na validação, então "127.0.0.1" e "localhost"
    # não são intercambiáveis mesmo apontando para o mesmo lugar — a
    # divergência devolve "Erro 400: invalid_request".
    #
    # A porta pode variar livremente: em cliente do tipo Desktop, o Google
    # aceita qualquer porta no loopback.
    registrados = conf.get("redirect_uris") or ["http://localhost"]
    host = "localhost" if any("localhost" in u for u in registrados) else "127.0.0.1"
    redirect_uri = f"http://{host}:{porta}"

    url = conf["auth_uri"] + "?" + urllib.parse.urlencode({
        "client_id": conf["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": ESCOPO,
        "access_type": "offline",
        "prompt": "consent",
        "state": estado,
        "code_challenge": desafio,
        "code_challenge_method": "S256",
    })

    print("=" * 72)
    print("Abra esta URL no navegador e autorize:\n")
    print(url)
    print("\n" + "=" * 72)
    print(f"(aguardando o redirect em {redirect_uri} ...)")
    sys.stdout.flush()

    try:
        webbrowser.open(url)
    except Exception:
        pass  # em WSL/servidor pode não haver navegador — a URL acima basta

    t = threading.Thread(target=servidor.handle_request, daemon=True)
    t.start()
    t.join(timeout=ESPERA_S)

    if "code" not in _codigo:
        print(f"\nERRO: nao recebi o codigo (timeout de {ESPERA_S // 60} min "
              "ou acesso negado).")
        print(f"detalhe: {_codigo}")
        print("Rode o script de novo — a URL muda a cada execucao (porta e PKCE).")
        return 1
    if _codigo.get("state") != estado:
        print("\nERRO: 'state' nao confere — possivel CSRF. Abortando.")
        return 1

    dados = urllib.parse.urlencode({
        "client_id": conf["client_id"],
        "client_secret": conf["client_secret"],
        "code": _codigo["code"],
        "code_verifier": verificador,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }).encode()

    req = urllib.request.Request(
        conf["token_uri"], data=dados,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            tok = json.load(r)
    except urllib.error.HTTPError as e:
        print("\nERRO ao trocar o codigo por token:", e.read().decode()[:500])
        return 1

    if "refresh_token" not in tok:
        print("\nERRO: veio sem refresh_token — o assistente pararia em 1 hora.")
        print("Revogue o acesso em https://myaccount.google.com/permissions")
        print("e rode de novo (o prompt=consent forca o refresh_token).")
        return 1

    TOKEN.write_text(json.dumps({
        "client_id": conf["client_id"],
        "client_secret": conf["client_secret"],
        "token_uri": conf["token_uri"],
        "refresh_token": tok["refresh_token"],
        "access_token": tok.get("access_token", ""),
        "scope": ESCOPO,
    }, indent=2), encoding="utf-8")
    TOKEN.chmod(0o600)

    print(f"\n✅ {TOKEN} gravado (refresh_token obtido).")
    print("\nCopie para o aparelho:")
    print("    scp token.json caipora:~/caipora/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
