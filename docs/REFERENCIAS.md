# 🧭 Referências externas

Este projeto foi construído e validado num **Samsung Galaxy M52 5G / Android 13
(One UI 5) / Snapdragon 778G**. Boa parte do runbook (`OPERACAO.md`) é
específica demais para outro aparelho.

Esta página existe para o momento em que o passo a passo não bate com o seu
caso: **onde procurar quando o caminho do menu tem outro nome, o fabricante
mata processos de outro jeito, ou a API mudou.**

Preferimos **pontos de entrada estáveis** (raiz de wiki, repositório, RFC) a
links profundos, que quebram com o tempo. Onde a busca é o caminho mais
confiável, indicamos o que buscar.

> Verificado em 2026-07-30. Links envelhecem: se algum tiver mudado, o termo
> de busca ao lado costuma ser mais duradouro que a URL.

---

## 1. Android matando seus processos

O maior obstáculo do projeto não foi CPU nem RAM — foi o Android encerrando
serviços em segundo plano. **Isso varia MUITO por fabricante**, e é onde o
runbook deste repositório menos se generaliza.

### 🔗 https://dontkillmyapp.com — *comece aqui*

Instruções **por fabricante** (Samsung, Xiaomi, Huawei, OnePlus, Oppo, Vivo,
Nokia…), com ranking de agressividade. Huawei e Xiaomi são os piores; Android
puro (Pixel/AOSP) praticamente não interfere.

Se o seu aparelho não é Samsung, **use este site em vez da seção 2 do
`OPERACAO.md`** — os caminhos de menu que documentamos são One UI.

### Phantom Process Killer (Android 12+)

Limite de processos-filho que atinge em cheio o `llama.cpp` (várias threads) e
qualquer coisa iniciada dentro do Termux. Sintoma: processo some sem log, ou
`[Process completed (signal 9) - press Enter]`.

- **Issue canônica:** https://github.com/termux/termux-app/issues/2366
  (aberta em 2021, com a solução num comentário fixado — leia os comentários,
  não só a descrição; a correção mudou entre versões do Android)
- Busca útil: `termux phantom process killer android 12 disable`

O ajuste via ADB está no `OPERACAO.md` §2.3. Em Android 14+ há relatos de
comportamento diferente — confira a issue antes de assumir que vale igual.

---

## 2. Termux

- **Wiki:** https://wiki.termux.com — raiz; procure por `Termux-services`,
  `Termux:Boot`, `Termux:API`
- **App (F-Droid):** https://f-droid.org/packages/com.termux/
  ⚠️ A versão da Play Store está congelada e **não** funciona com este projeto.
  Os add-ons (`Termux:Boot`, `Termux:API`) precisam vir da **mesma fonte** que
  o app principal, senão a assinatura não confere e eles não se comunicam.
- **Repositório:** https://github.com/termux/termux-app

### Serviços e boot

Usamos `runit` via pacote `termux-services`, e `Termux:Boot` para iniciar no
boot. Alternativas se isso não servir ao seu caso:

- `tmux` + script no `~/.bashrc` (mais simples, menos robusto)
- `cronie` (⚠️ **não** dispara durante o Doze — ver §5)
- Termux:Boot é o único caminho sem root para "iniciar após reiniciar"

---

## 3. Inferência local (llama.cpp)

- **Repositório:** https://github.com/ggml-org/llama.cpp
  ⚠️ O projeto migrou de `ggerganov/llama.cpp` para `ggml-org/llama.cpp`.
  Tutoriais antigos ainda apontam para o endereço antigo.
- **Build para Android:** `docs/` dentro do repositório (busque `android`)
- **Servidor HTTP:** `tools/server/README.md` — parâmetros do `llama-server`
- **Gramáticas GBNF:** `grammars/README.md` — sintaxe da decodificação restrita

### Restrição encontrada neste build (3018a11)

Cada regra GBNF precisava caber em **uma única linha**; continuação com `|`
falhava. Se a sua versão aceitar multilinha, ótimo — verifique com uma
gramática mínima antes de escrever uma grande. Detalhes em `CALENDAR.md` §2.

### Modelos

- **Hugging Face:** https://huggingface.co/models?library=gguf
- Quantizações usadas aqui: `unsloth/Qwen3-1.7B-GGUF`
- Regra prática de RAM: `Q4_K_M ≈ 0,6 GB por bilhão de parâmetros`.
  Meça a RAM realmente livre (`MemAvailable` em `/proc/meminfo`), não a total —
  o SO consome uma fatia grande.

### Escolha de threads

Não copie o `-t 2` daqui. É resultado de medição **neste** SoC. Rode:

```bash
llama-bench -m modelo.gguf -t 2,4,6,8 -p 128 -n 64 -r 3
```

E tire **3+ medições**: a primeira execução costuma vir com boost de
frequência e mentir por um fator de ~2 (aconteceu conosco — `OPERACAO.md` §5).

---

## 4. Rede: Tailscale e SSH

- **Tailscale Android:** https://tailscale.com/kb/1083/install-android
- **KB geral:** https://tailscale.com/kb — busque `always-on VPN`, `exit node`

⚠️ **A Tailscale não reconecta sozinha após reboot no Android.** Ative
*Always-on VPN* nas configurações de VPN do sistema (caminho varia: em One UI
é `Ajustes → Conexões → Mais configurações de conexão → VPN`).

### OpenSSH: `PerSourcePenalties`

- **Notas de versão:** https://www.openssh.com/releasenotes.html — procure
  `9.8` e `PerSourcePenalties`

Introduzido no OpenSSH 9.8 (2024). Penaliza exponencialmente IPs cujas
conexões não completam autenticação — inclusive as **suas**, se você testar
com `timeout` curto. Sintoma: SSH trava sempre no mesmo ponto do handshake.
Diagnóstico e correção em `OPERACAO.md` §8.

**Truque de diagnóstico que vale para qualquer caso:** teste por loopback no
próprio aparelho. Se `ssh -p 8022 user@127.0.0.1` funciona e o remoto não, o
problema está no caminho de rede ou na origem — não no sshd, na criptografia
nem na CPU.

---

## 5. Agendamento sob Doze

O Doze (Android 6+) congela processos ociosos. Consequência prática:
**`cron` e `at` não disparam com a tela apagada.**

- **Documentação:** https://developer.android.com/training/monitoring-device-state/doze-standby
- **JobScheduler:** https://developer.android.com/reference/android/app/job/JobScheduler
- **Alarmes exatos:** busque `SCHEDULE_EXACT_ALARM permission android 12`

### Qual usar

| Situação | Ferramenta |
|---|---|
| Processo já residente (nosso caso) | laço próprio + `termux-wake-lock` |
| App que não está rodando | `termux-job-scheduler` (JobScheduler) |
| Horário exato, Android 12+ | AlarmManager + `SCHEDULE_EXACT_ALARM` |

⚠️ O JobScheduler impõe **janela mínima de ~15 minutos** — inviável para
"me lembra em 5 minutos". Foi por isso que trocamos por laço interno; a
justificativa completa está no cabeçalho de `src/vigia.py`.

---

## 6. Telegram Bot API

- **Documentação:** https://core.telegram.org/bots/api
- **Guia:** https://core.telegram.org/bots

Pontos que custam tempo a quem começa:

- **`offset` no `getUpdates`** — sem avançá-lo você recebe as mesmas mensagens
  em laço infinito. Erro nº 1 de quem usa a API crua.
- **HTTP 409 Conflict** — dois processos consumindo o mesmo token. O Telegram
  aceita **um** consumidor de `getUpdates` por bot.
- **Webhook exige HTTPS público**; atrás de NAT de operadora, use long polling.
- **`sendChatAction` expira em ~5 s** — para respostas mais longas, renove.
- **Bots são públicos.** Qualquer um que descubra o @username fala com o seu
  bot. Filtre por `chat_id`.

---

## 7. Google Calendar / OAuth

- **OAuth para apps nativos:** https://developers.google.com/identity/protocols/oauth2/native-app
- **Calendar API — eventos:** https://developers.google.com/calendar/api/v3/reference/events
- **Recorrência (RRULE), RFC 5545:** https://datatracker.ietf.org/doc/html/rfc5545
- **Console:** https://console.cloud.google.com
- **Revogar acessos:** https://myaccount.google.com/permissions

Armadilhas verificadas na prática (detalhes em `CALENDAR.md` §3):

- **Chave de API (`AIza…`) não serve.** Acessa só dados públicos, nunca a
  agenda de um usuário. É OAuth, obrigatoriamente.
- **Projeto `gen-lang-client-*`** foi criado automaticamente pelo AI Studio e
  costuma vir sem tela de permissão OAuth e sem a Calendar API ativada.
- **`127.0.0.1` ≠ `localhost`** na validação do `redirect_uri` → `Erro 400:
  invalid_request`.
- **Modo "Testing": refresh token expira em 7 dias.** Publique o app para uso
  contínuo.
- **Fluxo OOB ("copie o código") foi descontinuado** — busque
  `google oauth out-of-band deprecation`. Use loopback com porta efêmera.
- **`BYMONTHDAY=31` pula meses sem dia 31.** Para vencimento, use
  `BYMONTHDAY=31,-1;BYSETPOS=1`.

---

## 8. Quando nada disso resolver

Ordem de diagnóstico que funcionou repetidamente neste projeto — **camada de
baixo primeiro**, porque investigar serviço sem confirmar rota desperdiça
horas:

```
1. o aparelho está na rede?      tailscale status | grep <host>
2. responde?                     ping -c 3 <ip>
3. a porta está aberta?          nc -vz <ip> <porta>
4. o processo existe?            ps -ef | grep <nome>
5. o que o log diz?              tail -50 <log do serviço>
```

Erramos essa ordem pelo menos duas vezes e perdemos tempo investigando o
Termux quando o problema era a VPN caída.

### Comunidades

- **Termux:** https://github.com/termux/termux-app/issues (busque antes de
  abrir; a maioria dos problemas de OEM já está documentada)
- **llama.cpp:** https://github.com/ggml-org/llama.cpp/discussions
- **Tailscale:** https://forum.tailscale.com
- **Stack Overflow:** tags `termux`, `android-doze`, `llama.cpp`,
  `google-calendar-api`, `python-telegram-bot`

Ao pedir ajuda, informe sempre: **fabricante + versão do Android + versão da
camada do fabricante** (One UI, MIUI, EMUI…). Metade das respostas sobre
processos mortos em segundo plano depende disso.
