#!/usr/bin/env python3
import time
import psutil
import subprocess
import socket
import struct
import threading
import numpy as np
import json
import os
from datetime import datetime

class PerformanceTester:
    def __init__(self, server_ip='127.0.0.1', server_port=8888):
        self.server_ip = server_ip
        self.server_port = server_port
        self.results = {
            'start_time': datetime.now().isoformat(),
            'tests': []
        }
        
    def test_cpu_baseline(self, duration=10):
        """Testar CPU em idle"""
        print(f"\n📊 Testando CPU baseline ({duration}s)...")
        
        cpu_samples = []
        temp_samples = []
        
        for _ in range(duration):
            cpu_samples.append(psutil.cpu_percent(interval=1))
            
            try:
                with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                    temp_samples.append(int(f.read()) / 1000)
            except:
                temp_samples.append(0)
        
        result = {
            'test': 'cpu_baseline',
            'cpu_avg': np.mean(cpu_samples),
            'cpu_max': np.max(cpu_samples),
            'temp_avg': np.mean(temp_samples),
            'temp_max': np.max(temp_samples)
        }
        
        print(f"  CPU: {result['cpu_avg']:.1f}% avg, {result['cpu_max']:.1f}% max")
        print(f"  Temp: {result['temp_avg']:.1f}°C avg, {result['temp_max']:.1f}°C max")
        
        self.results['tests'].append(result)
        return result
    
    def test_vosk_performance(self):
        """Testar performance do Vosk"""
        print("\n🎤 Testando Vosk...")
        
        import vosk
        
        model_path = "/home/mendel/vosk-model-pt"
        if not os.path.exists(model_path):
            print("  ❌ Modelo não encontrado")
            return None
        
        # Medir tempo de carregamento
        start = time.time()
        model = vosk.Model(model_path)
        load_time = time.time() - start
        
        print(f"  Tempo de carga do modelo: {load_time:.2f}s")
        
        # Testar reconhecimento
        recognizer = vosk.KaldiRecognizer(model, 16000)
        
        # Gerar áudio de teste (1 segundo de silêncio)
        test_audio = np.zeros(16000, dtype=np.int16)
        
        start = time.time()
        recognizer.AcceptWaveform(test_audio.tobytes())
        result = recognizer.FinalResult()
        process_time = time.time() - start
        
        print(f"  Tempo de processamento (1s áudio): {process_time*1000:.1f}ms")
        
        result = {
            'test': 'vosk_performance',
            'model_load_time': load_time,
            'process_time_1s': process_time,
            'realtime_factor': process_time / 1.0
        }
        
        self.results['tests'].append(result)
        return result
    
    def test_udp_latency(self, num_packets=100):
        """Testar latência UDP"""
        print(f"\n📡 Testando latência UDP ({num_packets} pacotes)...")
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(1.0)
        
        latencies = []
        lost = 0
        
        # Header simulado
        header_struct = struct.Struct('IIHHHHHBB')
        
        for i in range(num_packets):
            # Criar pacote de teste
            header = struct.pack('IIHHHHHBB', i, int(time.time()*1000), 1, 16000, 240, 0, 0, 0)
            audio = np.random.randint(-1000, 1000, 240, dtype=np.int16).tobytes()
            packet = header + audio
            
            start = time.time()
            try:
                sock.sendto(packet, (self.server_ip, self.server_port))
                # Não esperamos resposta, medimos apenas envio
                latency = (time.time() - start) * 1000
                latencies.append(latency)
            except:
                lost += 1
            
            time.sleep(0.02)  # 50 pacotes/s
        
        sock.close()
        
        if latencies:
            result = {
                'test': 'udp_latency',
                'packets_sent': num_packets,
                'packets_lost': lost,
                'loss_rate': (lost/num_packets)*100,
                'latency_avg': np.mean(latencies),
                'latency_p99': np.percentile(latencies, 99)
            }
            
            print(f"  Perda: {result['loss_rate']:.1f}%")
            print(f"  Latência: {result['latency_avg']:.2f}ms avg, {result['latency_p99']:.2f}ms p99")
        else:
            result = {'test': 'udp_latency', 'error': 'Sem conexão'}
            print("  ❌ Erro: sem conexão UDP")
        
        self.results['tests'].append(result)
        return result
    
    def test_concurrent_load(self, duration=30):
        """Testar carga com 2 dispositivos simultâneos"""
        print(f"\n⚡ Testando carga simultânea ({duration}s)...")
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        header_struct = struct.Struct('IIHHHHHBB')
        
        stats = {
            'packets_sent': 0,
            'cpu_samples': [],
            'mem_samples': []
        }
        
        running = True
        
        def send_packets():
            seq = 0
            while running:
                for device_id in [1, 2]:
                    header = struct.pack('IIHHHHHBB', seq, int(time.time()*1000), 
                                       device_id, 16000, 480, 0, 0, 0)
                    audio = np.random.randint(-5000, 5000, 480, dtype=np.int16).tobytes()
                    
                    try:
                        sock.sendto(header + audio, (self.server_ip, self.server_port))
                        stats['packets_sent'] += 1
                    except:
                        pass
                    
                    seq += 1
                
                time.sleep(0.03)  # ~33 pacotes/s por dispositivo
        
        # Iniciar envio
        sender = threading.Thread(target=send_packets)
        sender.start()
        
        # Monitorar recursos
        start_time = time.time()
        while time.time() - start_time < duration:
            stats['cpu_samples'].append(psutil.cpu_percent(interval=1))
            stats['mem_samples'].append(psutil.virtual_memory().percent)
        
        running = False
        sender.join()
        sock.close()
        
        result = {
            'test': 'concurrent_load',
            'duration': duration,
            'packets_sent': stats['packets_sent'],
            'packets_per_second': stats['packets_sent'] / duration,
            'cpu_avg': np.mean(stats['cpu_samples']),
            'cpu_max': np.max(stats['cpu_samples']),
            'mem_avg': np.mean(stats['mem_samples']),
            'mem_max': np.max(stats['mem_samples'])
        }
        
        print(f"  Pacotes: {result['packets_sent']} ({result['packets_per_second']:.1f}/s)")
        print(f"  CPU: {result['cpu_avg']:.1f}% avg, {result['cpu_max']:.1f}% max")
        print(f"  MEM: {result['mem_avg']:.1f}% avg, {result['mem_max']:.1f}% max")
        
        self.results['tests'].append(result)
        return result
    
    def test_wake_word_detection(self):
        """Testar detecção de wake word"""
        print("\n🎯 Testando wake word...")
        
        # Simular pacotes com wake word
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        header_struct = struct.Struct('IIHHHHHBB')
        
        # Aqui você precisaria de um arquivo de áudio real com "motorista"
        # Por ora, simulamos
        print("  ⚠️  Teste simplificado (sem áudio real)")
        
        result = {
            'test': 'wake_word_detection',
            'status': 'simplified',
            'note': 'Requer arquivo de áudio com wake word'
        }
        
        self.results['tests'].append(result)
        sock.close()
        return result
    
    def generate_report(self):
        """Gerar relatório"""
        self.results['end_time'] = datetime.now().isoformat()
        
        # Verificar se é Coral Dev Board
        is_coral = os.path.exists('/sys/devices/platform/soc/soc:gpio')
        self.results['platform'] = 'Coral Dev Board' if is_coral else 'PC/Other'
        
        # Info do sistema
        self.results['system'] = {
            'cpu_count': psutil.cpu_count(),
            'memory_total': psutil.virtual_memory().total / (1024**3),  # GB
            'python_version': subprocess.check_output(['python3', '--version']).decode().strip()
        }
        
        filename = f"performance_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, 'w') as f:
            json.dump(self.results, f, indent=2)
        
        print(f"\n📄 Relatório salvo: {filename}")
        
        # Resumo
        print("\n" + "="*50)
        print("RESUMO DOS TESTES")
        print("="*50)
        
        for test in self.results['tests']:
            print(f"\n{test['test']}:")
            for k, v in test.items():
                if k != 'test':
                    if isinstance(v, float):
                        print(f"  {k}: {v:.2f}")
                    else:
                        print(f"  {k}: {v}")

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Teste de Performance - Coral Voice Assistant')
    parser.add_argument('--server-ip', default='127.0.0.1', help='IP do servidor')
    parser.add_argument('--server-port', type=int, default=8888, help='Porta do servidor')
    parser.add_argument('--quick', action='store_true', help='Testes rápidos')
    args = parser.parse_args()
    
    print("🧪 TESTE DE PERFORMANCE - CORAL VOICE ASSISTANT")
    print("="*50)
    
    tester = PerformanceTester(args.server_ip, args.server_port)
    
    # Verificar se servidor está rodando
    print("\n⏳ Verificando servidor...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1.0)
    try:
        sock.sendto(b'test', (args.server_ip, args.server_port))
        print("✅ Servidor acessível")
    except:
        print("❌ Servidor não encontrado - iniciando testes offline")
    sock.close()
    
    # Executar testes
    tests = [
        ('cpu_baseline', lambda: tester.test_cpu_baseline(5 if args.quick else 10)),
        ('vosk_performance', tester.test_vosk_performance),
        ('udp_latency', tester.test_udp_latency),
        ('concurrent_load', lambda: tester.test_concurrent_load(10 if args.quick else 30)),
        ('wake_word_detection', tester.test_wake_word_detection)
    ]
    
    for test_name, test_func in tests:
        try:
            test_func()
        except Exception as e:
            print(f"\n❌ Erro no teste {test_name}: {e}")
            tester.results['tests'].append({
                'test': test_name,
                'error': str(e)
            })
    
    # Gerar relatório
    tester.generate_report()

if __name__ == "__main__":
    main()