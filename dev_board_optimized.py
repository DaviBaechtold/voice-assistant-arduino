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
import subprocess
import psutil

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
        
        # Verificar modelo Vosk
        if not os.path.exists(model_path):
            logging.error(f"Modelo Vosk não encontrado em {model_path}")
            raise FileNotFoundError(f"Modelo não encontrado: {model_path}")
        
        # Configurar Vosk com otimizações para ARM
        vosk.SetLogLevel(-1)  # Desabilitar logs verbosos
        self.model = vosk.Model(model_path)
        
        # Criar recognizers com configurações otimizadas
        self.recognizers = {
            1: vosk.KaldiRecognizer(self.model, self.sample_rate),
            2: vosk.KaldiRecognizer(self.model, self.sample_rate)
        }
        
        # Configurar gramática para wake words (melhora performance)
        wake_grammar = json.dumps(['motorista', 'passageiro', 'olá', 'oi'], ensure_ascii=False)
        for rec in self.recognizers.values():
            rec.SetGrammar(wake_grammar)
        
        # Wake words
        self.wake_words = {
            1: "motorista",
            2: "passageiro"
        }
        
        # Queues com prioridade
        self.audio_queue = queue.PriorityQueue(maxsize=30)
        self.processing_queue = queue.Queue(maxsize=10)
        
        # Buffers circulares otimizados
        self.buffer_size = int(self.sample_rate * 2.5)  # 2.5 segundos
        self.device_buffers = {
            1: deque(maxlen=self.buffer_size),
            2: deque(maxlen=self.buffer_size)
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
            'buffer': bytearray(),
            'start_time': None,
            'timeout': 5.0  # Timeout de 5 segundos
        }
        self.recording_lock = threading.Lock()
        
        # Cache de comandos (evita reprocessamento)
        self.command_cache = {}
        self.cache_timeout = 2.0
        
        # Verificar espeak
        self.tts_enabled = self._check_espeak()
        
        # Monitor de recursos
        self.resource_monitor = {
            'cpu_threshold': 80.0,
            'mem_threshold': 80.0,
            'last_check': 0
        }
        
        # Estatísticas
        self.stats = {
            1: {'packets': 0, 'errors': 0, 'last_seen': 0, 'wake_detections': 0},
            2: {'packets': 0, 'errors': 0, 'last_seen': 0, 'wake_detections': 0}
        }
        
        os.makedirs('recordings', exist_ok=True)
        
        logging.info("Coral Voice Assistant otimizado iniciado")
        logging.info(f"Modelo: {model_path}")
        logging.info(f"TTS: {'espeak' if self.tts_enabled else 'desabilitado'}")
        logging.info(f"CPU cores: {os.cpu_count()}")
        
    def _check_espeak(self):
        """Verificar espeak"""
        try:
            result = subprocess.run(['which', 'espeak'], capture_output=True)
            return result.returncode == 0
        except:
            return False
    
    def _check_resources(self):
        """Verificar recursos do sistema"""
        now = time.time()
        if now - self.resource_monitor['last_check'] < 5:
            return True
        
        self.resource_monitor['last_check'] = now
        
        cpu_percent = psutil.cpu_percent(interval=0.1)
        mem_percent = psutil.virtual_memory().percent
        
        if cpu_percent > self.resource_monitor['cpu_threshold']:
            logging.warning(f"CPU alta: {cpu_percent:.1f}%")
            # Limpar buffers se necessário
            if cpu_percent > 90:
                self._clear_buffers()
        
        if mem_percent > self.resource_monitor['mem_threshold']:
            logging.warning(f"Memória alta: {mem_percent:.1f}%")
        
        return cpu_percent < 95 and mem_percent < 95
    
    def _clear_buffers(self):
        """Limpar buffers para liberar memória"""
        for device_id in [1, 2]:
            with self.buffer_locks[device_id]:
                self.device_buffers[device_id].clear()
        
        # Limpar queue
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except:
                break
    
    def start_server(self):
        """Iniciar servidor UDP"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 131072)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind(('0.0.0.0', self.port))
            self.socket.settimeout(0.5)
            self.running = True
            
            # Threads com prioridades
            threads = [
                threading.Thread(target=self.receive_loop, daemon=True, name="Receiver"),
                threading.Thread(target=self.process_audio, daemon=True, name="Processor"),
                threading.Thread(target=self.command_processor, daemon=True, name="Commander"),
                threading.Thread(target=self.status_monitor, daemon=True, name="Monitor")
            ]
            
            for t in threads:
                t.start()
            
            logging.info(f"Servidor iniciado na porta {self.port}")
            return True
            
        except Exception as e:
            logging.error(f"Erro ao iniciar: {e}")
            return False
    
    def receive_loop(self):
        """Receber pacotes UDP com otimização"""
        header_struct = struct.Struct('IIHHHHHBB')  # I=uint32, H=uint16, B=uint8
        
        while self.running:
            try:
                data, addr = self.socket.recvfrom(4096)
                if len(data) < header_struct.size:
                    continue
                
                # Parse header
                header = header_struct.unpack_from(data, 0)
                sequence, timestamp, device_id, sample_rate, samples_count, checksum, flags, _ = header
                
                if device_id not in [1, 2]:
                    continue
                
                # Extrair áudio
                audio_offset = header_struct.size
                audio_size = samples_count * 2
                
                if len(data) < audio_offset + audio_size:
                    self.stats[device_id]['errors'] += 1
                    continue
                
                audio_data = data[audio_offset:audio_offset + audio_size]
                
                # CRC rápido (só verificar 1 em cada 10 pacotes)
                if sequence % 10 == 0:
                    if self._calculate_crc16_fast(audio_data) != checksum:
                        self.stats[device_id]['errors'] += 1
                        continue
                
                # Stats
                self.stats[device_id]['packets'] += 1
                self.stats[device_id]['last_seen'] = time.time()
                
                # Flags
                is_start = flags & 0x01
                is_end = flags & 0x02
                
                # Prioridade: pacotes de fim têm prioridade
                priority = 0 if is_end else 1
                
                # Adicionar à queue com prioridade
                try:
                    self.audio_queue.put_nowait((priority, device_id, audio_data, is_start, is_end))
                except queue.Full:
                    # Remover item mais antigo se necessário
                    try:
                        self.audio_queue.get_nowait()
                        self.audio_queue.put_nowait((priority, device_id, audio_data, is_start, is_end))
                    except:
                        pass
                        
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    logging.error(f"Erro recepção: {e}")
    
    def _calculate_crc16_fast(self, data):
        """CRC16 otimizado"""
        crc = 0xFFFF
        for i in range(0, len(data), 2):  # Processar 2 bytes por vez
            if i + 1 < len(data):
                crc ^= (data[i] | (data[i + 1] << 8))
            else:
                crc ^= data[i]
            
            for _ in range(8):
                crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
        
        return crc & 0xFFFF
    
    def process_audio(self):
        """Processar áudio com detecção otimizada"""
        while self.running:
            try:
                # Verificar recursos antes de processar
                if not self._check_resources():
                    time.sleep(0.1)
                    continue
                
                _, device_id, audio_data, is_start, is_end = self.audio_queue.get(timeout=0.1)
                
                with self.recording_lock:
                    if self.recording_state['active']:
                        # Modo gravação
                        if device_id == self.recording_state['device_id']:
                            self.recording_state['buffer'].extend(audio_data)
                            
                            # Verificar timeout
                            if time.time() - self.recording_state['start_time'] > self.recording_state['timeout']:
                                logging.warning("Timeout na gravação")
                                is_end = True
                            
                            if is_end:
                                self.processing_queue.put(('command', device_id, bytes(self.recording_state['buffer'])))
                                self.recording_state['active'] = False
                                self.recording_state['buffer'] = bytearray()
                    else:
                        # Modo detecção wake word
                        if is_start:
                            # Limpar buffer no início
                            with self.buffer_locks[device_id]:
                                self.device_buffers[device_id].clear()
                        
                        # Adicionar ao buffer circular
                        with self.buffer_locks[device_id]:
                            samples = struct.unpack(f'{len(audio_data)//2}h', audio_data)
                            self.device_buffers[device_id].extend(samples)
                        
                        if is_end or len(self.device_buffers[device_id]) >= self.sample_rate:
                            # Processar para wake word
                            self.processing_queue.put(('wake', device_id, None))
                            
            except queue.Empty:
                continue
            except Exception as e:
                logging.error(f"Erro processamento: {e}")
    
    def command_processor(self):
        """Processar comandos em thread separada"""
        while self.running:
            try:
                task_type, device_id, audio_data = self.processing_queue.get(timeout=0.5)
                
                if task_type == 'wake':
                    self._detect_wake_word(device_id)
                elif task_type == 'command':
                    self._process_command(device_id, audio_data)
                    
            except queue.Empty:
                continue
            except Exception as e:
                logging.error(f"Erro no processador: {e}")
    
    def _detect_wake_word(self, device_id):
        """Detectar wake word otimizado"""
        try:
            with self.buffer_locks[device_id]:
                if len(self.device_buffers[device_id]) < self.sample_rate // 2:
                    return
                
                # Pegar últimos 1.5 segundos
                samples = list(self.device_buffers[device_id])[-int(self.sample_rate * 1.5):]
            
            # Converter para bytes
            audio_bytes = struct.pack(f'{len(samples)}h', *samples)
            
            # Resetar recognizer para limpar estado
            self.recognizers[device_id].Reset()
            
            # Processar
            if self.recognizers[device_id].AcceptWaveform(audio_bytes):
                result = json.loads(self.recognizers[device_id].Result())
                text = result.get('text', '').strip()
                
                if text and self.wake_words[device_id] in text:
                    device_name = "Motorista" if device_id == 1 else "Passageiro"
                    logging.info(f"🎯 Wake word detectada: '{text}' - {device_name}")
                    
                    self.stats[device_id]['wake_detections'] += 1
                    
                    with self.recording_lock:
                        self.recording_state['active'] = True
                        self.recording_state['device_id'] = device_id
                        self.recording_state['buffer'] = bytearray()
                        self.recording_state['start_time'] = time.time()
                    
                    # Feedback sonoro
                    self._play_beep()
                    
        except Exception as e:
            logging.error(f"Erro detecção wake word: {e}")
    
    def _process_command(self, device_id, audio_data):
        """Processar comando de voz"""
        try:
            device_name = "Motorista" if device_id == 1 else "Passageiro"
            logging.info(f"⏹️ Processando comando - {device_name}")
            
            # Criar novo recognizer para comando completo
            recognizer = vosk.KaldiRecognizer(self.model, self.sample_rate)
            
            # Processar áudio
            recognizer.AcceptWaveform(audio_data)
            result = json.loads(recognizer.FinalResult())
            text = result.get('text', '').strip()
            
            if text:
                logging.info(f"💬 {device_name}: '{text}'")
                
                # Verificar cache
                cache_key = f"{device_id}:{text}"
                if cache_key in self.command_cache:
                    if time.time() - self.command_cache[cache_key]['time'] < self.cache_timeout:
                        response = self.command_cache[cache_key]['response']
                        logging.info("Resposta do cache")
                    else:
                        response = self._generate_response(text, device_id)
                        self.command_cache[cache_key] = {'response': response, 'time': time.time()}
                else:
                    response = self._generate_response(text, device_id)
                    self.command_cache[cache_key] = {'response': response, 'time': time.time()}
                
                if response:
                    self._speak_response(response)
                    
                # Salvar gravação
                self._save_recording(device_id, audio_data, text)
            else:
                logging.warning("Comando vazio ou não reconhecido")
                self._play_error_beep()
                
        except Exception as e:
            logging.error(f"Erro processamento comando: {e}")
    
    def _generate_response(self, text, device_id):
        """Gerar resposta para comando"""
        text_lower = text.lower()
        device_name = "motorista" if device_id == 1 else "passageiro"
        
        # Comandos com respostas
        commands = {
            'hora': lambda: f"São {datetime.now().strftime('%H horas e %M minutos')}",
            'data': lambda: f"Hoje é {datetime.now().strftime('%d de %B de %Y')}",
            'temperatura': lambda: self._get_temperature(),
            'status': lambda: self._get_system_status(),
            'música': lambda: f"Iniciando música para {device_name}",
            'parar música': lambda: "Música pausada",
            'navegação': lambda: "Calculando rota" if device_id == 1 else "Solicitação enviada ao motorista",
            'volume': lambda: "Ajustando volume",
            'emergência': lambda: "Acionando protocolo de emergência",
            'bateria': lambda: self._get_battery_status(),
        }
        
        # Buscar comando
        for cmd, func in commands.items():
            if cmd in text_lower:
                return func()
        
        # Respostas contextuais
        if any(word in text_lower for word in ['olá', 'oi', 'bom dia', 'boa tarde', 'boa noite']):
            return f"Olá {device_name}, como posso ajudar?"
        elif any(word in text_lower for word in ['obrigado', 'valeu', 'agradeço']):
            return "Por nada, estou aqui para ajudar"
        elif 'desligar' in text_lower:
            return "Sistema permanece ativo"
        
        return None
    
    def _get_temperature(self):
        """Obter temperatura do sistema"""
        try:
            # Temperatura da CPU
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                cpu_temp = int(f.read()) / 1000
            
            # Temperatura do TPU (se disponível)
            tpu_temp = "não disponível"
            try:
                with open('/sys/class/thermal/thermal_zone1/temp', 'r') as f:
                    tpu_temp = f"{int(f.read()) / 1000:.1f}°C"
            except:
                pass
            
            return f"CPU {cpu_temp:.1f}°C, TPU {tpu_temp}"
        except:
            return "Temperatura não disponível"
    
    def _get_system_status(self):
        """Status do sistema"""
        cpu = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory().percent
        
        online = []
        for dev_id in [1, 2]:
            if time.time() - self.stats[dev_id]['last_seen'] < 30:
                online.append("motorista" if dev_id == 1 else "passageiro")
        
        return f"CPU {cpu:.0f}%, memória {mem:.0f}%, dispositivos online: {', '.join(online) if online else 'nenhum'}"
    
    def _get_battery_status(self):
        """Status da bateria (simulado)"""
        return "Bateria em 85%, autonomia estimada 4 horas"
    
    def _play_beep(self):
        """Tocar beep de confirmação"""
        if self.tts_enabled:
            try:
                os.system("beep -f 1000 -l 100 2>/dev/null || espeak -s 400 -p 80 'beep' 2>/dev/null &")
            except:
                pass
    
    def _play_error_beep(self):
        """Tocar beep de erro"""
        if self.tts_enabled:
            try:
                os.system("beep -f 500 -l 200 2>/dev/null || espeak -s 400 -p 30 'erro' 2>/dev/null &")
            except:
                pass
    
    def _speak_response(self, text):
        """TTS otimizado"""
        if self.tts_enabled and text:
            try:
                # Usar espeak com configurações otimizadas
                cmd = f'espeak -v pt-br -s 150 -p 50 "{text}" 2>/dev/null &'
                subprocess.Popen(cmd, shell=True)
                logging.info(f"🔊 TTS: '{text}'")
            except Exception as e:
                logging.error(f"Erro TTS: {e}")
    
    def _save_recording(self, device_id, audio_data, text):
        """Salvar gravação com metadados"""
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            device_name = "motorista" if device_id == 1 else "passageiro"
            
            # Nome do arquivo
            safe_text = "".join(c for c in text[:30] if c.isalnum() or c in (' ', '-', '_')).strip()
            filename = f"recordings/{device_name}_{timestamp}_{safe_text}.wav"
            
            # Converter para array numpy
            samples = struct.unpack(f'{len(audio_data)//2}h', audio_data)
            audio_array = np.array(samples, dtype=np.int16)
            
            # Salvar WAV
            with wave.open(filename, 'wb') as wf:
                wf.setnchannels(self.channels)
                wf.setsampwidth(self.sample_width)
                wf.setframerate(self.sample_rate)
                wf.writeframes(audio_array.tobytes())
            
            # Salvar metadados
            metadata = {
                'device_id': device_id,
                'device_name': device_name,
                'timestamp': timestamp,
                'text': text,
                'duration': len(audio_array) / self.sample_rate
            }
            
            with open(f"{filename}.json", 'w', encoding='utf-8') as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)
            
            logging.info(f"💾 Gravação salva: {filename}")
            
        except Exception as e:
            logging.error(f"Erro ao salvar: {e}")
    
    def status_monitor(self):
        """Monitor de status otimizado"""
        while self.running:
            time.sleep(20)
            
            # Limpar cache antigo
            now = time.time()
            self.command_cache = {k: v for k, v in self.command_cache.items() 
                                 if now - v['time'] < self.cache_timeout}
            
            # Status dos dispositivos
            online_devices = []
            for device_id in [1, 2]:
                if now - self.stats[device_id]['last_seen'] < 30:
                    online_devices.append(device_id)
            
            if online_devices:
                stats_msg = []
                for dev_id in online_devices:
                    dev_name = "Mot" if dev_id == 1 else "Pas"
                    s = self.stats[dev_id]
                    stats_msg.append(f"{dev_name}: {s['packets']}pkt, {s['wake_detections']}wake")
                
                logging.info(f"📊 Online: {', '.join(stats_msg)}")
            
            # Recursos do sistema
            cpu = psutil.cpu_percent(interval=0.1)
            mem = psutil.virtual_memory().percent
            if cpu > 50 or mem > 50:
                logging.info(f"💻 Sistema: CPU {cpu:.0f}%, MEM {mem:.0f}%")
    
    def stop(self):
        """Parar servidor"""
        logging.info("Parando servidor...")
        self.running = False
        
        if self.socket:
            self.socket.close()
        
        # Salvar estatísticas finais
        stats_file = f"stats_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(stats_file, 'w') as f:
            json.dump(self.stats, f, indent=2)
        
        logging.info(f"Estatísticas salvas em {stats_file}")
        logging.info("Servidor parado")

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Coral Voice Assistant Otimizado')
    parser.add_argument('--port', type=int, default=8888, help='Porta UDP')
    parser.add_argument('--model', default='/home/mendel/vosk-model-pt', 
                       help='Caminho do modelo Vosk')
    parser.add_argument('--debug', action='store_true', help='Modo debug')
    args = parser.parse_args()
    
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Verificar se está rodando no Coral
    is_coral = os.path.exists('/sys/devices/platform/soc/soc:gpio')
    
    assistant = CoralVoiceAssistant(port=args.port, model_path=args.model)
    
    try:
        if assistant.start_server():
            print("\n" + "="*70)
            print("🎙️  CORAL VOICE ASSISTANT - VERSÃO OTIMIZADA")
            print("="*70)
            print(f"✅ Plataforma: {'Coral Dev Board' if is_coral else 'PC/Debug'}")
            print("✅ Reconhecimento offline com Vosk")
            print("✅ Processamento paralelo otimizado")
            print("✅ Cache de comandos")
            print("✅ Monitoramento de recursos")
            print(f"📡 Porta UDP: {args.port}")
            print("\n🎯 Wake words:")
            print("  🚗 Motorista: 'motorista'")
            print("  🧑 Passageiro: 'passageiro'")
            print("\n⚡ Comandos disponíveis:")
            print("  - hora, data, temperatura, status")
            print("  - música, navegação, volume")
            print("  - emergência, bateria")
            print("\n❌ Ctrl+C para parar")
            print("="*70 + "\n")
            
            while True:
                time.sleep(1)
                
    except KeyboardInterrupt:
        print("\n🛑 Interrompido pelo usuário")
    except Exception as e:
        logging.error(f"Erro fatal: {e}")
    finally:
        assistant.stop()

if __name__ == "__main__":
    main()