"""
Détection des containers Docker en fonctionnement sur les serveurs distants.

Deux méthodes, essayées dans l'ordre :

1. **docker CLI** — si l'utilisateur SSH est dans le groupe ``docker`` :
   ``docker ps`` + ``docker stats --no-stream``.

2. **Fallback /proc** — sans aucun droit supplémentaire :
   - processus groupés par ID container via le champ ``cgroup`` de ``ps``
   - mémoire depuis ``/sys/fs/cgroup/memory/docker/`` (cgroupv1) ou
     ``/sys/fs/cgroup/docker/`` (cgroupv2)
   - ports exposés depuis les processus ``docker-proxy``
"""
import json
import logging
import re
import subprocess

from server import _ssh_cmd

log = logging.getLogger(__name__)

_CGID_RE  = re.compile(r'/docker/([0-9a-f]{64})')
_HP_RE    = re.compile(r'-host-port (\d+)')
_CP_RE    = re.compile(r'-container-port (\d+)')
_PROTO_RE = re.compile(r'-proto (\w+)')
_HOST_RE  = re.compile(r'-host-ip ([\d.]+)')


def _fmt_bytes(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024**3:.1f} GB"
    if n >= 1024 ** 2:
        return f"{n / 1024**2:.0f} MB"
    return f"{n / 1024:.0f} KB"


def _detect_via_proc(server: dict) -> dict:
    """Détecte les containers Docker via ``/proc``, ``ps`` et ``cgroup``.

    Ne nécessite aucun accès au socket Docker ni de droits particuliers.
    Fournit : ID court (12 hex), processus principaux, CPU%, RAM (cgroup ou RSS),
    ports exposés sur l'hôte.

    :param server: Dictionnaire de configuration du serveur.
    :returns: Même structure que :func:`docker_status_server`,
              avec ``method="proc"`` et ``all_ports`` (liste globale).
    """
    name, ip = server["name"], server["ip"]

    # Un seul appel SSH — trois sections séparées par des marqueurs
    script = (
        "ps axo pid,pcpu,rss,cgroup,comm --no-headers 2>/dev/null ;"
        " echo '---PORTS---' ;"
        " ps axo args --no-headers 2>/dev/null | grep 'docker-proxy' | grep -v grep ;"
        " echo '---CGMEM---' ;"
        # cgroupv1
        " for d in /sys/fs/cgroup/memory/docker/*/; do"
        "   cid=$(basename \"$d\"); [ ${#cid} -ge 64 ] || continue;"
        "   echo \"${cid:0:12}:$(cat \"${d}memory.usage_in_bytes\" 2>/dev/null)"
        ":$(cat \"${d}memory.limit_in_bytes\" 2>/dev/null)\"; done 2>/dev/null ;"
        # cgroupv2
        " for d in /sys/fs/cgroup/docker/*/; do"
        "   cid=$(basename \"$d\"); [ ${#cid} -ge 64 ] || continue;"
        "   echo \"${cid:0:12}:$(cat \"${d}memory.current\" 2>/dev/null)"
        ":$(cat \"${d}memory.max\" 2>/dev/null)\"; done 2>/dev/null"
    )

    r = subprocess.run(_ssh_cmd(server, script), capture_output=True, text=True, timeout=20)
    out = r.stdout

    # Séparer les trois sections
    ps_sec = ports_sec = cgmem_sec = ""
    if "---PORTS---" in out:
        ps_sec, rest = out.split("---PORTS---", 1)
    else:
        ps_sec, rest = out, ""
    if "---CGMEM---" in rest:
        ports_sec, cgmem_sec = rest.split("---CGMEM---", 1)
    else:
        ports_sec = rest

    # 1. Grouper les processus par container ID (via cgroup)
    groups: dict[str, dict] = {}
    for line in ps_sec.splitlines():
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        _, _, cpu_s, rss_s, cgroup, comm = parts
        m = _CGID_RE.search(cgroup)
        if not m:
            continue
        sid = m.group(1)[:12]
        g = groups.setdefault(sid, {"cpu": 0.0, "rss": 0, "comms": set()})
        try:
            g["cpu"] += float(cpu_s)
            g["rss"] += int(rss_s)
        except ValueError:
            pass
        if comm not in ("pause", "containerd-shi"):
            g["comms"].add(comm)

    # 2. Ports exposés (docker-proxy) — IPv4 uniquement, dédupliqués
    seen: set[tuple] = set()
    ports: list[str] = []
    for line in ports_sec.splitlines():
        hip = _HOST_RE.search(line)
        if not hip or hip.group(1) != "0.0.0.0":
            continue
        hp = _HP_RE.search(line)
        cp = _CP_RE.search(line)
        pr = _PROTO_RE.search(line)
        if hp and cp:
            key = (hp.group(1), cp.group(1), pr.group(1) if pr else "tcp")
            if key not in seen:
                seen.add(key)
                ports.append(f"{key[0]}→{key[1]}/{key[2]}")

    # 3. Mémoire cgroup par container
    cg: dict[str, tuple[int, int]] = {}
    for line in cgmem_sec.splitlines():
        parts = line.split(":")
        if len(parts) < 2:
            continue
        sid = parts[0]
        try:
            used = int(parts[1])
            raw = parts[2].strip() if len(parts) > 2 else ""
            # cgroupv1 : valeur très grande = pas de limite ; cgroupv2 : "max"
            limit = 0 if raw in ("", "max") or int(raw) > 2 ** 60 else int(raw)
            cg[sid] = (used, limit)
        except (ValueError, IndexError):
            pass

    if not groups:
        log.info(f"[{name}] Aucun container détecté via /proc (cgroup non lisible ?)")
        return {"name": name, "ip": ip, "installed": None, "error": None, "containers": [], "method": "proc"}

    # 4. Construire la liste des containers
    containers = []
    for sid, g in sorted(groups.items(), key=lambda x: x[1]["cpu"], reverse=True):
        comms = sorted(g["comms"])
        if sid in cg:
            used, limit = cg[sid]
            mem_used  = _fmt_bytes(used)
            mem_limit = _fmt_bytes(limit) if limit > 0 else "—"
        else:
            mem_used  = _fmt_bytes(g["rss"] * 1024)  # RSS en kB → bytes
            mem_limit = "—"

        containers.append({
            "name":      sid,
            "image":     ", ".join(comms[:2]) or "?",
            "status":    "En cours",
            "ports":     "",
            "cpu_pct":   f"{g['cpu']:.1f}%",
            "mem_used":  mem_used,
            "mem_limit": mem_limit,
            "net_rx":    "—",
            "net_tx":    "—",
        })

    return {
        "name": name, "ip": ip,
        "installed": True, "error": None,
        "containers": containers,
        "all_ports": sorted(ports, key=lambda p: int(p.split("→")[0])),
        "method": "proc",
    }


def docker_status_server(server: dict) -> dict:
    """Récupère le statut des containers Docker sur un serveur distant.

    Essaie d'abord le **docker CLI** (``docker ps`` + ``docker stats``).
    Si le socket est inaccessible (utilisateur hors du groupe ``docker``),
    bascule automatiquement sur la détection **via /proc** — sans droits
    particuliers.

    :param server: Dictionnaire de configuration du serveur.
    :returns: Dictionnaire avec les clés :

              - ``name`` (str), ``ip`` (str)
              - ``installed`` (bool|None)
              - ``error`` (str|None)
              - ``method`` (str) : ``"docker"`` ou ``"proc"``
              - ``containers`` (list[dict]) :
                ``name``, ``image``, ``status``, ``ports``,
                ``cpu_pct``, ``mem_used``, ``mem_limit``, ``net_rx``, ``net_tx``
              - ``all_ports`` (list[str], méthode proc uniquement)
    """
    name = server["name"]
    ip = server["ip"]

    # 1. Vérifier que docker est installé
    r = subprocess.run(
        _ssh_cmd(server, "which docker >/dev/null 2>&1 && echo INSTALLED || echo NOT_INSTALLED"),
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0 or "NOT_INSTALLED" in r.stdout:
        # Docker CLI absent — tenter quand même /proc (dockerd peut tourner sans CLI accessible)
        log.debug(f"[{name}] docker CLI absent, tentative via /proc")
        return _detect_via_proc(server)

    # 2. docker ps — essai sans sudo, puis avec sudo si permission refusée
    docker_cmd = "docker"
    r_ps = subprocess.run(
        _ssh_cmd(server, f"{docker_cmd} ps --format '{{{{json .}}}}'"),
        capture_output=True, text=True, timeout=15,
    )
    if r_ps.returncode != 0 and "permission" in r_ps.stderr.lower():
        log.debug(f"[{name}] docker ps sans sudo refusé — essai sudo")
        docker_cmd = "sudo docker"
        r_ps = subprocess.run(
            _ssh_cmd(server, f"{docker_cmd} ps --format '{{{{json .}}}}'"),
            capture_output=True, text=True, timeout=15,
        )

    if r_ps.returncode != 0:
        log.debug(f"[{name}] docker ps échoué ({r_ps.stderr.strip()[:60]}) — fallback /proc")
        return _detect_via_proc(server)

    ps_entries = []
    for line in r_ps.stdout.strip().splitlines():
        if line.strip():
            try:
                ps_entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    if not ps_entries:
        log.info(f"[{name}] Docker installé — aucun container en cours")
        return {"name": name, "ip": ip, "installed": True, "error": None,
                "containers": [], "method": "docker"}

    # 3. docker stats --no-stream
    r_stats = subprocess.run(
        _ssh_cmd(server, f"{docker_cmd} stats --no-stream --format '{{{{json .}}}}'"),
        capture_output=True, text=True, timeout=30,
    )
    stats_by_name: dict[str, dict] = {}
    if r_stats.returncode == 0:
        for line in r_stats.stdout.strip().splitlines():
            if line.strip():
                try:
                    s = json.loads(line)
                    stats_by_name[s.get("Name", "")] = s
                except json.JSONDecodeError:
                    pass

    containers = []
    for c in ps_entries:
        cname = c.get("Names", "?")
        stats = stats_by_name.get(cname, {})

        mem_usage = stats.get("MemUsage", "—")
        mem_parts = mem_usage.split("/") if "/" in mem_usage else [mem_usage, "?"]

        net_io = stats.get("NetIO", "—")
        net_parts = net_io.split("/") if "/" in net_io else [net_io, "?"]

        containers.append({
            "name":      cname,
            "image":     c.get("Image", "?"),
            "status":    c.get("Status", "?"),
            "ports":     c.get("Ports", ""),
            "cpu_pct":   stats.get("CPUPerc", "—"),
            "mem_used":  mem_parts[0].strip(),
            "mem_limit": mem_parts[1].strip() if len(mem_parts) > 1 else "?",
            "net_rx":    net_parts[0].strip(),
            "net_tx":    net_parts[1].strip() if len(net_parts) > 1 else "?",
        })

    log.info(f"[{name}] {len(containers)} container(s) actif(s) (via docker CLI)")
    return {"name": name, "ip": ip, "installed": True, "error": None,
            "containers": containers, "method": "docker"}
