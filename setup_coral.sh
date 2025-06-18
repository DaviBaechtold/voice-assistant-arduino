#!/bin/bash
# setup_service.sh - Configurar serviço systemd para Voice Assistant

set -e

# Cores
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}=== Configuração do Coral Voice Assistant ===${NC}"

# Verificar root
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}Execute como root (sudo)${NC}"
    exit 1
fi

# Diretórios
INSTALL_DIR="/opt/voice-assistant"
SERVICE_FILE="/etc/systemd/system/voice-assistant.service"

# Criar diretório
echo -e "${YELLOW}Criando diretórios...${NC}"
mkdir -p $INSTALL_DIR
mkdir -p $INSTALL_DIR/recordings
mkdir -p $INSTALL_DIR/logs

# Copiar arquivos
echo -e "${YELLOW}Copiando arquivos...${NC}"
cp dev_board_optimized.py $INSTALL_DIR/
chmod +x $INSTALL_DIR/dev_board_optimized.py

# Criar serviço systemd
echo -e "${YELLOW}Criando serviço systemd...${NC}"
cat > $SERVICE_FILE << EOF
[Unit]
Description=Coral Voice Assistant
After=network.target sound.target

[Service]
Type=simple
User=mendel
Group=mendel
WorkingDirectory=$INSTALL_DIR
Environment="PATH=/home/mendel/vosk_env/bin:/usr/local/bin:/usr/bin:/bin"
Environment="PYTHONPATH=/home/mendel/vosk_env/lib/python3.7/site-packages"
ExecStartPre=/bin/sleep 10
ExecStart=/home/mendel/vosk_env/bin/python3 $INSTALL_DIR/dev_board_optimized.py --port 8888 --model /home/mendel/vosk-model-pt
Restart=always
RestartSec=10
StandardOutput=append:$INSTALL_DIR/logs/voice-assistant.log
StandardError=append:$INSTALL_DIR/logs/voice-assistant-error.log

# Limites de recursos
LimitNOFILE=4096
CPUQuota=80%
MemoryMax=512M

[Install]
WantedBy=multi-user.target
EOF

# Criar script de log rotation
echo -e "${YELLOW}Configurando rotação de logs...${NC}"
cat > /etc/logrotate.d/voice-assistant << EOF
$INSTALL_DIR/logs/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    create 0644 mendel mendel
    postrotate
        systemctl reload voice-assistant >/dev/null 2>&1 || true
    endscript
}
EOF

# Script de monitoramento
cat > $INSTALL_DIR/monitor.sh << 'EOF'
#!/bin/bash
# Monitor de saúde do serviço

check_service() {
    if systemctl is-active --quiet voice-assistant; then
        echo "✅ Serviço ativo"
    else
        echo "❌ Serviço inativo"
        systemctl status voice-assistant --no-pager | tail -5
    fi
}

check_resources() {
    echo -e "\n📊 Recursos:"
    
    # CPU
    CPU=$(top -bn1 | grep "Cpu(s)" | awk '{print $2}' | cut -d'%' -f1)
    echo "  CPU: ${CPU}%"
    
    # Memória
    MEM=$(free | grep Mem | awk '{print ($3/$2) * 100.0}')
    printf "  MEM: %.1f%%\n" $MEM
    
    # Temperatura
    if [ -f /sys/class/thermal/thermal_zone0/temp ]; then
        TEMP=$(cat /sys/class/thermal/thermal_zone0/temp)
        echo "  Temp: $((TEMP/1000))°C"
    fi
    
    # Processos do serviço
    PROCS=$(pgrep -f dev_board_optimized | wc -l)
    echo "  Processos: $PROCS"
}

check_logs() {
    echo -e "\n📄 Últimas mensagens:"
    tail -n 10 $INSTALL_DIR/logs/voice-assistant.log | grep -E "(ERROR|WARNING|🎯|💬)" || echo "  Sem erros recentes"
}

check_devices() {
    echo -e "\n📡 Dispositivos:"
    
    # Verificar se há atividade UDP
    PACKETS=$(timeout 2 tcpdump -i any -c 10 udp port 8888 2>/dev/null | wc -l)
    if [ $PACKETS -gt 0 ]; then
        echo "  ✅ Recebendo dados UDP"
    else
        echo "  ⚠️  Sem dados UDP"
    fi
}

# Menu
echo "🎙️  MONITOR - CORAL VOICE ASSISTANT"
echo "===================================="
check_service
check_resources
check_logs
check_devices
echo ""
EOF

chmod +x $INSTALL_DIR/monitor.sh

# Criar comando global
ln -sf $INSTALL_DIR/monitor.sh /usr/local/bin/voice-monitor

# Ajustar permissões
chown -R mendel:mendel $INSTALL_DIR

# Recarregar systemd
systemctl daemon-reload

echo -e "${GREEN}✅ Instalação concluída!${NC}"
echo ""
echo "Comandos disponíveis:"
echo "  sudo systemctl start voice-assistant    # Iniciar"
echo "  sudo systemctl stop voice-assistant     # Parar"
echo "  sudo systemctl status voice-assistant   # Status"
echo "  sudo systemctl enable voice-assistant   # Ativar no boot"
echo "  voice-monitor                           # Monitorar"
echo "  journalctl -u voice-assistant -f       # Logs em tempo real"
echo ""
echo -e "${YELLOW}Iniciar agora? (s/n)${NC}"
read -r response
if [[ "$response" =~ ^[Ss]$ ]]; then
    systemctl enable voice-assistant
    systemctl start voice-assistant
    sleep 3
    systemctl status voice-assistant --no-pager
fi