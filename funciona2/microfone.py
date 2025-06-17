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
import os
from collections import deque
import logging

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

class AudioReceiver:
    def __init__(self, port=8888):
        self.port = port
        self.socket = None
        self.running = False
        
        # Thread-safe queues
        self.audio_queue = queue.Queue(maxsize=100)
        
        # Configurações de áudio
        self.sample_rate = 16000
        self.channels = 1
        self.sample_width = 2
        
        # Buffers limitados por dispositivo (máx 5 segundos)
        self.max_buffer_seconds = 5
        self.max_buffer_size = self.sample_rate * self.max_buffer_seconds
        self.device_buffers = {
            1: deque(maxlen=self.max_buffer_size),
            2: deque(maxlen=self.max_buffer_size)
        }
        
        # Locks para sincronização
        self.buffer_locks = {
            1: threading.Lock(),
            2: threading.Lock()
        }
        
        # Wake words
        self.wake_words = {
            1: "motorista",
            2: "passageiro"
        }
        
        # Estado de gravação
        self.recording_state = {
            'active': False,
            'device_id': None,
            'buffer': [],
            'start_time': None,
            'packets_received': 0
        }
        self.recording_lock = threading.Lock()
        
        # Estatísticas
        self.stats = {
            1: {'packets': 0, 'bytes': 0, 'errors': 0, 'last_seen': 0},
            2: {'packets': 0, 'bytes': 0, 'errors': 0, 'last_seen': 0}
        }
        
        # Voice Assistant
        self.recognizer = sr.Recognizer()
        self.recognizer.energy_threshold = 300
        self.recognizer.dynamic_energy_threshold = False
        
        self.tts = pyttsx3.init()
        self.setup_tts()
        
        # Diretório para gravações
        os.makedirs('recordings', exist_ok=True)
        
        logging.info("Sistema Voice Assistant inicializado")
        logging.info(f"Porta UDP: {self.port}")
        
    def setup_tts(self):
        """Configurar TTS"""
        voices = self.tts.getProperty('voices')
        if voices:
            self.tts.setProperty('voice', voices[0].id)
        self.tts.setProperty('rate', 180)
        self.tts.setProperty('volume', 0.8)
        
    def start_server(self):
        """Iniciar servidor UDP"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
            self.socket.bind(('0.0.0.0', self.port))
            self.socket.settimeout(0.5)
            self.running = True
            
            # Threads
            threading.Thread(target=self.receive_loop, daemon=True).start()
            threading.Thread(target=self.process_audio, daemon=True).start()
            threading.Thread(target=self.status_monitor, daemon=True).start()
            
            logging.info(f"Servidor iniciado em 0.0.0.0:{self.port}")
            return True
            
        except Exception as e:
            logging.error(f"Erro ao iniciar servidor: {e}")
            return False
    
    def receive_loop(self):
        """Loop de recepção UDP"""
        header_struct = struct.Struct('LLHHHHHBB')
        
        while self.running:
            try:
                data, addr = self.socket.recvfrom(4096)
                if len(data) < header_struct.size:
                    continue
                    
                # Decodificar header
                header = header_struct.unpack(data[:header_struct.size])
                sequence, timestamp, device_id, sample_rate, samples_count, checksum, flags, _ = header
                
                # Validar device_id
                if device_id not in [1, 2]:
                    logging.warning(f"Device ID inválido: {device_id}")
                    continue
                
                # Extrair dados de áudio
                audio_data = data[header_struct.size:]
                expected_size = samples_count * 2
                
                if len(audio_data) != expected_size:
                    logging.warning(f"Tamanho incorreto: esperado {expected_size}, recebido {len(audio_data)}")
                    self.stats[device_id]['errors'] += 1
                    continue
                
                # Verificar CRC16
                calculated_crc = self.calculate_crc16(audio_data)
                if calculated_crc != checksum:
                    logging.warning(f"CRC inválido: esperado {checksum}, calculado {calculated_crc}")
                    self.stats[device_id]['errors'] += 1
                    continue
                
                # Converter para samples
                samples = struct.unpack(f'{samples_count}h', audio_data)
                
                # Atualizar estatísticas
                self.stats[device_id]['packets'] += 1
                self.stats[device_id]['bytes'] += len(data)
                self.stats[device_id]['last_seen'] = time.time()
                
                # Verificar flags
                is_start = flags & 0x01
                is_end = flags & 0x02
                
                # Processar áudio
                if is_start:
                    logging.info(f"📡 Início de transmissão - Device {device_id}")
                
                # Adicionar ao buffer thread-safe
                with self.buffer_locks[device_id]:
                    self.device_buffers[device_id].extend(samples)
                
                # Adicionar à fila se não estiver cheia
                try:
                    self.audio_queue.put_nowait((device_id, samples, is_end))
                except queue.Full:
                    logging.warning("Fila de áudio cheia, descartando pacote")
                
                if is_end:
                    logging.info(f"📡 Fim de transmissão - Device {device_id}")
                    
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    logging.error(f"Erro na recepção: {e}")
    
    def calculate_crc16(self, data):
        """Calcular CRC16"""
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc
    
    def process_audio(self):
        """Processar áudio recebido"""
        wake_word_buffer_size = self.sample_rate * 2  # 2 segundos
        
        while self.running:
            try:
                # Timeout para não bloquear
                device_id, samples, is_end = self.audio_queue.get(timeout=0.1)
                
                with self.recording_lock:
                    if self.recording_state['active'] and device_id == self.recording_state['device_id']:
                        # Modo gravação
                        self.recording_state['buffer'].extend(samples)
                        self.recording_state['packets_received'] += 1
                        
                        if is_end:
                            self.finish_recording()
                    else:
                        # Modo detecção wake word
                        with self.buffer_locks[device_id]:
                            buffer_copy = list(self.device_buffers[device_id])[-wake_word_buffer_size:]
                        
                        if len(buffer_copy) >= self.sample_rate:  # Mínimo 1 segundo
                            self.detect_wake_word(buffer_copy, device_id)
                        
            except queue.Empty:
                continue
            except Exception as e:
                logging.error(f"Erro no processamento: {e}")
    
    def detect_wake_word(self, audio_buffer, device_id):
        """Detectar wake word"""
        try:
            audio_array = np.array(audio_buffer, dtype=np.int16)
            
            # Verificar nível de áudio
            audio_level = np.abs(audio_array).mean()
            if audio_level < 100:
                return
            
            # Criar AudioData
            audio_data = sr.AudioData(
                audio_array.tobytes(),
                self.sample_rate,
                self.sample_width
            )
            
            # Reconhecer
            try:
                text = self.recognizer.recognize_google(audio_data, language='pt-BR')
                wake_word = self.wake_words[device_id]
                
                if text and wake_word.lower() in text.lower():
                    device_name = "Motorista" if device_id == 1 else "Passageiro"
                    logging.info(f"🎯 Wake word detectada: '{text}' - {device_name}")
                    
                    with self.recording_lock:
                        if not self.recording_state['active']:
                            self.start_recording(device_id)
                            
                            # Limpar buffer após detecção
                            with self.buffer_locks[device_id]:
                                self.device_buffers[device_id].clear()
                                
            except sr.UnknownValueError:
                pass
            except sr.RequestError as e:
                logging.error(f"Erro no reconhecimento: {e}")
                
        except Exception as e:
            logging.error(f"Erro na detecção: {e}")
    
    def start_recording(self, device_id):
        """Iniciar gravação"""
        self.recording_state['active'] = True
        self.recording_state['device_id'] = device_id
        self.recording_state['buffer'] = []
        self.recording_state['start_time'] = datetime.now()
        self.recording_state['packets_received'] = 0
        
        device_name = "Motorista" if device_id == 1 else "Passageiro"
        logging.info(f"🔴 Gravação iniciada - {device_name}")
    
    def finish_recording(self):
        """Finalizar gravação"""
        if not self.recording_state['active']:
            return
            
        device_id = self.recording_state['device_id']
        device_name = "Motorista" if device_id == 1 else "Passageiro"
        duration = (datetime.now() - self.recording_state['start_time']).total_seconds()
        
        logging.info(f"⏹️ Gravação finalizada - {device_name}")
        logging.info(f"Duração: {duration:.1f}s, Pacotes: {self.recording_state['packets_received']}")
        
        # Salvar e processar
        if self.recording_state['buffer']:
            filename = self.save_recording(device_id, duration)
            
            # Reconhecer fala
            audio_array = np.array(self.recording_state['buffer'], dtype=np.int16)
            text = self.recognize_speech(audio_array)
            
            if text:
                logging.info(f"💬 {device_name}: '{text}'")
                response = self.process_command(text, device_id)
                if response:
                    self.speak_response(response)
            else:
                logging.warning("Não foi possível reconhecer a fala")
        
        # Reset estado
        self.recording_state['active'] = False
        self.recording_state['device_id'] = None
        self.recording_state['buffer'] = []
        
    def save_recording(self, device_id, duration):
        """Salvar gravação"""
        timestamp = self.recording_state['start_time'].strftime('%Y%m%d_%H%M%S')
        device_name = "motorista" if device_id == 1 else "passageiro"
        filename = f"recordings/session_{device_name}_{timestamp}_{duration:.1f}s.wav"
        
        audio_array = np.array(self.recording_state['buffer'], dtype=np.int16)
        
        with wave.open(filename, 'wb') as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(self.sample_width)
            wf.setframerate(self.sample_rate)
            wf.writeframes(audio_array.tobytes())
        
        logging.info(f"📁 Salvo: {filename}")
        return filename
    
    def recognize_speech(self, audio_data):
        """Reconhecer fala"""
        try:
            audio_sr = sr.AudioData(
                audio_data.tobytes(),
                self.sample_rate,
                self.sample_width
            )
            
            text = self.recognizer.recognize_google(audio_sr, language='pt-BR')
            return text
            
        except Exception as e:
            logging.error(f"Erro no reconhecimento: {e}")
            return None
    
    def process_command(self, text, device_id):
        """Processar comando"""
        text_lower = text.lower()
        device_name = "Motorista" if device_id == 1 else "Passageiro"
        
        commands = {
            'hora': lambda: f"São {datetime.now().strftime('%H:%M')}",
            'olá': lambda: f"Olá {device_name}, como posso ajudar?",
            'música': lambda: f"Vou tocar música para o {device_name}",
            'navegação': lambda: "Calculando rota..." if device_id == 1 else "Informando ao motorista",
            'obrigado': lambda: "De nada!"
        }
        
        for cmd, response in commands.items():
            if cmd in text_lower:
                return response()
        
        return f"Você disse: {text}"
    
    def speak_response(self, text):
        """Falar resposta"""
        try:
            logging.info(f"🔊 Resposta: '{text}'")
            self.tts.say(text)
            self.tts.runAndWait()
        except Exception as e:
            logging.error(f"Erro no TTS: {e}")
    
    def status_monitor(self):
        """Monitor de status"""
        while self.running:
            time.sleep(10)
            
            with self.recording_lock:
                if self.recording_state['active']:
                    continue
            
            # Mostrar status
            logging.info("📊 Status do sistema:")
            for device_id in [1, 2]:
                device_name = "Motorista" if device_id == 1 else "Passageiro"
                stats = self.stats[device_id]
                last_seen = time.time() - stats['last_seen'] if stats['last_seen'] > 0 else -1
                
                if last_seen >= 0 and last_seen < 30:
                    status = "✅ Online"
                else:
                    status = "❌ Offline"
                    
                logging.info(f"  {device_name}: {status} - {stats['packets']} pacotes, {stats['errors']} erros")
    
    def stop(self):
        """Parar servidor"""
        self.running = False
        if self.socket:
            self.socket.close()
        logging.info("Servidor parado")

def main():
    receiver = AudioReceiver(port=8888)
    
    try:
        if receiver.start_server():
            print("\n" + "="*70)
            print("🎙️  SISTEMA VOICE ASSISTANT - VERSÃO OTIMIZADA")
            print("="*70)
            print("✅ VAD (Voice Activity Detection) nos Arduinos")
            print("✅ Verificação CRC16 em todos os pacotes")
            print("✅ Buffers limitados e thread-safe")
            print("✅ Sistema de flags para início/fim de transmissão")
            print("\n🎯 Wake words:")
            print("  🚗 Motorista: 'motorista'")
            print("  🧑 Passageiro: 'passageiro'")
            print("\n❌ Ctrl+C para parar")
            print("="*70 + "\n")
            
            while True:
                time.sleep(1)
                
    except KeyboardInterrupt:
        print("\n🛑 Sistema interrompido")
    finally:
        receiver.stop()

if __name__ == "__main__":
    main()