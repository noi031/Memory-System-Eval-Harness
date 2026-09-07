"""Read container limits through the Docker CLI or local Unix socket."""

import http.client
import json
import shutil
import socket
import subprocess
from urllib.parse import quote


def inspect_container(name: str) -> dict:
    if not name:
        raise ValueError("A test container name is required")
    if shutil.which("docker"):
        return json.loads(subprocess.check_output(
            ["docker", "inspect", name], timeout=15, text=True
        ))[0]
    connection = http.client.HTTPConnection("localhost", timeout=15)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(15)
    try:
        sock.connect("/var/run/docker.sock")
        connection.sock = sock
        connection.request("GET", "/containers/" + quote(name, safe="") + "/json")
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(f"Docker inspect returned HTTP {response.status}")
        return json.loads(response.read())
    finally:
        connection.close()
        sock.close()
