import socket
import h11

conn = h11.Connection(our_role=h11.CLIENT)
sock = socket.create_connection(("www.python.org", 80))

def send(event):
    # Pass the event to h11's state machine and encoding machinery
    data = conn.send(event)
    # Send the resulting bytes on the wire
    sock.sendall(data)

send(h11.Request(method="GET",
                 target="/",
                 headers=[("Host", "www.python.org")]))
send(h11.EndOfMessage())

event = None
while type(event) is not h11.EndOfMessage:
    # Fetch data from the socket
    data = sock.recv(2048)
    # Give it to h11 to convert back into events
    for event in conn.receive_data(data):
        print(event)

s.close()
