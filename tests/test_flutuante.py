"""
Testes do lembrete flutuante — a modalidade "demanda adiável".

"lembra de estudar cálculo" não pertence a um minuto do calendário: pertence a
uma janela do dia. Antes disto o parser recusava a frase inteira, e o usuário
que quisesse registrar a pendência tinha de inventar um horário falso.

O risco desta modalidade é virar spam: um lembrete que não tem hora pode
disparar em QUALQUER hora. As três travas que este arquivo protege são o
espaçamento mínimo entre cutucadas, o teto diário, e o silêncio quando há
compromisso marcado por perto. Sem elas o usuário desliga o assistente — e
assistente desligado é pior que nenhum.

Rodar:  python tests/test_flutuante.py
"""

import sys
import tempfile
from datetime import datetime
from functools import partial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from analise import analisar  # noqa: E402
from calendario import AgendaHibrida, AgendaLocal, flutuantes, proximos  # noqa: E402
from dominio import FUSO, Compromisso, Tipo  # noqa: E402
import roteador as _roteador  # noqa: E402
from vigia import Vigia  # noqa: E402


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=FUSO)


AGORA = dt("2026-07-29 10:00")
Roteador = partial(_roteador.Roteador, relogio=lambda: AGORA)


class LLMProibido:
    def conversar(self, remetente, texto):
        raise AssertionError(f"LLM chamado indevidamente com {texto!r}")

    def esquecer(self, remetente):
        pass


class Prova:
    def __init__(self):
        self.falhas = 0

    def checa(self, desc, cond, extra=""):
        if cond:
            print(f"ok     {desc}")
        else:
            print(f"FALHA  {desc} {extra}")
            self.falhas += 1

    def igual(self, desc, obtido, esperado):
        self.checa(desc, obtido == esperado, f"\n       {obtido!r} != {esperado!r}")


def main() -> int:
    p = Prova()

    with tempfile.TemporaryDirectory() as tmp:
        raiz = Path(tmp)

        # ------------------------------------------------------- criação
        comp, avisos = analisar("me lembra de estudar calculo", agora=AGORA)
        p.checa("frase sem hora vira flutuante", comp.eh_flutuante)
        p.igual("titulo limpo", comp.titulo, "Estudar calculo")
        p.igual("tipo", comp.tipo, Tipo.LEMBRETE)
        # Não ter hora é a modalidade escolhida, não um problema a reportar —
        # a confirmação já mostra "quando der".
        p.igual("nao emite alerta por nao ter hora", avisos, [])
        p.checa("sem avisos ancorados", comp.avisos == [], f"({comp.avisos})")
        p.checa("humano diz que e sem hora", "quando der" in comp.humano())

        # ------------------------------------------- não polui a agenda
        ag = AgendaLocal(raiz / "a.json")
        ag.criar(Compromisso.flutuante("Estudar calculo", agora=dt("2026-07-30 06:00")))
        ag.criar(Compromisso.nova(
            Tipo.REUNIAO, "Daily", dt("2026-07-30 12:00"), agora=dt("2026-07-29 20:00")
        ))
        p.igual("flutuante fica fora da /agenda",
                [c.titulo for c, _ in proximos(ag, agora=dt("2026-07-30 07:00"))],
                ["Daily"])
        p.igual("flutuante aparece na lista propria",
                [c.titulo for c in flutuantes(ag)], ["Estudar calculo"])

        # ------------------------------------------------ ritmo da cutucada
        vigia = Vigia(ag, lambda d, t: None, ["u1"])
        alvo = flutuantes(ag)[0]

        p.checa("nao cutuca logo apos criar",
                vigia._proxima_cutucada(dt("2026-07-30 07:00")) is None)
        p.checa("nao cutuca fora da janela (madrugada)",
                vigia._proxima_cutucada(dt("2026-07-30 04:00")) is None)
        p.checa("nao cutuca em cima de compromisso marcado",
                vigia._proxima_cutucada(dt("2026-07-30 12:05")) is None)

        escolhido = vigia._proxima_cutucada(dt("2026-07-30 09:30"))
        p.checa("cutuca depois do espacamento, na janela",
                escolhido is not None and escolhido.titulo == "Estudar calculo")

        # Registrada a primeira, a segunda só vem 3h depois.
        vigia._registrar_cutucada(escolhido, dt("2026-07-30 09:30"))
        p.checa("nao repete meia hora depois",
                vigia._proxima_cutucada(dt("2026-07-30 10:00")) is None)
        p.checa("volta a cutucar 3h depois",
                vigia._proxima_cutucada(dt("2026-07-30 13:00")) is not None)

        # -------------------------------------------------- teto diário
        ag2 = AgendaLocal(raiz / "b.json")
        ag2.criar(Compromisso.flutuante(
            "Comprar cafe", janela="06:00-23:00", agora=dt("2026-07-30 06:00")
        ))
        v2 = Vigia(ag2, lambda d, t: None, ["u1"])

        horas = ["09:01", "12:02", "15:03", "18:04"]
        cutucadas = []
        for h in horas:
            alvo = v2._proxima_cutucada(dt(f"2026-07-30 {h}"))
            if alvo is not None:
                v2._registrar_cutucada(alvo, dt(f"2026-07-30 {h}"))
                cutucadas.append(h)
        p.igual("no maximo 3 cutucadas por dia", cutucadas, ["09:01", "12:02", "15:03"])
        p.checa("contador reseta no dia seguinte",
                v2._proxima_cutucada(dt("2026-07-31 09:00")) is not None)

        # ------------------------------------------------------- feito
        pendente = flutuantes(ag2)[0]
        pendente.feito = True
        ag2.atualizar(pendente)
        p.igual("marcado como feito some da lista", flutuantes(ag2), [])
        p.checa("marcado como feito nao cutuca mais",
                v2._proxima_cutucada(dt("2026-07-31 09:00")) is None)

        # --------------------------------------------- comandos do roteador
        ag3 = AgendaLocal(raiz / "c.json")
        rot = Roteador(LLMProibido(), ag3)

        resposta = rot.responder("u1", "me lembra de ligar pro contador")
        p.checa("roteador propoe pendencia solta",
                "quando der" in resposta and "sim / não" in resposta, f"({resposta!r})")
        rot.responder("u1", "sim")
        p.igual("gravou a pendencia", [c.titulo for c in flutuantes(ag3)],
                ["Ligar pro contador"])

        lista = rot.responder("u1", "/lembretes")
        p.checa("/lembretes numera as pendencias",
                "1. Ligar pro contador" in lista, f"({lista!r})")

        p.checa("/feito 1 risca a pendencia",
                "Feito" in rot.responder("u1", "/feito 1"))
        p.igual("pendencia sumiu", flutuantes(ag3), [])

        # ------------------------------------------------- agenda híbrida
        # Flutuantes vão para o arquivo separado; marcados, para o principal.
        principal = AgendaLocal(raiz / "principal.json")
        soltos = AgendaLocal(raiz / "soltos.json")
        hibrida = AgendaHibrida(principal, soltos)

        hibrida.criar(Compromisso.flutuante("Solto", agora=AGORA))
        hibrida.criar(Compromisso.nova(Tipo.REUNIAO, "Marcado",
                                       dt("2026-07-30 10:00"), agora=AGORA))

        p.igual("marcado vai para o backend principal",
                [c.titulo for c in principal.todos()], ["Marcado"])
        p.igual("solto vai para o arquivo separado",
                [c.titulo for c in soltos.todos()], ["Solto"])
        p.igual("hibrida devolve os dois",
                sorted(c.titulo for c in hibrida.todos()), ["Marcado", "Solto"])

        alvo = soltos.todos()[0]
        p.checa("hibrida remove do arquivo certo", hibrida.remover(alvo.id))
        p.igual("removido mesmo", soltos.todos(), [])

    print(f"\n{'FALHOU' if p.falhas else 'TODOS OK'} ({p.falhas} falha(s))")
    return 1 if p.falhas else 0


if __name__ == "__main__":
    sys.exit(main())
