// Arduino Nano 33 BLE Sense - Audio Capture and WiFi Transmission
// Para Voice Assistant - Motorista

#include <WiFiNINA.h>
#include <WiFiUdp.h>
#include <PDM.h>

// Configurações WiFi
const char* ssid = "FRITZ!Box 7590 SU_EXT";
const char* password = "00747139424723748140";

// Configurações do servidor
const char* host_ip = "192.168.178.169"; // IP do computador host
const int host_port = 8888;

// Configurações de áudio
const int SAMPLE_RATE = 16000;
const int BUFFER_SIZE = 512;
const int PACKET_SIZE = 256;

// Buffers
short audioBuffer[BUFFER_SIZE];
volatile int samplesRead = 0;
volatile bool audioReady = false;

// Objetos WiFi
WiFiUDP udp;
int localPort = 2390; // Porta específica do motorista

// Configurações PDM
void setup() {
  Serial.begin(115200);
  while (!Serial) delay(10);
  
  Serial.println("=== Arduino Audio Capture System ===");
  Serial.println("Dispositivo: MOTORISTA");
  
  // Inicializar WiFi
  if (WiFi.status() == WL_NO_MODULE) {
    Serial.println("ERRO: Módulo WiFi não encontrado!");
    while (true);
  }
  
  // Conectar ao WiFi
  Serial.print("Conectando ao WiFi: ");
  Serial.println(ssid);
  
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  
  Serial.println("\nWiFi conectado!");
  Serial.print("IP: ");
  Serial.println(WiFi.localIP());
  
  // Inicializar UDP
  udp.begin(localPort);
  Serial.print("UDP iniciado na porta: ");
  Serial.println(localPort);
  
  // Configurar PDM (microfone)
  PDM.onReceive(onPDMdata);
  PDM.setBufferSize(BUFFER_SIZE);
  
  if (!PDM.begin(1, SAMPLE_RATE)) {
    Serial.println("ERRO: Falha ao inicializar PDM!");
    while (1);
  }
  
  Serial.println("Microfone inicializado!");
  Serial.println("Sistema pronto para captura de áudio...\n");
  
  delay(1000);
}

void loop() {
  // Verificar se há dados de áudio prontos
  if (audioReady) {
    audioReady = false;
    
    // Enviar áudio em pacotes
    sendAudioData();
    
    // Mostrar status
    static unsigned long lastStatus = 0;
    if (millis() - lastStatus > 5000) { // A cada 5 segundos
      Serial.print("[MOTORISTA] Enviando áudio... Samples: ");
      Serial.print(samplesRead);
      Serial.print(" | WiFi: ");
      Serial.print(WiFi.RSSI());
      Serial.println(" dBm");
      lastStatus = millis();
    }
  }
  
  // Verificar conexão WiFi
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("WiFi desconectado! Tentando reconectar...");
    WiFi.begin(ssid, password);
    while (WiFi.status() != WL_CONNECTED) {
      delay(500);
      Serial.print(".");
    }
    Serial.println("\nWiFi reconectado!");
  }
  
  delay(1);
}

// Callback do PDM - executado quando há dados disponíveis
void onPDMdata() {
  int bytesAvailable = PDM.available();
  
  if (bytesAvailable > 0) {
    PDM.read(audioBuffer, bytesAvailable);
    samplesRead = bytesAvailable / 2; // 2 bytes por sample (16-bit)
    audioReady = true;
  }
}

// Função para enviar dados de áudio via UDP
void sendAudioData() {
  // Cabeçalho do pacote
  struct AudioPacket {
    uint32_t timestamp;
    uint16_t device_id;
    uint16_t sample_rate;
    uint16_t samples_count;
    uint16_t checksum;
  } packet_header;
  
  packet_header.timestamp = millis();
  packet_header.device_id = 1; // ID do MOTORISTA
  packet_header.sample_rate = SAMPLE_RATE;
  packet_header.samples_count = samplesRead;
  packet_header.checksum = calculateChecksum(audioBuffer, samplesRead);
  
  // Enviar em pacotes menores se necessário
  int totalBytes = samplesRead * 2; // 2 bytes por sample
  int packetCount = (totalBytes + PACKET_SIZE - 1) / PACKET_SIZE;
  
  for (int i = 0; i < packetCount; i++) {
    udp.beginPacket(host_ip, host_port);
    
    // Enviar cabeçalho apenas no primeiro pacote
    if (i == 0) {
      udp.write((uint8_t*)&packet_header, sizeof(packet_header));
    }
    
    // Calcular dados do pacote atual
    int startIdx = i * (PACKET_SIZE / 2);
    int endIdx = min(startIdx + (PACKET_SIZE / 2), samplesRead);
    int packetSamples = endIdx - startIdx;
    
    // Enviar dados de áudio
    udp.write((uint8_t*)&audioBuffer[startIdx], packetSamples * 2);
    
    int result = udp.endPacket();
    if (result == 0) {
      Serial.println("ERRO: Falha ao enviar pacote UDP");
    }
    
    // Pequeno delay entre pacotes
    delayMicroseconds(100);
  }
}

// Função para calcular checksum simples
uint16_t calculateChecksum(short* data, int count) {
  uint32_t sum = 0;
  for (int i = 0; i < count; i++) {
    sum += abs(data[i]);
  }
  return (uint16_t)(sum % 65536);
}

// Função para debug - mostrar configurações
void printConfig() {
  Serial.println("\n=== Configurações MOTORISTA ===");
  Serial.print("SSID: "); Serial.println(ssid);
  Serial.print("IP Local: "); Serial.println(WiFi.localIP());
  Serial.print("Host: "); Serial.print(host_ip); Serial.print(":"); Serial.println(host_port);
  Serial.print("Sample Rate: "); Serial.println(SAMPLE_RATE);
  Serial.print("Buffer Size: "); Serial.println(BUFFER_SIZE);
  Serial.print("Device ID: 1 (MOTORISTA)");
  Serial.println("=====================\n");
}