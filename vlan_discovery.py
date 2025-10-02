#!/usr/bin/env python3
"""
Descoberta de VLANs via SNMP com integração PHPIpam
Autor: Carlos Eduardo Mendes Pereira
"""

import subprocess
import re
import requests
import json
import sys
import urllib3

# Desabilitar avisos SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configurações
SNMP_COMMUNITY = "automap_vlan"
PHPIPAM_URL = "https://192.168.10.100/phpipam/api"
PHPIPAM_APP_ID = "AutomapVLANs"
PHPIPAM_APP_CODE = "qWVFs6XH-p0VpYaSojjc4LYsq"

# Fabricantes suportados
VENDORS = {
    'cisco': {
        'oid': '1.3.6.1.4.1.9.9.46.1.3.1.1.4',
        'pattern': r'\.(\d+)\s*=\s*STRING:\s*"([^"]+)"',
        'skip': ['fddi-default', 'trcrf-default', 'fddinet-default', 'trbrf-default']
    },
    'huawei': {
        'oid': '1.3.6.1.4.1.2011.5.25.42.3.1.3.4.1.2',
        'pattern': r'\.(\d+)\s*=\s*STRING:\s*"([^"]+)"',
        'skip': []
    },
    'mikrotik': {
        'oid': '1.3.6.1.2.1.2.2.1.2',
        'pattern': r'STRING:\s*"vlan(\d+)"',
        'skip': []
    }
}

def buscar_vlans(ip, vendor):
    """Busca VLANs via SNMP"""
    if vendor not in VENDORS:
        print(f"Fabricante inválido. Use: {list(VENDORS.keys())}")
        return []
    
    config = VENDORS[vendor]
    print(f"Buscando VLANs em {ip}...")
    
    cmd = f"snmpwalk -v2c -c {SNMP_COMMUNITY} {ip} {config['oid']}"
    try:
        result = subprocess.run(cmd.split(), capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return []
    except:
        return []
    
    vlans = []
    for linha in result.stdout.split('\n'):
        match = re.search(config['pattern'], linha)
        if match:
            vlan_id = int(match.group(1))
            vlan_nome = match.group(2) if len(match.groups()) > 1 else f"VLAN{vlan_id}"
            if vlan_nome not in config['skip']:
                vlans.append({'vlan_id': vlan_id, 'name': vlan_nome})
    
    vlans.sort(key=lambda x: x['vlan_id'])
    print(f"Encontradas {len(vlans)} VLANs\n")
    return vlans

def api_get(endpoint):
    """Requisição GET na API"""
    headers = {"token": PHPIPAM_APP_CODE}
    url = f"{PHPIPAM_URL}/{PHPIPAM_APP_ID}/{endpoint}"
    try:
        r = requests.get(url, headers=headers, timeout=10, verify=False)
        if r.status_code == 200:
            return r.json().get('data', [])
    except:
        pass
    return []

def api_post(endpoint, data):
    """Requisição POST na API"""
    headers = {"token": PHPIPAM_APP_CODE, "Content-Type": "application/json"}
    url = f"{PHPIPAM_URL}/{PHPIPAM_APP_ID}/{endpoint}"
    try:
        r = requests.post(url, headers=headers, json=data, timeout=10, verify=False)
        return r.status_code in [200, 201, 409]
    except:
        return False

def api_patch(endpoint, data):
    """Requisição PATCH na API"""
    headers = {"token": PHPIPAM_APP_CODE, "Content-Type": "application/json"}
    url = f"{PHPIPAM_URL}/{PHPIPAM_APP_ID}/{endpoint}"
    try:
        r = requests.patch(url, headers=headers, json=data, timeout=10, verify=False)
        return r.status_code in [200, 201]
    except:
        return False

def api_delete(endpoint):
    """Requisição DELETE na API"""
    headers = {"token": PHPIPAM_APP_CODE}
    url = f"{PHPIPAM_URL}/{PHPIPAM_APP_ID}/{endpoint}"
    try:
        r = requests.delete(url, headers=headers, timeout=10, verify=False)
        return r.status_code in [200, 204]
    except:
        return False

def buscar_domain(nome):
    """Busca ou cria domain"""
    domains = api_get("l2domains/")
    for d in domains:
        if d.get('name') == nome:
            return d['id']
    
    # Criar novo domain
    if api_post("l2domains/", {"name": nome, "description": f"VLANs {nome}"}):
        domains = api_get("l2domains/")
        for d in domains:
            if d.get('name') == nome:
                return d['id']
    return None

def buscar_vlans_existentes(domain_id):
    """Carrega VLANs existentes"""
    vlans_dict = {}
    vlans = api_get(f"l2domains/{domain_id}/vlans/")
    for v in vlans:
        vlans_dict[int(v.get('number', 0))] = {
            'id': v.get('vlanId'),
            'name': v.get('name')
        }
    return vlans_dict

def enviar_phpipam(vlans, nome_switch):
    """Envia VLANs para PHPIpam"""
    domain_id = buscar_domain(nome_switch)
    if not domain_id:
        print("Erro ao buscar/criar domain")
        return
    
    vlans_existentes = buscar_vlans_existentes(domain_id)
    vlans_snmp = {v['vlan_id'] for v in vlans}
    
    criadas = atualizadas = removidas = ja_existem = 0
    
    # Processar VLANs do SNMP
    for vlan in vlans:
        vid = vlan['vlan_id']
        nome = vlan['name']
        
        if vid in vlans_existentes:
            if vlans_existentes[vid]['name'] != nome:
                if api_patch(f"vlan/{vlans_existentes[vid]['id']}/", 
                           {"name": nome, "description": f"SNMP - {nome_switch}"}):
                    print(f"↻ VLAN {vid} - {nome}")
                    atualizadas += 1
            else:
                ja_existem += 1
        else:
            if api_post("vlan/", {
                "number": vid,
                "name": nome,
                "description": f"SNMP - {nome_switch}",
                "domainId": domain_id
            }):
                print(f"✓ VLAN {vid} - {nome}")
                criadas += 1
    
    # Remover VLANs que não existem mais no switch
    for vid, vlan_info in vlans_existentes.items():
        if vid not in vlans_snmp:
            if api_delete(f"vlan/{vlan_info['id']}/"):
                print(f"✗ VLAN {vid} - {vlan_info['name']} (removida)")
                removidas += 1
    
    print(f"\nCriadas: {criadas} | Atualizadas: {atualizadas} | Removidas: {removidas} | Já existem: {ja_existem}")

def salvar_json(vlans):
    """Salva VLANs em JSON"""
    with open("vlans.json", 'w') as f:
        json.dump(vlans, f, indent=2)
    print("Salvo em vlans.json")

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Uso: python3 vlan_discovery.py <IP> <FABRICANTE> <NOME_SWITCH> [--json] [--phpipam]")
        print("Fabricantes: cisco, huawei, mikrotik")
        sys.exit(1)
    
    ip, vendor, switch = sys.argv[1], sys.argv[2], sys.argv[3]
    
    vlans = buscar_vlans(ip, vendor)
    if not vlans:
        print("Nenhuma VLAN encontrada")
        sys.exit(1)
    
    print("VLANs encontradas:")
    for v in vlans:
        print(f"  {v['vlan_id']:4d} - {v['name']}")
    
    if '--json' in sys.argv:
        salvar_json(vlans)
    
    if '--phpipam' in sys.argv:
        enviar_phpipam(vlans, switch)