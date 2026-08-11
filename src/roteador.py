"""
Roteador: decide quem responde cada mensagem.

A regra central do projeto:

    o LLM nunca decide nada que possa ser decidido por código.

Não é preferência estética, é resultado de medição. Com gramática GBNF
garantindo JSON válido, o Qwen3 1.7B errou a DATA em 4 de 7 frases de teste
(colapsou "sexta"/"segunda"/"quarta" para "hoje"/"amanha"). O mesmo conjunto
no parser determinístico: 7/7. Ver docs/CALENDAR.md §1.

Divisão:
  L0  agendamento, consultas e comandos  -> código, <1ms, sem alucinação
  L1  conversa livre                     -> Qwen3 local, ~4s
"""

import logging
import re
import subprocess
from datetime import datetime, timedelta

from analise import NaoEntendi, analisar
from calendario import Agenda, flutuantes, proximos, total_a_pagar
from dominio import (
    FUSO,
    Compromisso,
    Tipo,
    antecedencia_min,
    aviso_eh_ancora,
    momento_do_aviso,
)
from llm import LLM, ErroLLM

log = logging.getLogger(__name__)

AJUDA = """Caipora — assistente local 🔥

*Compromisso marcado* 👥 — avisa 07:30, 30/15/10 min antes e na hora
  dentista amanhã 14h
  reunião com o time das 14h às 16h

*Horário reservado* 📌 — rotina; avisa 07:30 e 30 min antes
  academia de segunda a sexta das 7h às 8h
  aula de inglês toda terça e quinta 19h30
  psicólogo quinzenal quarta 15h

*Conta a pagar* 💰 — avisa 2 dias antes e no dia
  pagar aluguel todo dia 5, R$ 1.850
  internet dia 12 de todo mês, 129,90

*Lembrete* 🔔
  me lembra em 2 horas de ligar pro João      (na hora marcada)
  me lembra de estudar cálculo                (quando houver espaço)

*Consultar*
  /agenda      próximos compromissos
  /contas      pagamentos e total do mês
  /hoje        só o dia de hoje
  /lembretes   pendências sem hora marcada
  /feito N     risca a pendência N
  /cancelar N  cancela o item N da /agenda

*Sistema*
  /status    temperatura, memória, avisos
  /esquecer  limpa histórico da conversa

Qualquer outra coisa vai para o modelo local."""

SIM = re.compile(r"^\s*(sim|s|ok|isso|confirma(r)?|pode|claro|yes|y|👍)\s*[.!]?\s*$", re.I)
NAO = re.compile(r"^\s*(nao|não|n|cancela(r)?|deixa|no)\s*[.!]?\s*$", re.I)


def _temperatura_cpu() -> str:
    try:
        saida = subprocess.run(
            ["sh", "-c",
             "cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | sort -rn | head -1"],
            capture_output=True, text=True, timeout=5,
        )
        return f"{int(saida.stdout.strip()) / 1000:.1f} °C"
    except (ValueError, OSError, subprocess.SubprocessError):
        return "indisponível"


def _memoria_livre() -> str:
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for linha in f:
                if linha.startswith("MemAvailable:"):
                    return f"{int(linha.split()[1]) / 1024 / 1024:.2f} GB"
    except (OSError, ValueError):
        pass
    return "indisponível"


def _reais(centavos: int) -> str:
    return f"R$ {centavos / 100:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _resumo_avisos(comp: Compromisso) -> str:
    """Lista legível dos avisos, para o usuário conferir ANTES de confirmar.

    Mostrar isto na confirmação não é enfeite: a diferença entre "compromisso
    marcado" e "horário reservado" é justamente quantos avisos você vai
    receber, e essa é a hora de discordar da escolha do classificador.
    """
    if comp.eh_flutuante:
        return "🔔 quando houver espaço no dia"
    if not comp.avisos:
        return ""

    oc = comp.quando_dt
    partes: list[str] = []
    for aviso in comp.avisos:
        if aviso_eh_ancora(aviso):
            momento = momento_do_aviso(aviso, oc)
            partes.append(f"{momento:%H:%M}" if momento else aviso)
            continue
        mins = antecedencia_min(aviso, oc)
        if mins == 0:
            partes.append("na hora")
        elif mins % 1440 == 0:
            partes.append(f"{mins // 1440}d antes")
        elif mins % 60 == 0:
            partes.append(f"{mins // 60}h antes")
        else:
            partes.append(f"{mins}min antes")
    return "🔔 " + ", ".join(partes)


class Roteador:
    def __init__(self, llm: LLM, agenda: Agenda, relogio=None):
        self._llm = llm
        self._agenda = agenda
        # Relógio injetável. Sem isto, qualquer teste que use tempo relativo
        # ("em 2 horas") passa ou falha conforme a hora em que roda: perto da
        # meia-noite o compromisso cai no dia seguinte e some do /hoje.
        # Teste que depende do relógio da parede é teste que quebra sozinho.
        self._agora = relogio or (lambda: datetime.now(FUSO))
        # Compromisso aguardando confirmação, por remetente. Em memória: se o
        # processo cair, a confirmação se perde — comportamento correto.
        # Gravar com base em intenção de antes do reboot seria pior.
        self._pendente: dict[str, Compromisso] = {}
        # Espelha a última listagem mostrada, para /cancelar N referir-se ao
        # que o usuário viu. Recalcular a lista no cancelamento poderia
        # cancelar outro item se algo mudou no meio.
        self._ultima_lista: dict[str, list[Compromisso]] = {}
        # Lista separada para as pendências sem hora: elas não aparecem na
        # /agenda, então numerá-las junto faria /feito 2 e /cancelar 2
        # apontarem para coisas diferentes com a mesma cara.
        self._ultima_lista_flut: dict[str, list[Compromisso]] = {}

    # ---------------------------------------------------------------- consultas

    def _listar(self, remetente: str, dias: int = 60, titulo: str = "Próximos") -> str:
        pares = proximos(self._agenda, agora=self._agora(), dias=dias)
        soltos = flutuantes(self._agenda)

        if not pares and not soltos:
            return "Nada agendado."

        self._ultima_lista[remetente] = [c for c, _ in pares]
        linhas = [f"{i}. {c.humano(oc)}" for i, (c, oc) in enumerate(pares, 1)]
        saida = f"📅 *{titulo}*\n" + "\n".join(linhas) if pares else "Nada agendado."

        # As pendências soltas vêm depois e SEM numeração contínua: elas têm
        # a própria lista (/lembretes), e continuar a contagem faria
        # /cancelar 7 mirar algo que nem está na agenda.
        if soltos:
            self._ultima_lista_flut[remetente] = soltos
            saida += "\n\n🔔 *Sem hora marcada*\n" + "\n".join(
                f"{i}. {c.titulo}" for i, c in enumerate(soltos, 1)
            )
        return saida

    def _hoje(self, remetente: str) -> str:
        agora = self._agora()
        fim = agora.replace(hour=23, minute=59, second=59)
        pares = [
            (c, oc)
            for c in self._agenda.todos()
            for oc in c.ocorrencias(agora, fim, maximo=4)
        ]
        if not pares:
            return "Nada para hoje. 🎉"
        pares.sort(key=lambda p: p[1])
        self._ultima_lista[remetente] = [c for c, _ in pares]
        linhas = [f"{i}. {c.humano(oc)}" for i, (c, oc) in enumerate(pares, 1)]
        return "📅 *Hoje*\n" + "\n".join(linhas)

    def _contas(self, remetente: str) -> str:
        agora = self._agora()
        fim = agora + timedelta(days=30)
        pares = [
            (c, oc)
            for c in self._agenda.todos()
            if c.tipo is Tipo.PAGAMENTO
            for oc in c.ocorrencias(agora, fim, maximo=3)
        ]
        if not pares:
            return "Nenhuma conta nos próximos 30 dias."
        pares.sort(key=lambda p: p[1])
        self._ultima_lista[remetente] = [c for c, _ in pares]
        linhas = [f"{i}. {c.humano(oc)}" for i, (c, oc) in enumerate(pares, 1)]
        total = total_a_pagar(self._agenda, agora=agora, dias=30)
        rodape = f"\n\n*Total 30 dias:* {_reais(total)}" if total else ""
        return "💰 *Contas a pagar*\n" + "\n".join(linhas) + rodape

    def _cancelar(self, remetente: str, arg: str) -> str:
        lista = self._ultima_lista.get(remetente)
        if not lista:
            return "Veja /agenda primeiro, depois /cancelar N."
        try:
            idx = int(arg.strip())
        except ValueError:
            return "Use /cancelar N, com N da última lista mostrada."
        if not 1 <= idx <= len(lista):
            return f"Não existe item {idx} na última lista."
        alvo = lista[idx - 1]
        if self._agenda.remover(alvo.id):
            aviso = "\n(era recorrente — removi todas as ocorrências)" if alvo.recorrente else ""
            return f"🗑️ Cancelado: {alvo.titulo}{aviso}"
        return "Não consegui cancelar — veja /agenda de novo."

    # -------------------------------------------------------------- agendamento

    def _conflitos(self, comp: Compromisso) -> list[tuple[Compromisso, datetime]]:
        """Compromissos que ocupam tempo e colidem com a primeira ocorrência.

        Só reunião e reserva entram: pagamento e lembrete não bloqueiam nada,
        e avisar "sua conta de luz conflita com a academia" seria ruído.
        """
        if comp.eh_flutuante or not comp.ocupa_tempo:
            return []

        ini = comp.quando_dt
        fim = ini + timedelta(minutes=comp.duracao_min or 0)
        saida: list[tuple[Compromisso, datetime]] = []

        for outro in self._agenda.todos():
            if outro.eh_flutuante or not outro.ocupa_tempo or outro.id == comp.id:
                continue
            for oc in outro.ocorrencias(
                ini - timedelta(days=1), fim + timedelta(days=1), maximo=4
            ):
                outro_fim = oc + timedelta(minutes=outro.duracao_min or 0)
                # Sobreposição aberta nos extremos: terminar 10h e começar 10h
                # não é conflito.
                if oc < fim and ini < outro_fim:
                    saida.append((outro, oc))
                    break
        return saida

    def _tentar_agendar(self, remetente: str, texto: str) -> str | None:
        try:
            comp, avisos = analisar(texto, agora=self._agora())
        except NaoEntendi:
            return None

        self._pendente[remetente] = comp

        msg = f"Confirma?\n\n{comp.humano()}"
        for a in avisos:
            msg += f"\n⚠️ {a}"

        for outro, quando in self._conflitos(comp):
            msg += f"\n⚠️ choca com {outro.titulo} ({quando:%d/%m %H:%M})"

        resumo = _resumo_avisos(comp)
        if resumo:
            msg += f"\n{resumo}"
        return msg + "\n\n(sim / não)"

    # --------------------------------------------------------- flutuantes

    def _lembretes(self, remetente: str) -> str:
        pend = flutuantes(self._agenda)
        if not pend:
            return "Nenhuma pendência solta. 🎉"
        self._ultima_lista_flut[remetente] = pend
        linhas = [f"{i}. {c.titulo}" for i, c in enumerate(pend, 1)]
        return (
            "🔔 *Sem hora marcada*\n" + "\n".join(linhas)
            + "\n\n/feito N para riscar."
        )

    def _feito(self, remetente: str, arg: str) -> str:
        arg = arg.strip()
        pend = flutuantes(self._agenda)
        if not pend:
            return "Não há pendências soltas."

        if not arg:
            # Sem número: rende a última que foi cutucada. É a leitura certa
            # para quem responde direto à notificação — que é justamente
            # onde `/feito` sem argumento é oferecido.
            alvo = max(pend, key=lambda c: c.ultima_cutucada or "")
        else:
            lista = self._ultima_lista_flut.get(remetente) or pend
            try:
                idx = int(arg)
            except ValueError:
                return "Use /feito N, com N da lista de /lembretes."
            if not 1 <= idx <= len(lista):
                return f"Não existe item {idx}. Veja /lembretes."
            alvo = lista[idx - 1]

        alvo.feito = True
        self._agenda.atualizar(alvo)
        return f"✅ Feito: {alvo.titulo}"

    # ------------------------------------------------------------------ público

    def responder(self, remetente: str, texto: str) -> str:
        try:
            return self._responder(remetente, texto)
        except Exception as e:
            # A agenda pode ser remota (Google). Token expirado, rede fora ou
            # erro 5xx não podem derrubar o processo: o long polling e o vigia
            # morreriam junto. Reportamos e seguimos vivos.
            #
            # Em modo "Testing" o refresh token expira em 7 dias, então este
            # caminho é esperado, não hipotético.
            nome = type(e).__name__
            log.exception("falha ao responder %r", texto)
            if "Token" in nome:
                return (
                    "⚠️ A autorização do Google Calendar expirou.\n"
                    "Rode novamente: python scripts/autorizar_google.py\n"
                    "(em modo Testing o token vale 7 dias)"
                )
            return f"⚠️ Erro ao processar: {nome}. Veja /status."

    def _responder(self, remetente: str, texto: str) -> str:
        cmd = texto.strip()
        baixo = cmd.lower()

        # Confirmação pendente vem primeiro: enquanto há algo aguardando,
        # "sim" significa confirmar, não iniciar conversa com o modelo.
        pendente = self._pendente.get(remetente)
        if pendente is not None:
            if SIM.match(cmd):
                del self._pendente[remetente]
                criado = self._agenda.criar(pendente)
                log.info("criado: %s", criado.titulo)
                return f"✅ {criado.humano()}"
            if NAO.match(cmd):
                del self._pendente[remetente]
                return "Ok, não agendei."
            # Qualquer outra coisa abandona a confirmação e segue o fluxo
            # normal — melhor que prender o usuário num diálogo.
            del self._pendente[remetente]

        # ------------------------------------------------------------- nível L0
        if baixo in ("/start", "/ajuda", "/help"):
            return AJUDA

        if baixo == "/status":
            backend = type(self._agenda).__name__
            nome = {"AgendaGoogle": "Google Calendar",
                    "AgendaHibrida": "Google Calendar + soltos em disco",
                    "AgendaLocal": "local (JSON)"}.get(backend, backend)
            try:
                n = str(len(self._agenda.todos()))
            except Exception as e:
                n = f"erro ({e})"
            return (
                f"🌡️ Temperatura: {_temperatura_cpu()}\n"
                f"🧠 RAM livre: {_memoria_livre()}\n"
                f"📅 Agenda: {nome}\n"
                f"📋 Compromissos: {n}\n"
                f"🤖 Qwen3 1.7B Q4_K_M (-t 2)"
            )

        if baixo in ("/agenda", "/eventos"):
            return self._listar(remetente)

        if baixo in ("/hoje", "/dia"):
            return self._hoje(remetente)

        if baixo in ("/contas", "/pagamentos"):
            return self._contas(remetente)

        if baixo in ("/lembretes", "/pendencias", "/pendências"):
            return self._lembretes(remetente)

        if baixo.startswith("/feito"):
            return self._feito(remetente, cmd[len("/feito"):])

        if baixo.startswith("/cancelar"):
            return self._cancelar(remetente, cmd[len("/cancelar"):])

        if baixo == "/esquecer":
            self._llm.esquecer(remetente)
            return "Histórico limpo."

        if baixo.startswith("/agendar"):
            resto = cmd[len("/agendar"):].strip()
            if not resto:
                return "Use: /agendar dentista amanhã 14h"
            return self._tentar_agendar(remetente, resto) or (
                "Não identifiquei data e hora. Ex: dentista amanhã 14h"
            )

        if baixo.startswith("/"):
            # Comando desconhecido não vira conversa com o modelo — senão o
            # usuário digita /agendarr errado e recebe papo furado.
            return "Comando desconhecido. Use /ajuda."

        # Agendamento por frase natural. Só dispara com data/hora identificada
        # e sempre pede confirmação: frase real é mais bagunçada que teste.
        agendamento = self._tentar_agendar(remetente, cmd)
        if agendamento:
            return agendamento

        # ------------------------------------------------------------- nível L1
        try:
            return self._llm.conversar(remetente, cmd)
        except ErroLLM as e:
            log.error("falha no LLM: %s", e)
            return (
                "Não consegui falar com o modelo local agora. "
                "Verifique se o llama-server está de pé (/status)."
            )
