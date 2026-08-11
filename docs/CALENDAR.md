# 📅 Agenda — arquitetura e integração com Google Calendar

## Estado atual

**Google Calendar ativo desde 2026-07-30.** Validado contra a API real:
refresh de token, criação com recorrência, releitura reconstruindo o domínio
completo e remoção.

- Extração determinística de data/hora/faixa/duração/valor/recorrência (`src/datahora.py`, `src/analise.py`)
- Quatro tipos de compromisso com semânticas próprias (`src/dominio.py`)
- Backend Google (`AgendaGoogle`) com queda automática para local
- Avisos proativos com escalada em minutos (`src/vigia.py`)
- Comandos `/agenda`, `/hoje`, `/contas`, `/lembretes`, `/feito N`, `/cancelar N`, e frase natural

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
  a conversa. **Exceção deliberada:** se a frase traz verbo explícito de
  lembrete ("me lembra de estudar cálculo"), ela vira uma pendência
  *flutuante* em vez de ser recusada — ver §5. A exigência do verbo é o que
  impede "qual a capital da França" de virar item na lista.
- **Pendência em memória.** Se o processo cair, a confirmação se perde. É o
  comportamento certo: gravar evento com base em intenção de antes do reboot
  seria pior que pedir de novo.
- **Escrita atômica** (`tmp` + `replace`) no backend local. Este aparelho é
  propenso a ser morto pelo SO; sem isso uma queda no meio da escrita
  truncaria o JSON e perderia a agenda inteira.
- **`weekday` com desempate humano.** "sexta" numa sexta significa **hoje**,
  não daqui a 7 dias. "sexta que vem" significa a semana seguinte.

---

## 5. As quatro modalidades e o formato dos avisos

### Por que quatro tipos, e não um evento genérico

Cada tipo tem regra de aviso, de recorrência e de conflito **diferente**.
Enfiar os quatro no mesmo formato empurra essas regras para dentro do
roteador, onde se misturam e ninguém acha.

| Tipo | Ocupa tempo? | Avisos padrão |
|---|---|---|
| `REUNIAO` — compromisso marcado | sim | `D0@07:30`, `-30m`, `-15m`, `-10m`, `0` |
| `RESERVA` — horário reservado | sim | `D0@07:30`, `-30m` |
| `PAGAMENTO` — conta a pagar | não | `-2d`, `0` |
| `LEMBRETE` | não | `0`, ou nenhum (flutuante) |

A separação entre **reunião** e **reserva** é a que mais paga: uma academia de
segunda a sexta com a escalada completa seriam 25 notificações por semana.
Assistente que notifica demais é desligado — e desligado ele é pior que
nenhum. Rotina recebe o resumo do dia e um toque; compromisso pontual recebe
a escalada.

A classificação é por palavra-chave, determinística e auditável
(`dominio.classificar`). Nada de LLM: confundir pagamento com reunião muda a
regra de aviso e a recorrência. **Reserva sem recorrência vira reunião** —
"psicólogo amanhã 15h" é consulta pontual; "psicólogo toda quarta 15h" é
rotina.

### O formato dos avisos

Avisos são **strings**, não números de dias. O formato anterior
(`avisos_dias: list[int]`) tornava "30 minutos antes" literalmente
inexprimível.

```
"-30m"  "-2h"  "-2d"     deslocamento antes da ocorrência
"0"                      no instante da ocorrência
"D0@07:30"               07:30 do dia da ocorrência
"D-1@20:00"              20:00 da véspera
```

A âncora `D` não é açúcar sintático sobre o deslocamento: "avisar no começo do
dia" vale igual para uma reunião às 9h e uma às 18h, e expressar isso como
`-1h30` e `-10h30` seria acidental e frágil.

No Google Calendar os avisos viajam em `extendedProperties` **exatamente como
escritos** — a mesma string que o domínio interpreta, sem conversão nem perda
—, e além disso viram `reminders.overrides` nativos, como redundância barata
caso o aparelho esteja fora do ar.

### Três regras do vigia que a escalada em minutos tornou obrigatórias

Nenhuma delas importava quando o aviso mais fino era "1 dia antes".

1. **Tolerância proporcional.** Entregar "faltam 10 minutos" com 25 minutos de
   atraso é entregá-lo *depois* do compromisso começar — pior que não avisar,
   porque mente. A tolerância de cada aviso é limitada pela própria
   antecedência dele; avisos na hora e de dias antes mantêm os 30 minutos
   cheios.

2. **Nada retroativo no cadastro.** Agendar às 09:50 algo para as 10:00 não
   pode disparar `-30m`, `-15m` e `-10m` de uma vez. O campo `criado_em`
   descarta avisos cuja hora já tinha passado quando o compromisso nasceu.

3. **Uma mensagem por ciclo.** 07:30 é o minuto em que o dia inteiro dispara
   junto. Sem coalescência, o resumo matinal vira seis notificações seguidas.
   Quando tudo que disparou é âncora, a mensagem se anuncia como *Seu dia*.

O histórico de deduplicação também precisou crescer: com cinco avisos por
ocorrência, as cinco chaves do formato antigo não cobriam nem **uma**
ocorrência — a mais velha era esquecida e repetia no ciclo seguinte. Agora
são 24 chaves, podadas por idade.

### Lembrete flutuante

"Lembrar de estudar cálculo" não pertence a um minuto do calendário; pertence
a uma janela do dia. Um lembrete flutuante tem `quando` vazio, uma `janela`
(padrão `09:00-21:00`) e um `feito`.

O vigia o oferece dentro da janela, com no mínimo 3h entre cutucadas, no
máximo 3 por dia, e **nunca** em cima de um compromisso marcado (folga de 15
min para cada lado). Ele só some quando você responde `/feito`. Sem essas
travas, um lembrete sem hora pode disparar a qualquer hora — que é a receita
para o usuário desligar tudo.

**Onde ficam guardados:** num arquivo local separado (`AgendaHibrida`), mesmo
quando o backend é o Google. O Calendar não tem onde representar um item sem
horário; as saídas seriam inventar um evento ao meio-dia — poluindo a agenda
de verdade — ou criar um evento de dia inteiro que reapareceria para sempre.

### Recorrência: dias múltiplos e intervalo

`Freq.SEMANAL` carrega uma **lista** de dias (`dias_semana`), não um só:
"toda terça e quinta" é UM compromisso com dois dias, não dois compromissos —
cancelar deve derrubar a série inteira. O campo `ancora` ficou restrito ao dia
do mês (`MENSAL`).

`intervalo` multiplica a frequência: quinzenal é `SEMANAL` com `intervalo=2`,
"a cada 3 meses" é `MENSAL` com `intervalo=3`. Uma linha no lugar de dois
membros novos no enum, e vira `INTERVAL=n` na RRULE do Google.

O intervalo é contado a partir da **semana de origem**. Sem essa âncora,
"quinzenal" viraria "semanal" toda vez que a busca começasse numa semana par.

> "a cada 15 dias" é tratado como quinzenal (semanal, intervalo 2), não como
> 15 dias corridos: é assim que se fala em português, e preserva o dia da
> semana — contado em dias corridos, a consulta de quarta cairia numa quinta.

---

## 6. Fuso horário

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

## 7. Testes

```bash
for t in tests/*.py; do python3 "$t" || echo "FALHOU: $t"; done
```

| Arquivo | O que protege |
|---|---|
| `test_datahora.py` | extração de data, hora, faixa, meses por nome |
| `test_analise.py` | frase → `Compromisso`, incluindo tipo e dias da semana |
| `test_recorrencia.py` | expansão de ocorrências, dia 31, multi-dia, quinzenal |
| `test_avisos.py` | escalada em minutos, tolerância, coalescência, migração |
| `test_flutuante.py` | pendências sem hora: ritmo, teto diário, `AgendaHibrida` |
| `test_roteador.py` | fluxo de agendamento e a fronteira com o LLM |
| `test_vigia.py` | deduplicação e tolerância de atraso |
| `test_agenda_google.py` | ida e volta do domínio pelo `extendedProperties` |
| `test_resiliencia.py` | falhas de rede, JSON corrompido, token expirado |

Todos rodam no aparelho também (verificado), sem pytest — uma dependência a
menos.

O teste mais valioso da escalada é o minuto a minuto em `test_avisos.py`: ele
roda o vigia em cada minuto de uma janela de horas e exige que o conjunto de
minutos que produziram mensagem seja **exatamente** o esperado. Isso pega de
uma vez erro de escalada, buraco de tolerância e falha de deduplicação — os
três modos de falha que se disfarçam um do outro.

O `test_datahora.py` fixa "agora" numa **quarta-feira, 29/07/2026 10:00**. Sem
referência fixa, um teste de "sexta" passa hoje e falha na semana que vem.

O `test_roteador.py` injeta um `LLMProibido` que lança exceção se chamado —
é o teste que garante estruturalmente que agendamento não toca o modelo.
