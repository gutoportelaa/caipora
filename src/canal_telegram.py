"""
Canal do Telegram via long polling, usando apenas a biblioteca padrão.

Sem `requests`, sem `python-telegram-bot`. Motivo: são ~15 MB de dependências
para fazer duas chamadas HTTP. Num aparelho com 2,5 GB de orçamento de RAM,
cada dependência precisa se justificar — e essas duas não se justificam.

A API do Telegram é só HTTP + JSON:
    https://api.telegram.org/bot<TOKEN>/<metodo>
"""

import contextlib
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator

from canal import Mensagem

log = logging.getLogger(__name__)

# Quantos segundos o Telegram segura a conexão esperando uma mensagem chegar.
# É isso que faz o "long" do long polling: em vez de perguntar a cada segundo
# e desperdiçar bateria e rádio, abrimos UMA conexão e ela fica aberta até
# chegar mensagem ou estourar o tempo. Muito mais econômico no celular.
ESPERA_POLLING = 30


class ErroPermanente(Exception):
    """Erro que não se resolve tentando de novo — exige intervenção humana."""


class CanalTelegram:
    def __init__(self, token: str):
        if not token:
            raise ValueError("Token do Telegram vazio — confira o .env")
        self._base = f"https://api.telegram.org/bot{token}"
        # `offset` é como o Telegram sabe o que já entregamos. Cada update tem
        # um id crescente; mandar offset=N confirma o recebimento de tudo até
        # N-1 e o Telegram nunca mais reenvia. Sem isso, você recebe as
        # mesmas mensagens em loop infinito — é o erro nº 1 de quem começa.
        self._offset = 0

    # ---------------------------------------------------------------- interno

    def _chamar(self, metodo: str, params: dict[str, Any], timeout: int) -> Any:
        url = f"{self._base}/{metodo}?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                corpo = json.load(r)
        except urllib.error.HTTPError as e:
            # Distinguir permanente de transitório importa: tratar tudo como
            # transitório faz o bot girar em loop silencioso para sempre com
            # um token inválido — falha que ninguém percebe.
            #   401/404 = token errado ou revogado -> humano precisa agir
            #   409     = OUTRO processo já está no getUpdates deste bot.
            #             O Telegram aceita apenas um consumidor por token.
            #             Transitório de propósito: resolve quando o
            #             duplicado morre.
            #   429/5xx = limite de taxa ou problema no lado deles -> esperar
            if e.code in (401, 403, 404):
                raise ErroPermanente(
                    f"{metodo} rejeitado com HTTP {e.code} — token invalido ou revogado"
                ) from e
            raise
        if not corpo.get("ok"):
            raise RuntimeError(f"Telegram recusou {metodo}: {corpo}")
        return corpo["result"]

    # ---------------------------------------------------------------- utilidade

    def identificar(self) -> str:
        """Confirma que o token é válido e devolve o @username do bot.

        Vale chamar no boot: se o token estiver errado, você descobre aqui com
        uma mensagem clara, em vez de depurar um polling que nunca recebe nada.
        Também evita a confusão de estar falando com um bot antigo sem perceber.
        """
        info = self._chamar("getMe", {}, timeout=20)
        return f"@{info['username']} ({info.get('first_name', '')})".strip()

    # ---------------------------------------------------------- interface Canal

    def receber(self) -> Iterator[Mensagem]:
        while True:
            try:
                updates = self._chamar(
                    "getUpdates",
                    {"offset": self._offset, "timeout": ESPERA_POLLING},
                    # O timeout do socket precisa ser MAIOR que o do Telegram,
                    # senão desistimos antes dele responder e nunca recebemos
                    # nada.
                    timeout=ESPERA_POLLING + 15,
                )
            except (OSError, json.JSONDecodeError) as e:
                # Rede de celular cai o tempo todo. Isso é rotina, não erro
                # fatal: registra e tenta de novo no próximo ciclo.
                #
                # Capturamos OSError (e não só URLError/TimeoutError) porque
                # quando a conexão morre durante a LEITURA da resposta SSL, o
                # erro sobe cru — sem o embrulho do urllib. Foi assim que um
                # `ConnectionAbortedError: [Errno 103]` derrubou o processo em
                # produção. URLError e TimeoutError são subclasses de OSError,
                # então isto cobre os dois casos anteriores também.
                # JSONDecodeError entra para resposta truncada no meio.
                #
                # ErroPermanente NÃO é OSError, então continua subindo — token
                # inválido deve matar o processo com log claro, não virar
                # retry infinito.
                #
                # Pausa antes de repetir para não martelar a API quando a
                # causa persiste (ex.: 409 com outro processo ativo).
                log.warning("falha transitoria no polling (%s), tentando de novo", e)
                time.sleep(3)
                continue

            for upd in updates:
                self._offset = upd["update_id"] + 1

                msg = upd.get("message")
                if not msg or "text" not in msg:
                    # Foto, áudio, sticker, entrar/sair de grupo... ignoramos
                    # por ora. Áudio entra na V3 com whisper.cpp.
                    continue

                yield Mensagem(
                    remetente=str(msg["chat"]["id"]),
                    texto=msg["text"],
                )

    @contextlib.contextmanager
    def digitando(self, destinatario: str):
        """Mostra "digitando..." enquanto o bloco executa.

        Detalhe que quase todo mundo erra: o status do Telegram expira em ~5 s.
        Como nossa resposta leva ~8 s (12,8 t/s), mandar uma vez só faz o
        indicador sumir no meio e o usuário achar que travou. Por isso uma
        thread renova o status a cada 4 s até o bloco terminar.
        """
        parar = threading.Event()

        def renovar():
            while not parar.is_set():
                try:
                    self._chamar(
                        "sendChatAction",
                        {"chat_id": destinatario, "action": "typing"},
                        timeout=10,
                    )
                except Exception:
                    # Indicador é cosmético: NENHUMA falha aqui pode derrubar
                    # a resposta de verdade. Esta thread é daemon e roda em
                    # paralelo — uma exceção não tratada aqui morreria em
                    # silêncio e ainda assim seria ruído no log.
                    pass
                parar.wait(4)

        t = threading.Thread(target=renovar, daemon=True)
        t.start()
        try:
            yield
        finally:
            parar.set()

    def enviar(self, destinatario: str, texto: str) -> None:
        try:
            self._chamar(
                "sendMessage",
                {"chat_id": destinatario, "text": texto},
                timeout=20,
            )
        except ErroPermanente:
            raise
        except (OSError, RuntimeError, json.JSONDecodeError) as e:
            # Perder uma resposta é ruim, mas derrubar o bot é pior.
            # OSError cobre queda de conexão durante a leitura (Errno 103),
            # que não vem embrulhada em URLError.
            #
            # Este método é chamado também pelo Vigia, numa thread separada:
            # se a exceção subisse, mataria a thread de avisos em silêncio —
            # e um assistente que para de avisar sem reclamar é o pior modo
            # de falha possível.
            log.error("nao consegui enviar para %s: %s", destinatario, e)
