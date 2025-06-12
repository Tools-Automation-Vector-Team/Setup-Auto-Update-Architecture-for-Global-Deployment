#!/usr/bin/env python3

import os
import sys
import json
import tempfile
import requests
from git import Repo

# ====== LOAD CONFIG FROM JSON ======
with open('auto_update_config.json') as f:
    CONFIG = json.load(f)

# ====== ZABBIX AUTH ======
def zabbix_login():
    payload = {
        "jsonrpc": "2.0",
        "method": "user.login",
        "params": {
            "username": CONFIG['zabbix']['user'],
            "password": CONFIG['zabbix']['password']
        },
        "id": 1
    }

    headers = {"Content-Type": "application/json"}
    res = requests.post(CONFIG['zabbix']['url'], json=payload, headers=headers)
    res.raise_for_status()
    result = res.json()

    if 'result' in result:
        print(f"[✓] Zabbix login successful. Auth token: {result['result']}")
        return result['result']
    else:
        print(f"[✗] Zabbix login failed: {result}")
        sys.exit(1)

# ====== IMPORT ZABBIX TEMPLATE ======
def import_zabbix_template(auth_token, template_path):
    ext = template_path.split('.')[-1].lower()
    format_map = {"xml": "xml", "json": "json", "yaml": "yaml", "yml": "yaml"}

    if ext not in format_map:
        print(f"[WARN] Unsupported template format: {template_path}")
        return

    with open(template_path, 'r', encoding='utf-8') as file:
        source = file.read()

    payload = {
        "jsonrpc": "2.0",
        "method": "configuration.import",
        "params": {
            "format": format_map[ext],
            "rules": {
                "templates": {"createMissing": True, "updateExisting": True},
                "items": {"createMissing": True, "updateExisting": True},
                "triggers": {"createMissing": True, "updateExisting": True},
                "discoveryRules": {"createMissing": True, "updateExisting": True},
                "graphs": {"createMissing": True, "updateExisting": True},
                "valueMaps": {"createMissing": True, "updateExisting": True},
                "httptests": {"createMissing": True, "updateExisting": True}
            },
            "source": source
        },
        "id": 2
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {auth_token}"
    }

    res = requests.post(CONFIG['zabbix']['url'], json=payload, headers=headers)
    try:
        response_json = res.json()
        if "error" in response_json:
            print(f"[Zabbix] Import failed for {os.path.basename(template_path)}: {response_json['error']['data']}")
        else:
            print(f"[Zabbix] Successfully imported {os.path.basename(template_path)}")
    except ValueError:
        print(f"[Zabbix] Invalid response format for {os.path.basename(template_path)}: {res.text}")

# ====== COPY FILE FROM SCRIPT REPO ======
def copy_external_script(src_path):
    dst_path = os.path.join(CONFIG['externalscript_path'], os.path.basename(src_path))
    os.system(f'cp {src_path} {dst_path}')
    if dst_path.endswith((".sh", ".py")):
        os.system(f'chmod +x {dst_path}')
        print(f"[Zabbix] Script copied and made executable: {dst_path}")
    else:
        print(f"[Zabbix] File copied: {dst_path}")

# ====== UPLOAD GRAFANA DASHBOARD ======
def upload_grafana_dashboard(json_path):
    with open(json_path, 'r') as file:
        dashboard_json = json.load(file)

    payload = {"dashboard": dashboard_json, "overwrite": True}
    headers = {
        "Authorization": f"Bearer {CONFIG['grafana']['api_key']}",
        "Content-Type": "application/json"
    }

    r = requests.post(f"{CONFIG['grafana']['url']}/api/dashboards/db", headers=headers, json=payload)
    print(f"[Grafana] Dashboard upload result for {os.path.basename(json_path)}: {r.status_code} {r.text}")

# ====== CLONE OR PULL GIT REPOS ======
def clone_or_pull(repo_url, local_dir):
    if os.path.exists(local_dir):
        repo = Repo(local_dir)
        repo.remotes.origin.pull()
    else:
        repo = Repo.clone_from(repo_url, local_dir)
    return local_dir

# ====== CREATE VENV AND INSTALL DEPENDENCIES ======
def setup_virtualenv():
    import subprocess

    venv_dir = os.path.join(CONFIG['externalscript_path'], 'venv')
    requirements_file = os.path.join(CONFIG['externalscript_path'], 'requirements.txt')

    try:
        import venv
    except ImportError:
        print("[✗] python3-venv is not installed. Please run: sudo apt install python3-venv")
        sys.exit(1)

    if not os.path.exists(venv_dir):
        print(f"[*] Creating virtual environment at {venv_dir}")
        subprocess.run(["python3", "-m", "venv", venv_dir], check=True)
    else:
        print(f"[✓] Virtual environment already exists at {venv_dir}")

    pip_path = os.path.join(venv_dir, "bin", "pip")
    subprocess.run([pip_path, "install", "--upgrade", "pip"], check=True)

    if os.path.exists(requirements_file):
        print(f"[*] Installing dependencies from {requirements_file}")
        subprocess.run([pip_path, "install", "-r", requirements_file], check=True)
    else:
        print(f"[✗] No requirements.txt found at {requirements_file}")

# ====== MAIN ======
def main():
    temp_dir = tempfile.mkdtemp()

    print("[*] Cloning GitHub repositories...")
    zbx_tpl_dir = clone_or_pull(CONFIG['git_repos']['zabbix_templates'], os.path.join(temp_dir, 'zbx_tpl'))
    zbx_scr_dir = clone_or_pull(CONFIG['git_repos']['zabbix_scripts'], os.path.join(temp_dir, 'zbx_scr'))
    graf_dir = clone_or_pull(CONFIG['git_repos']['grafana_dashboards'], os.path.join(temp_dir, 'graf_dash'))

    print("[*] Logging in to Zabbix...")
    auth_token = zabbix_login()

    print("[*] Importing Zabbix templates...")
    for f in os.listdir(zbx_tpl_dir):
        if f.lower().endswith(('.xml', '.json', '.yaml', '.yml')):
            import_zabbix_template(auth_token, os.path.join(zbx_tpl_dir, f))

    print("[*] Copying all files from Zabbix script repo...")
    for f in os.listdir(zbx_scr_dir):
        full_path = os.path.join(zbx_scr_dir, f)
        if os.path.isfile(full_path):
            copy_external_script(full_path)

    print("[*] Setting up virtual environment...")
    setup_virtualenv()

    print("[*] Uploading Grafana dashboards...")
    for f in os.listdir(graf_dir):
        if f.endswith(".json"):
            upload_grafana_dashboard(os.path.join(graf_dir, f))

    print("[✔] Auto-update completed successfully.")

if __name__ == "__main__":
    main()


