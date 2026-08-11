<p align="center">
  <img src="caipora-icon.png" alt="Caipora" width="180">
</p>

<h1 align="center">Caipora</h1>

<p align="center">
  <em>Assistente pessoal local, rodando num Android reaproveitado.</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="Licença">
  <img src="https://img.shields.io/badge/Python-3.10+-yellow.svg" alt="Python">
  <img src="https://img.shields.io/badge/Platform-Termux%20%7C%20Android-green.svg" alt="Plataforma">
</p>

---

## 📖 Sobre o Projeto

O **Caipora** transforma um smartphone antigo (Samsung Galaxy M52, Snapdragon
778G, 6 GB de RAM) num servidor de IA **100% local**, autônomo e sem enviar
nada para terceiros.

Para caber no aparelho, o projeto **abandona frameworks pesados como
LangChain/LangGraph** e usa uma arquitetura modular em Python puro sobre
`llama.cpp`. A biblioteca padrão dá conta: não há `requests`, não há
`python-telegram-bot`, não há `google-api-python-client`.

A regra central, que explica quase todas as decisões daqui:

> **O LLM nunca decide nada que possa ser decidido por código.**

Não é preferência estética, é resultado de medição. Mesmo com gramática GBNF
garantindo JSON válido, o Qwen3 1.7B errou a **data** em 4 de 7 frases de
teste. O parser determinístico acertou 7/7. Detalhes em
[`docs/CALENDAR.md`](docs/CALENDAR.md) §1.

---

## ✨ O que ele faz

### Quatro modalidades de compromisso

Cada uma tem regra de aviso, de recorrência e de conflito **diferente** — por
isso são tipos distintos, e não um "evento" genérico com campos opcionais.

| | Modalidade | Quando avisa | Exemplo |
|---|---|---|---|
| 👥 | **Compromisso marcado** | 07:30, 30/15/10 min antes e na hora | `dentista amanhã 14h` |
| 📌 | **Horário reservado** | 07:30 e 30 min antes | `academia de segunda a sexta das 7h às 8h` |
| 💰 | **Conta a pagar** | 2 dias antes e no dia | `pagar aluguel todo dia 5, R$ 1.850` |
| 🔔 | **Lembrete** | na hora, ou quando houver espaço | `me lembra de estudar cálculo` |

O horário reservado existe separado do compromisso marcado por um motivo
prático: uma aula de segunda a sexta com a escalada completa seriam **25
notificações por semana**. Assistente que notifica demais é desligado — e
assistente desligado é pior que nenhum.

O **lembrete flutuante** (`me lembra de estudar cálculo`) é a modalidade das
demandas adiáveis: não tem hora, tem uma janela do dia. O vigia o oferece
quando há espaço na agenda, no máximo três vezes por dia, e ele só some
quando você responde `/feito`.

### Reconhecimento de linguagem

Tudo determinístico, em português do Brasil, sem passar pelo modelo:

```
pagar internet dia 12 de todo mês, 129,90     → 💰 mensal, dia 12, R$ 129,90
aula de inglês toda terça e quinta 19h30      → 📌 semanal, ter/qui
academia de segunda a sexta das 7h às 8h      → 📌 seg–sex, 07:00–08:00
psicólogo quinzenal quarta 15h                → 📌 a cada 2 semanas
consulta dia 15 de março às 9h                → 👥 15/03, 09:00
reunião das 14h às 16h30                      → 👥 14:00, 150 min
correr amanhã 8 e meia da manhã               → 🔔 08:30
ligar pro João daqui 3 dias                   → 🔔 relativo
```

### Comandos

```
/agenda      próximos compromissos          /lembretes   pendências sem hora
/hoje        só o dia de hoje               /feito N     risca a pendência N
/contas      pagamentos e total do mês      /cancelar N  cancela o item N
/status      temperatura, RAM, backend      /esquecer    limpa o histórico
```

Qualquer outra coisa vai para o modelo local.

---

## 🏗️ Arquitetura

Três processos, com fronteiras deliberadas:

1. **O cérebro** — `llama-server` (`llama.cpp`) servindo Qwen3 1.7B Q4_K_M.
2. **O roteador** — [`src/roteador.py`](src/roteador.py) decide entre **L0**
   (código: agendamento, consultas, comandos — <1 ms, sem alucinação) e **L1**
   (conversa livre: modelo local, ~4 s).
3. **O vigia** — [`src/vigia.py`](src/vigia.py) roda numa thread própria e
   envia avisos sem ninguém perguntar. É o que separa um assistente de um
   banco de dados.

| Módulo | Responsabilidade |
|---|---|
| [`dominio.py`](src/dominio.py) | Os quatro tipos, recorrência e o formato dos avisos |
| [`datahora.py`](src/datahora.py) | Extração de data, hora, faixa, valor e recorrência |
| [`analise.py`](src/analise.py) | Orquestra os extratores → `Compromisso` |
| [`calendario.py`](src/calendario.py) | Persistência, com backend trocável |
| [`agenda_google.py`](src/agenda_google.py) | Google Calendar em REST puro |
| [`canal.py`](src/canal.py) | Contrato do canal (Telegram hoje, WhatsApp depois) |

### O formato dos avisos

Avisos são **strings**, não números de dias — foi o que tornou "30 minutos
antes" possível de expressar:

```
"-30m"  "-2h"  "-2d"     deslocamento antes da ocorrência
"0"                      no instante da ocorrência
"D0@07:30"               07:30 do dia da ocorrência (o resumo matinal)
"D-1@20:00"              20:00 da véspera
```

A âncora `D` existe porque "avisar no começo do dia" não é um deslocamento:
uma reunião às 9h e outra às 18h compartilham o mesmo aviso matinal.

Três regras do vigia que só apareceram com a escalada em minutos:

- **Tolerância proporcional** — entregar "faltam 10 minutos" com meia hora de
  atraso é entregá-lo *depois* do compromisso começar.
- **Nada retroativo** — agendar às 09:50 algo para as 10:00 não pode cuspir
  `-30m`, `-15m` e `-10m` de uma vez.
- **Uma mensagem por ciclo** — às 07:30 o dia inteiro dispara junto, e sem
  coalescência o resumo matinal viraria seis notificações seguidas.

---

## 🛠️ Requisitos

- **Dispositivo:** Android com ~6 GB de RAM (validado em Galaxy M52 5G / SD 778G).
  O orçamento real de RAM é de ~2,5 GB — o One UI consome o resto.
- **Base:** [Termux](https://f-droid.org/packages/com.termux/) **da F-Droid**
  (a versão da Play Store está congelada e não funciona), com bateria irrestrita.
- **Modelo:** Qwen3 1.7B Q4_K_M (~1,1 GB). Llama 3.2 3B é o teto prático.
- **Threads:** `-t 2` neste SoC — resultado de medição, não de topologia.
  Não copie sem medir o seu; veja [`docs/REFERENCIAS.md`](docs/REFERENCIAS.md) §3.

---

## 🚀 Instalação

### 1. Ambiente

```bash
pkg update && pkg upgrade
pkg install clang cmake git python
```

> Compile o `llama.cpp` **nativamente** no Termux. O `proot-distro` adiciona
> sobrecarga de `ptrace` exatamente onde dói mais: criação de threads, I/O e
> `mmap` do GGUF.

### 2. Repositório

```bash
git clone https://github.com/gutoportelaa/caipora.git
cd caipora
cp .env.example .env   # preencha TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID
```

`TELEGRAM_CHAT_ID` **não é opcional**: bots do Telegram são públicos, e sem a
allowlist qualquer pessoa cria evento na sua agenda e queima sua bateria
rodando inferência.

### 3. Modelo

```bash
mkdir -p models
wget <URL_DO_GGUF> -O ./models/modelo.gguf
```

### 4. Google Calendar (opcional)

```bash
python scripts/autorizar_google.py
```

Sem `token.json`, o Caipora usa a agenda local em JSON e diz isso no
`/status`. Faça o consentimento no desktop e copie o `token.json` —
não tente abrir navegador por SSH. Passo a passo em
[`docs/CALENDAR.md`](docs/CALENDAR.md) §3.

### 5. Rodar

```bash
# Terminal 1 — o cérebro
llama-server -m models/modelo.gguf -t 2 -c 4096 --host 127.0.0.1 --port 8080

# Terminal 2 — o bot
python3 src/bot.py
```

Em produção, use `termux-services` (runit) + `Termux:Boot`. Runbook completo
em [`docs/OPERACAO.md`](docs/OPERACAO.md).

---

## 🧪 Testes

Sem pytest, de propósito — uma dependência a menos no aparelho:

```bash
for t in tests/*.py; do python3 "$t" || echo "FALHOU: $t"; done
```

Todos usam relógio **fixo e injetado**. Teste que depende da hora da parede é
teste que passa de manhã e falha à noite — já aconteceu aqui.

---

## 🗺️ Roadmap

- [x] **V1** — Telegram, LLM local, roteador L0/L1
- [x] **V1.1** — Google Calendar determinístico
- [x] **V1.2** — Quatro modalidades, avisos em minutos, lembretes flutuantes
- [ ] **V2** — WhatsApp (Cloud API oficial ou Evolution no Raspberry Pi)
- [ ] **V3** — Áudio local com `whisper.cpp`
- [ ] **V4** — Fallback para nuvem sob limite térmico

---

## 📚 Documentação

- [`docs/CALENDAR.md`](docs/CALENDAR.md) — agenda, Google Calendar e as medições que guiaram o desenho
- [`docs/OPERACAO.md`](docs/OPERACAO.md) — runbook do dispositivo
- [`docs/REFERENCIAS.md`](docs/REFERENCIAS.md) — onde procurar quando o passo a passo não bate

---

## 🤝 Como Contribuir

Este é um projeto de otimização extrema para hardware limitado: ideias que
reduzam RAM ou aumentem tokens/s são o foco. Toda decisão não óbvia deve vir
com a **medição** que a justifica.

1. Fork do projeto
2. Branch (`git checkout -b feat/nova-automacao`)
3. Commit (`git commit -m 'feat: integração com X'`)
4. Push e Pull Request

## 📝 Licença

MIT. Veja [LICENSE](LICENSE).

<p align="center"><sub>Desenvolvido com ☕ e resiliência de hardware por <a href="https://github.com/gutoportelaa">gutoportelaa</a></sub></p>
