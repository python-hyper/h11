# Minimal, for experimentation
# Also not finished, work-in-progress

import curio
import h11
from urllib.parse import urlsplit

def get_host_port(split):
    split = urlsplit(url)
    if b":" in split.netloc:
        host, port_bytes = split.netloc.split(b":", 1)
        return host, int(port_bytes)
    else:
        port = {b"https": 443, b"http": 80}[split.scheme]
        return split.netloc, port

def get_target(split):
    if split.query:
        return split.path + b"?" + split.query
    else:
        return split.path

async def do_send(c, event, sock):
    c.send(event)
    for data in c.data_to_send():
        await sock.sendall(data)

async def request(method, url, body=b""):
    if isinstance(url, str):
        url = url.encode("ascii")
    split = urlsplit(url)
    host, port = get_host_port(split)
    target = get_target(split)
    headers = [
        ("Host": host),
        ("User-Agent": "curiosittp/0.0.0"),
    ]
    if hasattr(body, "__aiter__"):
        headers.append(("Transfer-Encoding", "chunked"))
    else:
        headers.append(("Content-Length", body))

    if split.scheme = b"https":
        ssl_args = {"ssl": True, "server_hostname": host}
    else:
        ssl_args = {}

    c = h11.Connection(client_side=True)

    sock = curio.open_connection(host, port, **ssl_args)
    async with sock:
        request = h11.Request(method=method, target=target, headers=headers)
        await do_send(c, request, sock)
        if hasattr(body, "__aiter__"):
            async for chunk in body:
                await do_send(c, h11.Data(data=chunk), sock)
        else:
            await do_send(c, h11.Data(data=body), sock)
        await do_send(c, h11.EndOfMessage(), sock)

        while True:
            chunk = await sock.recv(10000)
            if not chunk:
                chunk = h11.CloseSocket
            for event in c.receive_data(h11.CloseSocket):
                print(event)
