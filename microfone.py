import socket
import struct
import numpy as np
import wave
import threading
import queue
import time
from datetime import datetime
import speech_recognition as sr
import pyttsx3
import pyaudio

class AudioReceiver:
    def __init__(self, port=8888):
        self.port = port
        self.socket = None
        self.running = False
        self.audio_queue = queue.Queue()
        self._just_finished_recording = False
        
        # Configurações de áudio
        self.sample_rate = 16000
        self.channels = 1
        self.sample_width = 2  # 16-bit
        
        # Buffers por dispositivo
        self.device_buffers = {
            1: [],  # Motorista
            2: []   # Passageiro
        }
        
        # Buffers contínuos por dispositivo para wake word
        self.device_continuous_buffers = {
            1: [],  # Motorista
            2: []   # Passageiro
        }
        
        # Sistema de ativação por palavra-chave
        self.wake_words = {
            1: "motorista",  # Motorista
            2: "passageiro"   # Passageiro
        }
        self.listening_mode = False
        self.active_device = None  # Qual dispositivo está gravando
        self.recording_buffer = []
        self.silence_counter = 0
        self.max_silence_frames = 20  # ~2 segundos de silêncio
        
        # Gravação completa de sessão
        self.session_audio = []
        self.session_recording = False
        self.session_start_time = None
        
        # Controle de logs por dispositivo
        self.last_status_time = 0
        self.packet_count = {1: 0, 2: 0}
        self.bytes_received = {1: 0, 2: 0}
        
        # Debug - contadores de detecção
        self.wake_word_attempts = {1: 0, 2: 0}
        self.last_recognition_time = 0
        
        # Voice Assistant
        self.recognizer = sr.Recognizer()
        self.tts = pyttsx3.init()
        self.setup_tts()
        
        # PyAudio para reprodução
        self.audio = pyaudio.PyAudio()
        
        print("=== Sistema Voice Assistant Multi-Dispositivo ===")
        print(f"Porta UDP: {self.port}")
        print(f"Sample Rate: {self.sample_rate} Hz")
        print("Wake Words configuradas:")
        print(f"  Motorista (ID 1): '{self.wake_words[1]}'")
        print(f"  Passageiro (ID 2): '{self.wake_words[2]}'")
        print("Aguardando conexão dos Arduinos...")
        
    def setup_tts(self):
        """Configurar Text-to-Speech"""
        voices = self.tts.getProperty('voices')
        if voices:
            # Tentar usar voz feminina se disponível
            for voice in voices:
                if 'female' in voice.name.lower() or 'woman' in voice.name.lower():
                    self.tts.setProperty('voice', voice.id)
                    break
            else:
                self.tts.setProperty('voice', voices[0].id)
        
        self.tts.setProperty('rate', 180)  # Velocidade da fala
        self.tts.setProperty('volume', 0.8)  # Volume
        
    def start_server(self):
        """Iniciar servidor UDP"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.bind(('0.0.0.0', self.port))
            self.socket.settimeout(1.0)  # Timeout para permitir parada
            self.running = True
            
            print(f"Servidor iniciado em 0.0.0.0:{self.port}")
            print("Aguardando dados de áudio dos Arduinos...")
            
            # Iniciar threads
            threading.Thread(target=self.receive_loop, daemon=True).start()
            threading.Thread(target=self.process_audio, daemon=True).start()
            threading.Thread(target=self.status_monitor, daemon=True).start()
            
            return True
            
        except Exception as e:
            print(f"Erro ao iniciar servidor: {e}")
            return False
    
    def receive_loop(self):
        """Loop principal de recepção UDP"""
        while self.running:
            try:
                data, addr = self.socket.recvfrom(4096)
                if len(data) > 0:
                    self.process_packet(data, addr)
                    
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"Erro na recepção: {e}")
    
    def process_packet(self, data, addr):
        """Processar pacote recebido"""
        try:
            device_id = 1  # Padrão
            
            # Verificar se é o primeiro pacote (com cabeçalho)
            if len(data) >= 12:  # Tamanho mínimo do cabeçalho
                # Tentar decodificar cabeçalho
                header_size = struct.calcsize('LHHHH')
                if len(data) >= header_size:
                    try:
                        header = struct.unpack('LHHHH', data[:header_size])
                        timestamp, device_id, sample_rate, samples_count, checksum = header
                        audio_data = data[header_size:]
                        
                        # Debug: mostrar dispositivo detectado
                        if device_id not in [1, 2]:
                            print(f"⚠️  Device ID inválido recebido: {device_id}, usando ID 1")
                            device_id = 1
                            
                    except Exception as e:
                        # Falha na decodificação, tratar como dados de áudio
                        audio_data = data
                        device_id = 1
                else:
                    # Pacote só com dados de áudio
                    audio_data = data
                    device_id = 1
            else:
                audio_data = data
                device_id = 1
            
            # Atualizar contadores por dispositivo
            if device_id not in self.packet_count:
                self.packet_count[device_id] = 0
                self.bytes_received[device_id] = 0
                
            self.packet_count[device_id] += 1
            self.bytes_received[device_id] += len(data)
            
            # Converter bytes para samples int16
            if len(audio_data) % 2 == 0 and len(audio_data) > 0:
                samples = struct.unpack(f'{len(audio_data)//2}h', audio_data)
                
                # Inicializar buffer se não existir
                if device_id not in self.device_buffers:
                    self.device_buffers[device_id] = []
                
                # Adicionar ao buffer do dispositivo
                self.device_buffers[device_id].extend(samples)
                
                # Se buffer está grande o suficiente, processar
                if len(self.device_buffers[device_id]) >= self.sample_rate // 2:  # 0.5 segundos
                    audio_chunk = np.array(self.device_buffers[device_id][:self.sample_rate//2], dtype=np.int16)
                    self.device_buffers[device_id] = self.device_buffers[device_id][self.sample_rate//2:]
                    
                    # Adicionar à fila de processamento
                    self.audio_queue.put((device_id, audio_chunk))
                    
        except Exception as e:
            print(f"Erro ao processar pacote: {e}")
    
    def status_monitor(self):
        """Thread para mostrar status periodicamente"""
        while self.running:
            try:
                current_time = time.time()
                
                # Mostrar status a cada 10 segundos
                if current_time - self.last_status_time >= 10.0:
                    total_packets = sum(self.packet_count.values())
                    
                    if total_packets > 0:
                        # Só mostrar se não estiver em modo de gravação
                        if not self.listening_mode and not self.session_recording:
                            print(f"📡 Status Multi-Dispositivo:")
                            for device_id in [1, 2]:
                                device_name = "Motorista" if device_id == 1 else "Passageiro"
                                packets = self.packet_count.get(device_id, 0)
                                bytes_recv = self.bytes_received.get(device_id, 0)
                                attempts = self.wake_word_attempts.get(device_id, 0)
                                if packets > 0:
                                    print(f"  ✅ {device_name} (ID {device_id}): {packets} pacotes, {bytes_recv/1024:.1f}KB, {attempts} tentativas de wake word")
                                else:
                                    print(f"  ❌ {device_name} (ID {device_id}): Sem dados")
                            print("🎧 Sistema aguardando wake words...")
                        
                        # Reset contadores
                        for device_id in self.packet_count:
                            self.packet_count[device_id] = 0
                            self.bytes_received[device_id] = 0
                            self.wake_word_attempts[device_id] = 0
                    else:
                        # Só mostrar aviso se não estiver gravando
                        if not self.listening_mode and not self.session_recording:
                            print("⚠️  Nenhum dado recebido de nenhum Arduino nos últimos 10 segundos")
                    
                    self.last_status_time = current_time
                
                time.sleep(1)
                
            except Exception as e:
                print(f"Erro no monitor de status: {e}")
                time.sleep(5)
    
    def process_audio(self):
        """Thread para processar áudio e voice assistant"""
        while self.running:
            try:
                if not self.audio_queue.empty():
                    device_id, audio_data = self.audio_queue.get(timeout=1.0)

                    # Limpa buffers logo após terminar gravação
                    if self._just_finished_recording:
                        print("🧹 Limpando buffers após gravação...")
                        # Limpar buffers contínuos de todos os dispositivos
                        for dev_id in self.device_continuous_buffers:
                            self.device_continuous_buffers[dev_id].clear()
                        # Limpar fila de áudio
                        try:
                            with self.audio_queue.mutex:
                                self.audio_queue.queue.clear()
                        except:
                            pass
                        self._just_finished_recording = False
                        print("✅ Buffers limpos - Sistema pronto para novos wake words!")
                        time.sleep(2)  # Pausa maior para estabilizar
                        continue

                    # Inicializar buffer contínuo se não existir
                    if device_id not in self.device_continuous_buffers:
                        self.device_continuous_buffers[device_id] = []

                    # Mantém últimos 3s de áudio por dispositivo (reduzido para melhor responsividade)
                    self.device_continuous_buffers[device_id].extend(audio_data)
                    max_buf = self.sample_rate * 3
                    if len(self.device_continuous_buffers[device_id]) > max_buf:
                        self.device_continuous_buffers[device_id] = self.device_continuous_buffers[device_id][-max_buf:]

                    # Verificar modo atual
                    if not self.listening_mode and not self.session_recording:
                        # Modo de detecção de wake word
                        self.detect_wake_word(self.device_continuous_buffers[device_id], device_id)
                    elif self.listening_mode and device_id == self.active_device:
                        # Modo de gravação ativa - só processar áudio do dispositivo ativo
                        self.process_active_recording(audio_data, device_id)
                else:
                    time.sleep(0.1)
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Erro no processamento de áudio: {e}")
                # Em caso de erro, resetar estado para recuperar
                self.listening_mode = False
                self.active_device = None
                self.session_recording = False

    def detect_wake_word(self, audio_buffer, device_id):
        """Detectar palavra de ativação"""
        try:
            # Evitar processamento muito frequente
            current_time = time.time()
            if current_time - self.last_recognition_time < 1.0:  # Mínimo 1 segundo entre tentativas
                return
                
            detection_length = self.sample_rate * 2  # 2 segundos para detecção
            if len(audio_buffer) >= detection_length:
                chunk = np.array(audio_buffer[-detection_length:], dtype=np.int16)
                
                # Verificar se há áudio suficiente (não só silêncio)
                audio_level = np.abs(chunk).mean()
                if audio_level < 100:  # Muito baixo, provavelmente silêncio
                    return
                
                self.wake_word_attempts[device_id] += 1
                self.last_recognition_time = current_time
                
                # Debug: mostrar tentativa
                device_name = "Motorista" if device_id == 1 else "Passageiro"
                print(f"🔍 Tentando reconhecer wake word - {device_name} (nível: {int(audio_level)})")
                
                text = self.recognize_speech(chunk)
                
                if text:
                    print(f"🎯 Reconhecido: '{text}' de {device_name}")
                    
                    # Verificar wake word específica do dispositivo
                    wake_word = self.wake_words.get(device_id, "assistente")
                    if wake_word.lower() in text.lower():
                        print(f"\n🎙️  WAKE WORD DETECTADA - {device_name}! Iniciando gravação...")
                        print("Fale agora - a gravação será salva até você parar de falar.\n")
                        self.start_recording_session(device_id)
                        # Limpar buffer para evitar re-detecção
                        audio_buffer.clear()
                        return
                else:
                    print(f"❌ Não reconhecido - {device_name}")
                    
        except Exception as e:
            print(f"Erro na detecção de wake word: {e}")

    def start_recording_session(self, device_id):
        """Iniciar sessão de gravação"""
        # Garantir que apenas um dispositivo grava por vez
        if self.listening_mode or self.session_recording:
            print("⚠️  Já existe uma gravação em andamento!")
            return
            
        self.listening_mode = True
        self.active_device = device_id
        self.recording_buffer = []
        self.silence_counter = 0
        self.session_recording = True
        self.session_start_time = datetime.now()
        self.session_audio = []
        
        device_name = "Motorista" if device_id == 1 else "Passageiro"
        print(f"[{self.session_start_time.strftime('%H:%M:%S')}] 🔴 GRAVAÇÃO INICIADA - {device_name} (ID {device_id})")

    def process_active_recording(self, audio_data, device_id):
        """Processar áudio durante gravação ativa"""
        try:
            # Adicionar ao buffer de gravação
            self.recording_buffer.extend(audio_data)
            self.session_audio.extend(audio_data)
            
            # Detectar silêncio
            audio_level = np.abs(audio_data).mean()
            silence_threshold = 300  # Threshold mais baixo
            
            if audio_level < silence_threshold:
                self.silence_counter += 1
            else:
                self.silence_counter = 0
                
            # Mostrar nível de áudio em tempo real
            bars = int(audio_level / 500)
            level_display = "█" * min(bars, 20)
            device_name = "Motorista" if device_id == 1 else "Passageiro"
            print(f"\r🎙️  [{device_name}] Gravando: [{level_display:<20}] Nível: {int(audio_level)}", end="", flush=True)
            
            # Se silêncio por muito tempo, finalizar gravação
            if self.silence_counter >= self.max_silence_frames:
                self.stop_recording_session(device_id)
                
        except Exception as e:
            print(f"\nErro na gravação ativa: {e}")
    
    def stop_recording_session(self, device_id):
        """Finalizar sessão de gravação"""
        device_name = "Motorista" if device_id == 1 else "Passageiro"
        print(f"\n\n⏹️  GRAVAÇÃO FINALIZADA - {device_name} - Processando áudio...")
        
        end_time = datetime.now()
        duration = (end_time - self.session_start_time).total_seconds()
        
        # Salvar áudio completo da sessão
        session_filename = self.save_session_audio(device_id, duration)
        
        # Processar reconhecimento de voz
        if len(self.recording_buffer) > 0:
            full_audio = np.array(self.recording_buffer, dtype=np.int16)
            text = self.recognize_speech(full_audio)
            
            if text:
                print(f"\n[{device_name.upper()}] Disse: '{text}'")
                print(f"📁 Áudio salvo: {session_filename}")
                print(f"⏱️  Duração: {duration:.1f} segundos")
                
                # Processar comando e responder
                response = self.process_command(text, device_id)
                if response:
                    self.speak_response(response)
            else:
                print("❌ Não foi possível reconhecer a fala")
        
        # Reset do state - ORDEM IMPORTANTE
        self.listening_mode = False
        self.session_recording = False
        self.active_device = None
        self.recording_buffer = []
        self.session_audio = []
        self.silence_counter = 0

        # Sinalizar limpeza de buffers DEPOIS do reset
        self._just_finished_recording = True

        print("\n" + "="*70)
        print("💤 SISTEMA PRONTO PARA PRÓXIMOS WAKE WORDS:")
        print(f"  🚗 Motorista: Diga '{self.wake_words[1]}'")
        print(f"  🧑‍🤝‍🧑 Passageiro: Diga '{self.wake_words[2]}'")
        print("="*70 + "\n")
    
    def save_session_audio(self, device_id, duration):
        """Salvar áudio completo da sessão"""
        try:
            timestamp = self.session_start_time.strftime('%Y%m%d_%H%M%S')
            device_name = "motorista" if device_id == 1 else "passageiro"
            filename = f"session_{device_name}_{timestamp}_{duration:.1f}s.wav"
            
            if len(self.session_audio) > 0:
                audio_array = np.array(self.session_audio, dtype=np.int16)
                
                with wave.open(filename, 'wb') as wf:
                    wf.setnchannels(self.channels)
                    wf.setsampwidth(self.sample_width)
                    wf.setframerate(self.sample_rate)
                    wf.writeframes(audio_array.tobytes())
                
                return filename
            else:
                return "Nenhum áudio para salvar"
                
        except Exception as e:
            print(f"Erro ao salvar sessão: {e}")
            return "Erro ao salvar"
    
    def recognize_speech(self, audio_data):
        """Reconhecer fala usando SpeechRecognition"""
        try:
            # Criar objeto AudioData
            audio_sr = sr.AudioData(
                audio_data.tobytes(), 
                self.sample_rate, 
                self.sample_width
            )
            
            # Reconhecimento com timeout menor
            try:
                text = self.recognizer.recognize_google(audio_sr, language='pt-BR')
                return text
            except sr.UnknownValueError:
                return None
            except sr.RequestError as e:
                print(f"Erro no serviço de reconhecimento: {e}")
                return None
                
        except Exception as e:
            print(f"Erro no reconhecimento: {e}")
            return None
    
    def process_command(self, text, device_id):
        """Processar comando de voz com contexto do dispositivo"""
        text_lower = text.lower()
        device_name = "Motorista" if device_id == 1 else "Passageiro"
        
        # Comandos básicos
        if any(word in text_lower for word in ['olá', 'oi', 'hey']):
            return f"Olá {device_name}! Como posso ajudar?"
        
        elif any(word in text_lower for word in ['hora', 'horas']):
            now = datetime.now()
            return f"Agora são {now.hour} horas e {now.minute} minutos"
        
        elif any(word in text_lower for word in ['clima', 'tempo']):
            return "Desculpe, ainda não tenho acesso às informações meteorológicas"
        
        elif any(word in text_lower for word in ['música', 'musica']):
            if device_id == 1:  # Motorista
                return "Como motorista, que tipo de música relaxante você gostaria?"
            else:  # Passageiro
                return "Que tipo de música você gostaria de ouvir durante a viagem?"
        
        elif any(word in text_lower for word in ['navegação', 'navegacao', 'rota']):
            if device_id == 1:  # Motorista
                return "Para onde você gostaria de ir? Vou configurar a rota"
            else:  # Passageiro
                return "Vou informar ao motorista sobre o destino desejado"
        
        elif 'obrigado' in text_lower:
            return f"De nada, {device_name}! Estou aqui para ajudar"
        
        elif any(word in text_lower for word in ['parar', 'pare', 'cancelar']):
            return "Entendido! Estarei aqui quando precisar"
        
        else:
            return f"{device_name}, você disse: {text}. Como posso ajudar com isso?"
    
    def speak_response(self, text):
        """Falar resposta usando TTS"""
        try:
            print(f"[ASSISTENTE] Respondendo: '{text}'")
            self.tts.say(text)
            self.tts.runAndWait()
            
            # Pausa após falar para evitar interferência
            time.sleep(1)
            
        except Exception as e:
            print(f"Erro no TTS: {e}")
    
    def stop(self):
        """Parar servidor"""
        print("\nParando servidor...")
        self.running = False
        if self.socket:
            self.socket.close()
        self.audio.terminate()

def main():
    """Função principal"""
    receiver = AudioReceiver(port=8888)
    
    try:
        if receiver.start_server():
            print("\n" + "="*70)
            print("🎙️  SISTEMA VOICE ASSISTANT MULTI-DISPOSITIVO ATIVO")
            print("="*70)
            print("🚗 Motorista: Diga 'motorista' para começar")
            print("🧑‍🤝‍🧑 Passageiro: Diga 'passageiro' para começar")
            print("🔴 O áudio será gravado automaticamente após wake word")
            print("⏹️  Para após 2 segundos de silêncio")
            print("💾 Arquivos salvos como session_motorista/passageiro_timestamp.wav")
            print("❌ Pressione Ctrl+C para parar...")
            print("="*70 + "\n")
            print("💤 Aguardando dados dos Arduinos e wake words...\n")
            
            while True:
                time.sleep(1)
                
    except KeyboardInterrupt:
        print("\n" + "="*30)
        print("🛑 Sistema interrompido pelo usuário")
        print("="*30)
    except Exception as e:
        print(f"❌ Erro: {e}")
    finally:
        receiver.stop()

if __name__ == "__main__":
    main()