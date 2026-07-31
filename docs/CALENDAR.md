# 📅 Agenda — arquitetura e integração com Google Calendar

## Estado atual

**Google Calendar ativo desde 2026-07-30.** Validado contra a API real:
refresh de token, criação com recorrência, releitura reconstruindo o domínio
completo e remoção.

- Extração determinística de data/hora/duração/valor/recorrência (`src/datahora.py`, `src/analise.py`)
- Três tipos de compromisso com semânticas próprias (`src/dominio.py`)
- Backend Google (`AgendaGoogle`) com queda automática para local
- Avisos proativos (`src/vigia.py`)
- Comandos `/agenda`, `/hoje`, `/contas`, `/cancelar N`, e frase natural

### Qual backend está ativo

`/status` sempre mostra. A escolha é feita no boot:

```
token.json existe e responde  -> Google Calendar
qualquer falha                -> local (JSON), com log de erro
```

A degradação é deliberada: em modo "Testing" o refresh token expira em 7 dias
e uma falha dura pararia os avisos de conta. **Mas tem custo conhecido** —
compromissos criados enquanto o Google está fora ficam só no arquivo local e
não sobem depois. Por isso `/status` expõe o backend: divergência silenciosa
seria pior que o erro.

---

## 1. Por que o LLM não interpreta datas

Isto não é escolha estética. Foi medido no aparelho, com as mesmas 7 frases
nos dois caminhos.

**LLM (Qwen3 1.7B Q4_K_M) + gramática GBNF:**

| Frase | Data extraída | Correto? |
|---|---|---|
| dentista **amanhã** 14h | `amanha` | ✅ |
| reunião **sexta** 9h30 | `amanha` | ❌ |
| consulta **2026-08-15** 10h, **30 min** | `2026-08-15`, hora `10:30`, dur `60` | ❌ hora e duração |
| academia **hoje** 19h | `hoje` | ✅ |
| almoço **depois de amanhã meio dia** | `amanha`, hora `15:00` | ❌ |
| call **segunda** 8h | `hoje` | ❌ |
| encomenda **quarta** 15:45 | `hoje` | ❌ |

**Resultado:** JSON válido **7/7** (a gramática cumpriu o prometido), mas data
correta em apenas **3/7**. O modelo colapsa dias da semana para
"hoje"/"amanha" e confunde `"10h, 30 minutos"` com `10:30`. Títulos vieram
poluídos com emoji-shortcodes (`:megaupload:`, `:red_circle:`, `:D`).

**Parser determinístico (`datahora.py`), mesmas frases:** **7/7 corretas**,
em <1 ms, com títulos limpos.

### A lição

**GBNF garante FORMA, não SEMÂNTICA.** Ela torna JSON malformado
estruturalmente impossível — o amostrador só escolhe entre tokens que o
autômato permite naquele ponto. Mas não faz o modelo entender que "sexta"
não é "amanhã".

Data e hora são as partes **mais regulares** da frase, e regex acerta 100%
delas. O que é genuinamente difuso é o **título** — e mesmo esse sai bem de
subtração: remove-se do texto o que foi consumido por data/hora/duração e
limpa-se verbo inicial ("marca", "agenda", "lembra de") e preposições órfãs.

Conclusão prática: **o LLM não participa do agendamento em nenhum ponto.**
Há um teste que garante isso — `tests/test_roteador.py` injeta um LLM que
lança exceção se for chamado.

---

## 2. Onde o GBNF ainda serve

A gramática (`src/gramaticas/evento.gbnf`) fica no repositório porque é útil
para o resíduo: frases que o parser determinístico recusa e que valeria tentar
interpretar de forma aproximada. Mas **só com confirmação obrigatória**, dada
a taxa de erro medida.

### Restrição do parser GBNF neste build (llama.cpp 3018a11)

**Cada regra precisa caber em UMA linha.** Continuação começando com `|`
falha com `failed to parse grammar` — inclusive a forma que a documentação
do GBNF sugere:

```gbnf
# FALHA
relativa ::=
      "hoje"
    | "amanha"

# FUNCIONA
relativa ::= "hoje" | "amanha"
```

Verificado por bisecção com 14 construções isoladas. Também confirmado:
classes negadas (`[^"\\]+`), grupos com alternativa (`("0" [0-9] | "1" [0-9])`),
literais com quote escapado e comentários `#` funcionam normalmente.

O endpoint `/v1/chat/completions` do llama-server **aceita** o campo
`grammar` (extensão do llama.cpp além da API da OpenAI).

---

## 3. Habilitar o Google Calendar

Esta parte exige navegador — **não dá para fazer por SSH**.

### 3.1 No Google Cloud Console

1. Acesse https://console.cloud.google.com e crie um projeto (ex.: `caipora`)
2. **APIs e Serviços → Biblioteca** → busque **Google Calendar API** → *Ativar*
3. **APIs e Serviços → Tela de permissão OAuth**
   - Tipo: **Externo**
   - Preencha nome do app e e-mail
   - Em **Usuários de teste**, adicione o seu próprio e-mail
4. **APIs e Serviços → Credenciais → Criar credenciais → ID do cliente OAuth**
   - Tipo de aplicativo: **App para computador**
   - Baixe o JSON → renomeie para `credentials.json`

> ⚠️ **Armadilha do refresh token:** enquanto o app estiver com status
> **"Testing"**, o refresh token **expira em 7 dias** e o assistente para de
> agendar sem aviso claro. Para uso contínuo, publique o app
> (**Tela de permissão OAuth → Publicar app**). Como o escopo de Calendar é
> sensível, o Google pede verificação — mas para app com usuário único e
> escopo próprio, o modo "Em produção" sem verificação funciona com uma tela
> de aviso.

### 3.2 Consentimento

```bash
python scripts/autorizar_google.py
```

Imprime uma URL, abre um servidor local e espera o redirect (janela de 30 min).
Sem `google-auth-oauthlib`: o fluxo de loopback é HTTP puro e a biblioteca
padrão dá conta.

Escopo `calendar.events` de propósito, não `calendar` completo: o assistente
cria e remove eventos, não gerencia calendários. Menor privilégio.

**Armadilhas que o script trata:**

| Detalhe | Sem ele |
|---|---|
| `access_type=offline` + `prompt=consent` | vem só access_token (1 h) e o assistente para |
| PKCE | o client_secret de Desktop app não é secreto de fato; PKCE é o que protege |
| `redirect_uri` = host registrado | `127.0.0.1` ≠ `localhost` na validação → **Erro 400 invalid_request** |
| loopback com porta efêmera | o Google descontinuou o fluxo OOB ("copie o código") |

### 3.3 Erro 400: invalid_request

Aconteceu neste projeto. Duas causas, nesta ordem de probabilidade:

1. **Projeto sem OAuth configurado.** Se o `project_id` tem cara de
   `gen-lang-client-*`, ele foi criado automaticamente ao gerar uma chave de
   API no AI Studio — nasce **sem tela de permissão OAuth** e **sem Calendar
   API ativada**. Verifique os dois no console; considere criar um projeto
   limpo.
2. **Host do redirect divergente** — corrigido no script (§3.2).

> **Chave de API não serve.** Uma `AIza...` identifica o *aplicativo* e só
> acessa dados públicos; nunca a agenda de um usuário. Para isso é OAuth,
> obrigatoriamente.

### 3.4 Copiar para o aparelho

```bash
scp token.json caipora:~/caipora/
ssh caipora "chmod 600 ~/caipora/token.json"
```

O `credentials.json` **não** precisa ir para o celular — o `token.json` já
carrega client_id/secret/token_uri.

`.gitignore` cobre `token.json`, `credentials.json`, `client_secret*.json` e
`*.apps.googleusercontent.com.json` — este último porque é o nome com que o
Google baixa o arquivo, e sem ele um `git add .` publicaria o segredo.

### 3.5 Troca de backend

Automática: `src/bot.py` detecta `token.json`, valida com uma chamada real e
usa o Google; qualquer falha cai para local com log de erro.

---

## 3.6 Por que REST puro, sem `google-api-python-client`

A biblioteca oficial arrasta `google-api-core`, `protobuf` e
`googleapis-common-protos` — dezenas de MB e centenas de ms de import, num
aparelho com ~1,3 GB de RAM livre. São quatro chamadas HTTP. `urllib` resolve.

`src/agenda_google.py` implementa renovação de token (com margem de 120 s
antes do vencimento), criação, listagem, atualização e remoção.

### RRULE: a armadilha do dia 31

Recorrência é delegada ao Google via `RRULE` (RFC 5545) em vez de calculada no
aparelho. Mas o mapeamento ingênuo diverge do backend local:

| Vencimento | RRULE ingênua | Problema |
|---|---|---|
| dia 5 | `BYMONTHDAY=5` | ok |
| dia 31 | `BYMONTHDAY=31` | **pula** meses sem dia 31 → perde vencimento |
| dia 31 | `BYMONTHDAY=-1` | ok para 31… |
| dia 29 | `BYMONTHDAY=-1` | **errado**: em janeiro vira dia 31 |

Idioma correto para 29–31 — pede `{dia, último}` e fica com o primeiro:

```
RRULE:FREQ=MONTHLY;BYMONTHDAY=29,-1;BYSETPOS=1
    janeiro   -> {29, 31} -> 29  ✓
    fevereiro -> {28}     -> 28  ✓
```

Reproduz exatamente o `min(dia, último_dia)` do backend local. Divergência
entre backends é o bug que ninguém percebe até perder uma conta —
`tests/test_agenda_google.py` trava isso.

### Reconstrução do domínio

O Google não conhece nossos tipos, então tipo/frequência/âncora/valor/avisos
vão em `extendedProperties.private` (prefixo `caipora_`). Na leitura o
domínio é reconstruído desses campos, sem heurística sobre o texto do evento.
Eventos de dia inteiro criados fora do Caipora são **ignorados**, não
convertidos com horário inventado.

Pagamentos também ganham `reminders` nativos do Google, redundantes ao Vigia —
custam nada e cobrem o caso de o celular estar fora do ar.

---

## 4. Fluxo de agendamento

```
"marca dentista amanhã 14h"
        │
        ├─ datahora.interpretar()      regex, <1ms, sem LLM
        │     data=2026-07-30  hora=14:00  dur=60  titulo="Dentista"
        │
        ├─ se data OU hora ausente -> não agenda, cai na conversa com o LLM
        │
        ├─ "Agendar *Dentista* quinta 30/07 às 14:00? (sim/não)"
        │
        └─ "sim" -> Agenda.criar()
```

Decisões deliberadas:

- **Confirmação sempre.** Criar evento tem efeito colateral; frase real é mais
  bagunçada que caso de teste.
- **Recusar em vez de adivinhar.** Sem data nem hora identificável, o parser
  devolve `None`. "marca o dentista" não vira evento às 9h de hoje — vai para
  a conversa.
- **Pendência em memória.** Se o processo cair, a confirmação se perde. É o
  comportamento certo: gravar evento com base em intenção de antes do reboot
  seria pior que pedir de novo.
- **Escrita atômica** (`tmp` + `replace`) no backend local. Este aparelho é
  propenso a ser morto pelo SO; sem isso uma queda no meio da escrita
  truncaria o JSON e perderia a agenda inteira.
- **`weekday` com desempate humano.** "sexta" numa sexta significa **hoje**,
  não daqui a 7 dias. "sexta que vem" significa a semana seguinte.

---

## 5. Fuso horário

`America/Sao_Paulo` via `zoneinfo`, com o pacote **`tzdata` do pip**.

Necessário porque o Android guarda zoneinfo em formato binário próprio, que o
`zoneinfo` do Python não lê, e o Termux não empacota `tzdata`:

```bash
pip install tzdata
```

Abrimos exceção à regra de "só biblioteca padrão" aqui de propósito. A
alternativa sem dependência seria fixar UTC-3 — hoje correto, já que o Brasil
aboliu o horário de verão em 2019. Mas se o DST voltar, o assistente passaria
a marcar compromissos na hora errada **em silêncio**. ~500 KB por correção
garantida é troca boa.

---

## 6. Testes

```bash
python tests/test_datahora.py    # 14 casos de extração — 14/14
python tests/test_roteador.py    # fluxo de agendamento — todos OK
```

Ambos rodam no aparelho também (verificado), sem pytest — uma dependência a
menos.

O `test_datahora.py` fixa "agora" numa **quarta-feira, 29/07/2026 10:00**. Sem
referência fixa, um teste de "sexta" passa hoje e falha na semana que vem.

O `test_roteador.py` injeta um `LLMProibido` que lança exceção se chamado —
é o teste que garante estruturalmente que agendamento não toca o modelo.
