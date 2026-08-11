"""
Testes da análise completa: classificação + data/hora + recorrência + valor.

Referência fixa: quarta-feira, 29/07/2026, 10:00 (-03:00).

Rodar:  python tests/test_analise.py
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from analise import NaoEntendi, analisar  # noqa: E402
from dominio import FUSO, Freq, Tipo  # noqa: E402

AGORA = datetime(2026, 7, 29, 10, 0, tzinfo=FUSO)
assert AGORA.weekday() == 2

# frase -> (tipo, quando_iso, freq, ancora, valor_centavos, titulo)
CASOS = [
    # ------------------------------------------------------------- pagamentos
    ("pagar aluguel todo dia 5, R$ 1.850,00",
     Tipo.PAGAMENTO, "2026-08-05 09:00", Freq.MENSAL, 5, 185000, "Aluguel"),
    ("conta de luz vence dia 20",
     Tipo.PAGAMENTO, "2026-08-20 09:00", Freq.UNICA, None, None, "Conta de luz vence"),
    ("boleto do carro 15/08 R$ 890",
     Tipo.PAGAMENTO, "2026-08-15 09:00", Freq.UNICA, None, 89000, "Boleto do carro"),
    ("internet todo mes dia 12, 129,90",
     Tipo.PAGAMENTO, "2026-08-12 09:00", Freq.MENSAL, 12, 12990, "Internet"),
    ("fatura do cartao dia 10 de todo mes R$ 2.340,55",
     Tipo.PAGAMENTO, "2026-08-10 09:00", Freq.MENSAL, 10, 234055, "Fatura do cartao"),
    # --------------------------------------------------------------- reuniões
    # Recorrência semanal guarda os dias em `dias_semana`, não em `ancora` —
    # `ancora` ficou só para o dia do mês. Ver o bloco DIAS_SEMANA no fim.
    ("reuniao com o time toda segunda 10h",
     Tipo.REUNIAO, "2026-08-03 10:00", Freq.SEMANAL, None, None, "Reuniao com o time"),
    ("call com cliente sexta 15h, 30 min",
     Tipo.REUNIAO, "2026-07-31 15:00", Freq.UNICA, None, None, "Call com cliente"),
    ("dentista amanha as 14h",
     Tipo.REUNIAO, "2026-07-30 14:00", Freq.UNICA, None, None, "Dentista"),
    ("daily todo dia 9h30",
     Tipo.REUNIAO, "2026-07-30 09:30", Freq.DIARIA, None, None, "Daily"),
    # -------------------------------------------------------------- lembretes
    ("lembra de ligar pro Joao em 2 horas",
     Tipo.LEMBRETE, "2026-07-29 12:00", Freq.UNICA, None, None, "Ligar pro Joao"),
    ("me avisa em 30 min de tirar o bolo",
     Tipo.LEMBRETE, "2026-07-29 10:30", Freq.UNICA, None, None, "Tirar o bolo"),
    ("lembra de comprar pao amanha de manha",
     Tipo.LEMBRETE, "2026-07-30 09:00", Freq.UNICA, None, None, "Comprar pao"),
    ("regar as plantas hoje a noite",
     Tipo.LEMBRETE, "2026-07-29 20:00", Freq.UNICA, None, None, "Regar as plantas"),
    ("lembra de tomar remedio todo dia 8h",
     Tipo.LEMBRETE, "2026-07-30 08:00", Freq.DIARIA, None, None, "Tomar remedio"),
    # "daqui 3 dias" sem o "a" — como se fala.
    ("ligar pro Joao daqui 3 dias",
     Tipo.LEMBRETE, "2026-08-01 10:00", Freq.UNICA, None, None, "Ligar pro Joao"),
    # ------------------------------------------------------ horário reservado
    # Rotina recorrente que ocupa uma faixa fixa: tipo próprio, com escalada
    # de avisos mais discreta que a de reunião.
    ("academia de segunda a sexta das 7h as 8h",
     Tipo.RESERVA, "2026-07-30 07:00", Freq.SEMANAL, None, None, "Academia"),
    ("aula de ingles toda terca e quinta 19h30",
     Tipo.RESERVA, "2026-07-30 19:30", Freq.SEMANAL, None, None, "Aula de ingles"),
    ("psicologo quinzenal quarta 15h",
     Tipo.RESERVA, "2026-07-29 15:00", Freq.SEMANAL, None, None, "Psicologo"),
    # Sem recorrência, "horário reservado" é na prática compromisso pontual —
    # e recebe a escalada de reunião.
    ("psicologo amanha 15h",
     Tipo.REUNIAO, "2026-07-30 15:00", Freq.UNICA, None, None, "Psicologo"),
]

# Frases que não descrevem compromisso nenhum: seguem para o modelo local.
# "marca o dentista" fica aqui porque é ordem de agendar SEM quando — e sem
# verbo de lembrete não temos direito de transformá-la em pendência solta.
RECUSAR = ["marca o dentista", "oi tudo bem", "qual a capital da Franca"]

# Verbo explícito de lembrete e nenhum horário => pendência flutuante, não
# recusa. É a modalidade "demanda adiável": vale, mas não tem hora.
FLUTUANTES = [
    ("lembra de comprar pao", "Comprar pao"),
    ("me lembra de estudar calculo", "Estudar calculo"),
    ("nao esquecer de mandar mensagem pra Ana", "Mandar mensagem pra Ana"),
]

# frase -> dias da semana esperados (0=segunda)
DIAS_SEMANA = [
    ("reuniao com o time toda segunda 10h", [0]),
    ("aula de ingles toda terca e quinta 19h30", [1, 3]),
    ("academia de segunda a sexta das 7h as 8h", [0, 1, 2, 3, 4]),
    ("treino todas as segundas, quartas e sextas 18h", [0, 2, 4]),
    ("estudar todo dia util as 8h", [0, 1, 2, 3, 4]),
    ("feira todo fim de semana 9h", [5, 6]),
]


def main() -> int:
    falhas = 0

    for frase, tipo_e, quando_e, freq_e, ancora_e, valor_e, tit_e in CASOS:
        try:
            c, avisos = analisar(frase, agora=AGORA)
        except NaoEntendi:
            print(f"FALHA  {frase!r}\n       recusou, mas deveria interpretar")
            falhas += 1
            continue

        erros = []
        if c.tipo is not tipo_e:
            erros.append(f"tipo {c.tipo.value} != {tipo_e.value}")
        obtido = c.quando_dt.strftime("%Y-%m-%d %H:%M")
        if obtido != quando_e:
            erros.append(f"quando {obtido} != {quando_e}")
        if c.freq is not freq_e:
            erros.append(f"freq {c.freq.value} != {freq_e.value}")
        if c.ancora != ancora_e:
            erros.append(f"ancora {c.ancora} != {ancora_e}")
        if c.valor_centavos != valor_e:
            erros.append(f"valor {c.valor_centavos} != {valor_e}")
        if c.titulo != tit_e:
            erros.append(f"titulo {c.titulo!r} != {tit_e!r}")

        if erros:
            print(f"FALHA  {frase!r}\n       " + "\n       ".join(erros))
            falhas += 1
        else:
            print(f"ok     {frase!r}\n       -> {c.humano()}")

    for frase in RECUSAR:
        try:
            c, _ = analisar(frase, agora=AGORA)
            print(f"FALHA  {frase!r} deveria ser recusado, veio {c.humano()}")
            falhas += 1
        except NaoEntendi:
            print(f"ok     {frase!r} -> recusado")

    for frase, titulo_e in FLUTUANTES:
        try:
            c, _ = analisar(frase, agora=AGORA)
        except NaoEntendi:
            print(f"FALHA  {frase!r} deveria virar pendencia solta, foi recusada")
            falhas += 1
            continue
        if not c.eh_flutuante or c.titulo != titulo_e:
            print(f"FALHA  {frase!r} -> flutuante={c.eh_flutuante} titulo={c.titulo!r}")
            falhas += 1
        else:
            print(f"ok     {frase!r} -> {c.humano()}")

    for frase, dias_e in DIAS_SEMANA:
        try:
            c, _ = analisar(frase, agora=AGORA)
        except NaoEntendi:
            print(f"FALHA  {frase!r} recusada, esperava recorrencia semanal")
            falhas += 1
            continue
        if sorted(c.dias_semana) != dias_e:
            print(f"FALHA  {frase!r} dias {sorted(c.dias_semana)} != {dias_e}")
            falhas += 1
        else:
            print(f"ok     {frase!r} -> {c._freq_fmt()}")

    total = len(CASOS) + len(RECUSAR) + len(FLUTUANTES) + len(DIAS_SEMANA)
    print(f"\n{total - falhas}/{total} passaram")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
