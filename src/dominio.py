"""
Modelo de domínio do Caipora.

Três tipos de compromisso com semânticas genuinamente diferentes — não um
"evento" genérico com campos opcionais espalhados:

  REUNIAO    início + duração. Ocupa tempo. Conflita com outras reuniões.
  PAGAMENTO  data de vencimento + valor. Quase sempre recorrente. Precisa de
             aviso ANTECIPADO: lembrar no dia do vencimento não serve para
             nada, porque a conta já está vencendo.
  LEMBRETE   um instante. Sem duração. Dispara e acabou.

A distinção não é burocracia: cada tipo tem regra de aviso, de recorrência e
de conflito diferente. Enfiar os três no mesmo formato empurra essas regras
para dentro do roteador, onde elas se misturam e ninguém acha.

Serialização em JSON plano (não aninhado) de propósito: o backend local grava
com `asdict` e o Google Calendar consome campo por campo. Aninhar recorrência
como subobjeto só criaria trabalho de conversão nos dois lados.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("America/Sao_Paulo")

DIAS_PT = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")


class Tipo(str, Enum):
    REUNIAO = "reuniao"
    PAGAMENTO = "pagamento"
    LEMBRETE = "lembrete"


class Freq(str, Enum):
    UNICA = "unica"
    DIARIA = "diaria"
    SEMANAL = "semanal"
    MENSAL = "mensal"
    ANUAL = "anual"


# Antecedência padrão de aviso, por tipo.
#
# Pagamento avisa 2 dias antes E no dia: 2 dias dá tempo de resolver
# (transferir, pegar o boleto), e o aviso do dia é a rede de segurança.
# Reunião e lembrete avisam na hora — antecedência aqui só gera ruído.
ANTECEDENCIA_PADRAO = {
    Tipo.PAGAMENTO: (2, 0),
    Tipo.REUNIAO: (0,),
    Tipo.LEMBRETE: (0,),
}

DURACAO_PADRAO_REUNIAO = 60


@dataclass
class Compromisso:
    tipo: Tipo
    titulo: str
    quando: str                     # ISO 8601 com fuso
    duracao_min: int | None = None  # só REUNIAO
    valor_centavos: int | None = None  # só PAGAMENTO
    freq: Freq = Freq.UNICA
    # Para MENSAL: dia do mês. Para SEMANAL: dia da semana (0=segunda).
    # Para ANUAL: usamos mês/dia do próprio `quando`.
    ancora: int | None = None
    avisos_dias: list[int] = field(default_factory=list)
    id: str = ""
    # Marca o último disparo de aviso já enviado, para não repetir.
    avisado_em: str = ""

    # ------------------------------------------------------------- construção

    @classmethod
    def nova(
        cls,
        tipo: Tipo,
        titulo: str,
        quando: datetime,
        duracao_min: int | None = None,
        valor_centavos: int | None = None,
        freq: Freq = Freq.UNICA,
        ancora: int | None = None,
    ) -> "Compromisso":
        if tipo is Tipo.REUNIAO and duracao_min is None:
            duracao_min = DURACAO_PADRAO_REUNIAO
        if tipo is not Tipo.REUNIAO:
            duracao_min = None
        return cls(
            tipo=tipo,
            titulo=titulo,
            quando=quando.isoformat(),
            duracao_min=duracao_min,
            valor_centavos=valor_centavos,
            freq=freq,
            ancora=ancora,
            avisos_dias=list(ANTECEDENCIA_PADRAO[tipo]),
        )

    # ---------------------------------------------------------------- derivado

    @property
    def quando_dt(self) -> datetime:
        return datetime.fromisoformat(self.quando)

    @property
    def fim_dt(self) -> datetime:
        return self.quando_dt + timedelta(minutes=self.duracao_min or 0)

    @property
    def recorrente(self) -> bool:
        return self.freq is not Freq.UNICA

    def valor_fmt(self) -> str:
        if self.valor_centavos is None:
            return ""
        return f"R$ {self.valor_centavos / 100:,.2f}".replace(",", "_").replace(
            ".", ","
        ).replace("_", ".")

    # ------------------------------------------------------------- recorrência

    def proxima_ocorrencia(self, depois: datetime) -> datetime | None:
        """Primeira ocorrência estritamente após `depois`.

        Para compromissos únicos, devolve `quando` se ainda estiver no futuro.
        """
        base = self.quando_dt

        if self.freq is Freq.UNICA:
            return base if base > depois else None

        if self.freq is Freq.DIARIA:
            dias = (depois.date() - base.date()).days
            cand = base + timedelta(days=max(dias, 0))
            while cand <= depois:
                cand += timedelta(days=1)
            return cand

        if self.freq is Freq.SEMANAL:
            alvo = self.ancora if self.ancora is not None else base.weekday()
            cand = base
            # Alinha ao dia da semana correto antes de avançar.
            cand += timedelta(days=(alvo - cand.weekday()) % 7)
            while cand <= depois:
                cand += timedelta(days=7)
            return cand

        if self.freq is Freq.MENSAL:
            dia = self.ancora or base.day
            ano, mes = depois.year, depois.month
            for _ in range(14):  # margem para meses sem o dia (ex.: 31)
                ultimo = _ultimo_dia(ano, mes)
                # Dia 31 em fevereiro cai no último dia do mês, não pula o mês.
                # Perder um vencimento por causa disso seria inaceitável.
                cand = base.replace(year=ano, month=mes, day=min(dia, ultimo))
                if cand > depois:
                    return cand
                mes += 1
                if mes > 12:
                    mes, ano = 1, ano + 1
            return None

        if self.freq is Freq.ANUAL:
            cand = base.replace(year=depois.year)
            if cand <= depois:
                cand = base.replace(year=depois.year + 1)
            return cand

        return None

    def ocorrencias(self, inicio: datetime, fim: datetime, maximo: int = 50) -> list[datetime]:
        """Todas as ocorrências dentro de uma janela."""
        saida: list[datetime] = []
        cursor = inicio - timedelta(microseconds=1)
        while len(saida) < maximo:
            prox = self.proxima_ocorrencia(cursor)
            if prox is None or prox > fim:
                break
            saida.append(prox)
            cursor = prox
        return saida

    # ------------------------------------------------------------ apresentação

    def humano(self, momento: datetime | None = None) -> str:
        dt = momento or self.quando_dt
        icone = {Tipo.REUNIAO: "👥", Tipo.PAGAMENTO: "💰", Tipo.LEMBRETE: "🔔"}[self.tipo]
        partes = [f"{icone} {self.titulo}"]

        if self.tipo is Tipo.PAGAMENTO:
            partes.append(f"vence {DIAS_PT[dt.weekday()]} {dt:%d/%m}")
            if self.valor_centavos is not None:
                partes.append(self.valor_fmt())
        else:
            partes.append(f"{DIAS_PT[dt.weekday()]} {dt:%d/%m} às {dt:%H:%M}")
            if self.duracao_min and self.duracao_min != DURACAO_PADRAO_REUNIAO:
                partes.append(f"{self.duracao_min}min")

        if self.recorrente:
            partes.append(f"({self._freq_fmt()})")

        return " — ".join(partes[:2]) + ("  " + "  ".join(partes[2:]) if len(partes) > 2 else "")

    def _freq_fmt(self) -> str:
        if self.freq is Freq.MENSAL:
            return f"todo dia {self.ancora or self.quando_dt.day}"
        if self.freq is Freq.SEMANAL:
            alvo = self.ancora if self.ancora is not None else self.quando_dt.weekday()
            return f"toda {DIAS_PT[alvo]}"
        if self.freq is Freq.DIARIA:
            return "todo dia"
        if self.freq is Freq.ANUAL:
            return "todo ano"
        return ""

    def para_dict(self) -> dict:
        d = asdict(self)
        d["tipo"] = self.tipo.value
        d["freq"] = self.freq.value
        return d

    @classmethod
    def de_dict(cls, d: dict) -> "Compromisso":
        d = dict(d)
        d["tipo"] = Tipo(d["tipo"])
        d["freq"] = Freq(d.get("freq", "unica"))
        return cls(**d)


def _ultimo_dia(ano: int, mes: int) -> int:
    if mes == 12:
        return 31
    return (date(ano, mes + 1, 1) - timedelta(days=1)).day


# --------------------------------------------------------------- classificação

# A classificação por palavra-chave é determinística e auditável. Nada de LLM:
# confundir um pagamento com uma reunião muda a regra de aviso e a recorrência.
_PAGAMENTO = re.compile(
    r"\b(pag(?:ar|amento|a)|cont(?:a|as)|boleto|fatura|vence(?:r|ndo|nto)?|"
    r"aluguel|luz|agua|água|internet|telefone|condominio|condomínio|iptu|ipva|"
    r"parcela|prestacao|prestação|mensalidade|assinatura|cartao|cartão)\b",
    re.IGNORECASE,
)
_REUNIAO = re.compile(
    r"\b(reuni(?:ao|ão|oes|ões)|call|meeting|encontro|entrevista|"
    r"apresenta(?:cao|ção)|daily|weekly|1:1|alinhamento|consulta|"
    r"dentista|medico|médico|exame)\b",
    re.IGNORECASE,
)
_LEMBRETE = re.compile(
    r"\b(lembr(?:a|ar|e|ete)|avis(?:a|ar|e)|n[aã]o esquec|memoriza)\b",
    re.IGNORECASE,
)


def classificar(frase: str) -> Tipo:
    """Decide o tipo do compromisso pela linguagem usada.

    Ordem importa: "lembra de pagar a conta de luz" é PAGAMENTO, não lembrete
    — o que importa é o objeto, não o verbo. Por isso pagamento vem primeiro.
    """
    if _PAGAMENTO.search(frase):
        return Tipo.PAGAMENTO
    if _REUNIAO.search(frase):
        return Tipo.REUNIAO
    if _LEMBRETE.search(frase):
        return Tipo.LEMBRETE
    # Sem sinal claro: lembrete é o padrão menos intrusivo — não reserva
    # tempo na agenda nem cria expectativa de recorrência.
    return Tipo.LEMBRETE
