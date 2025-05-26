#include <WiFiNINA.h>
#include <WiFiUdp.h>
#include <PDM.h>

// Configurações WiFi
const char* ssid = "FRITZ!Box 7590 SU_EXT";
const char* password = "00747139424723748140";
const char* host_ip = "192.168.178.169";
const int host_port = 8888;

// Configurações de áudio
const int SAMPLE_RATE = 16000;
const int BUFFER_SIZE = 256;  // Reduzido para melhor performance
const uint16_t DEVICE_ID = 2;
const char* DEVICE_NAME = "PASSAGEIRO";

// Buffers e variáveis
short audioBuffer[BUFFER_SIZE];
volatile int samplesRead = 0;
volatile bool audioReady = false;
WiFiUDP udp;
uint32_t sequenceNumber = 0;

// Estrutura de pacote simplificada
struct __attribute__((packed)) AudioPacket {
  uint32_t magic;
  uint32_t timestamp;
  uint16_t device_id;
  uint16_t samples_count;
  uint32_t sequence;
};

void setup() {
  Serial.begin(115200);
  while (!Serial) delay(10);
  
  Serial.println("=== Arduino Voice Capture - PASSAGEIRO ===");
  
  // Conectar WiFi
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi conectado!");
  Serial.println(WiFi.localIP());
  
  // Inicializar UDP
  udp.begin(2392);
  
  // Configurar PDM
  PDM.onReceive(onPDMdata);
  PDM.setBufferSize(BUFFER_SIZE);
  PDM.setGain(25);  // Ganho otimizado
  
  if (!PDM.begin(1, SAMPLE_RATE)) {
    Serial.println("ERRO: PDM falhou!");
    while (1);
  }
  
  Serial.println("Sistema pronto!");
}

void loop() {
  if (audioReady) {
    audioReady = false;
    sendAudioData();
  }
  
  // Verificar WiFi
  if (WiFi.status() != WL_CONNECTED) {
    WiFi.begin(ssid, password);
  }
  
  delay(1);
}

void onPDMdata() {
  int bytesAvailable = PDM.available();
  if (bytesAvailable > 0) {
    PDM.read(audioBuffer, bytesAvailable);
    samplesRead = bytesAvailable / 2;
    audioReady = true;
  }
}

void sendAudioData() {
  AudioPacket header;
  header.magic = 0xABCD1234;
  header.timestamp = millis();
  header.device_id = DEVICE_ID;
  header.samples_count = samplesRead;
  header.sequence = sequenceNumber++;
  
  udp.beginPacket(host_ip, host_port);
  udp.write((uint8_t*)&header, sizeof(header));
  udp.write((uint8_t*)audioBuffer, samplesRead * 2);
  udp.endPacket();
  
  // Debug ocasional
  static unsigned long lastDebug = 0;
  if (millis() - lastDebug > 5000) {
    Serial.print("Enviando: ");
    Serial.print(samplesRead);
    Serial.print(" samples, seq: ");
    Serial.println(sequenceNumber);
    lastDebug = millis();
  }
}