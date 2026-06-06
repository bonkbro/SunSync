import os
import subprocess
import sys
import json
from typing import Any, List

def handle_interrupt():
    print("\nScript interrupted by user. Exiting...")
    sys.exit(0)

def _clean_env() -> dict:
    env = os.environ.copy()
    venv = env.get('VIRTUAL_ENV')
    if venv:
        venv_bin = os.path.join(venv, 'bin')
        env['PATH'] = ':'.join(p for p in env.get('PATH', '').split(':') if p != venv_bin)
    return env

def run_command(cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_clean_env())

def parse_json_output(result: subprocess.CompletedProcess) -> Any:
    if result.returncode != 0:
        print(f"Error executing command: {result.stderr.decode()}")
        return None
    try:
        return json.loads(result.stdout.decode())
    except json.JSONDecodeError:
        print("Error parsing JSON output.")
        return None

def parse_bottles_output(result: subprocess.CompletedProcess) -> List[str]:
    if result.returncode != 0:
        print(f"Error executing Bottles command: {result.stderr.decode()}")
        return []
    lines = result.stdout.decode().split('\n')
    return [line.strip('- ') for line in lines if line.startswith('-')]

def parse_bottles_programs(result: subprocess.CompletedProcess) -> List[str]:
    if result.returncode != 0:
        print(f"Error executing Bottles command: {result.stderr.decode()}")
        return []
    lines = result.stdout.decode().split('\n')
    # Skip the "Found X programs:" line, empty lines, and remove leading "- "
    return [line.strip("- ").strip() for line in lines if line.strip() and not line.startswith("Found")]
