#!/usr/bin/env python3
import socket
import struct
import numpy as np
import wave
import threading
import queue
import time
import json
import os
from datetime import datetime
from collections import deque
import logging
import vosk
import pyaudio

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

class CoralVoiceAssistant:
    def __init__(self, port=8888, model_path="/home/mendel/vosk-model-pt"):
        self.port = port
        self.socket = None
        self.running = False
        
        # Configurações de áudio
        self.sample_rate = 16000
        self.channels = 1
        self.sample_width = 2
        
        # Vosk offline
        if not os.path.exists(model_path):
            logging.error(f"Modelo Vosk não encontrado em {model_path}")
            logging.info("Baixe o modelo PT-BR de https://alphacephei.com/vosk/models")
            raise FileNotFoundError(f"Modelo não encontrado: {model_path}")
            
        self.model = vosk.Model(model_path)
        self.recognizers = {
            1: vosk.KaldiRecognizer(self.model, self.sample_rate),
            2: vosk.KaldiRecognizer(self.model, self.sample_rate)
        }
        
        # Wake words
        self.wake_words = {
            1: "motorista",
            2: "passageiro"
        }
        
        # Thread-safe queues
        self.audio_queue = queue.Queue(maxsize=50)
        
        # Buffers limitados
        self.max_buffer_size = self.sample_rate * 3  # 3 segundos
        self.device_buffers = {
            1: deque(maxlen=self.max_buffer_size),
            2: deque(maxlen=self.max_buffer_size)
        }
        
        # Locks
        self.buffer_locks = {
            1: threading.Lock(),
            2: threading.Lock()
        }
        
        # Estado de gravação
        self.recording_state = {
            'active': False,
            'device_id': None,
            'buffer': [],
            'start_time': None
        }
        self.recording_lock = threading.Lock()
        
        # TTS offline usando espeak
        self.tts_enabled = self.check_espeak()
        
        # Estatísticas
        self.stats = {
            1: {'packets': 0, 'errors': 0, 'last_seen': 0},
            2: {'packets': 0, 'errors': 0, 'last_seen': 0}
        }
        
        # PyAudio para teste local (opcional)
        self.audio = None
        try:
            self.audio = pyaudio.PyAudio()
        except:
            logging.warning("PyAudio não disponível - apenas processamento UDP")
        
        os.makedirs('recordings', exist_ok=True)
        
        logging.info("Coral Voice Assistant iniciado")
        logging.info(f"Modelo Vosk: {model_path}")
        logging.info(f"TTS: {'espeak' if self.tts_enabled else 'desabilitado'}")
        
    def check_espeak(self):
        """Verificar se espeak está instalado"""
        try:
            os.system("which espeak > /dev/null 2>&1")
            return os.WEXITSTATUS(os.system("which espeak > /dev/null 2>&1")) == 0
        except:
            return False
    
    def start_server(self):
        """Iniciar servidor UDP"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
            self.socket.bind(('0.0.0.0', self.port))
            self.socket.settimeout(0.5)
            self.running = True
            
            # Threads otimizadas para ARM
            threading.Thread(target=self.receive_loop, daemon=True).start()
            threading.Thread(target=self.process_audio, daemon=True).start()
            threading.Thread(target=self.status_monitor, daemon=True).start()
            
            logging.info(f"Servidor UDP iniciado na porta {self.port}")
            return True
            
        except Exception as e:
            logging.error(f"Erro ao iniciar: {e}")
            return False
    
    def receive_loop(self):
        """Receber pacotes UDP"""
        header_struct = struct.Struct('LLHHHHHBB')
        
        while self.running:
            try:
                data, addr = self.socket.recvfrom(4096)
                if len(data) < header_struct.size:
                    continue
                
                # Header
                header = header_struct.unpack(data[:header_struct.size])
                sequence, timestamp, device_id, sample_rate, samples_count, checksum, flags, _ = header
                
                if device_id not in [1, 2]:
                    continue
                
                # Áudio
                audio_data = data[header_struct.size:]
                if len(audio_data) != samples_count * 2:
                    self.stats[device_id]['errors'] += 1
                    continue
                
                # CRC16
                if self.calculate_crc16(audio_data) != checksum:
                    self.stats[device_id]['errors'] += 1
                    continue
                
                # Samples
                samples = struct.unpack(f'{samples_count}h', audio_data)
                
                # Stats
                self.stats[device_id]['packets'] += 1
                self.stats[device_id]['last_seen'] = time.time()
                
                # Flags
                is_start = flags & 0x01
                is_end = flags & 0x02
                
                if is_start:
                    logging.info(f"▶️ Transmissão iniciada - Device {device_id}")
                
                # Buffer
                with self.buffer_locks[device_id]:
                    self.device_buffers[device_id].extend(samples)
                
                # Queue
                try:
                    self.audio_queue.put_nowait((device_id, samples, is_end))
                except queue.Full:
                    pass
                
                if is_end:
                    logging.info(f"⏹️ Transmissão finalizada - Device {device_id}")
                    
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    logging.error(f"Erro recepção: {e}")
    
    def calculate_crc16(self, data):
        """CRC16"""
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
        """Processar áudio com Vosk"""
        while self.running:
            try:
                device_id, samples, is_end = self.audio_queue.get(timeout=0.1)
                
                with self.recording_lock:
                    if self.recording_state['active'] and device_id == self.recording_state['device_id']:
                        # Gravando comando
                        self.recording_state['buffer'].extend(samples)
                        
                        if is_end:
                            self.finish_recording()
                    else:
                        # Detectar wake word
                        recognizer = self.recognizers[device_id]
                        
                        # Converter para bytes
                        audio_bytes = struct.pack(f'{len(samples)}h', *samples)
                        
                        # Processar com Vosk
                        if recognizer.AcceptWaveform(audio_bytes):
                            result = json.loads(recognizer.Result())
                            text = result.get('text', '')
                            
                            if text and self.wake_words[device_id] in text:
                                device_name = "Motorista" if device_id == 1 else "Passageiro"
                                logging.info(f"🎯 Wake word: '{text}' - {device_name}")
                                
                                with self.recording_lock:
                                    if not self.recording_state['active']:
                                        self.start_recording(device_id)
                                        
                                        # Limpar buffer
                                        with self.buffer_locks[device_id]:
                                            self.device_buffers[device_id].clear()
                                        
                                        # Reset recognizer
                                        recognizer.Reset()
                        
            except queue.Empty:
                continue
            except Exception as e:
                logging.error(f"Erro processamento: {e}")
    
    def start_recording(self, device_id):
        """Iniciar gravação"""
        self.recording_state['active'] = True
        self.recording_state['device_id'] = device_id
        self.recording_state['buffer'] = []
        self.recording_state['start_time'] = datetime.now()
        
        device_name = "Motorista" if device_id == 1 else "Passageiro"
        logging.info(f"🔴 Gravação iniciada - {device_name}")
    
    def finish_recording(self):
        """Finalizar gravação"""
        if not self.recording_state['active']:
            return
        
        device_id = self.recording_state['device_id']
        device_name = "Motorista" if device_id == 1 else "Passageiro"
        duration = (datetime.now() - self.recording_state['start_time']).total_seconds()
        
        logging.info(f"⏹️ Processando comando - {device_name}")
        
        if self.recording_state['buffer']:
            # Salvar
            filename = self.save_recording(device_id, duration)
            
            # Reconhecer com Vosk
            recognizer = vosk.KaldiRecognizer(self.model, self.sample_rate)
            audio_bytes = struct.pack(f'{len(self.recording_state["buffer"])}h', 
                                    *self.recording_state['buffer'])
            
            recognizer.AcceptWaveform(audio_bytes)
            result = json.loads(recognizer.FinalResult())
            text = result.get('text', '')
            
            if text:
                logging.info(f"💬 {device_name}: '{text}'")
                response = self.process_command(text, device_id)
                if response:
                    self.speak_response(response)
            else:
                logging.warning("Comando não reconhecido")
        
        # Reset
        self.recording_state['active'] = False
        self.recording_state['device_id'] = None
        self.recording_state['buffer'] = []
    
    def save_recording(self, device_id, duration):
        """Salvar gravação"""
        timestamp = self.recording_state['start_time'].strftime('%Y%m%d_%H%M%S')
        device_name = "motorista" if device_id == 1 else "passageiro"
        filename = f"recordings/{device_name}_{timestamp}_{duration:.1f}s.wav"
        
        audio_array = np.array(self.recording_state['buffer'], dtype=np.int16)
        
        with wave.open(filename, 'wb') as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(self.sample_width)
            wf.setframerate(self.sample_rate)
            wf.writeframes(audio_array.tobytes())
        
        logging.info(f"💾 Salvo: {filename}")
        return filename
    
    def process_command(self, text, device_id):
        """Processar comando"""
        text_lower = text.lower()
        device_name = "Motorista" if device_id == 1 else "Passageiro"
        
        # Comandos simples
        if any(word in text_lower for word in ['hora', 'horas']):
            return f"São {datetime.now().strftime('%H:%M')}"
        elif 'olá' in text_lower or 'oi' in text_lower:
            return f"Olá {device_name}"
        elif 'música' in text_lower:
            return f"Tocando música para {device_name}"
        elif 'navegação' in text_lower or 'rota' in text_lower:
            return "Calculando rota" if device_id == 1 else "Avisando motorista"
        elif 'temperatura' in text_lower:
            # Ler temperatura da CPU do Coral
            try:
                with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                    temp = int(f.read()) / 1000
                return f"Temperatura do sistema: {temp:.1f}°C"
            except:
                return "Temperatura não disponível"
        elif 'obrigado' in text_lower:
            return "De nada"
        
        return f"Comando recebido: {text}"
    
    def speak_response(self, text):
        """TTS com espeak"""
        if self.tts_enabled:
            try:
                # espeak em português
                os.system(f'espeak -v pt-br "{text}" 2>/dev/null &')
                logging.info(f"🔊 TTS: '{text}'")
            except Exception as e:
                logging.error(f"Erro TTS: {e}")
        else:
            logging.info(f"🔊 (TTS desabilitado): '{text}'")
    
    def status_monitor(self):
        """Monitor de status"""
        while self.running:
            time.sleep(15)
            
            with self.recording_lock:
                if self.recording_state['active']:
                    continue
            
            online_devices = []
            for device_id in [1, 2]:
                if time.time() - self.stats[device_id]['last_seen'] < 30:
                    online_devices.append(device_id)
            
            if online_devices:
                logging.info(f"📊 Dispositivos online: {online_devices}")
    
    def stop(self):
        """Parar servidor"""
        self.running = False
        if self.socket:
            self.socket.close()
        if self.audio:
            self.audio.terminate()
        logging.info("Servidor parado")

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Coral Voice Assistant')
    parser.add_argument('--port', type=int, default=8888, help='Porta UDP')
    parser.add_argument('--model', default='/home/mendel/vosk-model-pt', 
                       help='Caminho do modelo Vosk')
    args = parser.parse_args()
    
    assistant = CoralVoiceAssistant(port=args.port, model_path=args.model)
    
    try:
        if assistant.start_server():
            print("\n" + "="*60)
            print("🎙️  CORAL VOICE ASSISTANT - OFFLINE")
            print("="*60)
            print("✅ Reconhecimento offline com Vosk")
            print("✅ Otimizado para Coral Dev Board")
            print("✅ TTS com espeak (se disponível)")
            print(f"📡 Porta UDP: {args.port}")
            print("\n🎯 Wake words:")
            print("  🚗 Motorista: 'motorista'")
            print("  🧑 Passageiro: 'passageiro'")
            print("\n❌ Ctrl+C para parar")
            print("="*60 + "\n")
            
            while True:
                time.sleep(1)
                
    except KeyboardInterrupt:
        print("\n🛑 Interrompido")
    finally:
        assistant.stop()

if __name__ == "__main__":
    main()