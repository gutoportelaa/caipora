"""
Abstração de canal de mensagens.

Por que isso existe: hoje falamos com o Telegram, amanhã talvez com o
WhatsApp (Evolution API) ou Z-API. Se o roteador conversar direto com a
biblioteca do Telegram, trocar de canal significa reescrever o roteador.

Com essa interface no meio, cada canal é uma implementação isolada e o
roteador não sabe nem quer saber de onde a mensagem veio.
"""

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Iterator, Protocol


@dataclass(frozen=True)
class Mensagem:
    """Uma mensagem recebida, já normalizada — sem formato de canal nenhum.

    `remetente` é o endereço de resposta (no Telegram, o chat_id). É opaco
    de propósito: o roteador só devolve isso para o canal, sem interpretar.
    """

    remetente: str
    texto: str


class Canal(Protocol):
    """Contrato que todo canal precisa cumprir. São só dois métodos."""

    def receber(self) -> Iterator[Mensagem]:
        """Gera mensagens conforme chegam. Bloqueia enquanto não há nada."""
        ...

    def enviar(self, destinatario: str, texto: str) -> None:
        """Envia texto de volta para um destinatário."""
        ...

    def digitando(self, destinatario: str) -> AbstractContextManager[None]:
        """Sinaliza "está digitando" enquanto o bloco executa.

        Faz parte do contrato porque a inferência local é lenta (~8 s) e sem
        esse retorno o usuário acha que o bot morreu. WhatsApp (Evolution) tem
        equivalente com presença "composing", então generaliza bem.
        """
        ...
