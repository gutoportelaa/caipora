"""
Modelo de domínio do Caipora.

Quatro tipos de compromisso com semânticas genuinamente diferentes — não um
"evento" genérico com campos opcionais espalhados:

  REUNIAO    compromisso marcado. Início + duração. Ocupa tempo, conflita, e
             merece escalada de avisos (começo do dia, 30/15/10 min, na hora)
             porque perder o horário tem custo real.
  RESERVA    horário reservado recorrente (aula, psicólogo, academia). Também
             ocupa tempo, mas é ROTINA: quem vai à academia toda manhã não
             precisa de cinco avisos por dia. Um toque antes e o resumo do
             dia bastam. Separar de REUNIAO existe exatamente para isso.
  PAGAMENTO  data de vencimento + valor. Quase sempre recorrente. Precisa de
             aviso ANTECIPADO: lembrar no dia do vencimento não serve para
             nada, porque a conta já está vencendo.
  LEMBRETE   um instante — dispara e acabou. Ou, quando não tem hora marcada,
             um lembrete FLUTUANTE: "lembrar de estudar cálculo" não pertence
             a um minuto do calendário, pertence a uma janela do dia. Ver a
             seção "flutuante" adiante.

A distinção não é burocracia: cada tipo tem regra de aviso, de recorrência e
de conflito diferente. Enfiar os quatro no mesmo formato empurra essas regras
para dentro do roteador, onde elas se misturam e ninguém acha.

AVISOS SÃO STRINGS, NÃO NÚMEROS DE DIAS. A versão anterior guardava
`avisos_dias: list[int]`, o que tornava "30 minutos antes" literalmente
inexprimível. O formato agora tem duas formas, ambas legíveis a olho nu:

    "-30m" "-2h" "-2d"   deslocamento antes da ocorrência
    "0"                  no instante da ocorrência
    "D0@07:30"           07:30 do dia da ocorrência (o "resumo do dia")
    "D-1@20:00"          20:00 da véspera

A âncora `D` existe porque "avisar no começo do dia" não é um deslocamento:
uma reunião às 9h e outra às 18h têm o mesmo aviso matinal, e expressar isso
como "-1h30" e "-10h30" seria acidental e frágil.

Serialização em JSON plano (não aninhado) de propósito: o backend local grava
com `asdict` e o Google Calendar consome campo por campo. Aninhar recorrência
como subobjeto só criaria trabalho de conversão nos dois lados.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("America/Sao_Paulo")

DIAS_PT = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")
DIAS_PT_CURTO = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")


class Tipo(str, Enum):
    REUNIAO = "reuniao"
    RESERVA = "reserva"
    PAGAMENTO = "pagamento"
    LEMBRETE = "lembrete"


class Freq(str, Enum):
    UNICA = "unica"
    DIARIA = "diaria"
    SEMANAL = "semanal"
    MENSAL = "mensal"
    ANUAL = "anual"


# Avisos padrão por tipo. É aqui que mora a diferença de comportamento entre
# "compromisso marcado" e "horário reservado".
#
# REUNIAO leva a escalada completa: o resumo matinal dá contexto, e 30/15/10
# são os degraus em que ainda dá para reagir (sair de casa, entrar na call).
#
# RESERVA leva só o resumo e um toque de 30 min. Aula de segunda a sexta com
# a escalada completa seriam 25 notificações por semana — e um assistente que
# notifica demais é desligado, o que o torna pior que nenhum.
#
# PAGAMENTO avisa 2 dias antes E no dia: 2 dias dá tempo de resolver
# (transferir, pegar o boleto), e o aviso do dia é a rede de segurança.
#
# LEMBRETE avisa na hora — antecedência aqui só gera ruído.
AVISOS_PADRAO: dict[Tipo, list[str]] = {
    Tipo.REUNIAO: ["D0@07:30", "-30m", "-15m", "-10m", "0"],
    Tipo.RESERVA: ["D0@07:30", "-30m"],
    Tipo.PAGAMENTO: ["-2d", "0"],
    Tipo.LEMBRETE: ["0"],
}

DURACAO_PADRAO_REUNIAO = 60

# Janela padrão em que um lembrete flutuante pode ser cutucado. Fora dela o
# vigia fica quieto: lembrete de estudar às 3 da manhã não ajuda ninguém.
JANELA_PADRAO = "09:00-21:00"


# --------------------------------------------------------------------- avisos

_AVISO_OFFSET = re.compile(r"^-(\d+)(m|h|d)$")
_AVISO_ANCORA = re.compile(r"^D([+-]?\d+)@(\d{1,2}):(\d{2})$")

_UNIDADE = {"m": "minutos", "h": "horas", "d": "days"}


def momento_do_aviso(aviso: str, ocorrencia: datetime) -> datetime | None:
    """Quando um aviso deve disparar, dada a ocorrência a que se refere.

    Devolve None para aviso malformado — preferimos ignorar um aviso inválido
    a derrubar o vigia, que é o componente cuja morte silenciosa é pior.
    """
    if aviso in ("0", "-0m", ""):
        return ocorrencia

    m = _AVISO_OFFSET.match(aviso)
    if m:
        qtd = int(m[1])
        if m[2] == "m":
            return ocorrencia - timedelta(minutes=qtd)
        if m[2] == "h":
            return ocorrencia - timedelta(hours=qtd)
        return ocorrencia - timedelta(days=qtd)

    m = _AVISO_ANCORA.match(aviso)
    if m:
        dia = ocorrencia.date() + timedelta(days=int(m[1]))
        try:
            return datetime.combine(dia, time(int(m[2]), int(m[3])), tzinfo=ocorrencia.tzinfo)
        except ValueError:
            return None

    return None


def aviso_eh_ancora(aviso: str) -> bool:
    """True para avisos do tipo "D0@07:30" — hora fixa do dia, não offset."""
    return bool(_AVISO_ANCORA.match(aviso))


def antecedencia_min(aviso: str, ocorrencia: datetime) -> int:
    """Quantos minutos antes da ocorrência este aviso dispara.

    Usado para ordenar avisos e para calcular a tolerância de atraso. Negativo
    seria um aviso depois do evento — possível com âncora, e tratado como 0.
    """
    momento = momento_do_aviso(aviso, ocorrencia)
    if momento is None:
        return 0
    return max(0, int((ocorrencia - momento).total_seconds() // 60))


def descrever_aviso(aviso: str, ocorrencia: datetime) -> str:
    """Rótulo curto do aviso, para o texto da notificação."""
    mins = antecedencia_min(aviso, ocorrencia)
    if _AVISO_ANCORA.match(aviso):
        return "hoje"
    if mins == 0:
        return "agora"
    if mins < 60:
        return f"em {mins} min"
    if mins < 1440:
        horas = mins / 60
        return f"em {horas:.0f}h" if horas == int(horas) else f"em {horas:.1f}h"
    dias = mins // 1440
    return f"em {dias} dia" + ("s" if dias > 1 else "")


@dataclass
class Compromisso:
    tipo: Tipo
    titulo: str
    # ISO 8601 com fuso. VAZIO significa lembrete flutuante: sem instante
    # marcado, o vigia escolhe a hora dentro da `janela`.
    quando: str
    duracao_min: int | None = None      # REUNIAO e RESERVA
    valor_centavos: int | None = None   # só PAGAMENTO
    freq: Freq = Freq.UNICA
    # MENSAL: dia do mês. ANUAL: mês/dia vêm do próprio `quando`.
    ancora: int | None = None
    # SEMANAL: dias da semana (0=segunda). Lista porque "toda terça e quinta"
    # é um caso comum e representá-lo como dois compromissos separados
    # duplicaria título, avisos e cancelamento.
    dias_semana: list[int] = field(default_factory=list)
    # Multiplicador da frequência: quinzenal é SEMANAL com intervalo 2, e
    # "a cada 3 meses" é MENSAL com intervalo 3. Uma linha no lugar de dois
    # membros novos no enum.
    intervalo: int = 1
    avisos: list[str] = field(default_factory=list)
    id: str = ""
    # Chaves dos avisos já enviados, separadas por "|". Ver `vigia.py`.
    avisado_em: str = ""
    # Nascimento do compromisso. Serve para não disparar retroativamente a
    # escalada de um evento criado em cima da hora: agendar às 09:50 algo
    # para as 10:00 não pode cuspir "-30m", "-15m" e "-10m" de uma vez.
    criado_em: str = ""

    # --------------------------------------------------------- flutuante
    janela: str = ""            # "09:00-21:00"; vazio usa JANELA_PADRAO
    feito: bool = False
    cutucadas: int = 0          # quantas no dia de `ultima_cutucada`
    ultima_cutucada: str = ""

    # ------------------------------------------------------------- construção

    @classmethod
    def nova(
        cls,
        tipo: Tipo,
        titulo: str,
        quando: datetime | None,
        duracao_min: int | None = None,
        valor_centavos: int | None = None,
        freq: Freq = Freq.UNICA,
        ancora: int | None = None,
        dias_semana: list[int] | None = None,
        intervalo: int = 1,
        janela: str = "",
        agora: datetime | None = None,
    ) -> "Compromisso":
        if tipo in (Tipo.REUNIAO, Tipo.RESERVA) and duracao_min is None:
            duracao_min = DURACAO_PADRAO_REUNIAO
        if tipo in (Tipo.PAGAMENTO, Tipo.LEMBRETE):
            duracao_min = None
        return cls(
            tipo=tipo,
            titulo=titulo,
            quando=quando.isoformat() if quando is not None else "",
            duracao_min=duracao_min,
            valor_centavos=valor_centavos,
            freq=freq,
            ancora=ancora,
            dias_semana=list(dias_semana or []),
            intervalo=max(1, intervalo),
            avisos=list(AVISOS_PADRAO[tipo]),
            criado_em=(agora or datetime.now(FUSO)).isoformat(),
            janela=janela,
        )

    @classmethod
    def flutuante(
        cls,
        titulo: str,
        janela: str = "",
        agora: datetime | None = None,
    ) -> "Compromisso":
        """Lembrete sem hora marcada — "lembrar de estudar cálculo".

        Não é um compromisso às 9h da manhã com título vago: é uma demanda
        adiável que o vigia oferece quando há espaço na agenda. Por isso não
        tem `quando`, tem `janela`, e só some quando marcada como feita.
        """
        c = cls.nova(Tipo.LEMBRETE, titulo, None, janela=janela or JANELA_PADRAO,
                     agora=agora)
        c.avisos = []  # não há ocorrência a que ancorar avisos
        return c

    # ---------------------------------------------------------------- derivado

    @property
    def eh_flutuante(self) -> bool:
        return not self.quando

    @property
    def quando_dt(self) -> datetime:
        if self.eh_flutuante:
            raise ValueError(f"compromisso flutuante nao tem instante: {self.titulo!r}")
        return datetime.fromisoformat(self.quando)

    @property
    def criado_em_dt(self) -> datetime | None:
        return datetime.fromisoformat(self.criado_em) if self.criado_em else None

    @property
    def fim_dt(self) -> datetime:
        return self.quando_dt + timedelta(minutes=self.duracao_min or 0)

    @property
    def recorrente(self) -> bool:
        return self.freq is not Freq.UNICA

    @property
    def ocupa_tempo(self) -> bool:
        """Reserva e reunião bloqueiam a agenda; pagamento e lembrete não."""
        return self.tipo in (Tipo.REUNIAO, Tipo.RESERVA)

    def janela_horas(self) -> tuple[time, time]:
        bruto = self.janela or JANELA_PADRAO
        try:
            ini, _, fim = bruto.partition("-")
            hi, mi = ini.split(":")
            hf, mf = fim.split(":")
            return time(int(hi), int(mi)), time(int(hf), int(mf))
        except (ValueError, IndexError):
            return time(9, 0), time(21, 0)

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
        Flutuantes não têm ocorrência: quem os agenda é o vigia, em tempo real.
        """
        if self.eh_flutuante:
            return None

        base = self.quando_dt
        passo = max(1, self.intervalo)

        if self.freq is Freq.UNICA:
            return base if base > depois else None

        if self.freq is Freq.DIARIA:
            atraso = (depois.date() - base.date()).days
            cand = base + timedelta(days=max(0, atraso // passo) * passo)
            while cand <= depois:
                cand += timedelta(days=passo)
            return cand

        if self.freq is Freq.SEMANAL:
            dias = sorted(self.dias_semana) or [base.weekday()]
            # Semana de referência para o intervalo: quinzenal precisa saber
            # a partir de QUAL semana contar, senão "quinzenal" viraria
            # "semanal" sempre que a busca começasse numa semana par.
            seg_base = base.date() - timedelta(days=base.weekday())
            cursor = max(base.date(), depois.date())
            # Horizonte suficiente para achar o próximo em qualquer intervalo:
            # um ciclo completo mais uma semana de folga.
            for _ in range(7 * passo + 8):
                if cursor >= base.date() and cursor.weekday() in dias:
                    seg = cursor - timedelta(days=cursor.weekday())
                    if ((seg - seg_base).days // 7) % passo == 0:
                        cand = datetime.combine(cursor, base.timetz())
                        if cand > depois:
                            return cand
                cursor += timedelta(days=1)
            return None

        if self.freq is Freq.MENSAL:
            dia = self.ancora or base.day
            meses_passados = (depois.year - base.year) * 12 + (depois.month - base.month)
            k = max(0, meses_passados // passo)
            for _ in range(14 + 12 // passo):
                total = (base.month - 1) + k * passo
                ano, mes = base.year + total // 12, total % 12 + 1
                ultimo = _ultimo_dia(ano, mes)
                # Dia 31 em fevereiro cai no último dia do mês, não pula o mês.
                # Perder um vencimento por causa disso seria inaceitável.
                cand = base.replace(year=ano, month=mes, day=min(dia, ultimo))
                if cand > depois:
                    return cand
                k += 1
            return None

        if self.freq is Freq.ANUAL:
            k = max(0, (depois.year - base.year) // passo)
            for _ in range(4):
                cand = _com_ano(base, base.year + k * passo)
                if cand is not None and cand > depois:
                    return cand
                k += 1
            return None

        return None

    def ocorrencias(self, inicio: datetime, fim: datetime, maximo: int = 50) -> list[datetime]:
        """Todas as ocorrências dentro de uma janela."""
        if self.eh_flutuante:
            return []
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

    ICONES = {
        Tipo.REUNIAO: "👥",
        Tipo.RESERVA: "📌",
        Tipo.PAGAMENTO: "💰",
        Tipo.LEMBRETE: "🔔",
    }

    def humano(self, momento: datetime | None = None) -> str:
        icone = self.ICONES[self.tipo]

        if self.eh_flutuante:
            marca = "✅ " if self.feito else ""
            return f"{marca}{icone} {self.titulo} — quando der ({self.janela or JANELA_PADRAO})"

        dt = momento or self.quando_dt
        partes = [f"{icone} {self.titulo}"]

        if self.tipo is Tipo.PAGAMENTO:
            partes.append(f"vence {DIAS_PT[dt.weekday()]} {dt:%d/%m}")
            if self.valor_centavos is not None:
                partes.append(self.valor_fmt())
        else:
            quadro = f"{DIAS_PT[dt.weekday()]} {dt:%d/%m} às {dt:%H:%M}"
            # Reserva é definida pelo bloco que ocupa ("das 7h às 8h"), então
            # mostrar o fim é informação, não enfeite.
            if self.tipo is Tipo.RESERVA and self.duracao_min:
                quadro += f"–{(dt + timedelta(minutes=self.duracao_min)):%H:%M}"
            partes.append(quadro)
            if (
                self.tipo is Tipo.REUNIAO
                and self.duracao_min
                and self.duracao_min != DURACAO_PADRAO_REUNIAO
            ):
                partes.append(f"{self.duracao_min}min")

        if self.recorrente:
            partes.append(f"({self._freq_fmt()})")

        return " — ".join(partes[:2]) + ("  " + "  ".join(partes[2:]) if len(partes) > 2 else "")

    def _freq_fmt(self) -> str:
        cada = "" if self.intervalo == 1 else f"a cada {self.intervalo} "

        if self.freq is Freq.MENSAL:
            dia = self.ancora or (self.quando_dt.day if not self.eh_flutuante else 1)
            return f"todo dia {dia}" if self.intervalo == 1 else f"{cada}meses, dia {dia}"

        if self.freq is Freq.SEMANAL:
            dias = sorted(self.dias_semana) or (
                [] if self.eh_flutuante else [self.quando_dt.weekday()]
            )
            if len(dias) == 1:
                nome = f"toda {DIAS_PT[dias[0]]}"
            elif dias == [0, 1, 2, 3, 4]:
                nome = "de segunda a sexta"
            elif dias == [5, 6]:
                nome = "fins de semana"
            else:
                nome = "/".join(DIAS_PT_CURTO[d] for d in dias)
            return nome if self.intervalo == 1 else f"{nome}, {cada}semanas"

        if self.freq is Freq.DIARIA:
            return "todo dia" if self.intervalo == 1 else f"{cada}dias"

        if self.freq is Freq.ANUAL:
            return "todo ano" if self.intervalo == 1 else f"{cada}anos"

        return ""

    # ------------------------------------------------------------ serialização

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

        # Migração do formato antigo, em que aviso era um número de dias e a
        # âncora semanal morava em `ancora`. Ler dados gravados pela versão
        # anterior não pode falhar: seria perder a agenda do usuário numa
        # atualização.
        if "avisos_dias" in d:
            antigos = d.pop("avisos_dias") or [0]
            d["avisos"] = [f"-{n}d" if n else "0" for n in antigos]
        if d["freq"] is Freq.SEMANAL and d.get("ancora") is not None and not d.get("dias_semana"):
            d["dias_semana"] = [int(d["ancora"])]
            d["ancora"] = None

        conhecidos = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in conhecidos})


def _ultimo_dia(ano: int, mes: int) -> int:
    if mes == 12:
        return 31
    return (date(ano, mes + 1, 1) - timedelta(days=1)).day


def _com_ano(base: datetime, ano: int) -> datetime | None:
    """`base` com outro ano, resolvendo 29/02 em ano não bissexto."""
    try:
        return base.replace(year=ano)
    except ValueError:
        return base.replace(year=ano, day=28)


# --------------------------------------------------------------- classificação

# A classificação por palavra-chave é determinística e auditável. Nada de LLM:
# confundir um pagamento com uma reunião muda a regra de aviso e a recorrência.
_PAGAMENTO = re.compile(
    r"\b(pag(?:ar|amento|a)|cont(?:a|as)|boleto|fatura|vence(?:r|ndo|nto)?|"
    r"aluguel|luz|agua|água|internet|telefone|condominio|condomínio|iptu|ipva|"
    r"parcela|prestacao|prestação|mensalidade|assinatura|cartao|cartão)\b",
    re.IGNORECASE,
)
# Horário reservado: atividade de rotina que ocupa uma faixa fixa da semana.
# Vem ANTES de reunião porque "consulta com o psicólogo toda quarta" é rotina,
# não compromisso pontual — e a diferença muda quantos avisos você recebe.
_RESERVA = re.compile(
    r"\b(aula|aulas|curso|treino|academia|muscula(?:cao|ção)|crossfit|nata(?:cao|ção)|"
    r"pilates|yoga|ioga|fisioterapia|psic\w*|terapia|ensaio|est[aá]gio|plant[aã]o|"
    r"monitoria|laborat[oó]rio)\b",
    re.IGNORECASE,
)
_REUNIAO = re.compile(
    r"\b(reuni(?:ao|ão|oes|ões)|call|meeting|encontro|entrevista|"
    r"apresenta(?:cao|ção)|daily|weekly|1:1|alinhamento|consulta|"
    r"dentista|medico|médico|exame)\b",
    re.IGNORECASE,
)
# Verbos que marcam pedido explícito de lembrete. Usado pelo `analise.py` para
# decidir se uma frase SEM data vira lembrete flutuante ou vai para o LLM —
# sem isso, "qual a capital da França" viraria um lembrete.
# As terminações são listadas em vez de um `\w*` solto: `lembr\w*` casaria
# "lembrança", e "que lembrança boa" viraria uma pendência na lista do
# usuário. Errar para o lado de mandar a frase ao modelo é o lado barato.
PEDIDO_DE_LEMBRETE = re.compile(
    r"\b(?:lembr(?:a|ar|e|es|ete|etes|ando)|avis(?:a|ar|e)|"
    r"n[aã]o\s+(?:me\s+)?deix[ae]\s+esquecer|"
    r"n[aã]o\s+(?:me\s+)?esquec(?:er|a|e)|"
    r"memoriz(?:a|ar)|anot(?:a|ar))\b",
    re.IGNORECASE,
)


def classificar(frase: str) -> Tipo:
    """Decide o tipo do compromisso pela linguagem usada.

    Ordem importa: "lembra de pagar a conta de luz" é PAGAMENTO, não lembrete
    — o que importa é o objeto, não o verbo. Por isso pagamento vem primeiro.
    Reserva vem antes de reunião pelo mesmo motivo: "consulta com o psicólogo"
    casa os dois, e a rotina é a leitura certa.
    """
    if _PAGAMENTO.search(frase):
        return Tipo.PAGAMENTO
    if _RESERVA.search(frase):
        return Tipo.RESERVA
    if _REUNIAO.search(frase):
        return Tipo.REUNIAO
    if PEDIDO_DE_LEMBRETE.search(frase):
        return Tipo.LEMBRETE
    # Sem sinal claro: lembrete é o padrão menos intrusivo — não reserva
    # tempo na agenda nem cria expectativa de recorrência.
    return Tipo.LEMBRETE
