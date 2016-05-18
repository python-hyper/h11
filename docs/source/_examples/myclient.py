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

    # max_bytes_per_recv intentionally set low for pedagogical purposes
    def next_event(self, max_bytes_per_recv=200):
        while True:
            # If we already have a complete event buffered internally, just
            # return that. Otherwise, read some data, add it to the internal
            # buffer, and then try again.
            event = self.conn.next_event()
            if event is h11.NEED_DATA:
                self.conn.receive_data(self.sock.recv(max_bytes_per_recv))
                continue
            return event
