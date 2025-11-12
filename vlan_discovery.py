#!/usr/bin/env python3
"""
Descoberta de VLANs via SNMP (usando snmpwalk do sistema)
Integração opcional com phpIPAM
Configurações em config.yaml
"""

import os
import re
import sys
import json
import yaml
import time
import logging
import subprocess
from datetime import datetime
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# -----------------------------------------------------------------------------
# LOGGING
# -----------------------------------------------------------------------------
LOG_DIR = "/var/log/netmap_vlans"
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "vlan_discovery.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),               # vai pro systemd/journal
        logging.FileHandler(LOG_FILE, encoding="utf-8")  # vai pra /var/log/netmap_vlans/vlan_discovery.log
    ],
)

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# FUNÇÕES BÁSICAS
# -----------------------------------------------------------------------------
def load_config(path="config.yaml"):
    if not os.path.exists(path):
        logger.error(f"Arquivo de configuração não encontrado: {path}")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# -----------------------------------------------------------------------------
# SNMP (USANDO COMANDO LINUX)
# -----------------------------------------------------------------------------
def buscar_vlans(ip, vendor, community, vendors_conf):
    """Executa snmpwalk para coletar VLANs"""
    if vendor not in vendors_conf:
        logger.error(f"Fabricante '{vendor}' não configurado no YAML.")
        return []

    vendor_cfg = vendors_conf[vendor]
    oid = vendor_cfg["oid"]
    pattern = re.compile(vendor_cfg["pattern"])
    skip = vendor_cfg.get("skip", [])

    cmd = ["snmpwalk", "-v2c", "-c", community, ip, oid]
    logger.info(f"Executando: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception as e:
        logger.error(f"Erro ao executar snmpwalk: {e}")
        return []

    if result.returncode != 0:
        logger.warning(f"snmpwalk retornou erro: {result.stderr.strip()}")
        return []

    vlans = []
    for line in result.stdout.splitlines():
        match = pattern.search(line)
        if match:
            try:
                if len(match.groups()) == 2:
                    vlan_id = int(match.group(1))
                    name = match.group(2)
                else:
                    vlan_id = int(match.group(1))
                    name = f"VLAN{vlan_id}"
                if name not in skip:
                    vlans.append({"vlan_id": vlan_id, "name": name})
            except Exception:
                continue

    vlans.sort(key=lambda x: x["vlan_id"])
    logger.info(f"{ip}: {len(vlans)} VLANs encontradas")
    return vlans


# -----------------------------------------------------------------------------
# PHPIPAM CLIENT
# -----------------------------------------------------------------------------
class PhpipamClient:
    def __init__(self, url, app_id, token, verify_ssl=False):
        self.base = f"{url.rstrip('/')}/{app_id}"
        self.h = {"token": token, "Content-Type": "application/json"}
        self.verify = verify_ssl

    def _req(self, method, endpoint, data=None):
        url = f"{self.base}/{endpoint.lstrip('/')}"
        try:
            r = requests.request(method, url, headers=self.h, json=data, verify=self.verify, timeout=10)
            if r.status_code in (200, 201, 204, 409):
                return r.json().get("data", None) if r.text else {}
            logger.warning(f"{method} {url} → {r.status_code}")
        except Exception as e:
            logger.error(f"Erro {method} {url}: {e}")
        return None

    def get(self, e): return self._req("GET", e)
    def post(self, e, d): return self._req("POST", e, d)
    def patch(self, e, d): return self._req("PATCH", e, d)
    def delete(self, e): return self._req("DELETE", e)

    def ensure_domain(self, name):
        domains = self.get("l2domains/") or []
        for d in domains:
            if d.get("name") == name:
                return d["id"]
        self.post("l2domains/", {"name": name, "description": f"VLANs {name}"})
        domains = self.get("l2domains/") or []
        for d in domains:
            if d.get("name") == name:
                return d["id"]
        return None

    def get_vlans(self, domain_id):
        data = self.get(f"l2domains/{domain_id}/vlans/") or []
        return {int(v["number"]): {"id": v["vlanId"], "name": v["name"]} for v in data if "number" in v}

    def create_vlan(self, d_id, vid, name, desc):
        return self.post("vlan/", {"number": vid, "name": name, "description": desc, "domainId": d_id})

    def update_vlan(self, vid, name, desc):
        return self.patch(f"vlan/{vid}/", {"name": name, "description": desc})

    def delete_vlan(self, vid):
        return self.delete(f"vlan/{vid}/")


# -----------------------------------------------------------------------------
# FUNÇÕES DE INTEGRAÇÃO
# -----------------------------------------------------------------------------
def sincronizar_phpipam(client, device_name, vlans):
    domain_id = client.ensure_domain(device_name)
    if not domain_id:
        logger.error(f"Erro ao criar domain {device_name}")
        return

    existentes = client.get_vlans(domain_id)
    novos = {v["vlan_id"] for v in vlans}

    criadas = atualizadas = removidas = ja_existem = 0

    for v in vlans:
        vid, nome = v["vlan_id"], v["name"]
        if vid in existentes:
            if existentes[vid]["name"] != nome:
                client.update_vlan(existentes[vid]["id"], nome, f"SNMP - {device_name}")
                atualizadas += 1
                logger.info(f"↻ VLAN {vid} - {nome}")
            else:
                ja_existem += 1
        else:
            client.create_vlan(domain_id, vid, nome, f"SNMP - {device_name}")
            criadas += 1
            logger.info(f"✓ VLAN {vid} - {nome}")

    for vid, info in existentes.items():
        if vid not in novos:
            client.delete_vlan(info["id"])
            removidas += 1
            logger.info(f"✗ VLAN {vid} - {info['name']} (removida)")

    logger.info(
        f"{device_name}: Criadas={criadas} Atualizadas={atualizadas} Removidas={removidas} Já existem={ja_existem}"
    )


def salvar_backup(nome, vlans):
    os.makedirs("backups", exist_ok=True)
    arq = f"backups/{nome}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    with open(arq, "w", encoding="utf-8") as f:
        json.dump(vlans, f, indent=2, ensure_ascii=False)
    logger.info(f"Backup salvo: {arq}")


# -----------------------------------------------------------------------------
# EXECUÇÃO
# -----------------------------------------------------------------------------
def executar(config, usar_phpipam=False, backup=False):
    snmp = config["snmp"]
    php_conf = config.get("phpipam", {})
    vendors = config["vendors"]

    php_client = None
    if usar_phpipam:
        php_client = PhpipamClient(
            php_conf["url"], php_conf["app_id"], php_conf["app_code"], php_conf.get("verify_ssl", False)
        )

    for dev in config["devices"]:
        if not dev.get("enabled", True):
            continue
        vlans = buscar_vlans(dev["ip"], dev["vendor"], snmp["community"], vendors)
        if not vlans:
            continue

        logger.info(f"VLANs encontradas em {dev['name']}:")
        for v in vlans:
            logger.info(f"  {v['vlan_id']:4d} - {v['name']}")

        if backup:
            salvar_backup(dev["name"], vlans)
        if usar_phpipam and php_client:
            sincronizar_phpipam(php_client, dev["name"], vlans)


def main():
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--phpipam", action="store_true", help="Enviar VLANs ao phpIPAM")
    p.add_argument("--backup", action="store_true", help="Salvar backup JSON das VLANs")
    p.add_argument("--loop", type=int, default=0, help="Intervalo (segundos) para rodar continuamente")
    args = p.parse_args()

    config = load_config()
    if args.loop <= 0:
        executar(config, args.phpipam, args.backup)
    else:
        logger.info(f"Rodando em loop a cada {args.loop}s...")
        while True:
            executar(config, args.phpipam, args.backup)
            time.sleep(args.loop)


if __name__ == "__main__":
    main()
