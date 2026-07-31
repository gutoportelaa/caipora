"""
Testes do roteador: agendamento, consultas e a fronteira com o LLM.

O `LLMProibido` lança exceção se for chamado. É o teste que garante
ESTRUTURALMENTE que agendamento e consultas não tocam o modelo — se alguém
no futuro rotear isso pelo LLM, o teste quebra.

Rodar:  python tests/test_roteador.py
"""

import sys
import tempfile
from datetime import datetime
from functools import partial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from calendario import AgendaLocal  # noqa: E402
from dominio import FUSO, Freq, Tipo  # noqa: E402
import roteador as _roteador  # noqa: E402

# Relógio FIXO: quarta-feira, 29/07/2026, 10:00 (-03:00).
#
# Sem isto o teste é uma bomba-relógio: "me lembra em 2 horas" depende da hora
# real, e depois das 22h o compromisso cai no dia seguinte e some do /hoje.
# Aconteceu — a suíte passou de manhã e falhou à noite, sem nenhuma mudança
# de código.
AGORA = datetime(2026, 7, 29, 10, 0, tzinfo=FUSO)
assert AGORA.weekday() == 2, "referencia precisa ser quarta-feira"

Roteador = partial(_roteador.Roteador, relogio=lambda: AGORA)


class LLMProibido:
    def conversar(self, remetente, texto):
        raise AssertionError(f"LLM chamado indevidamente com {texto!r}")

    def esquecer(self, remetente):
        pass


class LLMFalso(LLMProibido):
    def conversar(self, remetente, texto):
        return f"[modelo respondeu a {texto!r}]"


def main() -> int:
    falhas = 0

    def checa(desc, obtido, contem):
        nonlocal falhas
        if contem.lower() in obtido.lower():
            print(f"ok     {desc}")
        else:
            print(f"FALHA  {desc}\n       esperava conter {contem!r}\n       obtive {obtido!r}")
            falhas += 1

    def nao_contem(desc, obtido, proibido):
        nonlocal falhas
        if proibido.lower() not in obtido.lower():
            print(f"ok     {desc}")
        else:
            print(f"FALHA  {desc}: {obtido!r} contem {proibido!r}")
            falhas += 1

    with tempfile.TemporaryDirectory() as tmp:
        agenda = AgendaLocal(Path(tmp) / "a.json")
        r = Roteador(LLMProibido(), agenda)  # nada aqui pode chamar o LLM

        checa("/ajuda sem LLM", r.responder("u1", "/ajuda"), "Caipora")
        checa("agenda vazia", r.responder("u1", "/agenda"), "Nada agendado")
        checa("contas vazias", r.responder("u1", "/contas"), "Nenhuma conta")
        checa("hoje vazio", r.responder("u1", "/hoje"), "Nada para hoje")

        # ------------------------------------------------------- pagamento
        resp = r.responder("u1", "pagar aluguel todo dia 5, R$ 1.850")
        checa("pagamento pede confirmacao", resp, "confirma?")
        checa("pagamento mostra valor", resp, "1.850,00")
        checa("pagamento mostra recorrencia", resp, "todo dia 5")
        checa("pagamento anuncia aviso antecipado", resp, "antes")
        checa("confirma cria", r.responder("u1", "sim"), "✅")

        listagem = r.responder("u1", "/contas")
        checa("conta aparece em /contas", listagem, "Aluguel")
        checa("total do mes", listagem, "Total 30 dias")

        # ------------------------------------------------------- reuniao
        r.responder("u1", "reuniao com o time toda segunda 10h")
        checa("reuniao criada", r.responder("u1", "sim"), "✅")

        ag = r.responder("u1", "/agenda")
        checa("reuniao na agenda", ag, "Reuniao com o time")
        checa("aluguel tambem na agenda", ag, "Aluguel")

        # Reuniao nao deve aparecer em /contas
        nao_contem("reuniao fora de /contas", r.responder("u1", "/contas"), "time")

        # ------------------------------------------------------- lembrete
        r.responder("u1", "me lembra em 2 horas de ligar pro Joao")
        checa("lembrete criado", r.responder("u1", "sim"), "✅")
        checa("lembrete aparece hoje", r.responder("u1", "/hoje"), "Ligar pro Joao")

        # ------------------------------------------------------- recusa
        r.responder("u1", "dentista sexta 9h")
        checa("nao descarta", r.responder("u1", "não"), "não agendei")
        nao_contem("descartado nao foi criado", r.responder("u1", "/agenda"), "dentista")

        # ------------------------------------------------------- cancelamento
        antes = r.responder("u1", "/agenda")
        assert "1." in antes
        checa("cancelar 1", r.responder("u1", "/cancelar 1"), "Cancelado")
        checa("cancelar fora do range", r.responder("u1", "/cancelar 99"), "não existe")

        # cancelar sem listar antes
        r2 = Roteador(LLMProibido(), agenda)
        checa("cancelar sem lista", r2.responder("novo", "/cancelar 1"), "/agenda primeiro")

        # ------------------------------------------------------- comandos
        checa("comando desconhecido", r.responder("u1", "/xpto"), "desconhecido")

        # ------------------------------------------------------- fronteira LLM
        r3 = Roteador(LLMFalso(), agenda)
        checa("conversa livre usa LLM", r3.responder("u9", "por que o ceu e azul"),
              "modelo respondeu")
        checa("frase sem data vai ao LLM", r3.responder("u9", "marca o dentista"),
              "modelo respondeu")

        # ------------------------------------------------------- isolamento
        r4 = Roteador(LLMFalso(), agenda)
        r4.responder("a", "dentista amanha 9h")
        checa("sim de outro remetente nao confirma", r4.responder("b", "sim"),
              "modelo respondeu")

        # ------------------------------------------------ classificacao correta
        comps = {c.titulo: c for c in agenda.todos()}
        if "Aluguel" in comps:
            c = comps["Aluguel"]
            if c.tipo is Tipo.PAGAMENTO and c.freq is Freq.MENSAL and c.ancora == 5:
                print("ok     aluguel: tipo/freq/ancora corretos")
            else:
                print(f"FALHA  aluguel: {c.tipo} {c.freq} {c.ancora}")
                falhas += 1

    print(f"\n{'FALHOU' if falhas else 'TODOS OK'} ({falhas} falha(s))")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
