import socket, ssl
import h11

class MyHttpClient:
    def __init__(self, host, port):
        self.sock = socket.create_connection((host, port))
        if port == 443:
            self.sock = ssl.wrap_socket(self.sock)
        self.conn = h11.Connection(our_role=h11.CLIENT)

    def send(self, *events):
        for event in events:
            data = self.conn.send(event)
            if data is None:
                # event was a ConnectionClosed(), meaning that we won't be
                # sending any more data:
                self.sock.shutdown(socket.SHUT_WR)
            else:
                self.sock.sendall(data)

    # max_bytes set intentionally small for pedagogical purposes
    def receive(self, max_bytes=200):
        return self.conn.receive_data(self.sock.recv(max_bytes))
