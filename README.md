# NETMAP_VLANs
Script to automate VLAN documentation in phpIPAM
NETMAP_VLANs – Descoberta Automática de VLANs via SNMP com Integração ao phpIPAM

O NETMAP_VLANs é uma ferramenta para descoberta automática de VLANs em equipamentos de rede utilizando SNMP, com suporte a múltiplos fabricantes e integração opcional ao phpIPAM.
Permite padronizar a documentação de VLANs de todo o ambiente, eliminando erros manuais e garantindo sincronização contínua.

Funcionalidades

Coleta VLANs via snmpwalk (MIB ou OID).

Compatível com:

Cisco (VTP MIB)

Mikrotik (IF-MIB::ifDescr)

VSOL / C-Data (Q-BRIDGE-MIB)

Huawei (IF-MIB / OIDs específicos)

Fácil expansão para novos vendors.

Integra automaticamente com phpIPAM (API REST).

Sincroniza:

Criação de VLANs

Atualização de nomes

Remoção de VLANs inexistentes

Gera backups diários das VLANs em JSON.

Pode rodar como serviço systemd.

Arquitetura Simplificada
[snmpwalk] → [vlan_discovery.py] → [parser YAML por vendor]
                                          ↓
                                     [lista VLANs]
                                          ↓
                                [phpIPAM API - opcional]
                                          ↓
                                 [json backup automático]

Requisitos

Linux (Debian recomendado)

Python 3.10+

Pacotes:

apt install snmp snmpd snmp-mibs-downloader -y
apt install python3-pip -y
pip install requests pyyaml

Configuração – Arquivo config-prod.yaml

Exemplo completo:

snmp:
  community: "public"
  port: 161
  timeout: 2
  retries: 1

phpipam:
  url: "https://10.0.0.10/phpipam/api"
  app_id: "AutomapVLANs"
  app_code: "TOKEN_AQUI"
  verify_ssl: false

vendors:
  cisco:
    mib: "CISCO-VTP-MIB::vtpVlanName"
    oid: "1.3.6.1.4.1.9.9.46.1.3.1.1.4"
    pattern: "\\.(\\d+)\\s*=\\s*STRING:\\s*\"([^\"]+)\""
    skip:
      - "default"

  mikrotik:
    mib: "IF-MIB::ifDescr"
    pattern: "STRING:\\s*vlan-?(\\d+)(?:-([^\\r\\n]+))?"
    skip: []

  vsol:
    mib: "Q-BRIDGE-MIB::dot1qVlanStaticName"
    pattern: "\\.(\\d+)\\s*=\\s*STRING:\\s*(.+)"
    skip:
      - "default"

devices:
  - name: "SW-CISCO-EXEMPLO"
    ip: "10.1.1.1"
    vendor: "cisco"
    enabled: true

  - name: "CCR-MK-EXEMPLO"
    ip: "10.2.2.2"
    vendor: "mikrotik"
    enabled: true

  - name: "OLT-VSOL-EXEMPLO"
    ip: "10.3.3.3"
    vendor: "vsol"
    enabled: true

Como Executar Manualmente
Sem phpIPAM:
python3 vlan_discovery.py

Com integração phpIPAM:
python3 vlan_discovery.py --phpipam

Com backup JSON:
python3 vlan_discovery.py --backup

Execução contínua a cada 24h:
python3 vlan_discovery.py --phpipam --backup --loop 86400

Instalando como Serviço Systemd
Criar /etc/systemd/system/netmap_vlans.service
[Unit]
Description=Descoberta diária de VLANs e sincronização com phpIPAM
After=network-online.target

[Service]
ExecStart=/usr/bin/python3 /root/NETMAP_VLANs/vlan_discovery.py --phpipam --backup --loop 86400
Restart=always

[Install]
WantedBy=multi-user.target

Ativar o serviço:
systemctl daemon-reload
systemctl enable --now netmap_vlans.service
systemctl status netmap_vlans.service

Backups

Os backups são salvos automaticamente:

backups/DEVICE-YYYYMMDD-HHMMSS.json

Logs

Registrados em:

/var/log/netmap_vlans/vlan_discovery.log

Adição de novos fabricantes

Adicionar no config-prod.yaml:

vendors:
  newvendor:
    mib: "MIB-NAME::objectName"
    pattern: "REGEX PARA EXTRAIR VLAN"
    skip: []


Sem necessidade de alterar o código principal.