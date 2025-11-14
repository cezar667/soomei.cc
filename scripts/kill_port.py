#!/usr/bin/env python3
"""
kill_port.py
--------------
Mata todos os processos que estiverem escutando em uma porta TCP específica.

Uso:
    python scripts/kill_port.py 8000
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from typing import Iterable, Set


def _pids_from_netstat(port: int) -> Set[int]:
    """Extrai PIDs usando netstat (disponível tanto no Windows quanto em distros Unix)."""
    try:
        raw = subprocess.check_output(
            ["netstat", "-ano"] if os.name == "nt" else ["netstat", "-anp", "tcp"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return set()

    target = f":{port}"
    pids: Set[int] = set()
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("proto"):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        proto = parts[0].lower()
        if proto not in {"tcp", "tcp6"}:
            continue
        local_addr = parts[1]
        state = parts[3] if len(parts) >= 4 else ""
        pid_field = parts[-1]
        if state and state.upper() != "LISTENING":
            continue
        if not local_addr.endswith(target):
            continue
        try:
            pids.add(int(pid_field))
        except ValueError:
            continue
    return pids


def _pids_from_lsof(port: int) -> Set[int]:
    """Tenta usar lsof -nP -iTCP:PORT -sTCP:LISTEN para extrair PIDs (Unix)."""
    if os.name == "nt":
        return set()
    try:
        raw = subprocess.check_output(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return set()
    pids = set()
    for line in raw.splitlines():
        try:
            pids.add(int(line.strip()))
        except ValueError:
            continue
    return pids


def find_pids(port: int) -> Set[int]:
    """Combina todas as estratégias para encontrar processos na porta."""
    pids = _pids_from_lsof(port)
    pids.update(_pids_from_netstat(port))
    return pids


def kill_pids(pids: Iterable[int]) -> None:
    """Envia SIGTERM para cada PID encontrado; em caso de falha, tenta SIGKILL."""
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"[INFO] Enviado SIGTERM para PID {pid}")
        except PermissionError:
            print(f"[WARN] Sem permissão para matar PID {pid}")
        except ProcessLookupError:
            print(f"[INFO] PID {pid} já não existe")
        except Exception as exc:
            print(f"[WARN] Falha ao enviar SIGTERM para PID {pid}: {exc}")
            continue


def main() -> None:
    parser = argparse.ArgumentParser(description="Encerra processos escutando em uma porta TCP.")
    parser.add_argument("port", type=int, help="Porta a ser liberada (ex.: 8000)")
    args = parser.parse_args()

    if args.port <= 0 or args.port > 65535:
        parser.error("Informe uma porta entre 1 e 65535.")

    pids = find_pids(args.port)
    if not pids:
        print(f"Nenhum processo encontrado escutando na porta {args.port}.")
        return

    print(f"Finalizando {len(pids)} processo(s) na porta {args.port}: {', '.join(map(str, pids))}")
    kill_pids(pids)


if __name__ == "__main__":
    main()
