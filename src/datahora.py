"""
Extração determinística de data, hora e duração em português do Brasil.

Por que isso não é feito pelo LLM: medimos. Com gramática GBNF garantindo
JSON válido, o Qwen3 1.7B ainda errou a data em 4 de 7 frases de teste —
colapsou "sexta", "segunda" e "quarta" para "hoje"/"amanha", e leu
"10h, 30 minutos" como hora 10:30.

Data e hora são as partes MAIS regulares da frase. Regex acerta 100% delas.
O LLM não agrega nada aqui e só adiciona latência e erro. Ele fica com o
título, que é a parte genuinamente difusa.

Todo cálculo de calendário é feito com o relógio real, em America/Sao_Paulo.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("America/Sao_Paulo")

DURACAO_PADRAO = 60

# Ordem importa: segunda=0, alinhado com date.weekday()
DIAS_SEMANA = {
    "segunda": 0,
    "terca": 1,
    "quarta": 2,
    "quinta": 3,
    "sexta": 4,
    "sabado": 5,
    "domingo": 6,
}


@dataclass(frozen=True)
class Quando:
    """Resultado da extração: instante inicial e duração."""

    inicio: datetime
    duracao_min: int

    @property
    def fim(self) -> datetime:
        return self.inicio + timedelta(minutes=self.duracao_min)


# Horas convencionais para períodos do dia sem hora explícita.
# "amanhã de manhã" precisa virar um instante concreto; escolher 9h é
# arbitrário mas previsível, e o usuário vê na confirmação antes de gravar.
PERIODOS = {
    "manha": time(9, 0),
    "manhã": time(9, 0),
    "tarde": time(14, 0),
    "noite": time(20, 0),
    "madrugada": time(3, 0),
}


def normalizar(texto: str) -> str:
    """Minúsculas sem acento, para casar padrões sem multiplicar variantes.

    "Terça" e "terca" devem casar com a mesma regra.
    """
    sem_acento = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return sem_acento.lower()


# --------------------------------------------------------------------- data


def _proximo_dia_semana(hoje: date, alvo: int, forcar_proxima: bool) -> date:
    """Próxima ocorrência de um dia da semana.

    Regra de desempate deliberada: "sexta" numa sexta-feira significa HOJE,
    não daqui a 7 dias — é como as pessoas falam. Mas "sexta que vem" numa
    sexta significa a semana seguinte.
    """
    delta = (alvo - hoje.weekday()) % 7
    if forcar_proxima:
        delta = delta or 7
        if delta < 7 and (alvo - hoje.weekday()) % 7 == 0:
            delta = 7
    return hoje + timedelta(days=delta)


def extrair_data(texto: str, agora: datetime) -> tuple[date | None, str]:
    """Devolve (data, texto_restante). Remove do texto o trecho consumido."""
    t = normalizar(texto)
    hoje = agora.date()

    # ISO explícito: 2026-08-15 (o "dia" opcional é consumido junto, senão
    # sobra no título)
    m = re.search(r"\b(?:dia\s+)?(\d{4})-(\d{2})-(\d{2})\b", t)
    if m:
        try:
            d = date(int(m[1]), int(m[2]), int(m[3]))
            return d, _remover(texto, m.span())
        except ValueError:
            pass

    # dd/mm ou dd/mm/aaaa
    m = re.search(r"\b(?:dia\s+)?(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", t)
    if m:
        ano = int(m[3]) if m[3] else hoje.year
        if ano < 100:
            ano += 2000
        try:
            d = date(ano, int(m[2]), int(m[1]))
            # Sem ano explícito e data já passada => assume ano que vem.
            if not m[3] and d < hoje:
                d = date(ano + 1, int(m[2]), int(m[1]))
            return d, _remover(texto, m.span())
        except ValueError:
            pass

    # "depois de amanha" precisa vir ANTES de "amanha", senão o prefixo
    # "amanha" casa primeiro e perde-se um dia.
    m = re.search(r"\bdepois de amanha\b", t)
    if m:
        return hoje + timedelta(days=2), _remover(texto, m.span())

    m = re.search(r"\bamanha\b", t)
    if m:
        return hoje + timedelta(days=1), _remover(texto, m.span())

    m = re.search(r"\bhoje\b", t)
    if m:
        return hoje, _remover(texto, m.span())

    # dia da semana, com ou sem "que vem"/"proxima"
    for nome, idx in DIAS_SEMANA.items():
        m = re.search(
            rf"\b(?:(proxima|proximo)\s+)?{nome}(?:-feira|feira)?(\s+que vem)?\b", t
        )
        if m:
            forcar = bool(m[1] or m[2])
            return _proximo_dia_semana(hoje, idx, forcar), _remover(texto, m.span())

    # "dia 15"
    m = re.search(r"\bdia\s+(\d{1,2})\b", t)
    if m:
        dia = int(m[1])
        try:
            d = date(hoje.year, hoje.month, dia)
            if d < hoje:  # já passou neste mês => mês que vem
                mes = hoje.month + 1
                ano = hoje.year + (mes > 12)
                d = date(ano, mes if mes <= 12 else 1, dia)
            return d, _remover(texto, m.span())
        except ValueError:
            pass

    return None, texto


# --------------------------------------------------------------------- hora


def extrair_hora(texto: str, referencia_periodo: str | None = None) -> tuple[time | None, str]:
    t = normalizar(texto)

    m = re.search(r"\bmeio[- ]dia\b", t)
    if m:
        return time(12, 0), _remover(texto, m.span())

    m = re.search(r"\bmeia[- ]noite\b", t)
    if m:
        return time(0, 0), _remover(texto, m.span())

    periodo = referencia_periodo

    # 15:45 | 15h45 | 15h | 15 horas
    m = re.search(r"\b(\d{1,2})\s*(?::|h|hs|horas?)\s*(\d{2})?\b", t)
    if m:
        hora, minuto, fim = int(m[1]), int(m[2] or 0), m.end()
        # "8h da manha" — consome o marcador junto, senão sobra no título.
        mp = re.compile(r"\s*(?:da|de)\s+(manha|tarde|noite)\b").match(t, fim)
        if mp:
            periodo = "am" if mp[1] == "manha" else "pm"
            fim = mp.end()
    else:
        # "8 da noite" — número solto só conta como hora se vier seguido do
        # período. Sem essa exigência, "90 min" ou "3 pessoas" viraria hora.
        # O período é capturado NO PRÓPRIO match: consumi-lo sem capturar foi
        # um bug que fez "8 da noite" virar 08:00 em vez de 20:00.
        m = re.search(r"\b(\d{1,2})\s+(?:da|de)\s+(manha|tarde|noite)\b", t)
        if not m:
            return None, texto
        hora, minuto, fim = int(m[1]), 0, m.end()
        periodo = "am" if m[2] == "manha" else "pm"

    if hora > 23 or minuto > 59:
        return None, texto

    if periodo == "pm" and hora < 12:
        hora += 12
    elif periodo == "am" and hora == 12:
        hora = 0

    return time(hora, minuto), _remover(texto, (m.start(), fim))


# ------------------------------------------------------------------ duração


def extrair_duracao(texto: str) -> tuple[int | None, str]:
    t = normalizar(texto)

    m = re.search(r"\bmeia hora\b", t)
    if m:
        return 30, _remover(texto, m.span())

    # "30 min", "90 minutos"
    m = re.search(r"\b(\d{1,3})\s*(?:min|mins|minutos?)\b", t)
    if m:
        return int(m[1]), _remover(texto, m.span())

    # "2 horas" — mas cuidado: "as 14 horas" é HORA, não duração. Exigimos
    # que não haja "as"/"às" imediatamente antes.
    m = re.search(r"(?<!a[s']\s)\b(\d{1,2})\s*(?:h|horas?)\s+de\s+duracao\b", t)
    if m:
        return int(m[1]) * 60, _remover(texto, m.span())

    return None, texto


# --------------------------------------------------------------------- util


def _remover(texto: str, span: tuple[int, int]) -> str:
    """Remove um trecho pelo índice.

    Funciona porque normalizar() preserva o comprimento: só troca caixa e
    decompõe acentos removendo os combinantes — os índices continuam válidos
    sobre o texto original. Se isso mudar, esta função quebra silenciosamente.
    """
    return (texto[: span[0]] + " " + texto[span[1] :]).strip()


# Verbos de comando no início da frase: são instrução ao assistente, não
# conteúdo do título. "pagar aluguel" -> "Aluguel", já que o ícone 💰 do
# tipo PAGAMENTO já comunica que é pagamento.
VERBOS_LIXO = re.compile(
    r"^\s*(?:me\s+)?(?:marca(?:r)?|agenda(?:r)?|cria(?:r)?|coloca(?:r)?|bota(?:r)?|"
    r"lembra(?:r)?(?:\s+de|\s+da|\s+do)?|avisa(?:r)?(?:\s+de)?|anota(?:r)?|"
    r"adiciona(?:r)?|paga(?:r|mento)?(?:\s+de|\s+da|\s+do)?)\s+",
    re.IGNORECASE,
)

# Palavras que, sobrando nas PONTAS do título, são resíduo da remoção dos
# trechos de data/hora — nunca conteúdo. No meio do título elas são
# legítimas ("reuniao com A equipe"), por isso ancoramos nas bordas.
_BORDA = r"(?:as|a|ao|no|na|em|de|do|da|para|pra|pro|dia|e)"
FILLER_FIM = re.compile(rf"[\s,;]*\b{_BORDA}\b[\s,;]*$", re.IGNORECASE)
FILLER_INICIO = re.compile(rf"^[\s,;]*\b{_BORDA}\b[\s,;]*", re.IGNORECASE)


def limpar_titulo(resto: str) -> str:
    """Transforma o texto sobrante num título apresentável."""
    titulo = VERBOS_LIXO.sub("", resto.strip())

    # Repetido porque a remoção pode deixar duas preposições em sequência:
    # "consulta medica dia as" -> "consulta medica dia" -> "consulta medica".
    for _ in range(4):
        antes = titulo
        titulo = FILLER_FIM.sub("", titulo)
        titulo = FILLER_INICIO.sub("", titulo)
        if titulo == antes:
            break

    titulo = re.sub(r"\s{2,}", " ", titulo).strip(" ,.;:-")
    return titulo[:1].upper() + titulo[1:] if titulo else ""


# --------------------------------------------------------------- recorrência


def extrair_recorrencia(texto: str) -> tuple[str, int | None, str]:
    """Detecta recorrência. Devolve (freq, ancora, texto_restante).

    freq é a string do enum Freq ("unica", "mensal", ...). Devolver string
    evita import circular com dominio.py, que importa daqui.

    Contas e pagamentos são recorrentes por natureza — "todo dia 5" é a forma
    mais comum de expressar vencimento no Brasil.
    """
    t = normalizar(texto)

    # "todo dia 5", "todo mes dia 5", "dia 5 de todo mes"
    #
    # O lookahead separa dois sentidos que colidem:
    #   "todo dia 5"     -> MENSAL, dia 5 do mês
    #   "todo dia 9h30"  -> DIÁRIA, às 9h30
    # Sem ele, "todo dia 9h30" viraria mensal no dia 9.
    m = re.search(
        r"\btodo(?:s)?\s+(?:o\s+)?(?:mes|mês|meses)?\s*,?\s*dia\s+(\d{1,2})\b"
        r"(?!\s*(?::|h|hs|horas?))",
        t,
    )
    if m:
        return "mensal", int(m[1]), _remover(texto, m.span())
    m = re.search(r"\bdia\s+(\d{1,2})\s+de\s+todo(?:s)?\s+(?:o\s+)?(?:mes|mês|meses)\b", t)
    if m:
        return "mensal", int(m[1]), _remover(texto, m.span())

    # "mensalmente", "todo mes", "por mes"
    m = re.search(r"\b(mensalmente|todo\s+(?:o\s+)?mes|todo\s+(?:o\s+)?mês|por\s+mes|por\s+mês)\b", t)
    if m:
        return "mensal", None, _remover(texto, m.span())

    # "toda segunda", "todas as sextas"
    for nome, idx in DIAS_SEMANA.items():
        m = re.search(rf"\btod(?:a|as|o|os)\s+(?:as\s+|os\s+)?{nome}s?(?:-feira|feiras?)?\b", t)
        if m:
            return "semanal", idx, _remover(texto, m.span())

    # "semanalmente", "toda semana"
    m = re.search(r"\b(semanalmente|toda\s+(?:a\s+)?semana|por\s+semana)\b", t)
    if m:
        return "semanal", None, _remover(texto, m.span())

    # "todo dia", "diariamente".
    # Chega aqui só o que o padrão mensal acima recusou — ou seja, "todo dia"
    # sem dia-do-mês, ou seguido de hora ("todo dia 8h"). Nesse segundo caso
    # consumimos apenas "todo dia" e deixamos a hora para extrair_hora.
    m = re.search(r"\b(diariamente|todo\s+dia)\b", t)
    if m:
        return "diaria", None, _remover(texto, m.span())

    # "todo ano", "anualmente"
    m = re.search(r"\b(anualmente|todo\s+(?:o\s+)?ano)\b", t)
    if m:
        return "anual", None, _remover(texto, m.span())

    return "unica", None, texto


# ------------------------------------------------------------------ relativo


def extrair_relativo(texto: str, agora: datetime) -> tuple[datetime | None, str]:
    """"em 2 horas", "daqui a 30 min", "em meia hora".

    Essencial para lembrete rápido, que é justamente o caso em que o usuário
    não quer pensar em horário absoluto.
    """
    t = normalizar(texto)

    m = re.search(r"\b(?:em|daqui\s+a|dentro\s+de)\s+meia\s+hora\b", t)
    if m:
        return agora + timedelta(minutes=30), _remover(texto, m.span())

    m = re.search(
        r"\b(?:em|daqui\s+a|dentro\s+de)\s+(?:uma?\s+|1\s+)?(\d+)?\s*"
        r"(minutos?|mins?|horas?|hs?|dias?|semanas?)\b",
        t,
    )
    if m:
        qtd = int(m[1]) if m[1] else 1
        unidade = m[2]
        if unidade.startswith(("min", "mins")):
            delta = timedelta(minutes=qtd)
        elif unidade.startswith(("hora", "h")):
            delta = timedelta(hours=qtd)
        elif unidade.startswith("dia"):
            delta = timedelta(days=qtd)
        else:
            delta = timedelta(weeks=qtd)
        return agora + delta, _remover(texto, m.span())

    return None, texto


# -------------------------------------------------------------------- valores


def extrair_valor(texto: str) -> tuple[int | None, str]:
    """Valor monetário em centavos.

    Formato brasileiro: R$ 1.250,50 — ponto como separador de milhar e
    vírgula como decimal. Confundir os dois transforma R$ 1.250 em R$ 1,25.
    """
    t = normalizar(texto)

    # 1) Com R$ explícito — sinal mais forte.
    m = re.search(r"(?:r\$\s*)((?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{1,2})?)", t)
    # 2) Com a palavra reais/conto depois.
    if not m:
        m = re.search(
            r"\b((?:\d{1,3}(?:\.\d{3})+|\d+)(?:,\d{1,2})?)\s*(?:reais|real|conto)\b", t
        )
    # 3) Número com vírgula e exatamente 2 casas: formato monetário brasileiro.
    #    Não colide com hora (usa ":" ou "h") nem com data (usa "/" ou "-"),
    #    então "internet dia 12, 129,90" é lido como valor sem ambiguidade.
    if not m:
        m = re.search(r"\b((?:\d{1,3}(?:\.\d{3})+|\d+),\d{2})\b", t)
    if not m:
        return None, texto

    bruto = m[1].replace(".", "").replace(",", ".")
    try:
        return int(round(float(bruto) * 100)), _remover(texto, m.span())
    except ValueError:
        return None, texto


# ------------------------------------------------------------ período do dia


def extrair_periodo(texto: str) -> tuple[time | None, str]:
    """"de manhã", "à noite" — sem hora explícita."""
    t = normalizar(texto)
    m = re.search(r"\b(?:de|da|pela|à|a)\s+(manha|tarde|noite|madrugada)\b", t)
    if m:
        return PERIODOS[m[1]], _remover(texto, m.span())
    return None, texto


def interpretar(frase: str, agora: datetime | None = None) -> tuple[Quando | None, str, list[str]]:
    """Interpreta uma frase de agendamento.

    Devolve (quando, titulo, avisos). `quando` é None se não houver data ou
    hora identificável — nesse caso NÃO inventamos: é melhor pedir para o
    usuário reformular que agendar na hora errada.
    """
    agora = agora or datetime.now(FUSO)
    avisos: list[str] = []

    resto = frase
    duracao, resto = extrair_duracao(resto)
    data, resto = extrair_data(resto, agora)
    hora, resto = extrair_hora(resto)

    titulo = limpar_titulo(resto)

    if data is None and hora is None:
        return None, titulo, ["nao identifiquei data nem hora"]

    if hora is None:
        avisos.append("hora nao informada, assumindo 09:00")
        hora = time(9, 0)

    if data is None:
        # Só hora: hoje se ainda não passou, senão amanhã.
        candidato = datetime.combine(agora.date(), hora, tzinfo=FUSO)
        if candidato < agora:
            data = agora.date() + timedelta(days=1)
            avisos.append("horario ja passou hoje, assumindo amanha")
        else:
            data = agora.date()

    inicio = datetime.combine(data, hora, tzinfo=FUSO)

    if inicio < agora:
        avisos.append("data/hora no passado")

    return Quando(inicio, duracao or DURACAO_PADRAO), titulo, avisos
