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
from calendario import Agenda, proximos, total_a_pagar
from dominio import FUSO, Compromisso, Tipo
from llm import LLM, ErroLLM

log = logging.getLogger(__name__)

AJUDA = """Caipora — assistente local 🔥

*Agendar* (basta escrever)
  pagar aluguel todo dia 5, R$ 1.850
  reunião com o time toda segunda 10h
  dentista amanhã 14h
  me lembra em 2 horas de ligar pro João

*Consultar*
  /agenda      próximos compromissos
  /contas      pagamentos e total do mês
  /hoje        só o dia de hoje
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

    # ---------------------------------------------------------------- consultas

    def _listar(self, remetente: str, dias: int = 60, titulo: str = "Próximos") -> str:
        pares = proximos(self._agenda, agora=self._agora(), dias=dias)
        if not pares:
            return "Nada agendado."
        self._ultima_lista[remetente] = [c for c, _ in pares]
        linhas = [f"{i}. {c.humano(oc)}" for i, (c, oc) in enumerate(pares, 1)]
        return f"📅 *{titulo}*\n" + "\n".join(linhas)

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

    def _tentar_agendar(self, remetente: str, texto: str) -> str | None:
        try:
            comp, avisos = analisar(texto, agora=self._agora())
        except NaoEntendi:
            return None

        self._pendente[remetente] = comp

        msg = f"Confirma?\n\n{comp.humano()}"
        for a in avisos:
            msg += f"\n⚠️ {a}"
        if comp.tipo is Tipo.PAGAMENTO:
            dias = [d for d in comp.avisos_dias if d > 0]
            if dias:
                msg += f"\n🔔 aviso {dias[0]} dia(s) antes e no dia"
        return msg + "\n\n(sim / não)"

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
