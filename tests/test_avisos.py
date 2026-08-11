"""
Testes da escalada de avisos em minutos.

O que este arquivo protege, e que o modelo antigo (`avisos_dias`) não conseguia
sequer expressar:

  * a escalada dispara nos cinco degraus certos e em nenhum outro minuto;
  * um aviso de "10 minutos antes" NÃO é entregue com meia hora de atraso,
    porque aí ele já estaria falando do passado;
  * criar um compromisso em cima da hora não cospe a escalada inteira de uma
    vez retroativamente;
  * avisos que caem no mesmo minuto viram UMA mensagem, não seis;
  * dados gravados pela versão anterior continuam sendo lidos.

O teste do minuto a minuto (`_minutos_com_aviso`) é o mais importante: ele roda
o vigia em cada minuto de uma janela de horas e exige que o conjunto de minutos
que produziram mensagem seja exatamente o esperado. Isso pega de uma vez erro
de escalada, buraco de tolerância e falha de deduplicação — os três modos de
falha que se disfarçam um do outro.

Rodar:  python tests/test_avisos.py
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
        self.checa(desc, obtido == esperado, f"\n       {obtido!r}\n       != {esperado!r}")


def _minutos_com_aviso(agenda, inicio: datetime, fim: datetime) -> tuple[list[str], list[str]]:
    """Roda o vigia minuto a minuto. Devolve (minutos que avisaram, textos)."""
    recebidas: list[str] = []
    quando: list[str] = []

    def registrar(_dest, texto):
        recebidas.append(texto)

    vigia = Vigia(agenda, registrar, ["u1"])
    agora = inicio
    while agora <= fim:
        antes = len(recebidas)
        vigia.checar(agora=agora)
        if len(recebidas) > antes:
            quando.append(f"{agora:%H:%M}")
        agora += timedelta(minutes=1)
    return quando, recebidas


def main() -> int:
    p = Prova()

    with tempfile.TemporaryDirectory() as tmp:
        raiz = Path(tmp)

        # ------------------------------------------------ escalada da reunião
        # Criada na véspera, então nenhum degrau é retroativo.
        ag = AgendaLocal(raiz / "reuniao.json")
        ag.criar(Compromisso.nova(
            Tipo.REUNIAO, "Call com cliente", dt("2026-07-30 10:00"),
            agora=dt("2026-07-29 20:00"),
        ))

        minutos, textos = _minutos_com_aviso(
            ag, dt("2026-07-30 07:00"), dt("2026-07-30 10:30")
        )
        p.igual(
            "reuniao avisa nos 5 degraus e em nenhum outro minuto",
            minutos, ["07:30", "09:30", "09:45", "09:50", "10:00"],
        )
        p.checa("degrau de 30 min se identifica", "30 min" in textos[1], f"({textos[1]!r})")
        p.checa("degrau final diz 'agora'", "Agora" in textos[4], f"({textos[4]!r})")

        # ------------------------------------------------ escalada da reserva
        # Rotina não leva os cinco degraus: seriam 25 notificações por semana.
        ag2 = AgendaLocal(raiz / "reserva.json")
        ag2.criar(Compromisso.nova(
            Tipo.RESERVA, "Academia", dt("2026-07-30 07:00"),
            freq=Freq.SEMANAL, dias_semana=[0, 1, 2, 3, 4],
            agora=dt("2026-07-29 20:00"),
        ))
        minutos2, _ = _minutos_com_aviso(
            ag2, dt("2026-07-30 05:00"), dt("2026-07-30 08:00")
        )
        # A âncora das 07:30 é descartada: o resumo do dia não tem o que dizer
        # sobre algo das 07:00, que já aconteceu quando ele dispararia.
        p.igual("reserva antes das 07:30 avisa so 30 min antes", minutos2, ["06:30"])

        # A mesma reserva no fim do dia recebe as duas: resumo e toque.
        ag2b = AgendaLocal(raiz / "reserva_tarde.json")
        ag2b.criar(Compromisso.nova(
            Tipo.RESERVA, "Ingles", dt("2026-07-30 19:30"),
            freq=Freq.SEMANAL, dias_semana=[1, 3],
            agora=dt("2026-07-29 20:00"),
        ))
        minutos2b, _ = _minutos_com_aviso(
            ag2b, dt("2026-07-30 07:00"), dt("2026-07-30 20:00")
        )
        p.igual("reserva a noite avisa no resumo e 30 min antes",
                minutos2b, ["07:30", "19:00"])

        # ---------------------------------------------- tolerância proporcional
        # Aparelho suspenso: acordamos 25 min depois do "10 minutos antes".
        # Entregá-lo agora seria mentir — o compromisso já começou.
        ag3 = AgendaLocal(raiz / "atraso.json")
        ag3.criar(Compromisso.nova(
            Tipo.REUNIAO, "Perdida", dt("2026-07-30 10:00"),
            agora=dt("2026-07-29 20:00"),
        ))
        enviados: list[str] = []
        v3 = Vigia(ag3, lambda d, t: enviados.append(t), ["u1"])

        v3.checar(agora=dt("2026-07-30 10:15"))
        p.checa(
            "nao entrega '10 min antes' 25 min atrasado",
            all("10 min" not in t for t in enviados),
            f"({enviados})",
        )
        p.checa("mas entrega o aviso da hora", len(enviados) == 1, f"({enviados})")

        # ------------------------------------------------- nada retroativo
        # Agendar 09:50 algo para as 10:00 não pode disparar -30m e -15m.
        ag4 = AgendaLocal(raiz / "emcima.json")
        ag4.criar(Compromisso.nova(
            Tipo.REUNIAO, "Em cima da hora", dt("2026-07-30 10:00"),
            agora=dt("2026-07-30 09:50"),
        ))
        minutos4, _ = _minutos_com_aviso(
            ag4, dt("2026-07-30 09:50"), dt("2026-07-30 10:05")
        )
        p.igual("criado em cima da hora nao dispara escalada passada",
                minutos4, ["09:50", "10:00"])

        # ------------------------------------------------------ coalescência
        # 07:30 é o minuto em que o dia inteiro dispara de uma vez.
        ag5 = AgendaLocal(raiz / "manha.json")
        for titulo, hora in (("Daily", "09:00"), ("Almoco", "12:00"), ("Retro", "16:00")):
            ag5.criar(Compromisso.nova(
                Tipo.REUNIAO, titulo, dt(f"2026-07-30 {hora}"),
                agora=dt("2026-07-29 20:00"),
            ))

        recebidas: list[str] = []
        v5 = Vigia(ag5, lambda d, t: recebidas.append(t), ["u1"])
        v5.checar(agora=dt("2026-07-30 07:30"))

        p.igual("tres compromissos viram UMA mensagem as 07:30", len(recebidas), 1)
        resumo = recebidas[0]
        p.checa("resumo se anuncia como o dia", "Seu dia" in resumo, f"({resumo!r})")
        p.checa("resumo lista os tres",
                all(t in resumo for t in ("Daily", "Almoco", "Retro")), f"({resumo!r})")

        # ------------------------------------- deduplicação sob recorrência
        # Cinco avisos por ocorrência estouravam o histórico antigo de 5
        # chaves: o mais velho era esquecido e repetia no ciclo seguinte.
        ag6 = AgendaLocal(raiz / "recorrente.json")
        ag6.criar(Compromisso.nova(
            Tipo.REUNIAO, "Daily", dt("2026-07-30 09:00"),
            freq=Freq.DIARIA, agora=dt("2026-07-29 20:00"),
        ))
        minutos6, _ = _minutos_com_aviso(
            ag6, dt("2026-07-30 07:00"), dt("2026-08-01 10:00")
        )
        p.igual("daily por 3 dias avisa 5x por dia, sem repetir", len(minutos6), 15)

    # --------------------------------------------------- migração do formato
    antigo = {
        "tipo": "pagamento", "titulo": "Aluguel",
        "quando": "2026-08-05T09:00:00-03:00",
        "duracao_min": None, "valor_centavos": 185000,
        "freq": "mensal", "ancora": 5,
        "avisos_dias": [2, 0], "id": "loc-1", "avisado_em": "",
    }
    c = Compromisso.de_dict(antigo)
    p.igual("avisos_dias antigo vira formato novo", c.avisos, ["-2d", "0"])
    p.igual("campos antigos preservados", (c.ancora, c.valor_centavos), (5, 185000))

    semanal_antigo = dict(antigo, freq="semanal", ancora=3, avisos_dias=[0])
    c2 = Compromisso.de_dict(semanal_antigo)
    p.igual("ancora semanal antiga migra para dias_semana", c2.dias_semana, [3])
    p.igual("ancora semanal antiga e limpa", c2.ancora, None)

    print(f"\n{'FALHOU' if p.falhas else 'TODOS OK'} ({p.falhas} falha(s))")
    return 1 if p.falhas else 0


if __name__ == "__main__":
    sys.exit(main())
