// Arduino Nano 33 BLE Sense - Audio com VAD (Voice Activity Detection)
// Base comum para motorista e passageiro

#include <WiFiNINA.h>
#include <WiFiUdp.h>
#include <PDM.h>

// === CONFIGURAÇÃO ESPECÍFICA DO DISPOSITIVO ===
// ALTERE APENAS ESTAS LINHAS:
const uint16_t DEVICE_ID = 1;      // 1=Motorista, 2=Passageiro
const int LOCAL_PORT = 2390;       // 2390=Motorista, 2391=Passageiro
// =============================================

// WiFi
const char* ssid = "FRITZ!Box 7590 SU_EXT";
const char* password = "00747139424723748140";
const char* host_ip = "192.168.178.169";
const int host_port = 8888;

// Áudio
const int SAMPLE_RATE = 16000;
const int BUFFER_SIZE = 512;
const int PACKET_SIZE = 480;  // Múltiplo de 2 para samples

// VAD (Voice Activity Detection)
const int ENERGY_THRESHOLD = 800;
const int SILENCE_FRAMES = 10;
const int MIN_VOICE_FRAMES = 3;

// Buffers
short audioBuffer[BUFFER_SIZE];
short sendBuffer[PACKET_SIZE/2];
volatile int samplesRead = 0;
volatile bool audioReady = false;

// Estado VAD
int silenceCounter = 0;
int voiceCounter = 0;
bool isTransmitting = false;
uint32_t lastActivityTime = 0;

// Sequência de pacotes
uint32_t packetSequence = 0;

// WiFi/UDP
WiFiUDP udp;

// Estrutura do pacote
struct __attribute__((packed)) AudioPacket {
    uint32_t sequence;      // Número sequencial
    uint32_t timestamp;     // Timestamp
    uint16_t device_id;     // ID do dispositivo
    uint16_t sample_rate;   // Taxa de amostragem
    uint16_t samples_count; // Número de samples
    uint16_t checksum;      // CRC16
    uint8_t flags;          // Flags (bit 0: início, bit 1: fim)
    uint8_t reserved;       // Reservado
};

void setup() {
    Serial.begin(115200);
    while (!Serial && millis() < 3000);
    
    const char* deviceName = (DEVICE_ID == 1) ? "MOTORISTA" : "PASSAGEIRO";
    Serial.println("=== Arduino Audio com VAD ===");
    Serial.print("Dispositivo: ");
    Serial.println(deviceName);
    Serial.print("ID: ");
    Serial.println(DEVICE_ID);
    
    // WiFi
    if (WiFi.status() == WL_NO_MODULE) {
        Serial.println("ERRO: Módulo WiFi não encontrado!");
        while (1);
    }
    
    connectWiFi();
    
    // UDP
    udp.begin(LOCAL_PORT);
    Serial.print("UDP porta: ");
    Serial.println(LOCAL_PORT);
    
    // PDM
    PDM.onReceive(onPDMdata);
    PDM.setBufferSize(BUFFER_SIZE);
    
    if (!PDM.begin(1, SAMPLE_RATE)) {
        Serial.println("ERRO: Falha ao inicializar PDM!");
        while (1);
    }
    
    Serial.println("Sistema pronto!");
    Serial.print("VAD Threshold: ");
    Serial.println(ENERGY_THRESHOLD);
}

void loop() {
    // Reconectar WiFi se necessário
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("WiFi perdido, reconectando...");
        connectWiFi();
    }
    
    // Processar áudio
    if (audioReady) {
        audioReady = false;
        processAudioWithVAD();
    }
    
    // Status periódico
    static unsigned long lastStatus = 0;
    if (millis() - lastStatus > 10000) {
        printStatus();
        lastStatus = millis();
    }
    
    delay(1);
}

void connectWiFi() {
    WiFi.begin(ssid, password);
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
    }
    Serial.println("\nWiFi conectado!");
    Serial.print("IP: ");
    Serial.println(WiFi.localIP());
}

void onPDMdata() {
    int bytesAvailable = PDM.available();
    if (bytesAvailable > 0) {
        PDM.read(audioBuffer, bytesAvailable);
        samplesRead = bytesAvailable / 2;
        audioReady = true;
    }
}

void processAudioWithVAD() {
    // Calcular energia do sinal
    uint32_t energy = 0;
    for (int i = 0; i < samplesRead; i++) {
        energy += abs(audioBuffer[i]);
    }
    energy /= samplesRead;
    
    // VAD logic
    bool voiceDetected = (energy > ENERGY_THRESHOLD);
    
    if (voiceDetected) {
        voiceCounter++;
        silenceCounter = 0;
        lastActivityTime = millis();
        
        // Começar transmissão após detecção consistente
        if (!isTransmitting && voiceCounter >= MIN_VOICE_FRAMES) {
            isTransmitting = true;
            packetSequence = 0;
            Serial.println("🎤 VOZ DETECTADA - Iniciando transmissão");
        }
    } else {
        silenceCounter++;
        voiceCounter = 0;
        
        // Parar transmissão após silêncio prolongado
        if (isTransmitting && silenceCounter >= SILENCE_FRAMES) {
            // Enviar pacote final
            sendAudioData(true);
            isTransmitting = false;
            Serial.println("🔇 SILÊNCIO - Transmissão finalizada");
        }
    }
    
    // Transmitir apenas se voz ativa
    if (isTransmitting) {
        // Copiar para buffer de envio
        int samplesToSend = min(samplesRead, PACKET_SIZE/2);
        memcpy(sendBuffer, audioBuffer, samplesToSend * 2);
        
        sendAudioData(false);
        
        // Debug
        static unsigned long lastDebug = 0;
        if (millis() - lastDebug > 1000) {
            Serial.print("📡 Transmitindo - Energia: ");
            Serial.println(energy);
            lastDebug = millis();
        }
    }
}

void sendAudioData(bool isFinal) {
    AudioPacket header;
    
    header.sequence = packetSequence++;
    header.timestamp = millis();
    header.device_id = DEVICE_ID;
    header.sample_rate = SAMPLE_RATE;
    header.samples_count = min(samplesRead, PACKET_SIZE/2);
    header.checksum = calculateCRC16((uint8_t*)sendBuffer, header.samples_count * 2);
    header.flags = 0;
    
    if (packetSequence == 1) header.flags |= 0x01;  // Início
    if (isFinal) header.flags |= 0x02;              // Fim
    
    header.reserved = 0;
    
    // Enviar pacote
    udp.beginPacket(host_ip, host_port);
    udp.write((uint8_t*)&header, sizeof(header));
    udp.write((uint8_t*)sendBuffer, header.samples_count * 2);
    
    if (udp.endPacket() == 0) {
        Serial.println("ERRO: Falha UDP");
    }
}

uint16_t calculateCRC16(uint8_t* data, size_t length) {
    uint16_t crc = 0xFFFF;
    
    for (size_t i = 0; i < length; i++) {
        crc ^= data[i];
        for (int j = 0; j < 8; j++) {
            if (crc & 0x0001) {
                crc = (crc >> 1) ^ 0xA001;
            } else {
                crc >>= 1;
            }
        }
    }
    
    return crc;
}

void printStatus() {
    const char* deviceName = (DEVICE_ID == 1) ? "MOTORISTA" : "PASSAGEIRO";
    Serial.print("\n📊 Status ");
    Serial.println(deviceName);
    Serial.print("WiFi RSSI: ");
    Serial.print(WiFi.RSSI());
    Serial.println(" dBm");
    Serial.print("Transmitindo: ");
    Serial.println(isTransmitting ? "SIM" : "NÃO");
    Serial.print("Última atividade: ");
    Serial.print((millis() - lastActivityTime) / 1000);
    Serial.println("s atrás\n");
}