"""
Testes de conversão do backend Google — sem rede.

Exercita só as funções puras (domínio <-> formato do Calendar). A parte de
rede depende de token e é validada à mão.

O que mais importa: a RRULE de vencimento mensal precisa se comportar como o
backend local, senão o mesmo compromisso cai em datas diferentes conforme o
backend — o tipo de divergência que ninguém percebe até perder uma conta.

Rodar:  python tests/test_agenda_google.py
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agenda_google import AgendaGoogle  # noqa: E402
from dominio import FUSO, Compromisso, Freq, Tipo  # noqa: E402


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=FUSO)


def main() -> int:
    falhas = 0

    def checa(desc, obtido, esperado):
        nonlocal falhas
        if obtido == esperado:
            print(f"ok     {desc}")
        else:
            print(f"FALHA  {desc}\n       esperava {esperado!r}\n       obtive   {obtido!r}")
            falhas += 1

    rrule = AgendaGoogle._rrule

    # ------------------------------------------------------------------ RRULE
    checa("unica nao gera RRULE",
          rrule(Compromisso.nova(Tipo.REUNIAO, "x", dt("2026-08-05 10:00"))), [])

    checa("diaria",
          rrule(Compromisso.nova(Tipo.LEMBRETE, "x", dt("2026-08-05 08:00"),
                                 freq=Freq.DIARIA)),
          ["RRULE:FREQ=DAILY"])

    checa("semanal segunda",
          rrule(Compromisso.nova(Tipo.REUNIAO, "x", dt("2026-08-03 10:00"),
                                 freq=Freq.SEMANAL, ancora=0)),
          ["RRULE:FREQ=WEEKLY;BYDAY=MO"])

    checa("semanal sexta",
          rrule(Compromisso.nova(Tipo.REUNIAO, "x", dt("2026-08-07 10:00"),
                                 freq=Freq.SEMANAL, ancora=4)),
          ["RRULE:FREQ=WEEKLY;BYDAY=FR"])

    checa("mensal dia 5",
          rrule(Compromisso.nova(Tipo.PAGAMENTO, "x", dt("2026-08-05 09:00"),
                                 freq=Freq.MENSAL, ancora=5)),
          ["RRULE:FREQ=MONTHLY;BYMONTHDAY=5"])

    # Dias 29-31 exigem o idioma com BYSETPOS, senão divergem do backend local
    for dia in (29, 30, 31):
        checa(f"mensal dia {dia} usa BYSETPOS",
              rrule(Compromisso.nova(Tipo.PAGAMENTO, "x", dt("2026-08-05 09:00"),
                                     freq=Freq.MENSAL, ancora=dia)),
              [f"RRULE:FREQ=MONTHLY;BYMONTHDAY={dia},-1;BYSETPOS=1"])

    checa("anual",
          rrule(Compromisso.nova(Tipo.LEMBRETE, "x", dt("2026-03-10 09:00"),
                                 freq=Freq.ANUAL)),
          ["RRULE:FREQ=YEARLY"])

    # -------------------------------------------------------- ida e volta
    original = Compromisso.nova(
        Tipo.PAGAMENTO, "Aluguel", dt("2026-08-05 09:00"),
        valor_centavos=185000, freq=Freq.MENSAL, ancora=5,
    )
    ev = AgendaGoogle._para_evento(AgendaGoogle.__new__(AgendaGoogle), original)

    checa("evento leva o titulo", ev["summary"], "Aluguel")
    checa("evento leva RRULE", ev["recurrence"], ["RRULE:FREQ=MONTHLY;BYMONTHDAY=5"])
    priv = ev["extendedProperties"]["private"]
    checa("propriedade tipo", priv["caipora_tipo"], "pagamento")
    checa("propriedade valor", priv["caipora_valor"], "185000")
    checa("propriedade ancora", priv["caipora_ancora"], "5")
    checa("pagamento tem lembretes nativos", "reminders" in ev, True)

    ev["id"] = "abc123"
    volta = AgendaGoogle._de_evento(ev)
    checa("volta: tipo", volta.tipo, Tipo.PAGAMENTO)
    checa("volta: titulo", volta.titulo, "Aluguel")
    checa("volta: valor", volta.valor_centavos, 185000)
    checa("volta: freq", volta.freq, Freq.MENSAL)
    checa("volta: ancora", volta.ancora, 5)
    checa("volta: avisos", volta.avisos, ["-2d", "0"])
    checa("volta: id", volta.id, "abc123")

    # Evento de dia inteiro criado fora do Caipora deve ser ignorado, nao
    # virar compromisso com horario inventado.
    checa("dia inteiro e ignorado",
          AgendaGoogle._de_evento({"summary": "Feriado", "start": {"date": "2026-09-07"}}),
          None)

    # ---------------------------------- paridade de datas com backend local
    # Vencimento dia 31: as datas do backend local precisam bater com o que a
    # RRULE do Google produz (dia 31 ou ultimo dia do mes).
    d31 = Compromisso.nova(Tipo.PAGAMENTO, "Cartao", dt("2026-01-31 09:00"),
                           freq=Freq.MENSAL, ancora=31)
    datas = [o.strftime("%m-%d") for o in
             d31.ocorrencias(dt("2026-01-01 00:00"), dt("2026-06-30 23:59"))]
    checa("local: dia 31 cai no ultimo dia de cada mes",
          datas, ["01-31", "02-28", "03-31", "04-30", "05-31", "06-30"])

    print(f"\n{'FALHOU' if falhas else 'TODOS OK'} ({falhas} falha(s))")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
