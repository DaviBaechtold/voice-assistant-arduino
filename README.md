# 🎙️ Voice Assistant Multi-Dispositivo para Arduino

Sistema de assistente de voz distribuído usando Arduino Nano 33 BLE Sense e Python, permitindo que múltiplos usuários (motorista e passageiro) interajam independentemente com comandos de voz via WiFi.

## 📋 Índice

- [Visão Geral](#-visão-geral)
- [Características](#-características)
- [Arquitetura do Sistema](#-arquitetura-do-sistema)
- [Requisitos](#-requisitos)
- [Instalação](#-instalação)
- [Configuração](#-configuração)
- [Como Usar](#-como-usar)
- [Estrutura do Projeto](#-estrutura-do-projeto)
- [Detalhes Técnicos](#-detalhes-técnicos)
- [Comandos Disponíveis](#-comandos-disponíveis)
- [Solução de Problemas](#-solução-de-problemas)

## 🎯 Visão Geral

Este projeto implementa um sistema de assistente de voz distribuído onde:

- **2 Arduinos** capturam áudio simultaneamente via microfones PDM
- **1 Servidor Python** processa reconhecimento de voz e comandos
- **Wake Words específicas** ativam cada dispositivo independentemente
- **Respostas contextuais** baseadas no usuário (motorista vs passageiro)

### Fluxo de Funcionamento

```
Arduino (Motorista) ──┐
                     ├─► WiFi/UDP ──► Python Server ──► Speech Recognition ──► Commands ──► TTS
Arduino (Passageiro) ──┘
```

## ✨ Características

- 🎤 **Captura de áudio simultânea** de múltiplos dispositivos
- 🔊 **Wake words específicas** ("motorista" / "passageiro")
- 🧠 **Reconhecimento de voz** via Google Speech API
- 🗣️ **Text-to-Speech** para respostas
- 📡 **Comunicação UDP** via WiFi
- 💾 **Gravação automática** das sessões
- 🔄 **Sistema multi-threading** para processamento paralelo
- 📊 **Monitoramento em tempo real** do status dos dispositivos

## 🏗️ Arquitetura do Sistema

### Componentes

| Componente | Função | Tecnologia |
|------------|--------|------------|
| **Arduino Nano 33 BLE** | Captura áudio via microfone PDM | WiFiNINA, PDM |
| **Servidor Python** | Processamento de voz e comandos | SpeechRecognition, pyttsx3 |
| **Comunicação** | Transmissão de dados de áudio | UDP sobre WiFi |

### Fluxo de Dados

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Arduino       │    │   Rede WiFi     │    │   Python        │
│                 │    │                 │    │                 │
│ 🎤 Microfone    │    │                 │    │ 🧠 Recognition  │
│ ↓               │    │                 │    │ ↓               │
│ 📊 PDM.read()   │ ──►│ 📡 UDP Packets  │──► │ 🔍 Wake Word    │
│ ↓               │    │                 │    │ ↓               │
│ 📦 UDP.send()   │    │                 │    │ 🎯 Commands     │
│                 │    │                 │    │ ↓               │
│                 │    │                 │    │ 🔊 TTS Response │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

## 📋 Requisitos

### Hardware
- 2x Arduino Nano 33 BLE Sense
- Rede WiFi
- Computador para executar o servidor Python

### Software
- Arduino IDE 1.8+
- Python 3.8+
- Bibliotecas Arduino:
  - WiFiNINA
  - PDM
- Bibliotecas Python:
  ```bash
  pip install numpy wave speechrecognition pyttsx3 pyaudio
  ```

## 🚀 Instalação

### 1. Clone o Repositório
```bash
git clone https://github.com/seu-usuario/voice-assistant-arduino.git
cd voice-assistant-arduino
```

### 2. Instale Dependências Python
```bash
pip install -r requirements.txt
```

### 3. Configure os Arduinos

#### Arduino Motorista
- Carregue `mic_motorista.ino`
- Device ID: `1`
- Porta UDP local: `2390`

#### Arduino Passageiro
- Carregue `mic_passageiro.ino`
- Device ID: `2`
- Porta UDP local: `2391`

## ⚙️ Configuração

### 1. Configurar WiFi nos Arduinos
```cpp
const char* ssid = "SUA_REDE_WIFI";
const char* password = "SUA_SENHA";
const char* host_ip = "IP_DO_COMPUTADOR_PYTHON";
```

### 2. Configurar Wake Words (Python)
```python
self.wake_words = {
    1: "motorista",   # Wake word para Arduino ID 1
    2: "passageiro"   # Wake word para Arduino ID 2
}
```

## 🎮 Como Usar

### 1. Iniciar o Sistema

#### Execute o servidor Python:
```bash
python microfone.py
```

#### Carregue os códigos nos Arduinos e conecte-os

### 2. Ativar o Assistente

- **Motorista**: Diga **"motorista"** próximo ao primeiro Arduino
- **Passageiro**: Diga **"passageiro"** próximo ao segundo Arduino

### 3. Dar Comandos

Após detectar a wake word:
1. O sistema inicia gravação automática
2. Fale seu comando
3. O sistema para após 2 segundos de silêncio
4. Receba a resposta via TTS

## 📁 Estrutura do Projeto

```
voice-assistant-arduino/
│
├── mic_motorista.ino          # Código Arduino motorista
├── mic_passageiro.ino         # Código Arduino passageiro
├── microfone.py              # Servidor Python principal
├── requirements.txt          # Dependências Python
├── README.md                # Este arquivo
│
└── recordings/              # Gravações das sessões (criado automaticamente)
    ├── session_motorista_20241201_143052_5.2s.wav
    └── session_passageiro_20241201_143128_3.8s.wav
```

## 🔧 Detalhes Técnicos

### Protocolo de Comunicação

#### Estrutura do Pacote UDP
```cpp
struct AudioPacket {
    uint32_t timestamp;      // Timestamp da captura
    uint16_t device_id;      // 1=Motorista, 2=Passageiro
    uint16_t sample_rate;    // 16000 Hz
    uint16_t samples_count;  // Número de samples
    uint16_t checksum;       // Verificação de integridade
} + short audioBuffer[];     // Dados de áudio RAW
```

### Configurações de Áudio
- **Sample Rate**: 16 kHz
- **Formato**: 16-bit PCM Mono
- **Buffer Size**: 512 samples por pacote
- **Latência**: ~0.5 segundos

### Sistema Multi-Threading
- **receive_loop()**: Recebe pacotes UDP dos Arduinos
- **process_audio()**: Processa wake words e comandos
- **status_monitor()**: Monitora status dos dispositivos

## 🎵 Comandos Disponíveis

### Comandos Básicos
- **"Olá"** → Saudação personalizada
- **"Que horas são?"** → Horário atual
- **"Clima"** → Informações meteorológicas (placeholder)
- **"Obrigado"** → Agradecimento

### Comandos Contextuais

#### Motorista
- **"Música"** → "Que tipo de música relaxante você gostaria?"
- **"Navegação"** → "Para onde você gostaria de ir? Vou configurar a rota"

#### Passageiro
- **"Música"** → "Que tipo de música você gostaria de ouvir durante a viagem?"
- **"Navegação"** → "Vou informar ao motorista sobre o destino desejado"

## 🐛 Solução de Problemas

### Arduino não conecta ao WiFi
```cpp
// Verifique as credenciais
const char* ssid = "NOME_CORRETO_DA_REDE";
const char* password = "SENHA_CORRETA";
```

### Python não recebe dados
1. Verifique se o IP do computador está correto nos Arduinos
2. Confirme que a porta 8888 não está bloqueada
3. Teste conectividade: `ping IP_DO_ARDUINO`

### Wake word não é detectada
1. Fale claramente próximo ao microfone
2. Verifique logs: `"🔍 Tentando reconhecer wake word"`
3. Ajuste threshold de áudio se necessário

### Erro de reconhecimento de voz
1. Verifique conexão com internet (Google Speech API)
2. Confirme que o microfone está captando áudio
3. Teste com comandos mais simples

## 📊 Logs e Monitoramento

### Status dos Dispositivos
```
📡 Status Multi-Dispositivo:
  ✅ Motorista (ID 1): 45 pacotes, 12.3KB, 3 tentativas de wake word
  ✅ Passageiro (ID 2): 38 pacotes, 10.1KB, 1 tentativas de wake word
🎧 Sistema aguardando wake words...
```

### Detecção de Wake Word
```
🔍 Tentando reconhecer wake word - Motorista (nível: 1520)
🎯 Reconhecido: 'motorista teste' de Motorista
🎙️  WAKE WORD DETECTADA - Motorista! Iniciando gravação...
```

### Gravação Ativa
```
🎙️  [Motorista] Gravando: [████████████████████] Nível: 2150
⏹️  GRAVAÇÃO FINALIZADA - Motorista - Processando áudio...
[MOTORISTA] Disse: 'Que horas são'
📁 Áudio salvo: session_motorista_20241201_143052_5.2s.wav
⏱️  Duração: 5.2 segundos
[ASSISTENTE] Respondendo: 'Agora são 14 horas e 30 minutos'
```

## 🚧 Divisão de Responsabilidades

### 🎤 Arduino (Captura e Transmissão)
- **Função**: Sensor de áudio remoto via WiFi
- **O que FAZ**:
  - ✅ Captura áudio RAW do microfone PDM
  - ✅ Empacota dados com metadados (device_id, timestamp)
  - ✅ Transmite via UDP para o servidor Python
- **O que NÃO FAZ**:
  - ❌ Reconhecimento de voz
  - ❌ Processamento de comandos
  - ❌ Detecção de wake words

### 🖥️ Python (Processamento Inteligente)
- **Função**: Cérebro do sistema
- **O que FAZ**:
  - ✅ Recebe dados RAW dos Arduinos
  - ✅ Converte áudio em texto (Speech-to-Text)
  - ✅ Detecta wake words específicas
  - ✅ Processa comandos de voz
  - ✅ Gera respostas contextuais
  - ✅ Converte texto em fala (Text-to-Speech)

## 🤝 Contribuição

1. Fork o projeto
2. Crie uma branch para sua feature (`git checkout -b feature/AmazingFeature`)
3. Commit suas mudanças (`git commit -m 'Add some AmazingFeature'`)
4. Push para a branch (`git push origin feature/AmazingFeature`)
5. Abra um Pull Request

## 📄 Licença

Distribuído sob a licença MIT. Veja `LICENSE` para mais informações.

## 📞 Contato

Dav's - [davicampos2002@gmail.com](davicampos2002@gmail.com)

Link do Projeto: [https://github.com/DaviBaechtold/agora-vai](https://github.com/DaviBaechtold/agora-vai)

---

⭐ **Se este projeto foi útil, deixe uma estrela!**