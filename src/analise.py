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
  3. intervalo   "das 7h às 8h" antes de duração E de hora: é a forma que dá
                 as duas coisas de uma vez, e se `hora` rodasse primeiro ela
                 levaria só o "7h" e deixaria "as 8h" no título.
  4. duração     "30 minutos" antes de hora, senão "10h, 30 min" -> 10:30
  5. recorrência "todo dia 5" antes de data, senão "dia 5" é consumido solto;
                 também consome os dias da semana ("toda terça e quinta")
                 antes que `data` os leia como uma data única
  6. data
  7. hora
  8. período     "de manhã" só depois de falhar hora explícita

Cada extrator devolve o texto restante; o que sobra no fim é o título.

FRASE SEM DATA NEM HORA não é necessariamente um erro. "me lembra de estudar
cálculo" é um lembrete legítimo — só que flutuante: pertence a uma janela do
dia, não a um minuto. Ver `Compromisso.flutuante`. Exigimos um verbo explícito
de lembrete para entrar nesse caminho, senão qualquer pergunta solta ("qual a
capital da França") viraria lembrete em vez de ir para o modelo.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from datahora import (
    DURACAO_PADRAO,
    FUSO,
    extrair_data,
    extrair_duracao,
    extrair_hora,
    extrair_intervalo,
    extrair_periodo,
    extrair_recorrencia,
    extrair_relativo,
    extrair_valor,
    limpar_titulo,
)
from dominio import PEDIDO_DE_LEMBRETE, Compromisso, Freq, Tipo, classificar

# Hora padrão quando o usuário dá data mas não hora.
# Pagamento usa 9h (início do dia útil, dá tempo de resolver); reunião e
# lembrete também 9h — mas o aviso na confirmação deixa isso explícito.
HORA_PADRAO = time(9, 0)


class NaoEntendi(Exception):
    """Frase sem data/hora identificável e sem cara de lembrete.

    Melhor recusar que adivinhar: o que cai aqui segue para o modelo local,
    onde ser uma conversa comum é o desfecho certo.
    """


def analisar(frase: str, agora: datetime | None = None) -> tuple[Compromisso, list[str]]:
    agora = agora or datetime.now(FUSO)
    avisos: list[str] = []

    tipo = classificar(frase)

    resto = frase
    valor, resto = extrair_valor(resto)
    instante_rel, resto = extrair_relativo(resto, agora)
    hora_ini, dur_intervalo, resto = extrair_intervalo(resto)
    duracao, resto = extrair_duracao(resto)
    rec, resto = extrair_recorrencia(resto)
    data, resto = extrair_data(resto, agora)
    hora, resto = extrair_hora(resto)
    if hora is None:
        hora, resto = extrair_periodo(resto)

    # A faixa explícita ganha das duas fontes soltas: quem escreveu "das 7h às
    # 8h" já disse início e fim, e qualquer outra leitura seria resíduo.
    if hora_ini is not None:
        if hora is not None and hora != hora_ini:
            avisos.append("usei o horário da faixa e ignorei a outra hora")
        hora = hora_ini
    if dur_intervalo is not None:
        duracao = dur_intervalo

    titulo = limpar_titulo(resto)
    freq = Freq(rec.freq)
    dias_semana = list(rec.dias_semana)

    sem_instante = instante_rel is None and data is None and hora is None

    # ------------------------------------------------------- lembrete flutuante
    if sem_instante and freq is Freq.UNICA:
        if not titulo or not PEDIDO_DE_LEMBRETE.search(frase):
            raise NaoEntendi(titulo)
        # Sem aviso de ⚠️: não ter hora não é um problema a reportar, é a
        # modalidade escolhida. A confirmação já diz "quando der".
        return Compromisso.flutuante(titulo, agora=agora), []

    # ------------------------------------------------------------ resolve data
    if instante_rel is not None:
        # "em 2 horas" é absoluto por si — data/hora explícitas seriam
        # contraditórias, então o relativo ganha.
        quando = instante_rel
        if data is not None or hora is not None:
            avisos.append("usei o tempo relativo e ignorei a data/hora explícita")
    else:
        if hora is None:
            hora = HORA_PADRAO
            avisos.append(f"hora não informada, assumindo {hora:%H:%M}")

        if data is None:
            if freq is Freq.MENSAL and rec.ancora:
                # "pagar aluguel todo dia 5" sem mês: próxima ocorrência do dia 5.
                base = agora.replace(day=1, hour=hora.hour, minute=hora.minute,
                                     second=0, microsecond=0)
                candidato = _dia_do_mes(base, rec.ancora)
                if candidato <= agora:
                    candidato = _dia_do_mes(_mes_seguinte(base), rec.ancora)
                quando = candidato
            elif freq is Freq.SEMANAL and dias_semana:
                # Ancora na PRÓXIMA ocorrência de qualquer um dos dias pedidos.
                # Com "de segunda a sexta" criado num sábado, o compromisso
                # nasce na segunda — não no sábado seguinte.
                quando = _proximo_dos_dias(agora, dias_semana, hora)
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

    # Horário reservado que não se repete é, na prática, um compromisso
    # marcado — e merece a escalada de avisos dele. "psicólogo amanhã 15h"
    # é uma consulta pontual; "psicólogo toda quarta 15h" é rotina.
    if tipo is Tipo.RESERVA and freq is Freq.UNICA:
        tipo = Tipo.REUNIAO

    comp = Compromisso.nova(
        tipo=tipo,
        titulo=titulo,
        quando=quando,
        duracao_min=duracao if tipo in (Tipo.REUNIAO, Tipo.RESERVA) else None,
        valor_centavos=valor,
        freq=freq,
        ancora=rec.ancora,
        dias_semana=dias_semana,
        intervalo=rec.intervalo,
        agora=agora,
    )
    if tipo in (Tipo.REUNIAO, Tipo.RESERVA) and duracao is None:
        comp.duracao_min = DURACAO_PADRAO

    return comp, avisos


def _proximo_dos_dias(agora: datetime, dias: list[int], hora: time) -> datetime:
    """Primeiro dos `dias` da semana, a partir de hoje, no horário `hora`."""
    for salto in range(8):
        d = agora.date() + timedelta(days=salto)
        if d.weekday() in dias:
            candidato = datetime.combine(d, hora, tzinfo=FUSO)
            if candidato > agora:
                return candidato
    return datetime.combine(agora.date() + timedelta(days=1), hora, tzinfo=FUSO)


def _dia_do_mes(base: datetime, dia: int) -> datetime:
    from dominio import _ultimo_dia

    return base.replace(day=min(dia, _ultimo_dia(base.year, base.month)))


def _mes_seguinte(d: datetime) -> datetime:
    return d.replace(year=d.year + (d.month == 12), month=1 if d.month == 12 else d.month + 1)
