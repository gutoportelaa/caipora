"""
Análise completa de uma frase em `Compromisso`.

Orquestra os extratores de `datahora.py` e a classificação de `dominio.py`.
Fica em módulo separado porque `datahora` não deve conhecer `dominio` (só
extrai pedaços) e `dominio` não deve conhecer `datahora` (só modela). Este
módulo é o único que conhece os dois.

ORDEM DOS EXTRATORES IMPORTA e não é arbitrária:

  1. valor       "R$ 1.250" antes de tudo, senão "1.250" vira data ou hora
  2. relativo    "em 30 min" ANTES de duração — senão "30 min" é lido como
                 duração e "me avisa em 30 min" perde o quando. Seguro porque
                 relativo exige preposição ("em", "daqui a"), então não
                 captura o "30 min" de "sexta 15h, 30 min".
  3. duração     "30 minutos" antes de hora, senão "10h, 30 min" -> 10:30
  4. recorrência "todo dia 5" antes de data, senão "dia 5" é consumido solto
  5. data
  6. hora
  7. período     "de manhã" só depois de falhar hora explícita

Cada extrator devolve o texto restante; o que sobra no fim é o título.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from datahora import (
    DURACAO_PADRAO,
    FUSO,
    extrair_data,
    extrair_duracao,
    extrair_hora,
    extrair_periodo,
    extrair_recorrencia,
    extrair_relativo,
    extrair_valor,
    limpar_titulo,
)
from dominio import Compromisso, Freq, Tipo, classificar

# Hora padrão quando o usuário dá data mas não hora.
# Pagamento usa 9h (início do dia útil, dá tempo de resolver); reunião e
# lembrete também 9h — mas o aviso na confirmação deixa isso explícito.
HORA_PADRAO = time(9, 0)


class NaoEntendi(Exception):
    """Frase sem data/hora identificável. Melhor recusar que adivinhar."""


def analisar(frase: str, agora: datetime | None = None) -> tuple[Compromisso, list[str]]:
    agora = agora or datetime.now(FUSO)
    avisos: list[str] = []

    tipo = classificar(frase)

    resto = frase
    valor, resto = extrair_valor(resto)
    instante_rel, resto = extrair_relativo(resto, agora)
    duracao, resto = extrair_duracao(resto)
    freq_str, ancora, resto = extrair_recorrencia(resto)
    data, resto = extrair_data(resto, agora)
    hora, resto = extrair_hora(resto)
    if hora is None:
        hora, resto = extrair_periodo(resto)

    titulo = limpar_titulo(resto)
    freq = Freq(freq_str)

    # ------------------------------------------------------------ resolve data
    if instante_rel is not None:
        # "em 2 horas" é absoluto por si — data/hora explícitas seriam
        # contraditórias, então o relativo ganha.
        quando = instante_rel
        if data is not None or hora is not None:
            avisos.append("usei o tempo relativo e ignorei a data/hora explícita")
    else:
        if data is None and hora is None and freq is Freq.UNICA:
            raise NaoEntendi(titulo)

        if hora is None:
            hora = HORA_PADRAO
            avisos.append(f"hora não informada, assumindo {hora:%H:%M}")

        if data is None:
            if freq is Freq.MENSAL and ancora:
                # "pagar aluguel todo dia 5" sem mês: próxima ocorrência do dia 5.
                base = agora.replace(day=1, hour=hora.hour, minute=hora.minute,
                                     second=0, microsecond=0)
                candidato = _dia_do_mes(base, ancora)
                if candidato <= agora:
                    candidato = _dia_do_mes(_mes_seguinte(base), ancora)
                quando = candidato
            elif freq is Freq.SEMANAL and ancora is not None:
                dias = (ancora - agora.weekday()) % 7
                quando = datetime.combine(
                    agora.date() + timedelta(days=dias), hora, tzinfo=FUSO
                )
                if quando <= agora:
                    quando += timedelta(days=7)
            else:
                candidato = datetime.combine(agora.date(), hora, tzinfo=FUSO)
                if candidato < agora:
                    candidato += timedelta(days=1)
                    avisos.append("horário já passou hoje, assumindo amanhã")
                quando = candidato
        else:
            quando = datetime.combine(data, hora, tzinfo=FUSO)

    if not titulo:
        raise NaoEntendi("")

    if quando < agora and freq is Freq.UNICA:
        avisos.append("data/hora no passado")

    # -------------------------------------------------- coerência por tipo
    if tipo is Tipo.PAGAMENTO and duracao is not None:
        # Pagamento não tem duração; se o usuário disse algo assim, ignoramos
        # em silêncio seria pior que avisar.
        avisos.append("pagamento não tem duração, ignorei")
        duracao = None

    if tipo is Tipo.PAGAMENTO and valor is None:
        avisos.append("valor não informado")

    comp = Compromisso.nova(
        tipo=tipo,
        titulo=titulo,
        quando=quando,
        duracao_min=duracao if tipo is Tipo.REUNIAO else None,
        valor_centavos=valor,
        freq=freq,
        ancora=ancora,
    )
    if tipo is Tipo.REUNIAO and duracao is None:
        comp.duracao_min = DURACAO_PADRAO

    return comp, avisos


def _dia_do_mes(base: datetime, dia: int) -> datetime:
    from dominio import _ultimo_dia

    return base.replace(day=min(dia, _ultimo_dia(base.year, base.month)))


def _mes_seguinte(d: datetime) -> datetime:
    return d.replace(year=d.year + (d.month == 12), month=1 if d.month == 12 else d.month + 1)
