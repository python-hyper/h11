import socket
import h11

conn = h11.Connection(our_role=h11.CLIENT)
sock = socket.create_connection(("www.python.org", 80))

def send(event):
    # Pass the event through h11's state machine and encoding machinery
    data = conn.send(event)
    # Send the resulting bytes on the wire
    sock.sendall(data)

send(h11.Request(method="GET",
                 target="/",
                 headers=[("Host", "www.python.org"),
                          ("Connection", "close")]))
send(h11.EndOfMessage())

done = False
while not done:
    # Fetch data from the socket
    data = sock.recv(2048)
    # Give it to h11 to convert back into events
    for event in conn.receive_data(data):
        print(event)
        if type(event) is h11.EndOfMessage:
            done = True

sock.close()
