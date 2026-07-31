"""
Testes de resiliência de rede do canal Telegram.

Motivados por falha REAL em produção: um
`ConnectionAbortedError: [Errno 103] Software caused connection abort`
escapou do tratamento e derrubou o processo. A conexão morreu durante a
leitura da resposta SSL, então o erro subiu cru, sem o embrulho do urllib.

O que estes testes travam:
  - erro transitório de rede NUNCA derruba o polling
  - erro permanente (token inválido) NUNCA vira retry infinito
  - falha de envio não propaga (mataria a thread do Vigia)

Rodar:  python tests/test_resiliencia.py
"""

import json
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import canal_telegram  # noqa: E402
from canal_telegram import CanalTelegram, ErroPermanente  # noqa: E402


def main() -> int:
    falhas = 0

    def checa(desc, cond, extra=""):
        nonlocal falhas
        if cond:
            print(f"ok     {desc}")
        else:
            print(f"FALHA  {desc} {extra}")
            falhas += 1

    # A hierarquia que o tratamento assume. Se o Python mudar isso, os
    # `except OSError` deixam de cobrir e o bug volta — por isso é teste,
    # não comentário.
    checa("URLError e OSError", issubclass(urllib.error.URLError, OSError))
    checa("TimeoutError e OSError", issubclass(TimeoutError, OSError))
    checa("ConnectionAbortedError e OSError", issubclass(ConnectionAbortedError, OSError))
    checa("ConnectionResetError e OSError", issubclass(ConnectionResetError, OSError))
    checa("ErroPermanente NAO e OSError", not issubclass(ErroPermanente, OSError),
          "-> seria engolido pelo retry")

    canal_telegram.time.sleep = lambda s: None  # não esperar nos testes

    # ---------------------------------------------- transitórios no polling
    TRANSITORIOS = [
        ConnectionAbortedError(103, "Software caused connection abort"),
        ConnectionResetError(104, "Connection reset by peer"),
        urllib.error.URLError("Network is unreachable"),
        TimeoutError("timed out"),
        json.JSONDecodeError("resposta truncada", "", 0),
        urllib.error.HTTPError("u", 409, "Conflict", {}, None),
        urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None),
        urllib.error.HTTPError("u", 502, "Bad Gateway", {}, None),
    ]

    for erro in TRANSITORIOS:
        canal = CanalTelegram("token-falso")
        estado = {"n": 0}

        def chamar(metodo, params, timeout, _e=erro):
            estado["n"] += 1
            if estado["n"] <= 2:
                raise _e
            return [{
                "update_id": 1,
                "message": {"chat": {"id": "42"}, "text": "oi"},
            }]

        canal._chamar = chamar
        try:
            msg = next(iter(canal.receber()))
            checa(f"polling sobrevive a {type(erro).__name__}"
                  + (f" {erro.code}" if hasattr(erro, "code") else ""),
                  msg.texto == "oi" and estado["n"] == 3,
                  f"(tentativas={estado['n']})")
        except Exception as e:
            print(f"FALHA  polling morreu com {type(erro).__name__}: {e!r}")
            falhas += 1

    # ------------------------------------------------- permanente no polling
    canal = CanalTelegram("token-falso")

    def chamar_permanente(metodo, params, timeout):
        raise ErroPermanente("token invalido")

    canal._chamar = chamar_permanente
    try:
        next(iter(canal.receber()))
        print("FALHA  ErroPermanente foi engolido — viraria loop infinito")
        falhas += 1
    except ErroPermanente:
        print("ok     ErroPermanente sobe e nao vira retry infinito")

    # ------------------------------------------------------------ envio
    canal = CanalTelegram("token-falso")

    def chamar_falha(metodo, params, timeout):
        raise ConnectionAbortedError(103, "abort")

    canal._chamar = chamar_falha
    try:
        canal.enviar("42", "oi")
        print("ok     enviar engole falha transitoria (protege thread do Vigia)")
    except Exception as e:
        print(f"FALHA  enviar propagou {type(e).__name__} — mataria o Vigia")
        falhas += 1

    # ------------------------------------------------- offset avanca sempre
    canal = CanalTelegram("token-falso")
    entregues = []

    def chamar_dois(metodo, params, timeout):
        if not entregues:
            entregues.append(1)
            return [
                {"update_id": 10, "message": {"chat": {"id": "1"}, "text": "a"}},
                {"update_id": 11, "message": {"chat": {"id": "1"}, "text": "b"}},
            ]
        return []

    canal._chamar = chamar_dois
    it = canal.receber()
    next(it)
    next(it)
    checa("offset avanca para ultimo update+1", canal._offset == 12,
          f"(offset={canal._offset})")

    print(f"\n{'FALHOU' if falhas else 'TODOS OK'} ({falhas} falha(s))")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
