#!/usr/bin/env python3
"""
Sistema de Recepção de Áudio - Voice Assistant
Recebe áudio dos Arduinos via UDP e processa para voice assistant
"""

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
        
        # Configurações de áudio
        self.sample_rate = 16000
        self.channels = 1
        self.sample_width = 2  # 16-bit
        
        # Buffers por dispositivo
        self.device_buffers = {
            1: [],  # Motorista
            2: []   # Passageiro (futuro)
        }
        
        # Sistema de ativação por palavra-chave
        self.wake_word = "olá assistente"
        self.listening_mode = False
        self.wake_word_detected = False
        self.recording_buffer = []
        self.silence_counter = 0
        self.max_silence_frames = 30  # ~2 segundos de silêncio para parar gravação
        
        # Gravação completa de sessão
        self.session_audio = []
        self.session_recording = False
        self.session_start_time = None
        
        # Controle de logs
        self.last_status_time = 0
        self.packet_count = 0
        self.bytes_received = 0
        
        # Voice Assistant
        self.recognizer = sr.Recognizer()
        self.tts = pyttsx3.init()
        self.setup_tts()
        
        # PyAudio para reprodução
        self.audio = pyaudio.PyAudio()
        
        print("=== Sistema de Voice Assistant com Wake Word ===")
        print(f"Porta UDP: {self.port}")
        print(f"Sample Rate: {self.sample_rate} Hz")
        print(f"Wake Word: '{self.wake_word}'")
        print("Diga 'olá assistente' para começar a gravar!")
        
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
            self.packet_count += 1
            self.bytes_received += len(data)
            
            # Verificar se é o primeiro pacote (com cabeçalho)
            if len(data) >= 12:  # Tamanho mínimo do cabeçalho
                # Tentar decodificar cabeçalho
                header_size = struct.calcsize('LHHHH')
                if len(data) >= header_size:
                    header = struct.unpack('LHHHH', data[:header_size])
                    timestamp, device_id, sample_rate, samples_count, checksum = header
                    
                    audio_data = data[header_size:]
                    
                else:
                    # Pacote só com dados de áudio
                    audio_data = data
                    device_id = 1  # Assumir motorista por padrão
            else:
                audio_data = data
                device_id = 1
            
            # Converter bytes para samples int16
            if len(audio_data) % 2 == 0:
                samples = struct.unpack(f'{len(audio_data)//2}h', audio_data)
                
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
                
                # Mostrar status a cada 5 segundos
                if current_time - self.last_status_time >= 5.0:
                    if self.packet_count > 0:
                        if not self.listening_mode:
                            print(f"📡 Recebendo áudio... "
                                  f"Pacotes: {self.packet_count} | "
                                  f"Dados: {self.bytes_received/1024:.1f}KB | "
                                  f"Status: 💤 Aguardando wake word")
                        
                        # Reset contadores
                        self.packet_count = 0
                        self.bytes_received = 0
                    else:
                        print("⚠️  Nenhum dado recebido do Arduino nos últimos 5 segundos")
                    
                    self.last_status_time = current_time
                
                time.sleep(1)
                
            except Exception as e:
                print(f"Erro no monitor de status: {e}")
                time.sleep(5)
    
    def process_audio(self):
        """Thread para processar áudio e voice assistant"""
        continuous_buffer = []
        
        while self.running:
            try:
                if not self.audio_queue.empty():
                    device_id, audio_data = self.audio_queue.get(timeout=1.0)
                    
                    # Adicionar ao buffer contínuo
                    continuous_buffer.extend(audio_data)
                    
                    # Manter buffer de tamanho razoável (5 segundos)
                    max_buffer_size = self.sample_rate * 5
                    if len(continuous_buffer) > max_buffer_size:
                        continuous_buffer = continuous_buffer[-max_buffer_size:]
                    
                    # Processar baseado no estado atual
                    if not self.listening_mode:
                        # Modo de detecção de wake word
                        self.detect_wake_word(continuous_buffer, device_id)
                    else:
                        # Modo de gravação ativa
                        self.process_active_recording(audio_data, device_id)
                    
                else:
                    time.sleep(0.1)
                    
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Erro no processamento de áudio: {e}")
    
    def detect_wake_word(self, audio_buffer, device_id):
        """Detectar palavra de ativação"""
        try:
            # Usar últimos 2 segundos para detecção
            detection_length = self.sample_rate * 2
            if len(audio_buffer) >= detection_length:
                detection_audio = np.array(audio_buffer[-detection_length:], dtype=np.int16)
                
                # Tentar reconhecer
                text = self.recognize_speech(detection_audio)
                if text and self.wake_word.lower() in text.lower():
                    print(f"\n🎙️  WAKE WORD DETECTADA! Iniciando gravação...")
                    print("Fale agora - a gravação será salva até você parar de falar.\n")
                    
                    self.start_recording_session(device_id)
                    
        except Exception as e:
            print(f"Erro na detecção de wake word: {e}")
    
    def start_recording_session(self, device_id):
        """Iniciar sessão de gravação"""
        self.listening_mode = True
        self.recording_buffer = []
        self.silence_counter = 0
        self.session_recording = True
        self.session_start_time = datetime.now()
        self.session_audio = []
        
        print(f"[{self.session_start_time.strftime('%H:%M:%S')}] 🔴 GRAVAÇÃO INICIADA - Device {device_id}")
    
    def process_active_recording(self, audio_data, device_id):
        """Processar áudio durante gravação ativa"""
        try:
            # Adicionar ao buffer de gravação
            self.recording_buffer.extend(audio_data)
            self.session_audio.extend(audio_data)
            
            # Detectar silêncio
            audio_level = np.abs(audio_data).mean()
            silence_threshold = 500  # Ajustar conforme necessário
            
            if audio_level < silence_threshold:
                self.silence_counter += 1
            else:
                self.silence_counter = 0
                
            # Mostrar nível de áudio em tempo real
            bars = int(audio_level / 1000)
            level_display = "█" * min(bars, 20)
            print(f"\r🎙️  Gravando: [{level_display:<20}] Nível: {int(audio_level)}", end="", flush=True)
            
            # Se silêncio por muito tempo, finalizar gravação
            if self.silence_counter >= self.max_silence_frames:
                self.stop_recording_session(device_id)
                
        except Exception as e:
            print(f"\nErro na gravação ativa: {e}")
    
    def stop_recording_session(self, device_id):
        """Finalizar sessão de gravação"""
        print(f"\n\n⏹️  GRAVAÇÃO FINALIZADA - Processando áudio...")
        
        end_time = datetime.now()
        duration = (end_time - self.session_start_time).total_seconds()
        
        # Salvar áudio completo da sessão
        session_filename = self.save_session_audio(device_id, duration)
        
        # Processar reconhecimento de voz
        if len(self.recording_buffer) > 0:
            full_audio = np.array(self.recording_buffer, dtype=np.int16)
            text = self.recognize_speech(full_audio)
            
            if text:
                print(f"\n[MOTORISTA] Disse: '{text}'")
                print(f"📁 Áudio salvo: {session_filename}")
                print(f"⏱️  Duração: {duration:.1f} segundos")
                
                # Processar comando e responder
                response = self.process_command(text)
                if response:
                    self.speak_response(response)
            else:
                print("❌ Não foi possível reconhecer a fala")
        
        # Reset para próxima sessão
        self.listening_mode = False
        self.session_recording = False
        self.recording_buffer = []
        self.session_audio = []
        
        print(f"\n💤 Aguardando próximo '{self.wake_word}'...\n")
    
    def save_session_audio(self, device_id, duration):
        """Salvar áudio completo da sessão"""
        try:
            timestamp = self.session_start_time.strftime('%Y%m%d_%H%M%S')
            filename = f"session_device_{device_id}_{timestamp}_{duration:.1f}s.wav"
            
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
            # Normalizar áudio
            audio_normalized = audio_data.astype(np.float32) / 32768.0
            
            # Criar objeto AudioData
            audio_sr = sr.AudioData(
                audio_data.tobytes(), 
                self.sample_rate, 
                self.sample_width
            )
            
            # Reconhecimento
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
    
    def process_command(self, text):
        """Processar comando de voz"""
        text_lower = text.lower()
        
        # Comandos básicos
        if any(word in text_lower for word in ['olá', 'oi', 'hey']):
            return "Olá! Como posso ajudar?"
        
        elif any(word in text_lower for word in ['hora', 'horas']):
            now = datetime.now()
            return f"Agora são {now.hour} horas e {now.minute} minutos"
        
        elif any(word in text_lower for word in ['clima', 'tempo']):
            return "Desculpe, ainda não tenho acesso às informações meteorológicas"
        
        elif any(word in text_lower for word in ['música', 'musica']):
            return "Que tipo de música você gostaria de ouvir?"
        
        elif any(word in text_lower for word in ['navegação', 'navegacao', 'rota']):
            return "Para onde você gostaria de ir?"
        
        elif 'obrigado' in text_lower:
            return "De nada! Estou aqui para ajudar"
        
        elif any(word in text_lower for word in ['parar', 'pare', 'cancelar']):
            return "Entendido! Estarei aqui quando precisar"
        
        elif any(word in text_lower for word in ['gravar', 'gravação', 'gravacao']):
            return "Sua fala já está sendo gravada automaticamente quando você diz olá assistente"
        
        else:
            return f"Você disse: {text}. Como posso ajudar com isso?"
    
    def speak_response(self, text):
        """Falar resposta usando TTS"""
        try:
            print(f"[ASSISTENTE] Respondendo: '{text}'")
            self.tts.say(text)
            self.tts.runAndWait()
        except Exception as e:
            print(f"Erro no TTS: {e}")
    
    def save_audio_chunk(self, audio_data, device_id):
        """Salvar chunk de áudio para debug"""
        try:
            timestamp = int(time.time())
            filename = f"audio_device_{device_id}_{timestamp}.wav"
            
            with wave.open(filename, 'wb') as wf:
                wf.setnchannels(self.channels)
                wf.setsampwidth(self.sample_width)
                wf.setframerate(self.sample_rate)
                wf.writeframes(audio_data.tobytes())
                
        except Exception as e:
            print(f"Erro ao salvar áudio: {e}")
    
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
            print("\n" + "="*60)
            print("🎙️  SISTEMA VOICE ASSISTANT ATIVO")
            print("="*60)
            print("📱 Diga 'olá assistente' para começar")
            print("🔴 O áudio será gravado automaticamente")
            print("⏹️  Para até 2 segundos de silêncio")
            print("💾 Arquivos salvos como session_device_X_timestamp.wav")
            print("❌ Pressione Ctrl+C para parar...")
            print("="*60 + "\n")
            print("💤 Aguardando dados do Arduino e wake word...\n")
            
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
    # Instalar dependências:
    # pip install numpy speechrecognition pyttsx3 pyaudio wave
    main()