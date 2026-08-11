"""
Testes do parser determinístico de data/hora.

Rodar:  python tests/test_datahora.py

Sem pytest de propósito — uma dependência a menos no aparelho, e para este
volume de casos o assert puro basta.

A referência de "agora" é FIXA (quarta-feira, 29/07/2026, 10:00) para que os
testes de "sexta", "amanha" e afins sejam determinísticos. Testar data
relativa contra o relógio real é receita para teste que passa hoje e falha
na semana que vem.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from datahora import FUSO, interpretar  # noqa: E402

# Quarta-feira, 29 de julho de 2026, 10:00 (-03:00)
AGORA = datetime(2026, 7, 29, 10, 0, tzinfo=FUSO)
assert AGORA.weekday() == 2, "referencia precisa ser quarta-feira"

# (frase, data_esperada_iso, hora_esperada, duracao_esperada, titulo_esperado)
CASOS = [
    ("marca dentista amanha as 14h", "2026-07-30", "14:00", 60, "Dentista"),
    ("agenda reuniao com a equipe sexta as 9h30", "2026-07-31", "09:30", 60,
     "Reuniao com a equipe"),
    ("consulta medica dia 2026-08-15 as 10h, 30 minutos", "2026-08-15", "10:00", 30,
     "Consulta medica"),
    ("lembra da academia hoje 19h", "2026-07-29", "19:00", 60, "Academia"),
    ("almoco com a Maria depois de amanha meio dia, 90 min", "2026-07-31", "12:00", 90,
     "Almoco com a Maria"),
    ("call com cliente segunda 8h da manha", "2026-08-03", "08:00", 60,
     "Call com cliente"),
    ("buscar encomenda no correio quarta as 15:45", "2026-07-29", "15:45", 60,
     "Buscar encomenda no correio"),
    # "quarta" numa quarta = hoje; "quarta que vem" = semana seguinte
    ("dentista quarta que vem 10h", "2026-08-05", "10:00", 60, "Dentista"),
    ("reuniao 05/08 14h", "2026-08-05", "14:00", 60, "Reuniao"),
    ("aniversario dia 3 as 20h", "2026-08-03", "20:00", 60, "Aniversario"),
    ("yoga terca 7h da manha, meia hora", "2026-08-04", "07:00", 30, "Yoga"),
    ("jantar sabado 8 da noite", "2026-08-01", "20:00", 60, "Jantar"),
    # ----------------------------------------------------------- meses por nome
    # Antes destes casos, "dia 15 de marco" caía no ramo genérico "dia 15" e
    # virava 15 do mês corrente — data errada e sem aviso nenhum.
    ("consulta dia 15 de marco as 9h", "2027-03-15", "09:00", 60, "Consulta"),
    ("aniversario da Maria 5 de setembro", "2026-09-05", "09:00", 60,
     "Aniversario da Maria"),
    ("viagem 20 de dezembro de 2027 as 6h", "2027-12-20", "06:00", 60, "Viagem"),
    ("prova 3 de ago 14h", "2026-08-03", "14:00", 60, "Prova"),
    # -------------------------------------------------------------- faixa horária
    # A forma natural de descrever horário reservado. Dá início E duração.
    ("academia amanha das 7h as 8h", "2026-07-30", "07:00", 60, "Academia"),
    ("reuniao sexta das 14h as 16h30", "2026-07-31", "14:00", 150, "Reuniao"),
    ("aula segunda de 19h30 as 22h", "2026-08-03", "19:30", 150, "Aula"),
    # ------------------------------------------------------------- hora falada
    ("correr amanha 8 e meia da manha", "2026-07-30", "08:30", 60, "Correr"),
    ("call quinta 14 e 45", "2026-07-30", "14:45", 60, "Call"),
]


def fmt(dt):
    return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M")


def main() -> int:
    falhas = 0
    for frase, data_esp, hora_esp, dur_esp, tit_esp in CASOS:
        quando, titulo, avisos = interpretar(frase, agora=AGORA)
        if quando is None:
            print(f"FALHA  {frase!r}\n       nao interpretou (avisos={avisos})")
            falhas += 1
            continue

        data_obt, hora_obt = fmt(quando.inicio)
        erros = []
        if data_obt != data_esp:
            erros.append(f"data {data_obt} != {data_esp}")
        if hora_obt != hora_esp:
            erros.append(f"hora {hora_obt} != {hora_esp}")
        if quando.duracao_min != dur_esp:
            erros.append(f"duracao {quando.duracao_min} != {dur_esp}")
        if titulo != tit_esp:
            erros.append(f"titulo {titulo!r} != {tit_esp!r}")

        if erros:
            print(f"FALHA  {frase!r}\n       " + "\n       ".join(erros))
            falhas += 1
        else:
            print(f"ok     {frase!r} -> {data_obt} {hora_obt} ({quando.duracao_min}min) {titulo!r}")

    # Casos que devem ser recusados, não adivinhados.
    for frase in ("marca o dentista", "lembra de comprar pao"):
        quando, _, avisos = interpretar(frase, agora=AGORA)
        if quando is not None:
            print(f"FALHA  {frase!r} deveria ser recusado, veio {quando}")
            falhas += 1
        else:
            print(f"ok     {frase!r} -> recusado corretamente")

    total = len(CASOS) + 2
    print(f"\n{total - falhas}/{total} passaram")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
