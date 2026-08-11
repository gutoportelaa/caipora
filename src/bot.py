"""
Caipora — laço principal.

Nesta primeira versão o bot só ecoa e responde /start, de propósito: primeiro
provamos que o encanamento funciona ponta a ponta (Telegram -> celular ->
Telegram). Só depois plugamos o LLM. Se juntarmos as duas coisas de uma vez e
algo falhar, não saberemos qual metade quebrou.

Rodar:
    cd src && python bot.py
"""

import logging
import os
import sys
import time
from pathlib import Path

from calendario import AgendaHibrida, AgendaLocal
from canal_telegram import CanalTelegram, ErroPermanente
from llm import LLM
from roteador import Roteador
from vigia import Vigia

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("caipora")


def carregar_env(caminho: Path) -> None:
    """Lê um .env simples para o ambiente.

    Poderíamos usar python-dotenv, mas são 12 linhas — não vale a dependência.
    """
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        os.environ.setdefault(chave.strip(), valor.strip().strip("\"'"))


def _escolher_agenda(raiz: Path):
    """Google se houver token válido; senão, agenda local.

    A degradação é deliberada e barulhenta. Em modo "Testing" no Google Cloud
    o refresh token expira em 7 dias — se isso derrubasse o processo, o
    assistente pararia de avisar sobre contas justamente por um detalhe de
    configuração externa. Melhor seguir funcionando em local e reclamar alto.

    O risco conhecido dessa escolha: compromissos criados enquanto o Google
    está fora ficam só no arquivo local e não aparecem no Google depois. Por
    isso `/status` mostra sempre qual backend está ativo — divergência
    silenciosa seria pior que o erro.

    Em qualquer cenário os lembretes SEM hora marcada ficam num arquivo à
    parte: não são eventos de calendário e não têm onde morar no Google.
    Ver `AgendaHibrida`.
    """
    soltos = AgendaLocal(raiz / "dados" / "lembretes.json")

    token = raiz / "token.json"
    if token.exists():
        try:
            from agenda_google import AgendaGoogle

            ag = AgendaGoogle(token)
            ag.todos()  # falha rápido: valida credencial e rede agora
            log.info("agenda: Google Calendar (+ lembretes soltos em disco)")
            return AgendaHibrida(ag, soltos)
        except Exception as e:
            log.error("Google Calendar indisponivel (%s) — caindo para agenda local", e)
            log.error("reautorize com: python scripts/autorizar_google.py")
    else:
        log.info("token.json ausente — usando agenda local")

    return AgendaLocal(raiz / "dados" / "agenda.json")


def carregar_autorizados() -> set[str]:
    """Quem pode falar com o Caipora.

    Bots do Telegram são PÚBLICOS: qualquer um que descubra o @username
    consegue mandar mensagem. Sem essa barreira, um desconhecido cria evento
    na sua agenda e consome a bateria do aparelho rodando inferência.

    Aceita vários ids separados por vírgula, para quando você quiser liberar
    para alguém da família.
    """
    bruto = os.environ.get("TELEGRAM_CHAT_ID", "")
    ids = {p.strip() for p in bruto.split(",") if p.strip()}
    if not ids:
        raise SystemExit(
            "TELEGRAM_CHAT_ID vazio — sem allowlist o bot fica aberto a "
            "qualquer pessoa. Preencha o .env antes de rodar."
        )
    return ids




def main() -> int:
    carregar_env(Path(__file__).resolve().parent.parent / ".env")

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        log.error("TELEGRAM_BOT_TOKEN nao definido. Copie .env.example para .env")
        return 1

    canal = CanalTelegram(token)

    # Confirma a credencial antes de entrar no laço.
    #
    # Precisa de tolerância porque no boot do aparelho este processo sobe junto
    # com o resto e a rede (Wi-Fi/Tailscale) ainda não está pronta. Sem retry,
    # o serviço morreria e o runit ficaria reiniciando em loop apertado até a
    # rede subir. Token errado, por outro lado, não se resolve esperando —
    # então logamos a diferença.
    for tentativa in range(1, 11):
        try:
            log.info("conectado como %s", canal.identificar())
            break
        except ErroPermanente as e:
            # Esperar não resolve. Sair rápido e alto é melhor que loop
            # silencioso — o runit vai reiniciar, mas o log dirá o motivo
            # em cada tentativa em vez de esconder o problema.
            log.error("ERRO PERMANENTE: %s", e)
            log.error("confira TELEGRAM_BOT_TOKEN no .env")
            return 1
        except Exception as e:
            espera = min(5 * tentativa, 30)
            log.warning(
                "tentativa %d falhou (%s) — nova tentativa em %ds", tentativa, e, espera
            )
            time.sleep(espera)
    else:
        log.error("nao consegui autenticar em 10 tentativas — token invalido ou sem rede")
        return 1

    autorizados = carregar_autorizados()
    log.info("allowlist: %d id(s)", len(autorizados))

    agenda = _escolher_agenda(Path(__file__).resolve().parent.parent)
    roteador = Roteador(
        LLM(os.environ.get("LLAMA_SERVER_URL", "http://127.0.0.1:8080")),
        agenda,
    )

    # O vigia é o que faz do Caipora um assistente e não um banco de dados:
    # ele envia avisos sem ninguém perguntar. Roda em thread própria e usa a
    # mesma allowlist como lista de destinatários.
    vigia = Vigia(agenda, canal.enviar, sorted(autorizados))
    vigia.iniciar()

    log.info("aguardando mensagens (long polling)...")

    for msg in canal.receber():
        if msg.remetente not in autorizados:
            # Silêncio deliberado: não respondemos nada. Responder "acesso
            # negado" confirmaria ao desconhecido que o bot está ativo e
            # convidaria insistência.
            log.warning("ignorando remetente nao autorizado: %s", msg.remetente)
            continue

        log.info("[%s] %s", msg.remetente, msg.texto)
        with canal.digitando(msg.remetente):
            resposta = roteador.responder(msg.remetente, msg.texto)
        canal.enviar(msg.remetente, resposta)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log.info("encerrando")
