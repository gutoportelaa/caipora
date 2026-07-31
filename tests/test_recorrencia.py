"""
Testes de recorrência.

O caso que mais quebra sistema de cobrança: vencimento no dia 31 em meses que
não têm dia 31. Pular o mês significa perder um vencimento — inaceitável.
A regra adotada é grudar no último dia do mês.

Rodar:  python tests/test_recorrencia.py
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dominio import FUSO, Compromisso, Freq, Tipo  # noqa: E402


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=FUSO)


def main() -> int:
    falhas = 0

    def checa(desc, obtido, esperado):
        nonlocal falhas
        o = obtido.strftime("%Y-%m-%d %H:%M") if obtido else None
        if o == esperado:
            print(f"ok     {desc} -> {o}")
        else:
            print(f"FALHA  {desc}\n       esperava {esperado}, obtive {o}")
            falhas += 1

    # ------------------------------------------------------------------ mensal
    mensal = Compromisso.nova(
        Tipo.PAGAMENTO, "Aluguel", dt("2026-08-05 09:00"), freq=Freq.MENSAL, ancora=5
    )
    checa("mensal: proxima apos 01/08", mensal.proxima_ocorrencia(dt("2026-08-01 00:00")),
          "2026-08-05 09:00")
    checa("mensal: proxima apos o proprio dia", mensal.proxima_ocorrencia(dt("2026-08-05 10:00")),
          "2026-09-05 09:00")
    checa("mensal: virada de ano", mensal.proxima_ocorrencia(dt("2026-12-10 00:00")),
          "2027-01-05 09:00")

    # ------------------------------------------- mensal dia 31 (caso crítico)
    d31 = Compromisso.nova(
        Tipo.PAGAMENTO, "Cartao", dt("2026-01-31 09:00"), freq=Freq.MENSAL, ancora=31
    )
    checa("dia 31: janeiro", d31.proxima_ocorrencia(dt("2026-01-01 00:00")),
          "2026-01-31 09:00")
    # 2026 nao e bissexto: fevereiro tem 28 dias
    checa("dia 31: fevereiro cai no dia 28", d31.proxima_ocorrencia(dt("2026-02-01 00:00")),
          "2026-02-28 09:00")
    checa("dia 31: abril cai no dia 30", d31.proxima_ocorrencia(dt("2026-04-01 00:00")),
          "2026-04-30 09:00")
    # 2028 e bissexto
    d31b = Compromisso.nova(
        Tipo.PAGAMENTO, "Cartao", dt("2028-01-31 09:00"), freq=Freq.MENSAL, ancora=31
    )
    checa("dia 31: fevereiro bissexto cai no 29",
          d31b.proxima_ocorrencia(dt("2028-02-01 00:00")), "2028-02-29 09:00")

    # ----------------------------------------------------------------- semanal
    sem = Compromisso.nova(
        Tipo.REUNIAO, "Daily", dt("2026-08-03 10:00"), freq=Freq.SEMANAL, ancora=0
    )
    checa("semanal: primeira", sem.proxima_ocorrencia(dt("2026-07-29 00:00")),
          "2026-08-03 10:00")
    checa("semanal: seguinte", sem.proxima_ocorrencia(dt("2026-08-03 11:00")),
          "2026-08-10 10:00")

    # ------------------------------------------------------------------ diaria
    dia = Compromisso.nova(
        Tipo.LEMBRETE, "Remedio", dt("2026-07-30 08:00"), freq=Freq.DIARIA
    )
    checa("diaria: seguinte", dia.proxima_ocorrencia(dt("2026-07-30 09:00")),
          "2026-07-31 08:00")
    checa("diaria: muito depois", dia.proxima_ocorrencia(dt("2026-09-15 09:00")),
          "2026-09-16 08:00")

    # ------------------------------------------------------------------- anual
    ano = Compromisso.nova(
        Tipo.LEMBRETE, "IPVA", dt("2026-03-10 09:00"), freq=Freq.ANUAL
    )
    checa("anual: seguinte", ano.proxima_ocorrencia(dt("2026-06-01 00:00")),
          "2027-03-10 09:00")

    # ------------------------------------------------------------------- unica
    uni = Compromisso.nova(Tipo.REUNIAO, "Dentista", dt("2026-07-30 14:00"))
    checa("unica: futura", uni.proxima_ocorrencia(dt("2026-07-29 00:00")),
          "2026-07-30 14:00")
    if uni.proxima_ocorrencia(dt("2026-08-01 00:00")) is not None:
        print("FALHA  unica passada deveria devolver None")
        falhas += 1
    else:
        print("ok     unica passada -> None")

    # ------------------------------------------------------------ janela larga
    ocs = mensal.ocorrencias(dt("2026-08-01 00:00"), dt("2026-12-31 23:59"))
    esperado = ["2026-08-05", "2026-09-05", "2026-10-05", "2026-11-05", "2026-12-05"]
    obtido = [o.strftime("%Y-%m-%d") for o in ocs]
    if obtido == esperado:
        print(f"ok     janela de 5 meses -> {len(obtido)} ocorrencias")
    else:
        print(f"FALHA  janela\n       esperava {esperado}\n       obtive {obtido}")
        falhas += 1

    # Uma unica nao deve gerar ocorrencia fora da janela
    ocs_uni = uni.ocorrencias(dt("2026-08-01 00:00"), dt("2026-12-31 23:59"))
    if ocs_uni:
        print(f"FALHA  unica passada gerou ocorrencias: {ocs_uni}")
        falhas += 1
    else:
        print("ok     unica passada nao gera ocorrencia na janela")

    print(f"\n{'FALHOU' if falhas else 'TODOS OK'} ({falhas} falha(s))")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
