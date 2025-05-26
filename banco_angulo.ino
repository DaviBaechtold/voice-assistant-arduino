#include <Arduino_LSM6DSOX.h>

// Estrutura para empacotamento dos dados
struct IMUData {
  float ax, ay, az;  // Acelerômetro
  float gx, gy, gz;  // Giroscópio
  uint32_t marker;   // Marcador final
};

void setup() {
  // Inicializa comunicação serial a 1 Mbps
  Serial.begin(1000000);
  
  // Aguarda conexão serial (opcional para debug)
  while (!Serial && millis() < 5000) {
    delay(10);
  }
  
  // Inicializa a IMU com configurações específicas
  if (!IMU.begin()) {
    Serial.println("Falha ao inicializar IMU!");
    while (1);
  }
  
  // Aguarda estabilização da IMU
  delay(100);
  
  Serial.println("IMU inicializada com sucesso");
  Serial.println("Aguardando primeira leitura...");
  
  // Teste inicial para verificar se a IMU está respondendo
  float ax, ay, az, gx, gy, gz;
  int tentativas = 0;
  
  while (tentativas < 10) {
    if (IMU.accelerationAvailable() && IMU.gyroscopeAvailable()) {
      IMU.readAcceleration(ax, ay, az);
      IMU.readGyroscope(gx, gy, gz);
      
      Serial.print("Teste inicial - Acel: ");
      Serial.print(ax, 3); Serial.print(", ");
      Serial.print(ay, 3); Serial.print(", ");
      Serial.print(az, 3);
      Serial.print(" | Giro: ");
      Serial.print(gx, 3); Serial.print(", ");
      Serial.print(gy, 3); Serial.print(", ");
      Serial.println(gz, 3);
      break;
    }
    delay(50);
    tentativas++;
  }
  
  if (tentativas >= 10) {
    Serial.println("ERRO: IMU não está respondendo!");
  } else {
    Serial.println("IMU funcionando - iniciando transmissão de dados");
  }
  
  delay(1000);
}

void loop() {
  IMUData data;
  static unsigned long lastRead = 0;
  static int readCount = 0;
  
  // Taxa controlada de leitura
  if (millis() - lastRead < 10) {
    return;
  }
  lastRead = millis();
  
  // Verifica se há dados novos disponíveis
  if (IMU.accelerationAvailable() && IMU.gyroscopeAvailable()) {
    // Lê acelerômetro (g - força gravitacional)
    if (IMU.readAcceleration(data.ax, data.ay, data.az)) {
      // Lê giroscópio (dps - graus por segundo)
      if (IMU.readGyroscope(data.gx, data.gy, data.gz)) {
        
        // Define marcador final
        data.marker = 0xFFFFFFFE;
        
        // Envia dados brutos via serial
        Serial.write((uint8_t*)&data, sizeof(IMUData));
        
        readCount++;
        
        // Debug a cada 100 leituras (aproximadamente 1 segundo)
        if (readCount % 100 == 0) {
          Serial.print("Debug #"); Serial.print(readCount);
          Serial.print(" - Acel: ");
          Serial.print(data.ax, 3); Serial.print(", ");
          Serial.print(data.ay, 3); Serial.print(", ");
          Serial.print(data.az, 3);
          Serial.print(" | Giro: ");
          Serial.print(data.gx, 2); Serial.print(", ");
          Serial.print(data.gy, 2); Serial.print(", ");
          Serial.println(data.gz, 2);
        }
      }
    }
  } else {
    // Se não há dados disponíveis, aguarda um pouco mais
    delay(1);
  }
}

// Função auxiliar para debug (não usada no loop principal)
void printIMUData() {
  float ax, ay, az, gx, gy, gz;
  
  if (IMU.accelerationAvailable() && IMU.gyroscopeAvailable()) {
    IMU.readAcceleration(ax, ay, az);
    IMU.readGyroscope(gx, gy, gz);
    
    Serial.print("Acel: ");
    Serial.print(ax, 4); Serial.print(", ");
    Serial.print(ay, 4); Serial.print(", ");
    Serial.print(az, 4);
    
    Serial.print(" | Giro: ");
    Serial.print(gx, 4); Serial.print(", ");
    Serial.print(gy, 4); Serial.print(", ");
    Serial.println(gz, 4);
  }
}