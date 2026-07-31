# 🔧 Runbook de Operação — Caipora

Guia de manutenção do dispositivo servidor (Samsung Galaxy M52 5G / Termux).
Tudo aqui foi aplicado e verificado em **2026-07-29/30**.

> 📱 **Outro aparelho?** Os caminhos de menu abaixo são do One UI (Samsung) e
> as medições são do Snapdragon 778G. Para outros fabricantes — sobretudo
> Xiaomi, Huawei e OnePlus, bem mais agressivos que a Samsung — veja
> [`REFERENCIAS.md`](REFERENCIAS.md), que aponta onde procurar o equivalente
> em cada caso.

---

## 1. Identidade do dispositivo

| Item | Valor |
|---|---|
| Modelo | Samsung Galaxy M52 5G (`SM-M526B`, codinome `m52xq`) |
| SoC | Snapdragon 778G — Kryo 670 (1×A78 2.4GHz + 3×A78 2.2 + 4×A55 1.9) |
| RAM | 6 GB (orçamento real utilizável: **~2,5 GB**) |
| Android | 13 (One UI 5), kernel 5.4.233 |
| Tailscale | `galaxy-m52-5g` → `100.115.15.59` |
| Usuário Termux | `u0_a309` |

Acesso: `ssh caipora` (alias já configurado em `~/.ssh/config` da máquina de trabalho).

---

## 2. Ajustes de sistema obrigatórios

Sem estes ajustes o Android mata os serviços de forma silenciosa e intermitente —
o sintoma é "funcionava e parou", sem nada nos logs.

### 2.1 Bateria (feito pela interface do One UI)

Para **Termux** *e* **Tailscale**, separadamente:

- `Ajustes → Apps → <app> → Bateria → Sem restrições`
- `Ajustes → Bateria → Limites de uso em segundo plano` → remover o app de
  "Apps em suspensão" / "Apps nunca usados"

No app **Tailscale**, habilitar também o **acesso à LAN**. Isso foi o que
estabilizou o túnel (antes dele, a VPN caía minutos após a tela apagar e
derrubava qualquer conexão no meio do handshake).

### 2.2 Proteção da bateria

O aparelho fica ligado na tomada 24/7. Célula a 100% permanente incha em poucos
meses. Habilitar:

- `Ajustes → Bateria → Mais configurações de bateria → Proteger bateria` (limita a 85%)

### 2.3 Phantom Process Killer (via ADB, uma vez)

O Android 12+ mata processos filhos além de um limite baixo — alvo direto de um
`llama-server` com múltiplas threads. Só se desativa via ADB:

```bash
adb shell "device_config set_sync_disabled_for_tests persistent"
adb shell "settings put global settings_enable_monitor_phantom_procs false"
adb shell "device_config put activity_manager max_phantom_processes 2147483647"
```

Verificar:

```bash
adb shell "settings get global settings_enable_monitor_phantom_procs"   # false
adb shell "device_config get activity_manager max_phantom_processes"     # 2147483647
```

> `set_sync_disabled_for_tests persistent` é necessário **antes**, senão o
> Google reverte o valor remotamente.

---

## 3. Serviços

Gerenciados por **runit** (`termux-services`). Definições em
`$PREFIX/var/service/<nome>/`.

| Serviço | Bind | Função |
|---|---|---|
| `sshd` | `0.0.0.0:8022` | acesso remoto |
| `llama-server` | `127.0.0.1:8080` | inferência LLM |
| `caipora-bot` | — (só saída) | bot do Telegram |

Todos com auto-restart **verificado** (`kill -9` → volta em ~10 s).

### 3.1 Comandos do dia a dia

⚠️ Em sessão SSH **não-interativa** o `$SVDIR` não é exportado e o `sv` falha com
`unable to change to service directory`. Exporte antes:

```bash
export SVDIR=$PREFIX/var/service

sv status llama-server      # estado
sv up    llama-server       # subir
sv down  llama-server       # derrubar
sv restart llama-server     # reiniciar
```

Habilitar/desabilitar autostart (é a presença do arquivo `down` que controla):

```bash
rm -f $SVDIR/llama-server/down     # habilita
touch $SVDIR/llama-server/down     # desabilita
```

### 3.2 Logs

```bash
tail -f $PREFIX/var/log/sv/llama-server/current
tail -f $PREFIX/var/log/sv/sshd/current
```

> Estes logs **não** são legíveis via `adb shell` (uid `shell` ≠ uid do app).
> Precisa ser por SSH ou no próprio Termux.

### 3.3 Auto-restart

Verificado: matar o processo com `kill -9` faz o runsv ressuscitá-lo em ~10s.

Para descobrir o PID **do servidor** (e não do logger), ancore o `sed` no início
da linha — `.*` guloso pega o PID errado:

```bash
sv status llama-server | sed -n "s/^run: llama-server: (pid \([0-9]*\)).*/\1/p"
```

### 3.4 Boot

Script em `~/.termux/boot/00-caipora.sh`: aplica `termux-wake-lock` e sobe o
supervisor runit, que por sua vez inicia todos os serviços sem arquivo `down`.

**Pendência:** requer o app **Termux:Boot**, instalado pelo **F-Droid**
(a versão da Play Store não serve). Depois de instalar, **abra o app uma vez** —
ele só registra o receptor de boot após a primeira execução. Sem isso, o script
não roda após reiniciar o aparelho.

**✅ VALIDADO EM REBOOT REAL (2026-07-30).** Após reiniciar o aparelho, os três
serviços subiram sozinhos com uptime consistente com o boot:

```
run: caipora-bot:  (pid 22952)  155s
run: llama-server: (pid 6810)  1068s
run: sshd:         (pid 6812)  1068s
```

O `caipora-bot` com uptime menor que os outros **não é falha** — é a
recuperação automática funcionando: sem internet no momento do boot, ele
tenta autenticar 10 vezes com backoff (~225 s), sai com log claro, e o runit
o ressuscita quando a rede volta.

### ⚠️ A Tailscale NÃO reconecta sozinha após reboot

Este é o ponto que engana. Depois do primeiro reboot, `ssh caipora` deu
timeout e a suspeita natural foi o Termux:Boot — mas o Termux:Boot tinha
funcionado. O aparelho estava **fora da rede**:

```
$ tailscale status | grep galaxy
100.115.15.59  galaxy-m52-5g  ...  offline, last seen 5m ago
```

Sem rota até o aparelho, o SSH não teria como chegar. **Diagnostique sempre
nesta ordem** — não adianta investigar serviço se não há rede:

```bash
tailscale status | grep galaxy      # 1. o aparelho está na tailnet?
ping -c 3 100.115.15.59             # 2. responde?
ssh caipora "echo ok"               # 3. só então o SSH
```

**Correção permanente** (no aparelho, uma vez):

`Ajustes → Rede e Internet → VPN` → engrenagem do **Tailscale** →
ativar **VPN sempre ativa** (*Always-on VPN*).

Em alguns One UI: `Ajustes → Conexões → Mais configurações de conexão → VPN`.

### Depuração sem fio também cai no reboot

O ADB sem fio costuma desligar sozinho ao reiniciar. Para reativar:
`Opções do desenvolvedor → Depuração sem fio` — e **a porta de conexão muda**.

---

## 4. Configuração do LLM

Definição do serviço em `$PREFIX/var/service/llama-server/run`:

```sh
exec $HOME/llama.cpp/build/bin/llama-server \
  -m $HOME/models/Qwen3-1.7B-Q4_K_M.gguf \
  -t 2 \
  -c 4096 \
  --reasoning-budget 0 \
  --host 127.0.0.1 \
  --port 8080
```

### Por que cada flag

- **`-t 2`** — medido, não deduzido. Ver §5. Melhor geração, menor variância,
  e deixa 4 núcleos livres para o roteador, o SO e o sshd.
- **`--reasoning-budget 0`** — o Qwen3 é modelo de raciocínio **híbrido e com
  thinking ligado por padrão**. Sem esta flag, requisições gastam todo o
  orçamento de tokens em `reasoning_content` e devolvem `content` **vazio**.
  Desligar no servidor (e não por requisição) garante que nenhum caminho de
  código esqueça.
- **`--host 127.0.0.1`** — bind local deliberado. O roteador roda no próprio
  aparelho. Vincular ao IP da Tailscale causaria crash-loop no boot, porque o
  serviço subiria antes da VPN existir.

### Acesso remoto para testes

Como o bind é local, use túnel SSH em vez de expor a porta:

```bash
ssh -N -L 8080:127.0.0.1:8080 caipora &
curl http://127.0.0.1:8080/v1/chat/completions ...
```

### Trocar de modelo

```bash
cd ~/models
curl -fL -o NOVO.gguf "<url>"
# editar o -m em $PREFIX/var/service/llama-server/run
export SVDIR=$PREFIX/var/service && sv restart llama-server
```

---

## 5. Desempenho medido

Qwen3 1.7B Q4_K_M, llama.cpp build `3018a11`, `pp128`/`tg64`, 2–3 repetições:

| threads | prompt (t/s) | variância | geração (t/s) |
|---|---|---|---|
| **2** | 42,2 | **±0,02** | **12,7** |
| 4 | 39,5 – 43,3 | ±3,7 a ±5,8 | 10,8 |
| 6 | 49,1 | ±0,11 | 10,9 |
| 8 | 44,7 | ±1,53 | 8,0 |

**Conclusões:**

1. **Geração é limitada por banda de memória**, não por CPU — mais threads só
   adicionam contenção (12,7 → 8,0 t/s de `t=2` para `t=8`). Por isso `-t 2`.
2. **Cuidado com o boost da primeira medição.** A primeira execução (aparelho
   recém-acordado) reportou **81,1 t/s** de prompt — ~2× o valor sustentado.
   Três medições seguintes ficaram em 40–49 t/s. **Sempre tire 3+ medições**
   antes de concluir qualquer coisa neste aparelho.
3. **O estado térmico/energético oscila o throughput ~2×.** Temperaturas
   estabilizam em ~43-44 °C sob carga. Consequência de projeto: instrumentar
   `/sys/class/thermal/thermal_zone*/temp` desde a V1 — a mesma requisição pode
   demorar o dobro sem explicação visível ao usuário.

Reproduzir o benchmark (sempre destacado, senão morre com SIGHUP ao cair o SSH):

```bash
cd ~/llama.cpp
setsid nohup ./build/bin/llama-bench \
  -m ~/models/Qwen3-1.7B-Q4_K_M.gguf \
  -t 2,4,6,8 -p 128 -n 64 -r 2 > ~/bench.log 2>&1 < /dev/null & disown
```

Temperaturas:

```bash
for z in /sys/class/thermal/thermal_zone*/; do
  printf "%s %s\n" "$(cat $z/temp)" "$(cat $z/type)"
done | sort -rn | head -10
```

---

## 6. Recompilar o llama.cpp

```bash
cd ~/llama.cpp && git pull
cmake -B build -DCMAKE_BUILD_TYPE=Release -DGGML_NATIVE=ON -DLLAMA_CURL=OFF
cmake --build build --config Release -j4      # ~10-15 min neste aparelho
export SVDIR=$PREFIX/var/service && sv restart llama-server
```

O build detecta corretamente: **`dotprod` presente**, **`i8mm`/SVE/SME ausentes**
(o A78 é ARMv8.2 — `i8mm` só existe a partir do ARMv8.6/A710). Isso significa que
os ganhos de repack Q4_0 no prompt processing são parciais neste hardware.

> Compilação **nativa** no Termux, deliberadamente **sem** proot/Ubuntu: o proot
> usa `ptrace` e o custo cai justamente em criação de threads, I/O e mmap do
> GGUF — exatamente o que importa para inferência.

---

## 6.1 Bot do Telegram

Bot: **@caipora_forestbot**. Código em `~/caipora/` no aparelho.

Recebe mensagens por **long polling**, não webhook — escolha deliberada: o
celular está atrás do NAT da operadora, sem IP público nem certificado TLS.
Com polling só existem conexões de saída, o que funciona em qualquer rede,
inclusive 4G.

Roda como serviço supervisionado `caipora-bot`:

```bash
export SVDIR=$PREFIX/var/service
sv status  caipora-bot
sv restart caipora-bot                                # forma correta de reiniciar
tail -f $PREFIX/var/log/sv/caipora-bot/current        # log
```

> O `run` do serviço usa `python -u`. Sem o `-u`, o Python acumula stdout
> quando não há tty e os logs aparecem no `svlogd` muito depois — ou nunca.

### HTTP 409 no log

`HTTP Error 409: Conflict` significa que **dois processos estão consumindo o
mesmo token** — o Telegram aceita apenas um consumidor de `getUpdates` por bot.
Causa típica: uma instância solta (lançada à mão com `setsid`) competindo com o
serviço. Verifique:

```bash
ps -eo pid,args | grep 'bot[.]p''y' | grep -v grep
```

O código trata 409 como transitório de propósito (resolve quando o duplicado
morre), mas erros **permanentes** — 401/403/404, token inválido ou revogado —
encerram o processo com mensagem clara em vez de girar em loop silencioso.

### Allowlist

Bots do Telegram são **públicos**: qualquer um que descubra o @username manda
mensagem. `TELEGRAM_CHAT_ID` no `.env` é a allowlist (aceita vários ids
separados por vírgula). Remetente não autorizado é ignorado **em silêncio** —
responder "acesso negado" confirmaria que o bot existe e convidaria insistência.

Para descobrir o chat_id de alguém: peça para a pessoa mandar mensagem e veja
o log.

### Desempenho em produção

Medido em uso real (2026-07-29): **12,7 t/s de geração** — idêntico ao
benchmark de bancada com `-t 2`, confirmando a escolha de configuração.
Resposta típica de 43 tokens em 3,4 s. Com o modelo carregado sobra
**~1,33 GB de RAM livre**, o que descarta manter um segundo modelo residente.

---

## 7. Segurança — pendências conhecidas

| Item | Estado | Ação recomendada |
|---|---|---|
| `llama-server` | `127.0.0.1`, sem API key, CORS `*` | OK enquanto local; se expor, usar `--api-key` |
| `sshd` | `0.0.0.0:8022`, chave pública | considerar restringir ao IP da Tailscale |
| `PerSourcePenalties` | **desativado** | ver §8 |
| Bot do Telegram | allowlist por `chat_id` | ✅ ativa |
| Token do bot | em `.env`, fora do git | `.gitignore` corrigido — o template original era de Android Studio e **não** ignorava `.env` |

---

## 8. Troubleshooting

### SSH conecta mas travava antes da autenticação

Já resolvido, mas se voltar: o OpenSSH 9.8+ tem o mecanismo anti-DoS
`PerSourcePenalties`, que **penaliza exponencialmente** um IP de origem cujas
conexões não completam a autenticação. Testes com timeout curto alimentam essa
penalidade até bloquear o IP por completo. Assinatura no log:

```
srclimit_penalise: <ip>: activating ipv4 penalty ... exceeded LoginGraceTime
```

Correção aplicada (`$PREFIX/etc/ssh/sshd_config`):

```
PerSourcePenalties no
UseDNS no
```

**Truque de diagnóstico que resolveu o caso:** testar por loopback no próprio
aparelho (`ssh -p 8022 u0_a309@127.0.0.1`). Se o loopback funciona e o remoto
não, o problema está no caminho de rede/origem — não no sshd, na criptografia
nem na CPU.

### Bot reinicia sozinho / `ConnectionAbortedError` no log

Rede de celular derruba conexão o tempo todo. O tratamento captura `OSError`
(não apenas `URLError`/`TimeoutError`) porque, quando a conexão morre durante
a **leitura da resposta SSL**, o erro sobe cru, sem o embrulho do urllib:

```
ConnectionAbortedError: [Errno 103] Software caused connection abort
```

Isso derrubou o processo em produção antes da correção. `URLError` e
`TimeoutError` são subclasses de `OSError`, então capturar `OSError` cobre os
três casos. `ErroPermanente` **não** é `OSError` de propósito — token inválido
precisa matar o processo com log claro, não virar retry infinito.

Travado por `tests/test_resiliencia.py`, que também verifica a hierarquia de
exceções: se o Python mudar isso, o teste quebra antes do bug voltar.

### Serviço morre sem deixar rastro

Ordem de investigação:

1. `tail -50 $PREFIX/var/log/sv/<servico>/current`
2. Confirmar phantom killer desativado (§2.3)
3. Confirmar bateria sem restrição para o Termux (§2.1)
4. Se foi lançado à mão, foi `SIGHUP` ao cair o SSH — use `setsid nohup ... & disown`

### Aparelho offline na Tailscale

```bash
tailscale status | grep galaxy     # da máquina de trabalho
```

Se aparecer `offline, last seen Xm ago`: abrir o app Tailscale no aparelho e
revisar §2.1 (acesso à LAN + bateria sem restrição).

### `pkill -f` mata a própria sessão SSH

`pkill -f <padrão>` casa com a linha de comando do shell remoto, que **contém o
próprio padrão**. Aconteceu duas vezes durante o desenvolvimento; sintoma é o
comando retornar `exit 255` sem nenhuma saída.

O truque do colchete resolve — `pkill -f 'bot[.]py'` — porque o regex
`bot[.]py` casa com a string `bot.py`, mas a linha de comando contém
`bot[.]py` *com* os colchetes, que não casa.

**Mas o truque falha** se o nome aparecer literal em outro trecho do mesmo
comando. Isto se auto-mata, apesar dos colchetes:

```bash
# ERRADO: 'python bot.py' no final casa com o padrão
ssh caipora "pkill -f 'bot[.]py'; cd ~/caipora/src && nohup python bot.py &"
```

**Regra:** nunca combine `pkill` e o start do processo na mesma invocação.
Sempre dois comandos separados.

### Reiniciar o bot corretamente

```bash
ssh caipora "pkill -f 'bot[.]py'"                                   # 1
ssh caipora "cd ~/caipora/src && setsid nohup python bot.py \
    > ~/caipora/bot.log 2>&1 < /dev/null & disown"                  # 2
ssh caipora "cat ~/caipora/bot.log"                                 # 3
```

---

## 9. Canal de acesso alternativo (ADB sem fio)

Fallback para quando o Termux/SSH estiver inacessível, e o único caminho para
ajustes de SO (§2.3).

```bash
# Pareamento (uma vez, exige mesma Wi-Fi local — não funciona pela Tailscale)
adb pair <ip-local>:<porta-pareamento> <codigo-6-digitos>
# Conexão (funciona pela Tailscale depois de pareado)
adb connect 100.115.15.59:<porta-conexao>
```

Portas e código ficam em `Ajustes → Opções do desenvolvedor → Depuração sem fio`.
A porta de conexão **muda** quando o serviço reinicia.

Limitação: `adb shell` roda como uid `shell` (2000), **diferente** do uid do
Termux (`u0_a309`). Serve para inspeção do SO (`ps -ef`, `getprop`, `settings`),
mas **não** lê o armazenamento privado do Termux nem executa `pkg`.
