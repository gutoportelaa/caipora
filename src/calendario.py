"""
Persistência de compromissos, com backend trocável.

  AgendaLocal   JSON em disco. Funciona sem OAuth e serve de fallback quando
                o Google estiver fora.
  AgendaGoogle  Google Calendar (pendente — ver docs/CALENDAR.md §3)

O roteador e o vigia falam com a interface `Agenda`, nunca com o backend.
Mesmo motivo do `Canal`: trocar de provedor não deve mexer no resto.

Nada aqui interpreta linguagem natural — isso é de `datahora.py`/`analise.py`.
Este módulo só guarda e consulta compromissos já validados.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from dominio import FUSO, Compromisso

log = logging.getLogger(__name__)


class Agenda(Protocol):
    def criar(self, comp: Compromisso) -> Compromisso: ...
    def todos(self) -> list[Compromisso]: ...
    def atualizar(self, comp: Compromisso) -> None: ...
    def remover(self, id_comp: str) -> bool: ...


class AgendaLocal:
    """Persistência em JSON. Simples de propósito.

    Um arquivo, um lock. Para a escala deste projeto (um usuário, dezenas de
    compromissos) qualquer coisa mais elaborada seria complexidade sem retorno.
    """

    def __init__(self, caminho: Path):
        self._caminho = caminho
        self._lock = threading.Lock()
        self._caminho.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ disco

    def _ler(self) -> list[Compromisso]:
        if not self._caminho.exists():
            return []
        try:
            dados = json.loads(self._caminho.read_text(encoding="utf-8"))
            return [Compromisso.de_dict(d) for d in dados]
        except (json.JSONDecodeError, TypeError, ValueError, KeyError, OSError) as e:
            # Não apagamos o arquivo: preferimos operar vazio e manter o
            # original para inspeção manual a destruir dados do usuário.
            log.error("agenda ilegível (%s) — operando vazia, arquivo preservado", e)
            return []

    def _gravar(self, comps: list[Compromisso]) -> None:
        # Escrita atômica: grava em temporário e renomeia. Sem isso, uma morte
        # no meio da escrita deixa o JSON truncado e perde a agenda inteira —
        # e este aparelho é justamente propenso a ser morto pelo SO.
        tmp = self._caminho.with_suffix(".tmp")
        tmp.write_text(
            json.dumps([c.para_dict() for c in comps], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self._caminho)

    # --------------------------------------------------------------- interface

    def criar(self, comp: Compromisso) -> Compromisso:
        with self._lock:
            comps = self._ler()
            comp.id = f"loc-{int(datetime.now(FUSO).timestamp() * 1000)}"
            comps.append(comp)
            comps.sort(key=lambda c: c.quando)
            self._gravar(comps)
        return comp

    def todos(self) -> list[Compromisso]:
        return self._ler()

    def atualizar(self, comp: Compromisso) -> None:
        with self._lock:
            comps = self._ler()
            for i, c in enumerate(comps):
                if c.id == comp.id:
                    comps[i] = comp
                    self._gravar(comps)
                    return
        log.warning("atualizar: id %s nao encontrado", comp.id)

    def remover(self, id_comp: str) -> bool:
        with self._lock:
            comps = self._ler()
            restantes = [c for c in comps if c.id != id_comp]
            if len(restantes) == len(comps):
                return False
            self._gravar(restantes)
        return True


# ------------------------------------------------------------------- consultas
# Funções puras sobre a lista, fora do backend: valem para qualquer Agenda.


def proximos(
    agenda: Agenda, agora: datetime | None = None, limite: int = 10, dias: int = 60
) -> list[tuple[Compromisso, datetime]]:
    """Próximas ocorrências, já expandindo recorrência.

    Devolve pares (compromisso, ocorrência) porque um compromisso mensal
    aparece várias vezes na janela — e a data de cada aparição é o que
    interessa mostrar, não a data original de cadastro.
    """
    agora = agora or datetime.now(FUSO)
    fim = agora + timedelta(days=dias)

    pares: list[tuple[Compromisso, datetime]] = []
    for comp in agenda.todos():
        for oc in comp.ocorrencias(agora, fim, maximo=6):
            pares.append((comp, oc))

    pares.sort(key=lambda p: p[1])
    return pares[:limite]


def total_a_pagar(
    agenda: Agenda, agora: datetime | None = None, dias: int = 30
) -> int:
    """Soma dos pagamentos com vencimento na janela, em centavos."""
    from dominio import Tipo

    agora = agora or datetime.now(FUSO)
    fim = agora + timedelta(days=dias)
    total = 0
    for comp in agenda.todos():
        if comp.tipo is not Tipo.PAGAMENTO or comp.valor_centavos is None:
            continue
        total += comp.valor_centavos * len(comp.ocorrencias(agora, fim, maximo=6))
    return total
