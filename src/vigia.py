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

TRÊS REGRAS QUE SÓ APARECERAM COM A ESCALADA DE MINUTOS
(antes, quando o aviso mais fino era "1 dia antes", nenhuma delas importava):

  1. TOLERÂNCIA PROPORCIONAL. Entregar "faltam 10 minutos" com 25 minutos de
     atraso é entregá-lo DEPOIS do compromisso começar — pior que não avisar,
     porque mente. A tolerância de cada aviso é limitada pela sua própria
     antecedência.

  2. NADA RETROATIVO NO CADASTRO. Agendar às 09:50 algo para as 10:00 não
     pode disparar "-30m", "-15m" e "-10m" de uma vez. Avisos cuja hora já
     tinha passado quando o compromisso nasceu são descartados.

  3. UMA MENSAGEM POR CICLO. Cinco avisos por compromisso significam que
     07:30 é o horário em que TODO o dia dispara ao mesmo tempo. Sem
     coalescência, o resumo matinal vira seis notificações seguidas — e o
     assistente que notifica demais é desligado, o que o torna pior que
     nenhum.

CUTUCADAS: lembretes flutuantes ("estudar cálculo") não têm hora. O vigia os
oferece dentro da janela do usuário, espaçados, no máximo três por dia e
nunca em cima de um compromisso marcado. Só somem quando marcados como feitos.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta

from calendario import Agenda
from dominio import (
    FUSO,
    Compromisso,
    Tipo,
    antecedencia_min,
    aviso_eh_ancora,
    descrever_aviso,
    momento_do_aviso,
)

log = logging.getLogger(__name__)

# Uma checagem por minuto: precisão suficiente para lembrete e barata o
# bastante para não pesar na bateria (é só leitura de um JSON pequeno).
INTERVALO_S = 60

# Tolerância para trás: se o aparelho ficou suspenso ou o processo reiniciou,
# ainda entregamos um aviso atrasado em até 30 min. Melhor avisar tarde que
# não avisar — mas não tão tarde que vire ruído sem sentido.
ATRASO_MAX = timedelta(minutes=30)

# Quantas chaves de aviso guardamos por compromisso. Com cinco avisos por
# ocorrência, as cinco do formato antigo não cobriam nem UMA ocorrência — o
# aviso mais velho era esquecido e repetia no ciclo seguinte.
MAX_CHAVES = 24
IDADE_MAX_CHAVE = timedelta(days=40)

# -------------------------------------------------------------- flutuantes
CUTUCADAS_MAX_DIA = 3
ESPACO_CUTUCADA = timedelta(hours=3)
# Não cutuca se houver compromisso marcado perto: quem está entrando numa
# reunião não quer ser lembrado de comprar café.
FOLGA_AGENDA = timedelta(minutes=15)


def _chave(quando: datetime, aviso: str) -> str:
    """Identidade de um aviso: ocorrência + qual dos avisos dela."""
    return f"{quando.isoformat()}#{aviso}"


def _tolerancia(aviso: str, ocorrencia: datetime) -> timedelta:
    """Quanto atraso ainda faz sentido para ESTE aviso.

    Um aviso de 10 minutos antes tolera no máximo 10 minutos de atraso — daí
    em diante ele já estaria falando do passado. Avisos na hora e de dias
    antes mantêm a tolerância cheia: chegar 20 min atrasado ainda informa.
    """
    if aviso_eh_ancora(aviso):
        return ATRASO_MAX
    mins = antecedencia_min(aviso, ocorrencia)
    if 0 < mins <= 60:
        return timedelta(minutes=mins)
    return ATRASO_MAX


def _podar(avisado_em: str, agora: datetime) -> list[str]:
    """Descarta chaves de ocorrências velhas e limita o tamanho."""
    vivas = []
    for chave in avisado_em.split("|"):
        if not chave:
            continue
        iso, _, _ = chave.partition("#")
        try:
            if agora - datetime.fromisoformat(iso) > IDADE_MAX_CHAVE:
                continue
        except ValueError:
            continue
        vivas.append(chave)
    return vivas[-MAX_CHAVES:]


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

    def pendentes(self, agora: datetime) -> list[tuple[Compromisso, datetime, str, str]]:
        """Avisos que deveriam ter sido enviados até `agora` e ainda não foram.

        Devolve (compromisso, ocorrência, aviso, chave).
        """
        saida = []
        for comp in self._agenda.todos():
            if comp.eh_flutuante or not comp.avisos:
                continue

            nascimento = comp.criado_em_dt
            enviadas = set(comp.avisado_em.split("|"))

            # Janela ampla o bastante para conter o aviso mais antecipado —
            # com pagamento avisando 2 dias antes, buscar só o dia de hoje
            # perderia o aviso inteiro.
            base = comp.quando_dt
            maior = max((antecedencia_min(a, base) for a in comp.avisos), default=0)
            recuo = timedelta(minutes=maior) + ATRASO_MAX

            for ocorrencia in comp.ocorrencias(
                agora - recuo, agora + recuo + timedelta(days=1), maximo=8
            ):
                for aviso in comp.avisos:
                    momento = momento_do_aviso(aviso, ocorrencia)
                    if momento is None:
                        # Aviso malformado: ignora este, não derruba o resto.
                        continue
                    # Um aviso nunca pode disparar DEPOIS daquilo que anuncia.
                    # Só acontece com âncora: o resumo das 07:30 não tem o que
                    # dizer sobre a academia das 07:00, que já aconteceu.
                    if momento > ocorrencia:
                        continue
                    if not (agora - _tolerancia(aviso, ocorrencia) <= momento <= agora):
                        continue
                    # Regra 2: nada retroativo. O aviso já tinha passado
                    # quando o compromisso foi criado.
                    if nascimento and momento < nascimento <= ocorrencia:
                        continue
                    chave = _chave(ocorrencia, aviso)
                    if chave in enviadas:
                        continue
                    saida.append((comp, ocorrencia, aviso, chave))

        saida.sort(key=lambda p: p[1])
        return saida

    def checar(self, agora: datetime | None = None) -> int:
        """Um ciclo. Devolve quantas mensagens saíram."""
        agora = agora or datetime.now(FUSO)
        mensagens: list[str] = []

        pend = self.pendentes(agora)
        if pend:
            mensagens.append(self._montar(pend))
            self._marcar(pend, agora)

        cutucada = self._proxima_cutucada(agora)
        if cutucada is not None:
            mensagens.append(self._formatar_cutucada(cutucada))
            self._registrar_cutucada(cutucada, agora)

        enviadas = 0
        for texto in mensagens:
            for dest in self._destinatarios:
                self._enviar(dest, texto)
                enviadas += 1

        for comp, ocorrencia, aviso, _ in pend:
            log.info("aviso enviado: %s (%s, %s)", comp.titulo, ocorrencia, aviso)

        return enviadas

    # -------------------------------------------------------------- marcação

    def _marcar(self, pend, agora: datetime) -> None:
        """Grava as chaves enviadas, um `atualizar` por compromisso.

        Agrupado de propósito: com cinco avisos disparando no mesmo minuto,
        gravar um a um seriam cinco escritas (ou cinco PATCHes no Google)
        para o mesmo objeto.
        """
        por_comp: dict[int, tuple[Compromisso, list[str]]] = {}
        for comp, _, _, chave in pend:
            _, chaves = por_comp.setdefault(id(comp), (comp, []))
            chaves.append(chave)

        for comp, chaves in por_comp.values():
            comp.avisado_em = "|".join(_podar(comp.avisado_em, agora) + chaves)
            self._agenda.atualizar(comp)

    # ------------------------------------------------------------ flutuantes

    def _proxima_cutucada(self, agora: datetime) -> Compromisso | None:
        """Um lembrete flutuante que caiba agora — no máximo um por ciclo."""
        if self._ocupado(agora):
            return None

        candidatos: list[tuple[datetime, Compromisso]] = []

        for comp in self._agenda.todos():
            if not comp.eh_flutuante or comp.feito:
                continue

            inicio, fim = comp.janela_horas()
            if not (inicio <= agora.time() <= fim):
                continue

            # Referência para o espaçamento: a última cutucada ou, na
            # primeira vez, o nascimento — cutucar no segundo seguinte ao
            # cadastro seria eco, não lembrete.
            ultima = comp.ultima_cutucada or comp.criado_em
            try:
                marco = datetime.fromisoformat(ultima) if ultima else None
            except ValueError:
                marco = None
            if marco is not None and agora - marco < ESPACO_CUTUCADA:
                continue

            if (
                comp.ultima_cutucada
                and marco is not None
                and marco.date() == agora.date()
                and comp.cutucadas >= CUTUCADAS_MAX_DIA
            ):
                continue

            candidatos.append((marco or agora, comp))

        if not candidatos:
            return None
        # O mais esquecido primeiro.
        candidatos.sort(key=lambda p: p[0])
        return candidatos[0][1]

    def _ocupado(self, agora: datetime) -> bool:
        """Há compromisso marcado colado no momento atual?"""
        for comp in self._agenda.todos():
            if comp.eh_flutuante or not comp.ocupa_tempo:
                continue
            for oc in comp.ocorrencias(agora - FOLGA_AGENDA, agora + FOLGA_AGENDA, maximo=3):
                fim = oc + timedelta(minutes=comp.duracao_min or 0)
                if oc - FOLGA_AGENDA <= agora <= fim + FOLGA_AGENDA:
                    return True
        return False

    def _registrar_cutucada(self, comp: Compromisso, agora: datetime) -> None:
        try:
            anterior = datetime.fromisoformat(comp.ultima_cutucada)
            mesmo_dia = anterior.date() == agora.date()
        except ValueError:
            mesmo_dia = False
        comp.cutucadas = comp.cutucadas + 1 if mesmo_dia else 1
        comp.ultima_cutucada = agora.isoformat()
        self._agenda.atualizar(comp)
        log.info("cutucada: %s (%dª do dia)", comp.titulo, comp.cutucadas)

    @staticmethod
    def _formatar_cutucada(comp: Compromisso) -> str:
        return (
            f"🔔 *Quando puder*\n{comp.titulo}\n\n"
            "(`/feito` para riscar da lista)"
        )

    # ------------------------------------------------------------ formatação

    def _montar(self, pend) -> str:
        """Uma mensagem para todos os avisos do ciclo (regra 3)."""
        if len(pend) == 1:
            comp, ocorrencia, aviso, _ = pend[0]
            return self._formatar(comp, ocorrencia, aviso)

        # Quando tudo que disparou é âncora, isto é o resumo matinal — e
        # merece se anunciar como tal em vez de "4 avisos".
        so_ancora = all(aviso_eh_ancora(a) for _, _, a, _ in pend)
        cabeca = "🌅 *Seu dia*" if so_ancora else f"🔔 *{len(pend)} avisos*"

        linhas = [cabeca, ""]
        for comp, ocorrencia, aviso, _ in pend:
            linhas.append(self._resumir(comp, ocorrencia, aviso))
        return "\n".join(linhas)

    @staticmethod
    def _resumir(comp: Compromisso, ocorrencia: datetime, aviso: str) -> str:
        """Uma linha por aviso, para a mensagem agrupada."""
        icone = comp.ICONES[comp.tipo]
        quando = descrever_aviso(aviso, ocorrencia)

        if comp.tipo is Tipo.PAGAMENTO:
            valor = f" — {comp.valor_fmt()}" if comp.valor_centavos is not None else ""
            return f"{icone} {comp.titulo}{valor}  ·  vence {ocorrencia:%d/%m}"

        corpo = f"{icone} {comp.titulo}  ·  {ocorrencia:%H:%M}"
        return corpo if quando == "hoje" else f"{corpo} ({quando})"

    @staticmethod
    def _formatar(comp: Compromisso, ocorrencia: datetime, aviso: str) -> str:
        if comp.tipo is Tipo.PAGAMENTO:
            dias = antecedencia_min(aviso, ocorrencia) // 1440
            if dias > 0:
                cabeca = f"💰 *Vence em {dias} dia{'s' if dias > 1 else ''}*"
            else:
                cabeca = "💰 *Vence hoje*"
            corpo = f"{comp.titulo}"
            if comp.valor_centavos is not None:
                corpo += f" — {comp.valor_fmt()}"
            return f"{cabeca}\n{corpo}\n{ocorrencia:%d/%m}"

        if comp.tipo in (Tipo.REUNIAO, Tipo.RESERVA):
            icone = comp.ICONES[comp.tipo]
            quando = descrever_aviso(aviso, ocorrencia)
            cabeca = "Hoje" if quando == "hoje" else quando.capitalize()
            corpo = f"{icone} *{cabeca}*\n{comp.titulo}\n{ocorrencia:%H:%M}"
            if comp.duracao_min:
                fim = ocorrencia + timedelta(minutes=comp.duracao_min)
                corpo += f"–{fim:%H:%M}"
            return corpo

        return f"🔔 *Lembrete*\n{comp.titulo}"
