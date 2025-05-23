import serial
import struct
import numpy as np
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
import matplotlib.animation as animation
from collections import deque
import time
import math

class KalmanFilter:
    """Filtro de Kalman simples para ângulos"""
    def __init__(self, process_variance=1e-3, measurement_variance=1e-1):
        self.process_variance = process_variance
        self.measurement_variance = measurement_variance
        self.posteri_estimate = 0.0
        self.posteri_error_estimate = 1.0
        
    def update(self, measurement):
        # Predição
        priori_estimate = self.posteri_estimate
        priori_error_estimate = self.posteri_error_estimate + self.process_variance
        
        # Atualização
        blending_factor = priori_error_estimate / (priori_error_estimate + self.measurement_variance)
        self.posteri_estimate = priori_estimate + blending_factor * (measurement - priori_estimate)
        self.posteri_error_estimate = (1 - blending_factor) * priori_error_estimate
        
        return self.posteri_estimate

class IMUProcessor:
    def __init__(self, port='COM3', baudrate=1000000):
        """
        Inicializa o processador IMU
        port: Porta serial do Arduino (ex: 'COM3' no Windows, '/dev/ttyACM0' no Linux)
        """
        self.port = port
        self.baudrate = baudrate
        self.serial_conn = None
        
        # Filtros Kalman para pitch e roll
        self.pitch_filter = KalmanFilter(process_variance=1e-3, measurement_variance=5e-2)
        self.roll_filter = KalmanFilter(process_variance=1e-3, measurement_variance=5e-2)
        
        # Histórico de dados para análise
        self.pitch_history = deque(maxlen=500)
        self.roll_history = deque(maxlen=500)
        self.raw_pitch_history = deque(maxlen=500)
        self.raw_roll_history = deque(maxlen=500)
        
        # Dados atuais
        self.current_pitch = 0.0
        self.current_roll = 0.0
        self.current_raw_pitch = 0.0
        self.current_raw_roll = 0.0
        
        # Dados brutos da IMU
        self.ax, self.ay, self.az = 0.0, 0.0, 0.0
        self.gx, self.gy, self.gz = 0.0, 0.0, 0.0
        
    def connect(self):
        """Conecta à porta serial"""
        try:
            self.serial_conn = serial.Serial(self.port, self.baudrate, timeout=1)
            print(f"Conectado à porta {self.port} a {self.baudrate} bps")
            time.sleep(2)  # Aguarda estabilização
            return True
        except Exception as e:
            print(f"Erro ao conectar: {e}")
            return False
    
    def disconnect(self):
        """Desconecta da porta serial"""
        if self.serial_conn and self.serial_conn.is_open:
            self.serial_conn.close()
            print("Desconectado da porta serial")
    
    def read_imu_packet(self):
        """Lê um pacote completo da IMU"""
        if not self.serial_conn or not self.serial_conn.is_open:
            return None
        
        try:
            # Limpa buffer se houver dados antigos
            if self.serial_conn.in_waiting > 280:  # ~10 pacotes
                self.serial_conn.reset_input_buffer()
                print("Buffer limpo - muitos dados acumulados")
            
            # Lê um pacote completo (28 bytes: 6 floats + 1 uint32)
            data = self.serial_conn.read(28)
            
            if len(data) == 28:
                # Desempacota os dados
                unpacked = struct.unpack('<6f1I', data)  # Little-endian
                
                # Verifica marcador
                if unpacked[6] == 0xFFFFFFFE:
                    old_ax, old_ay, old_az = self.ax, self.ay, self.az
                    self.ax, self.ay, self.az = unpacked[0], unpacked[1], unpacked[2]
                    self.gx, self.gy, self.gz = unpacked[3], unpacked[4], unpacked[5]
                    
                    # Detecta se os dados estão "travados"
                    if abs(old_ax - self.ax) < 0.001 and abs(old_ay - self.ay) < 0.001 and abs(old_az - self.az) < 0.001:
                        self.stuck_count = getattr(self, 'stuck_count', 0) + 1
                        if self.stuck_count > 50:  # ~0.5 segundos
                            print(f"\n⚠️  AVISO: Dados parecem travados há {self.stuck_count} leituras!")
                            print(f"   Aceleração fixa em: ({self.ax:.3f}, {self.ay:.3f}, {self.az:.3f})")
                            self.stuck_count = 0
                    else:
                        self.stuck_count = 0
                    
                    return True
                else:
                    print(f"Marcador inválido: {hex(unpacked[6])}")
                    # Tenta ressincronizar procurando pelo marcador
                    self.resync_serial()
                    
        except Exception as e:
            print(f"Erro na leitura: {e}")
            
        return False
    
    def resync_serial(self):
        """Tenta ressincronizar a comunicação serial"""
        print("Tentando ressincronizar...")
        marker_bytes = struct.pack('<I', 0xFFFFFFFE)
        
        # Lê até encontrar o marcador
        buffer = b''
        timeout_start = time.time()
        
        while time.time() - timeout_start < 2:  # Timeout de 2 segundos
            byte = self.serial_conn.read(1)
            if byte:
                buffer += byte
                if len(buffer) > 4:
                    buffer = buffer[-4:]  # Mantém apenas os últimos 4 bytes
                
                if buffer == marker_bytes:
                    print("Ressincronizado com sucesso!")
                    return True
        
        print("Falha na ressincronização")
        return False
    
    def calculate_angles(self):
        """Calcula pitch e roll a partir dos dados do acelerômetro"""
        # Converte aceleração em ângulos (em radianos)
        pitch_rad = math.atan2(self.ax, math.sqrt(self.ay**2 + self.az**2))
        roll_rad = math.atan2(self.ay, self.az)
        
        # Converte para graus
        raw_pitch = math.degrees(pitch_rad)
        raw_roll = math.degrees(roll_rad)
        
        # Aplica filtro Kalman
        filtered_pitch = self.pitch_filter.update(raw_pitch)
        filtered_roll = self.roll_filter.update(raw_roll)
        
        # Atualiza valores atuais
        self.current_raw_pitch = raw_pitch
        self.current_raw_roll = raw_roll
        self.current_pitch = filtered_pitch
        self.current_roll = filtered_roll
        
        # Adiciona ao histórico
        self.pitch_history.append(filtered_pitch)
        self.roll_history.append(filtered_roll)
        self.raw_pitch_history.append(raw_pitch)
        self.raw_roll_history.append(raw_roll)
        
        return filtered_pitch, filtered_roll
    
    def get_rotation_matrix(self, pitch, roll, yaw=0):
        """Calcula matriz de rotação 3D"""
        # Converte graus para radianos
        p = math.radians(pitch)
        r = math.radians(roll)
        y = math.radians(yaw)
        
        # Matrizes de rotação
        Rx = np.array([[1, 0, 0],
                       [0, math.cos(r), -math.sin(r)],
                       [0, math.sin(r), math.cos(r)]])
        
        Ry = np.array([[math.cos(p), 0, math.sin(p)],
                       [0, 1, 0],
                       [-math.sin(p), 0, math.cos(p)]])
        
        Rz = np.array([[math.cos(y), -math.sin(y), 0],
                       [math.sin(y), math.cos(y), 0],
                       [0, 0, 1]])
        
        return Rz @ Ry @ Rx

class IMUVisualizer:
    def __init__(self, processor):
        self.processor = processor
        self.fig = plt.figure(figsize=(15, 10))
        
        # Configuração dos subplots
        self.ax_3d = self.fig.add_subplot(221, projection='3d')
        self.ax_pitch = self.fig.add_subplot(222)
        self.ax_roll = self.fig.add_subplot(223)
        self.ax_raw = self.fig.add_subplot(224)
        
        # Configuração da visualização 3D
        self.setup_3d_plot()
        self.setup_2d_plots()
        
        # Vértices de um prisma retangular (Arduino)
        self.create_arduino_shape()
        
    def setup_3d_plot(self):
        """Configura o gráfico 3D"""
        self.ax_3d.set_xlim([-2, 2])
        self.ax_3d.set_ylim([-2, 2])
        self.ax_3d.set_zlim([-2, 2])
        self.ax_3d.set_xlabel('X')
        self.ax_3d.set_ylabel('Y')
        self.ax_3d.set_zlabel('Z')
        self.ax_3d.set_title('Orientação Arduino (Pitch & Roll)')
        
    def setup_2d_plots(self):
        """Configura os gráficos 2D"""
        self.ax_pitch.set_title('Pitch (°)')
        self.ax_pitch.set_ylim([-90, 90])
        self.ax_pitch.grid(True)
        
        self.ax_roll.set_title('Roll (°)')
        self.ax_roll.set_ylim([-180, 180])
        self.ax_roll.grid(True)
        
        self.ax_raw.set_title('Dados Brutos vs Filtrados')
        self.ax_raw.set_ylim([-90, 90])
        self.ax_raw.grid(True)
        
    def create_arduino_shape(self):
        """Cria a forma do prisma representando o Arduino"""
        # Dimensões aproximadas do Arduino Nano (escala ampliada)
        w, h, d = 1.8, 0.3, 0.7
        
        # Vértices do prisma
        self.vertices = np.array([
            [-w/2, -h/2, -d/2], [w/2, -h/2, -d/2], [w/2, h/2, -d/2], [-w/2, h/2, -d/2],  # Face inferior
            [-w/2, -h/2, d/2],  [w/2, -h/2, d/2],  [w/2, h/2, d/2],  [-w/2, h/2, d/2]    # Face superior
        ])
        
        # Faces do prisma (índices dos vértices)
        self.faces = [
            [0, 1, 2, 3],  # Face inferior
            [4, 5, 6, 7],  # Face superior
            [0, 1, 5, 4],  # Face frontal
            [2, 3, 7, 6],  # Face traseira
            [1, 2, 6, 5],  # Face direita
            [4, 7, 3, 0]   # Face esquerda
        ]
        
    def update_3d_visualization(self):
        """Atualiza a visualização 3D"""
        self.ax_3d.clear()
        self.setup_3d_plot()
        
        # Aplica rotação aos vértices
        rotation_matrix = self.processor.get_rotation_matrix(
            self.processor.current_pitch, 
            self.processor.current_roll
        )
        
        rotated_vertices = np.array([rotation_matrix @ v for v in self.vertices])
        
        # Desenha as faces do prisma
        colors = ['red', 'blue', 'green', 'yellow', 'orange', 'purple']
        for i, face in enumerate(self.faces):
            face_vertices = rotated_vertices[face]
            # Adiciona o primeiro vértice no final para fechar a face
            face_vertices = np.vstack([face_vertices, face_vertices[0]])
            
            self.ax_3d.plot(face_vertices[:, 0], face_vertices[:, 1], face_vertices[:, 2], 
                           color=colors[i % len(colors)], linewidth=2)
        
        # Adiciona informações de texto
        self.ax_3d.text2D(0.05, 0.95, f'Pitch: {self.processor.current_pitch:.1f}°', 
                         transform=self.ax_3d.transAxes, fontsize=12, weight='bold')
        self.ax_3d.text2D(0.05, 0.85, f'Roll: {self.processor.current_roll:.1f}°', 
                         transform=self.ax_3d.transAxes, fontsize=12, weight='bold')
        
    def update_2d_plots(self):
        """Atualiza os gráficos 2D"""
        if len(self.processor.pitch_history) > 1:
            x = range(len(self.processor.pitch_history))
            
            # Gráfico de Pitch
            self.ax_pitch.clear()
            self.ax_pitch.plot(x, list(self.processor.pitch_history), 'b-', label='Filtrado', linewidth=2)
            self.ax_pitch.plot(x, list(self.processor.raw_pitch_history), 'r-', alpha=0.5, label='Bruto')
            self.ax_pitch.set_title(f'Pitch: {self.processor.current_pitch:.1f}°')
            self.ax_pitch.set_ylim([-90, 90])
            self.ax_pitch.grid(True)
            self.ax_pitch.legend()
            
            # Gráfico de Roll
            self.ax_roll.clear()
            self.ax_roll.plot(x, list(self.processor.roll_history), 'g-', label='Filtrado', linewidth=2)
            self.ax_roll.plot(x, list(self.processor.raw_roll_history), 'r-', alpha=0.5, label='Bruto')
            self.ax_roll.set_title(f'Roll: {self.processor.current_roll:.1f}°')
            self.ax_roll.set_ylim([-180, 180])
            self.ax_roll.grid(True)
            self.ax_roll.legend()
            
            # Comparação Raw vs Filtrado
            self.ax_raw.clear()
            recent_data = min(100, len(x))  # Últimos 100 pontos
            x_recent = x[-recent_data:]
            self.ax_raw.plot(x_recent, list(self.processor.pitch_history)[-recent_data:], 'b-', label='Pitch Filtrado')
            self.ax_raw.plot(x_recent, list(self.processor.raw_pitch_history)[-recent_data:], 'b--', alpha=0.7, label='Pitch Bruto')
            self.ax_raw.plot(x_recent, list(self.processor.roll_history)[-recent_data:], 'g-', label='Roll Filtrado')
            self.ax_raw.plot(x_recent, list(self.processor.raw_roll_history)[-recent_data:], 'g--', alpha=0.7, label='Roll Bruto')
            self.ax_raw.set_title('Comparação: Dados Brutos vs Filtrados')
            self.ax_raw.set_ylim([-90, 90])
            self.ax_raw.grid(True)
            self.ax_raw.legend()

def main():
    # Configuração da porta serial - AJUSTE CONFORME SEU SISTEMA
    # Windows: 'COM3', 'COM4', etc.
    # Linux/Mac: '/dev/ttyACM0', '/dev/ttyUSB0', etc.
    
    # Detecta automaticamente a porta (opcional)
    import serial.tools.list_ports
    ports = serial.tools.list_ports.comports()
    print("Portas disponíveis:")
    for port in ports:
        print(f"  {port.device}: {port.description}")
    
    # Substitua pela sua porta
    PORT = 'COM5'  # Arduino na porta COM5
    
    # Pergunta se quer modo debug
    debug_mode = input("\nDeseja ativar modo debug detalhado? (s/n): ").lower().startswith('s')
    
    # Inicializa o processador
    processor = IMUProcessor(port=PORT)
    
    if not processor.connect():
        print("Falha na conexão. Verifique a porta e tente novamente.")
        print("\nDicas de diagnóstico:")
        print("1. Verifique se o Arduino está conectado na COM5")
        print("2. Certifique-se de que o código foi carregado no Arduino")
        print("3. Feche o Serial Monitor do Arduino IDE se estiver aberto")
        print("4. Tente desconectar e reconectar o cabo USB")
        return
    
    if debug_mode:
        print("\n=== MODO DEBUG ATIVADO ===")
        print("Lendo primeiros 10 pacotes para diagnóstico...")
        
        for i in range(10):
            if processor.read_imu_packet():
                print(f"Pacote {i+1}: ax={processor.ax:.3f}, ay={processor.ay:.3f}, az={processor.az:.3f}")
                print(f"          gx={processor.gx:.3f}, gy={processor.gy:.3f}, gz={processor.gz:.3f}")
                
                # Calcula magnitude da aceleração (deve ser ~1g quando parado)
                magnitude = math.sqrt(processor.ax**2 + processor.ay**2 + processor.az**2)
                print(f"          Magnitude: {magnitude:.3f}g")
                
                if abs(magnitude - 1.0) > 0.5:
                    print("          ⚠️  Magnitude anormal - possível problema no sensor")
                
                time.sleep(0.1)
            else:
                print(f"Pacote {i+1}: FALHA NA LEITURA")
        
        continuar = input("\nContinuar com visualização? (s/n): ").lower().startswith('s')
        if not continuar:
            processor.disconnect()
            return
    
    # Inicializa o visualizador
    visualizer = IMUVisualizer(processor)
    
    print("Iniciando visualização... Pressione Ctrl+C para parar")
    print("Incline o Arduino para ver a resposta na tela!")
    
    # Contador para detectar problemas
    no_change_count = 0
    last_values = (0, 0, 0)
    
    try:
        plt.ion()  # Modo interativo
        
        while True:
            # Lê dados da IMU
            if processor.read_imu_packet():
                # Calcula ângulos
                pitch, roll = processor.calculate_angles()
                
                # Detecta se os valores não estão mudando
                current_values = (round(processor.ax, 2), round(processor.ay, 2), round(processor.az, 2))
                if current_values == last_values:
                    no_change_count += 1
                    if no_change_count > 100:  # ~1 segundo
                        print(f"\n⚠️  PROBLEMA: Valores não mudam há {no_change_count} leituras!")
                        print("   Tente mover o Arduino ou verificar a conexão")
                        no_change_count = 0
                else:
                    no_change_count = 0
                    last_values = current_values
                
                # Atualiza visualizações
                visualizer.update_3d_visualization()
                visualizer.update_2d_plots()
                
                # Atualiza display
                plt.tight_layout()
                plt.pause(0.01)
                
                # Informações no console (menos verboso)
                if not debug_mode:
                    print(f"\rPitch: {pitch:6.1f}° | Roll: {roll:6.1f}° | "
                          f"Mag: {math.sqrt(processor.ax**2 + processor.ay**2 + processor.az**2):.2f}g", 
                          end='', flush=True)
    
    except KeyboardInterrupt:
        print("\n\nParando...")
    
    finally:
        processor.disconnect()
        plt.ioff()
        plt.show()

if __name__ == "__main__":
    print("=== Sistema de Monitoramento IMU ===")
    print("Requisitos: pip install pyserial numpy matplotlib")
    print()
    main()