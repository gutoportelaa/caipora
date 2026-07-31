"""
Testes do vigia (avisos proativos).

O que mais importa aqui é a DEDUPLICAÇÃO: o laço roda a cada minuto, então
um aviso que não se marca como enviado vira 60 mensagens por hora. O teste
chama checar() várias vezes seguidas e exige silêncio depois do primeiro.

Rodar:  python tests/test_vigia.py
"""

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from calendario import AgendaLocal  # noqa: E402
from dominio import FUSO, Compromisso, Freq, Tipo  # noqa: E402
from vigia import Vigia  # noqa: E402


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=FUSO)


def main() -> int:
    falhas = 0
    enviados: list[tuple[str, str]] = []

    def registrar(dest, texto):
        enviados.append((dest, texto))

    def checa(desc, cond, extra=""):
        nonlocal falhas
        if cond:
            print(f"ok     {desc}")
        else:
            print(f"FALHA  {desc} {extra}")
            falhas += 1

    with tempfile.TemporaryDirectory() as tmp:
        agenda = AgendaLocal(Path(tmp) / "a.json")
        vigia = Vigia(agenda, registrar, ["u1"])

        # ------------------------------------------------- lembrete na hora
        agenda.criar(
            Compromisso.nova(Tipo.LEMBRETE, "Tirar o bolo", dt("2026-07-30 15:00"))
        )

        enviados.clear()
        vigia.checar(agora=dt("2026-07-30 14:00"))
        checa("nao avisa antes da hora", len(enviados) == 0, f"({enviados})")

        enviados.clear()
        vigia.checar(agora=dt("2026-07-30 15:00"))
        checa("avisa na hora", len(enviados) == 1, f"({enviados})")
        checa("texto do lembrete", enviados and "Tirar o bolo" in enviados[0][1])

        # DEDUPLICAÇÃO: cinco ciclos seguidos, nenhum aviso novo
        enviados.clear()
        for i in range(5):
            vigia.checar(agora=dt("2026-07-30 15:00") + timedelta(minutes=i))
        checa("nao repete o aviso", len(enviados) == 0, f"({len(enviados)} repetidos)")

        # ------------------------------------------ pagamento: 2 dias antes
        agenda2 = AgendaLocal(Path(tmp) / "b.json")
        vigia2 = Vigia(agenda2, registrar, ["u1"])
        agenda2.criar(
            Compromisso.nova(
                Tipo.PAGAMENTO, "Aluguel", dt("2026-08-05 09:00"),
                valor_centavos=185000, freq=Freq.MENSAL, ancora=5,
            )
        )

        enviados.clear()
        vigia2.checar(agora=dt("2026-08-03 09:00"))
        checa("pagamento avisa 2 dias antes", len(enviados) == 1, f"({enviados})")
        checa("aviso menciona 2 dias", enviados and "2 dia" in enviados[0][1],
              f"({enviados[0][1] if enviados else ''})")
        checa("aviso mostra valor", enviados and "1.850,00" in enviados[0][1])

        enviados.clear()
        vigia2.checar(agora=dt("2026-08-03 09:10"))
        checa("nao repete aviso antecipado", len(enviados) == 0)

        # No dia do vencimento, avisa de novo (aviso diferente, dias=0)
        enviados.clear()
        vigia2.checar(agora=dt("2026-08-05 09:00"))
        checa("avisa tambem no dia", len(enviados) == 1, f"({enviados})")
        checa("aviso do dia diz 'hoje'", enviados and "hoje" in enviados[0][1].lower())

        # Mês seguinte: a recorrência dispara novo aviso
        enviados.clear()
        vigia2.checar(agora=dt("2026-09-03 09:00"))
        checa("recorrencia avisa no mes seguinte", len(enviados) == 1, f"({enviados})")

        # ---------------------------------------------- tolerancia de atraso
        agenda3 = AgendaLocal(Path(tmp) / "c.json")
        vigia3 = Vigia(agenda3, registrar, ["u1"])
        agenda3.criar(
            Compromisso.nova(Tipo.REUNIAO, "Call", dt("2026-07-30 10:00"))
        )

        # Aparelho ficou suspenso 10 min: ainda entrega
        enviados.clear()
        vigia3.checar(agora=dt("2026-07-30 10:10"))
        checa("entrega aviso atrasado em 10min", len(enviados) == 1, f"({enviados})")

        # Atraso de 2h: nao entrega (ruido sem sentido)
        agenda4 = AgendaLocal(Path(tmp) / "d.json")
        vigia4 = Vigia(agenda4, registrar, ["u1"])
        agenda4.criar(
            Compromisso.nova(Tipo.REUNIAO, "Antiga", dt("2026-07-30 10:00"))
        )
        enviados.clear()
        vigia4.checar(agora=dt("2026-07-30 12:00"))
        checa("ignora atraso grande", len(enviados) == 0, f"({enviados})")

        # ------------------------------------------------ multi-destinatario
        agenda5 = AgendaLocal(Path(tmp) / "e.json")
        vigia5 = Vigia(agenda5, registrar, ["u1", "u2"])
        agenda5.criar(
            Compromisso.nova(Tipo.LEMBRETE, "Aviso geral", dt("2026-07-30 16:00"))
        )
        enviados.clear()
        vigia5.checar(agora=dt("2026-07-30 16:00"))
        checa("envia para todos os destinatarios", len(enviados) == 2, f"({enviados})")

    print(f"\n{'FALHOU' if falhas else 'TODOS OK'} ({falhas} falha(s))")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
