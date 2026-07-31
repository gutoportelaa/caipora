"""
Cliente do llama-server local.

O servidor expõe a API compatível com OpenAI em /v1/chat/completions, então
isso é só um POST com JSON. De novo: biblioteca padrão, sem `openai` nem
`requests`.
"""

import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

# Curto de propósito. Cada token de system prompt é pago em TODA requisição
# que não acertar o cache: a ~42 t/s de prompt processing, 400 tokens custam
# ~9,5 s. Manter isso enxuto é a otimização de maior impacto no projeto.
SYSTEM_PROMPT = (
    "Você é o Caipora, assistente pessoal rodando localmente num celular. "
    "Responda em português do Brasil, de forma curta e direta. "
    "Se não souber, diga que não sabe."
)

# A 12,8 t/s, 256 tokens levam ~20 s. Somado ao prompt, 120 s dá folga
# confortável até em estado térmico degradado (o throughput cai ~2x).
TIMEOUT = 120

# Quantas mensagens de histórico manter por conversa (perguntas + respostas).
# O contexto do servidor é 4096 tokens; histórico longo tanto estoura quanto
# encarece cada requisição.
MAX_HISTORICO = 6


class ErroLLM(Exception):
    pass


class LLM:
    def __init__(self, url: str):
        self._url = url.rstrip("/") + "/v1/chat/completions"
        # Histórico por remetente. Em memória de propósito nesta etapa:
        # persistir conversa é decisão de privacidade que merece ser
        # tomada de forma explícita, não por acidente.
        self._historico: dict[str, list[dict[str, str]]] = {}

    def conversar(self, remetente: str, texto: str) -> str:
        historico = self._historico.setdefault(remetente, [])

        mensagens = [
            {"role": "system", "content": SYSTEM_PROMPT},
            *historico,
            {"role": "user", "content": texto},
        ]

        corpo = json.dumps(
            {
                "messages": mensagens,
                "max_tokens": 256,
                "temperature": 0.6,
                # Cinto e suspensório: o servidor já sobe com
                # --reasoning-budget 0, mas se alguém subir o servidor sem a
                # flag, isso evita resposta vazia. O thinking do Qwen3 gasta
                # todo o orçamento de tokens e devolve content vazio.
                "chat_template_kwargs": {"enable_thinking": False},
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            self._url,
            data=corpo,
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                resposta = json.load(r)
        except (urllib.error.URLError, TimeoutError) as e:
            raise ErroLLM(f"servidor de inferencia inacessivel: {e}") from e

        escolha = resposta["choices"][0]
        conteudo = (escolha["message"].get("content") or "").strip()

        if not conteudo:
            # Acontece se o thinking estiver ligado ou max_tokens for pequeno
            # demais. Melhor falhar visivelmente que devolver silêncio.
            raise ErroLLM(f"modelo devolveu resposta vazia (finish={escolha.get('finish_reason')})")

        t = resposta.get("timings", {})
        log.info(
            "llm: %d tokens em %.1fs (%.1f t/s geracao, %.1f t/s prompt)",
            t.get("predicted_n", 0),
            t.get("predicted_ms", 0) / 1000,
            t.get("predicted_per_second", 0),
            t.get("prompt_per_second", 0),
        )

        historico.append({"role": "user", "content": texto})
        historico.append({"role": "assistant", "content": conteudo})
        # Descarta as mais antigas mantendo o pareamento pergunta/resposta.
        del historico[:-MAX_HISTORICO]

        return conteudo

    def esquecer(self, remetente: str) -> None:
        self._historico.pop(remetente, None)
