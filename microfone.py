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
from scipy import signal
from scipy.fft import fft, ifft, fftfreq
from collections import deque

class VoiceAssistant:
    def __init__(self, port=8888):
        self.port = port
        self.socket = None
        self.running = False
        
        # Configurações de áudio
        self.sample_rate = 16000
        self.buffer_size = 1024  # Buffer para FFT
        
        # Buffers por dispositivo
        self.device_buffers = {
            1: deque(maxlen=self.sample_rate * 5),  # 5 segundos
            2: deque(maxlen=self.sample_rate * 5)
        }
        
        # Estado de captura
        self.capturing = False
        self.capture_buffer = []
        self.silence_count = 0
        self.max_silence = 40
        self.active_device = None
        
        # Wake word única
        self.wake_word = "assistente"
        
        # Reconhecimento de voz
        self.recognizer = sr.Recognizer()
        self.recognizer.energy_threshold = 200
        self.recognizer.pause_threshold = 0.8
        
        # TTS
        self.tts = pyttsx3.init()
        self.tts.setProperty('rate', 180)
        self.tts.setProperty('volume', 0.9)
        
        # Status dos dispositivos
        self.device_status = {
            1: {"name": "MOTORISTA", "connected": False, "packets": 0},
            2: {"name": "PASSAGEIRO", "connected": False, "packets": 0}
        }
        
        # Controle de cooldown para evitar detecções múltiplas
        self.last_wake_detection = 0
        self.wake_cooldown = 3.0  # 3 segundos de cooldown
        
        print("🎙️ Assistente de Voz Dual Simplificado")
        print(f"Wake word: '{self.wake_word}'")
        print("Funcionalidades: FFT + Cancelamento de Ruído")
        
    def start_server(self):
        """Iniciar servidor UDP"""
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.socket.bind(('0.0.0.0', self.port))
            self.socket.settimeout(1.0)
            self.running = True
            
            print(f"Servidor iniciado na porta {self.port}")
            
            # Iniciar threads
            threading.Thread(target=self.receive_loop, daemon=True).start()
            threading.Thread(target=self.process_audio, daemon=True).start()
            threading.Thread(target=self.status_monitor, daemon=True).start()
            
            return True
            
        except Exception as e:
            print(f"Erro ao iniciar servidor: {e}")
            return False
    
    def receive_loop(self):
        """Loop de recepção UDP"""
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
                    time.sleep(0.1)
    
    def process_packet(self, data, addr):
        """Processar pacote de áudio"""
        try:
            # Verificar tamanho mínimo
            if len(data) < 16:
                return
                
            # Decodificar cabeçalho
            header = struct.unpack('<LLHHL', data[:16])
            magic, timestamp, device_id, samples_count, sequence = header
            
            if magic != 0xABCD1234 or device_id not in [1, 2]:
                return
            
            # Extrair áudio
            audio_data = data[16:]
            if len(audio_data) != samples_count * 2:
                return
                
            samples = struct.unpack(f'<{samples_count}h', audio_data)
            audio_array = np.array(samples, dtype=np.int16)
            
            # Atualizar status
            if not self.device_status[device_id]["connected"]:
                print(f"✅ {self.device_status[device_id]['name']} conectado")
                self.device_status[device_id]["connected"] = True
            
            self.device_status[device_id]["packets"] += 1
            
            # Adicionar ao buffer do dispositivo
            self.device_buffers[device_id].extend(audio_array)
            
        except Exception as e:
            print(f"Erro ao processar pacote: {e}")
    
    def safe_int16_conversion(self, audio_data):
        """Conversão segura para int16"""
        try:
            # Garantir que está em float primeiro
            if audio_data.dtype != np.float64 and audio_data.dtype != np.float32:
                audio_data = audio_data.astype(np.float32)
            
            # Normalizar para evitar overflow
            max_val = np.max(np.abs(audio_data))
            if max_val > 32767:
                audio_data = audio_data * (32767 / max_val)
            
            # Clipear valores
            audio_data = np.clip(audio_data, -32768, 32767)
            
            # Converter para int16
            return audio_data.astype(np.int16)
            
        except Exception as e:
            print(f"Erro na conversão segura: {e}")
            # Fallback: retornar array vazio ou original clipeado
            if hasattr(audio_data, 'astype'):
                return np.clip(audio_data, -32768, 32767).astype(np.int16)
            else:
                return np.array([], dtype=np.int16)
    
    def apply_voice_filter_fft(self, audio_data):
        """Aplicar filtro de voz usando FFT - Versão Corrigida"""
        try:
            if len(audio_data) == 0:
                return audio_data
            
            # Garantir que é int16 válido
            audio_data = self.safe_int16_conversion(audio_data)
            
            # Converter para float para processamento
            audio_float = audio_data.astype(np.float32)
            
            # Padding para potência de 2 (melhora performance FFT)
            original_length = len(audio_float)
            padded_length = 2 ** int(np.ceil(np.log2(original_length)))
            audio_padded = np.pad(audio_float, (0, padded_length - original_length), mode='constant')
            
            # Aplicar janela para reduzir vazamento espectral
            window = np.hanning(len(audio_padded))
            audio_windowed = audio_padded * window
            
            # FFT
            fft_data = fft(audio_windowed)
            freqs = fftfreq(len(audio_windowed), 1/self.sample_rate)
            
            # Filtro passa-banda para voz humana (300Hz - 3400Hz)
            mask = np.zeros_like(fft_data, dtype=bool)
            mask[(np.abs(freqs) >= 300) & (np.abs(freqs) <= 3400)] = True
            
            # Aplicar filtro mantendo DC e componentes de baixa frequência mínimas
            fft_filtered = fft_data.copy()
            fft_filtered[~mask] = fft_filtered[~mask] * 0.1  # Reduzir ao invés de zerar
            
            # IFFT
            audio_filtered = np.real(ifft(fft_filtered))
            
            # Remover padding
            audio_filtered = audio_filtered[:original_length]
            
            # Normalização cuidadosa
            # Compensar janela apenas onde não é zero
            window_original = window[:original_length]
            window_original[window_original < 0.1] = 1.0  # Evitar divisão por zero
            audio_filtered = audio_filtered / window_original
            
            # Normalização adicional
            max_val = np.max(np.abs(audio_filtered))
            if max_val > 0:
                audio_filtered = audio_filtered * (np.max(np.abs(audio_float)) / max_val) * 0.8
            
            # Conversão segura de volta para int16
            return self.safe_int16_conversion(audio_filtered)
            
        except Exception as e:
            print(f"Erro no filtro FFT: {e}")
            return self.safe_int16_conversion(audio_data)
    
    def noise_cancellation(self, signal1, signal2):
        """Cancelamento de ruído simples entre os dois sinais - Versão Corrigida"""
        try:
            if len(signal1) == 0 or len(signal2) == 0:
                return signal1, signal2
            
            # Garantir que ambos são int16 válidos
            signal1 = self.safe_int16_conversion(signal1)
            signal2 = self.safe_int16_conversion(signal2)
            
            if len(signal1) != len(signal2):
                min_len = min(len(signal1), len(signal2))
                signal1 = signal1[:min_len]
                signal2 = signal2[:min_len]
            
            if len(signal1) == 0:
                return signal1, signal2
            
            # Converter para float para processamento
            sig1_float = signal1.astype(np.float32)
            sig2_float = signal2.astype(np.float32)
            
            # Calcular correlação cruzada para encontrar delay
            if len(sig1_float) > 100:  # Só fazer correlação se há dados suficientes
                correlation = np.correlate(sig1_float, sig2_float, mode='full')
                delay = np.argmax(correlation) - len(sig2_float) + 1
                delay = np.clip(delay, -len(sig1_float)//4, len(sig1_float)//4)  # Limitar delay
            else:
                delay = 0
            
            # Compensar delay
            if delay > 0:
                sig2_aligned = np.pad(sig2_float, (delay, 0), mode='constant')[:len(sig1_float)]
            elif delay < 0:
                sig1_aligned = np.pad(sig1_float, (-delay, 0), mode='constant')[:len(sig2_float)]
                sig2_aligned = sig2_float
                sig1_float = sig1_aligned
            else:
                sig2_aligned = sig2_float
            
            # Cancelamento adaptativo suave
            alpha = 0.2  # Fator de cancelamento mais conservador
            cancelled_signal1 = sig1_float - alpha * sig2_aligned
            cancelled_signal2 = sig2_aligned - alpha * sig1_float
            
            # Conversão segura de volta para int16
            return (self.safe_int16_conversion(cancelled_signal1), 
                    self.safe_int16_conversion(cancelled_signal2))
            
        except Exception as e:
            print(f"Erro no cancelamento: {e}")
            return (self.safe_int16_conversion(signal1), 
                    self.safe_int16_conversion(signal2))
    
    def get_combined_audio(self):
        """Obter áudio combinado e processado"""
        try:
            # Verificar se ambos dispositivos têm dados
            if (len(self.device_buffers[1]) < self.buffer_size or 
                len(self.device_buffers[2]) < self.buffer_size):
                return None, None
            
            # Obter últimos samples
            audio1 = np.array(list(self.device_buffers[1])[-self.buffer_size:], dtype=np.int16)
            audio2 = np.array(list(self.device_buffers[2])[-self.buffer_size:], dtype=np.int16)
            
            # Aplicar filtro FFT para isolamento de voz
            audio1_filtered = self.apply_voice_filter_fft(audio1)
            audio2_filtered = self.apply_voice_filter_fft(audio2)
            
            # Aplicar cancelamento de ruído
            audio1_clean, audio2_clean = self.noise_cancellation(audio1_filtered, audio2_filtered)
            
            # Determinar qual sinal é mais forte (dispositivo ativo)
            level1 = np.abs(audio1_clean.astype(np.float32)).mean() if len(audio1_clean) > 0 else 0
            level2 = np.abs(audio2_clean.astype(np.float32)).mean() if len(audio2_clean) > 0 else 0
            
            if level1 > level2:
                return audio1_clean, 1
            else:
                return audio2_clean, 2
                
        except Exception as e:
            print(f"Erro ao combinar áudio: {e}")
            return None, None
    
    def process_audio(self):
        """Thread principal de processamento de áudio - CORRIGIDO PARA LOOP CONTÍNUO"""
        print("🔄 Thread de processamento iniciada")
        
        while self.running:
            try:
                current_time = time.time()
                
                if not self.capturing:
                    # Modo de detecção de wake word
                    audio, device_id = self.get_combined_audio()
                    if audio is not None and len(audio) > 0:
                        audio_level = np.abs(audio.astype(np.float32)).mean()
                        
                        # Verificar cooldown para evitar detecções múltiplas
                        if (audio_level > 300 and 
                            current_time - self.last_wake_detection > self.wake_cooldown):
                            
                            # Usar buffer maior para detecção de wake word
                            detection_size = self.sample_rate * 2  # 2 segundos
                            if len(self.device_buffers[device_id]) >= detection_size:
                                detection_audio = np.array(list(self.device_buffers[device_id])[-detection_size:], dtype=np.int16)
                                detection_filtered = self.apply_voice_filter_fft(detection_audio)
                                
                                text = self.recognize_speech(detection_filtered)
                                if text and self.wake_word.lower() in text.lower():
                                    device_name = self.device_status[device_id]["name"]
                                    print(f"\n🎙️ WAKE WORD! ({device_name})")
                                    print(f"Texto: '{text}'")
                                    self.last_wake_detection = current_time
                                    self.start_capture(device_id)
                else:
                    # Modo de captura ativa
                    audio, device_id = self.get_combined_audio()
                    if audio is not None and device_id == self.active_device:
                        self.process_capture(audio)
                
                time.sleep(0.05)  # 50ms
                
            except Exception as e:
                print(f"Erro no processamento: {e}")
                time.sleep(1)
        
        print("🔄 Thread de processamento finalizada")
    
    def start_capture(self, device_id):
        """Iniciar captura de comando"""
        self.capturing = True
        self.active_device = device_id
        self.capture_buffer = []
        self.silence_count = 0
        device_name = self.device_status[device_id]["name"]
        print(f"🔴 GRAVANDO ({device_name})...")
    
    def process_capture(self, audio):
        """Processar áudio durante captura"""
        try:
            if len(audio) == 0:
                return
                
            # Garantir que é int16 válido
            audio = self.safe_int16_conversion(audio)
            self.capture_buffer.extend(audio)
            
            # Detectar silêncio
            audio_level = np.abs(audio.astype(np.float32)).mean()
            if audio_level < 200:
                self.silence_count += 1
            else:
                self.silence_count = 0
            
            # Mostrar nível
            bars = "█" * min(int(audio_level / 300), 15)
            print(f"\r🎙️ [{bars:<15}] {int(audio_level):4d}", end="", flush=True)
            
            # Finalizar se muito silêncio ou muito longo
            duration = len(self.capture_buffer) / self.sample_rate
            if (self.silence_count >= self.max_silence and duration >= 1.0) or duration >= 8.0:
                self.stop_capture()
                
        except Exception as e:
            print(f"\nErro na captura: {e}")
    
    def stop_capture(self):
        """Finalizar captura e processar comando - CORRIGIDO PARA RETORNAR AO LOOP"""
        print(f"\n\n✅ PROCESSANDO...")
        
        try:
            if len(self.capture_buffer) > 0:
                # Converter para numpy com verificação
                full_audio = np.array(self.capture_buffer, dtype=np.int16)
                full_audio = self.safe_int16_conversion(full_audio)
                
                duration = len(full_audio) / self.sample_rate
                
                # Aplicar filtro final
                filtered_audio = self.apply_voice_filter_fft(full_audio)
                
                # Salvar para debug
                timestamp = datetime.now().strftime('%H%M%S')
                device_name = self.device_status[self.active_device]["name"].lower()
                filename = f"cmd_{device_name}_{timestamp}_{duration:.1f}s.wav"
                self.save_audio(filtered_audio, filename)
                
                print(f"📁 Salvo: {filename}")
                print("🔍 Reconhecendo...")
                
                # Reconhecer fala
                text = self.recognize_speech(filtered_audio)
                
                if text:
                    device_name = self.device_status[self.active_device]["name"]
                    print(f"✅ [{device_name}] '{text}'")
                    
                    # Processar comando
                    response = self.process_command(text, self.active_device)
                    if response:
                        # Executar TTS em thread separada para não bloquear
                        threading.Thread(target=self.speak, args=(response,), daemon=True).start()
                else:
                    print("❌ Não reconhecido")
            
        except Exception as e:
            print(f"Erro ao processar comando: {e}")
        finally:
            # IMPORTANTE: Reset SEMPRE executado para garantir retorno ao loop
            self.reset_capture_state()
    
    def reset_capture_state(self):
        """Reset do estado de captura - FUNÇÃO SEPARADA PARA GARANTIR EXECUÇÃO"""
        try:
            self.capturing = False
            self.active_device = None
            self.capture_buffer = []
            self.silence_count = 0
            
            print(f"\n⏳ Aguardando '{self.wake_word}'...\n")
            print("🔄 Sistema pronto para próxima detecção")
            
        except Exception as e:
            print(f"Erro no reset: {e}")
    
    def recognize_speech(self, audio_data):
        """Reconhecer fala"""
        try:
            if len(audio_data) == 0:
                return None
                
            # Garantir que é int16 válido
            audio_data = self.safe_int16_conversion(audio_data)
            
            audio_sr = sr.AudioData(
                audio_data.tobytes(), 
                self.sample_rate, 
                2
            )
            return self.recognizer.recognize_google(audio_sr, language='pt-BR')
            
        except sr.UnknownValueError:
            return None
        except sr.RequestError as e:
            print(f"Erro no serviço Google: {e}")
            return None
        except Exception as e:
            print(f"Erro reconhecimento: {e}")
            return None
    
    def process_command(self, text, device_id):
        """Processar comando de voz"""
        text_lower = text.lower()
        device_name = self.device_status[device_id]["name"]
        
        # Comandos específicos por posição
        if device_id == 1:  # Motorista
            if any(word in text_lower for word in ['velocidade', 'radar']):
                return "Velocidade: 85 km/h. Limite: 90 km/h"
            elif any(word in text_lower for word in ['combustível', 'gasolina']):
                return "Combustível: 65%. Autonomia: 280 km"
            elif any(word in text_lower for word in ['navegação', 'rota']):
                return "Próxima saída em 1.5 quilômetros à direita"
        
        elif device_id == 2:  # Passageiro
            if any(word in text_lower for word in ['música', 'playlist']):
                return "Tocando sua playlist favorita"
            elif any(word in text_lower for word in ['temperatura', 'clima']):
                return "Ajustando temperatura para 22 graus"
            elif any(word in text_lower for word in ['janela']):
                return "Controlando janela do passageiro"
        
        # Comandos gerais
        if any(word in text_lower for word in ['olá', 'oi']):
            return f"Olá! Como posso ajudar, {device_name.lower()}?"
        elif any(word in text_lower for word in ['hora','horas']):
            now = datetime.now()
            return f"São {now.hour}:{now.minute:02d}"
        elif any(word in text_lower for word in ['obrigado', 'valeu']):
            return "De nada! Sempre à disposição"
        elif any(word in text_lower for word in ['teste']):
            return "Sistema funcionando perfeitamente!"
        else:
            return f"Comando '{text}' recebido. Em que posso ajudar?"
    
    def speak(self, text):
        """Falar resposta - EXECUTADO EM THREAD SEPARADA"""
        try:
            print(f"🔊 ASSISTENTE: '{text}'")
            self.tts.say(text)
            self.tts.runAndWait()
            print("🔄 Resposta finalizada, sistema pronto")
        except Exception as e:
            print(f"Erro TTS: {e}")
    
    def save_audio(self, audio_data, filename):
        """Salvar áudio em WAV"""
        try:
            # Garantir que é int16 válido antes de salvar
            audio_data = self.safe_int16_conversion(audio_data)
            
            with wave.open(filename, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(self.sample_rate)
                wf.writeframes(audio_data.tobytes())
        except Exception as e:
            print(f"Erro ao salvar: {e}")
    
    def status_monitor(self):
        """Monitor de status"""
        while self.running:
            try:
                time.sleep(10)
                
                print(f"\n{'='*50}")
                print(f"📊 STATUS - {datetime.now().strftime('%H:%M:%S')}")
                print(f"{'='*50}")
                
                for device_id in [1, 2]:
                    status = self.device_status[device_id]
                    conn_status = "🟢" if status["connected"] else "🔴"
                    active_status = "🎙️" if self.active_device == device_id else ""
                    
                    print(f"{conn_status} {status['name']:<10} {active_status}")
                    print(f"   Pacotes: {status['packets']}")
                
                print(f"Wake word: '{self.wake_word}'")
                print(f"Capturando: {'SIM' if self.capturing else 'NÃO'}")
                print(f"Último wake: {time.time() - self.last_wake_detection:.1f}s atrás")
                print(f"{'='*50}\n")
                
            except Exception as e:
                print(f"Erro no monitor: {e}")
    
    def stop(self):
        """Parar sistema"""
        print("🛑 Parando assistente...")
        self.running = False
        if self.socket:
            self.socket.close()

def main():
    assistant = VoiceAssistant(port=8888)
    
    try:
        if assistant.start_server():
            print("="*60)
            print("🎙️ ASSISTENTE DE VOZ DUAL ATIVO")
            print("="*60)
            print("🔧 Funcionalidades:")
            print("   ✓ Captura simultânea de 2 microfones")
            print("   ✓ Filtro FFT para isolamento de voz")
            print("   ✓ Cancelamento de ruído adaptativo")
            print("   ✓ Detecção automática do falante ativo")
            print("   ✓ Comandos específicos por posição")
            print("   ✓ Loop contínuo de detecção")
            print("   ✓ Cooldown anti-spam")
            print(f"🗣️  Wake word: '{assistant.wake_word}'")
            print("🛑 Ctrl+C para parar")
            print("="*60 + "\n")
            
            while True:
                time.sleep(1)
                
    except KeyboardInterrupt:
        print("\n🛑 Sistema interrompido")
    except Exception as e:
        print(f"❌ Erro: {e}")
    finally:
        assistant.stop()

if __name__ == "__main__":
    main()