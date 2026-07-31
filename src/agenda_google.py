"""
Backend do Google Calendar — REST puro, sem `google-api-python-client`.

Por que sem a biblioteca oficial: ela arrasta `google-api-core`, `protobuf`,
`googleapis-common-protos` e mais — dezenas de MB e centenas de milissegundos
de import, num aparelho com ~1,3 GB de RAM livre. A API do Calendar é HTTP+JSON
e precisamos de quatro chamadas. Não compensa.

Implementa a mesma interface `Agenda` do backend local, então trocar é uma
linha em bot.py.

RECORRÊNCIA: o Google tem RRULE nativo (RFC 5545), então delegamos a expansão
para ele em vez de calcular no aparelho. Mas mantemos os campos do domínio no
`extendedProperties`, para conseguir reconstruir o Compromisso na leitura sem
depender de heurística sobre o texto do evento.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from dominio import FUSO, Compromisso, Freq, Tipo

log = logging.getLogger(__name__)

BASE = "https://www.googleapis.com/calendar/v3"

# Renova o access_token um pouco antes de expirar, para não perder uma
# requisição por corrida de relógio.
MARGEM_RENOVACAO = 120


class ErroGoogle(Exception):
    pass


class TokenInvalido(ErroGoogle):
    """Refresh token revogado ou expirado — exige nova autorização humana.

    Acontece com frequência quando o app está em modo "Testing" no Google
    Cloud: nesse estado o refresh token expira em 7 dias.
    """


class AgendaGoogle:
    def __init__(self, caminho_token: Path, calendario: str = "primary"):
        self._caminho = caminho_token
        self._cal = calendario
        self._lock = threading.Lock()
        self._access = ""
        self._expira_em = 0.0

        if not caminho_token.exists():
            raise ErroGoogle(
                f"{caminho_token} nao existe — rode scripts/autorizar_google.py"
            )
        self._conf = json.loads(caminho_token.read_text(encoding="utf-8"))
        if not self._conf.get("refresh_token"):
            raise ErroGoogle("token.json sem refresh_token — reautorize")

    # ----------------------------------------------------------------- token

    def _token(self) -> str:
        with self._lock:
            if self._access and time.time() < self._expira_em - MARGEM_RENOVACAO:
                return self._access

            dados = urllib.parse.urlencode({
                "client_id": self._conf["client_id"],
                "client_secret": self._conf["client_secret"],
                "refresh_token": self._conf["refresh_token"],
                "grant_type": "refresh_token",
            }).encode()
            req = urllib.request.Request(
                self._conf["token_uri"], data=dados,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    tok = json.load(r)
            except urllib.error.HTTPError as e:
                corpo = e.read().decode()[:300]
                if e.code in (400, 401):
                    # invalid_grant = token revogado/expirado. Não adianta
                    # repetir: precisa de humano no navegador.
                    raise TokenInvalido(f"refresh recusado ({e.code}): {corpo}") from e
                raise ErroGoogle(f"falha ao renovar token ({e.code}): {corpo}") from e

            self._access = tok["access_token"]
            self._expira_em = time.time() + int(tok.get("expires_in", 3600))
            return self._access

    def _chamar(self, metodo: str, caminho: str, corpo: dict | None = None,
                params: dict | None = None) -> dict:
        url = f"{BASE}{caminho}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        dados = json.dumps(corpo).encode() if corpo is not None else None
        req = urllib.request.Request(url, data=dados, method=metodo, headers={
            "Authorization": f"Bearer {self._token()}",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                texto = r.read()
                return json.loads(texto) if texto else {}
        except urllib.error.HTTPError as e:
            corpo_err = e.read().decode()[:300]
            if e.code == 401:
                raise TokenInvalido(f"401 do Calendar: {corpo_err}") from e
            raise ErroGoogle(f"{metodo} {caminho} -> {e.code}: {corpo_err}") from e

    # ------------------------------------------------------------- conversão

    @staticmethod
    def _rrule(comp: Compromisso) -> list[str]:
        """Traduz nossa recorrência para RRULE (RFC 5545)."""
        if comp.freq is Freq.UNICA:
            return []
        if comp.freq is Freq.DIARIA:
            return ["RRULE:FREQ=DAILY"]
        if comp.freq is Freq.SEMANAL:
            dia = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")[
                comp.ancora if comp.ancora is not None else comp.quando_dt.weekday()
            ]
            return [f"RRULE:FREQ=WEEKLY;BYDAY={dia}"]
        if comp.freq is Freq.MENSAL:
            dia = comp.ancora or comp.quando_dt.day
            # BYMONTHDAY=31 sozinho PULA os meses sem dia 31 — o oposto do que
            # se quer num vencimento. Mas BYMONTHDAY=-1 (último dia) também
            # está errado para 29/30: em janeiro, "dia 29" viraria dia 31.
            #
            # O idioma correto da RFC 5545 é pedir {dia, último} e ficar com o
            # primeiro:
            #   dia 29 em janeiro  -> {29, 31} -> 29  ✓
            #   dia 29 em fevereiro-> {28}     -> 28  ✓
            # Isso reproduz exatamente o min(dia, último_dia) do backend local,
            # mantendo os dois backends com o mesmo comportamento.
            if dia >= 29:
                return [f"RRULE:FREQ=MONTHLY;BYMONTHDAY={dia},-1;BYSETPOS=1"]
            return [f"RRULE:FREQ=MONTHLY;BYMONTHDAY={dia}"]
        if comp.freq is Freq.ANUAL:
            return ["RRULE:FREQ=YEARLY"]
        return []

    def _para_evento(self, comp: Compromisso) -> dict:
        inicio = comp.quando_dt
        fim = inicio + timedelta(minutes=comp.duracao_min or 30)

        ev: dict = {
            "summary": comp.titulo,
            "start": {"dateTime": inicio.isoformat(), "timeZone": str(FUSO)},
            "end": {"dateTime": fim.isoformat(), "timeZone": str(FUSO)},
            # Guardamos o domínio aqui para reconstruir na leitura sem
            # adivinhar pelo texto — o Google não conhece nossos tipos.
            "extendedProperties": {"private": {
                "caipora_tipo": comp.tipo.value,
                "caipora_freq": comp.freq.value,
                "caipora_ancora": str(comp.ancora or ""),
                "caipora_valor": str(comp.valor_centavos or ""),
                "caipora_avisos": ",".join(str(d) for d in comp.avisos_dias),
            }},
        }

        rr = self._rrule(comp)
        if rr:
            ev["recurrence"] = rr

        if comp.tipo is Tipo.PAGAMENTO:
            partes = [f"Pagamento{': ' + comp.valor_fmt() if comp.valor_centavos else ''}"]
            ev["description"] = "\n".join(partes)
            # Lembretes nativos do Google além do nosso Vigia: redundância
            # barata e útil se o celular estiver fora do ar.
            ev["reminders"] = {"useDefault": False, "overrides": [
                {"method": "popup", "minutes": d * 24 * 60}
                for d in comp.avisos_dias
            ]}

        return ev

    @staticmethod
    def _de_evento(ev: dict) -> Compromisso | None:
        priv = (ev.get("extendedProperties") or {}).get("private") or {}
        inicio = (ev.get("start") or {}).get("dateTime")
        if not inicio:
            # Evento de dia inteiro criado fora do Caipora — ignoramos em vez
            # de inventar um horário.
            return None

        def _int(v):
            return int(v) if v not in (None, "") else None

        avisos = [int(x) for x in (priv.get("caipora_avisos") or "").split(",") if x]
        return Compromisso(
            tipo=Tipo(priv.get("caipora_tipo", "lembrete")),
            titulo=ev.get("summary", "(sem titulo)"),
            quando=inicio,
            duracao_min=None,
            valor_centavos=_int(priv.get("caipora_valor")),
            freq=Freq(priv.get("caipora_freq", "unica")),
            ancora=_int(priv.get("caipora_ancora")),
            avisos_dias=avisos or [0],
            id=ev.get("id", ""),
            avisado_em=priv.get("caipora_avisado", ""),
        )

    # -------------------------------------------------------------- interface

    def criar(self, comp: Compromisso) -> Compromisso:
        ev = self._chamar("POST", f"/calendars/{self._cal}/events",
                          self._para_evento(comp))
        comp.id = ev["id"]
        return comp

    def todos(self) -> list[Compromisso]:
        # singleEvents=False para receber a série recorrente uma vez só, com
        # sua RRULE — a expansão em ocorrências é feita pelo domínio, igual ao
        # backend local. Assim os dois backends se comportam da mesma forma.
        agora = datetime.now(FUSO)
        resp = self._chamar("GET", f"/calendars/{self._cal}/events", params={
            "timeMin": (agora - timedelta(days=1)).isoformat(),
            "timeMax": (agora + timedelta(days=400)).isoformat(),
            "singleEvents": "false",
            "maxResults": "250",
            "privateExtendedProperty": "caipora_tipo=pagamento",
        })
        itens = resp.get("items", [])

        # A API só filtra por uma propriedade por vez; buscamos os outros tipos
        # em chamadas separadas e juntamos.
        for tipo in ("reuniao", "lembrete"):
            r = self._chamar("GET", f"/calendars/{self._cal}/events", params={
                "timeMin": (agora - timedelta(days=1)).isoformat(),
                "timeMax": (agora + timedelta(days=400)).isoformat(),
                "singleEvents": "false",
                "maxResults": "250",
                "privateExtendedProperty": f"caipora_tipo={tipo}",
            })
            itens.extend(r.get("items", []))

        comps = [c for c in (self._de_evento(e) for e in itens) if c is not None]
        comps.sort(key=lambda c: c.quando)
        return comps

    def atualizar(self, comp: Compromisso) -> None:
        ev = self._para_evento(comp)
        ev["extendedProperties"]["private"]["caipora_avisado"] = comp.avisado_em
        self._chamar("PATCH", f"/calendars/{self._cal}/events/{comp.id}", ev)

    def remover(self, id_comp: str) -> bool:
        try:
            self._chamar("DELETE", f"/calendars/{self._cal}/events/{id_comp}")
            return True
        except ErroGoogle as e:
            log.warning("remover %s falhou: %s", id_comp, e)
            return False
