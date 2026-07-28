# 🔥Caipora: Assistente Pessoal Local em Android Reproveitado

![Licença](https://img.shields.io/badge/License-MIT-blue.svg)
![Python](https://img.shields.io/badge/Python-3.10+-yellow.svg)
![Plataforma](https://img.shields.io/badge/Platform-Termux%20%7C%20Android-green.svg)

> Transformando um smartphone antigo (Samsung Galaxy M52) em um servidor de Inteligência Artificial 100% local, autônomo e focado em privacidade.

## 📖 Sobre o Projeto

O **PocketAgent** é uma prova de conceito e um laboratório pessoal de IA. O objetivo é contornar as limitações de hardware de um smartphone intermediário (Snapdragon 778G, 6GB RAM) para rodar Pequenos Modelos de Linguagem (SLMs) localmente, integrando-os a ferramentas do dia a dia.

Para economizar recursos e evitar _Out of Memory (OOM)_, este projeto **abandona frameworks pesados como LangChain/LangGraph**, utilizando uma arquitetura modular minimalista em Python puro e `llama.cpp`.

### ✨ Funcionalidades (Versão 1.0)
- **🧠 Inferência 100% Local:** Sem APIs de terceiros para processamento de texto, garantindo privacidade total usando modelos quantizados (GGUF).
- **💬 Interface via Telegram:** Comunicação fluida com o assistente usando a API oficial do Telegram.
- **📅 Automação Determinística:** Integração direta com o Google Calendar para agendamento de eventos sem risco de "alucinações" da IA.
- **🔋 Otimizado para Mobile:** Rodando sob um ambiente Proot/Ubuntu via Termux, projetado para baixo consumo de bateria e RAM.

---

## 🏗️ Arquitetura do Sistema

O sistema é dividido em três módulos principais que operam em paralelo:

1. **O Cérebro (`llama.cpp`):** Servidor HTTP leve rodando o modelo LLM.
2. **O Roteador (`bot_telegram.py`):** Recebe as mensagens, decide se é um comando estrito ou conversação, e faz o roteamento.
3. **As Ferramentas (`google_calendar.py`):** Scripts de execução determinística para APIs externas.

---

## 🛠️ Requisitos de Hardware e Software

- **Dispositivo:** Smartphone Android (Testado em Samsung M52 5G / Snapdragon 778G). Mínimo de 6GB de RAM recomendados.
- **Sistema Base:** [Termux](https://f-droid.org/packages/com.termux/) (Versão F-Droid, **não** usar a da Play Store) com permissões de bateria irrestritas.
- **Ambiente Linux:** `proot-distro` rodando Ubuntu ou Debian.
- **Modelo Recomendado:** Phi-3-Mini (4-bit) ou Qwen-1.5-1.8B (GGUF).

---

## 🚀 Como Instalar e Rodar

### 1. Preparação do Dispositivo (Termux)
Certifique-se de que o Termux tem acesso ao armazenamento e instale o ambiente base:
```bash
pkg update && pkg upgrade
pkg install proot-distro git
proot-distro install ubuntu
proot-distro login ubuntu
```

### 2. Clonando o Repositório e Dependências
Dentro do ambiente Ubuntu:

```bash
apt update && apt install python3 python3-pip wget
git clone [https://github.com/SeuUsuario/PocketAgent.git](https://github.com/SeuUsuario/PocketAgent.git)
cd PocketAgent
pip install -r requirements.txt
```
### 3. Configurando as Variáveis de Ambiente
Copie o arquivo de exemplo e insira suas credenciais:

```bash
cp .env.example .env
nano .env
(Preencha seu TELEGRAM_BOT_TOKEN e os caminhos necessários).
```

### 4. Baixando o Modelo de IA
Baixe um modelo compatível no formato GGUF (ex: via HuggingFace) e coloque na pasta /models:

```bash
wget [URL_DO_MODELO_GGUF] -O ./models/modelo-slm.gguf
```
### 5. Iniciando os Serviços
Recomenda-se o uso de tmux ou pm2 para manter os processos ativos. Para um teste rápido:

```bash
# Terminal 1: Inicie o servidor do LLM
./src/start_llm.sh

# Terminal 2: Inicie o roteador do bot
python3 src/bot_telegram.py
```

## 🗺️ Roadmap e Próximos Passos
[x] V1: Estrutura base, integração com Telegram e LLM local.

[x] V1.1: Automação determinística com Google Calendar.

[ ] V2: Extração de entidades (JSON estructured output) via LLM.

[ ] V3: Integração com Whisper.cpp para transcrição de áudio local.

[ ] V4: Implementação de arquitetura híbrida (Fallback para APIs em nuvem caso o hardware local atinja limites térmicos).

## 🤝 Como Contribuir
Sugestões e Pull Requests são muito bem-vindos! Como este é um projeto focado em otimização extrema para hardware limitado, ideias para reduzir o uso de RAM ou melhorar a velocidade de inferência (tokens/s) são o foco principal.

- ** Faça um Fork do projeto
- ** Crie sua Feature Branch (git checkout -b feature/NovaAutomacao)
- ** Faça o Commit de suas mudanças (git commit -m 'Adiciona integração com X')
- ** Faça o Push para a Branch (git push origin feature/NovaAutomacao)
- ** Abra um Pull Request

## 📝 Licença
Distribuído sob a licença MIT. Veja LICENSE para mais informações.

Desenvolvido com ☕ e resiliência de hardware por [gutoportelaa].
