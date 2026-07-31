"""
Vigia: o que transforma o Caipora de banco de dados em assistente.

Sem isto, o sistema só responde quando perguntado — e lembrete que não avisa
não é lembrete.

DESENHO: laço próprio numa thread, acordando a cada minuto, em vez de
`termux-job-scheduler`.

Por que não o JobScheduler do Android, que eu mesmo recomendei antes: ele
existe para acordar um app que NÃO está rodando. Aqui o processo do bot já
está permanentemente de pé (supervisionado pelo runit, com wake-lock ativo)
porque precisa manter o long polling do Telegram. Dado isso, um laço interno
é mais simples, mais preciso e não depende de outro app (`termux-api`) nem da
janela mínima de 15 minutos que o JobScheduler impõe.

O JobScheduler voltaria a fazer sentido se algum dia o bot passasse a webhook
e deixasse de ter processo residente.

DEDUPLICAÇÃO: cada aviso enviado grava a chave da ocorrência em
`avisado_em`. Sem isso, um laço que roda a cada minuto manda o mesmo aviso
60 vezes por hora.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from calendario import Agenda
from dominio import FUSO, Compromisso, Tipo

log = logging.getLogger(__name__)

# Uma checagem por minuto: precisão suficiente para lembrete e barata o
# bastante para não pesar na bateria (é só leitura de um JSON pequeno).
INTERVALO_S = 60

# Tolerância para trás: se o aparelho ficou suspenso ou o processo reiniciou,
# ainda entregamos um aviso atrasado em até 30 min. Melhor avisar tarde que
# não avisar — mas não tão tarde que vire ruído sem sentido.
ATRASO_MAX = timedelta(minutes=30)


def _chave(quando: datetime, dias_antes: int) -> str:
    """Identidade de um aviso: ocorrência + qual dos avisos dela."""
    return f"{quando.isoformat()}#{dias_antes}"


class Vigia:
    def __init__(self, agenda: Agenda, enviar, destinatarios: list[str]):
        """`enviar` é uma função (destinatario, texto) -> None.

        Injetada em vez de receber o Canal inteiro: o vigia só precisa
        empurrar texto, e assim ele fica testável sem Telegram nenhum.
        """
        self._agenda = agenda
        self._enviar = enviar
        self._destinatarios = destinatarios
        self._parar = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ ciclo

    def iniciar(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._laco, daemon=True, name="vigia")
        self._thread.start()
        log.info("vigia iniciado (checagem a cada %ds)", INTERVALO_S)

    def parar(self) -> None:
        self._parar.set()

    def _laco(self) -> None:
        while not self._parar.is_set():
            try:
                self.checar()
            except Exception:
                # Uma falha aqui nunca deve derrubar a thread — se ela morrer,
                # o assistente para de avisar silenciosamente, que é o pior
                # modo de falha possível para este componente.
                log.exception("erro no ciclo do vigia")
            self._parar.wait(INTERVALO_S)

    # ------------------------------------------------------------------ lógica

    def pendentes(self, agora: datetime) -> list[tuple[Compromisso, datetime, int, str]]:
        """Avisos que deveriam ter sido enviados até `agora` e ainda não foram.

        Devolve (compromisso, ocorrência, dias_antes, chave).
        """
        saida = []
        for comp in self._agenda.todos():
            # Janela ampla o suficiente para pegar o aviso antecipado mais
            # distante (pagamento avisa 2 dias antes).
            maior_antecedencia = max(comp.avisos_dias or [0])
            limite = agora + timedelta(days=maior_antecedencia + 1)

            for ocorrencia in comp.ocorrencias(
                agora - ATRASO_MAX - timedelta(days=maior_antecedencia),
                limite,
                maximo=8,
            ):
                for dias in comp.avisos_dias or [0]:
                    momento_aviso = ocorrencia - timedelta(days=dias)
                    if not (agora - ATRASO_MAX <= momento_aviso <= agora):
                        continue
                    chave = _chave(ocorrencia, dias)
                    if comp.avisado_em == chave or chave in comp.avisado_em.split("|"):
                        continue
                    saida.append((comp, ocorrencia, dias, chave))
        return saida

    def checar(self, agora: datetime | None = None) -> int:
        agora = agora or datetime.now(FUSO)
        enviados = 0

        for comp, ocorrencia, dias, chave in self.pendentes(agora):
            texto = self._formatar(comp, ocorrencia, dias)
            for dest in self._destinatarios:
                self._enviar(dest, texto)
            # Mantém um histórico curto de chaves: com recorrência, a chave
            # anterior não pode ser esquecida ou o aviso repete no próximo
            # ciclo. Guardamos as 5 últimas — suficiente e não cresce sem fim.
            anteriores = [k for k in comp.avisado_em.split("|") if k]
            comp.avisado_em = "|".join((anteriores + [chave])[-5:])
            self._agenda.atualizar(comp)
            enviados += 1
            log.info("aviso enviado: %s (%s, %d dia(s) antes)", comp.titulo, ocorrencia, dias)

        return enviados

    @staticmethod
    def _formatar(comp: Compromisso, ocorrencia: datetime, dias: int) -> str:
        if comp.tipo is Tipo.PAGAMENTO:
            if dias > 0:
                cabeca = f"💰 *Vence em {dias} dia{'s' if dias > 1 else ''}*"
            else:
                cabeca = "💰 *Vence hoje*"
            corpo = f"{comp.titulo}"
            if comp.valor_centavos is not None:
                corpo += f" — {comp.valor_fmt()}"
            return f"{cabeca}\n{corpo}\n{ocorrencia:%d/%m}"

        if comp.tipo is Tipo.REUNIAO:
            return (
                f"👥 *Agora*\n{comp.titulo}\n{ocorrencia:%H:%M}"
                + (f" ({comp.duracao_min}min)" if comp.duracao_min else "")
            )

        return f"🔔 *Lembrete*\n{comp.titulo}"
