import socket
import h11

c = h11.Connection(client_side=True)
s = socket.create_connection(("www.python.org", 80))

def send(event):
    # Pass the event to h11's state machine and encoding machinery
    c.send(event)
    # Get the resulting bytes and send them on the wire
    for data in c.data_to_send():
        s.sendall(data)

send(h11.Request(method="GET",
                 target="/",
                 headers=[("Host", "www.python.org")]))
send(h11.EndOfMessage())

done = False
while not done:
    # Fetch data from the socket
    data = s.recv(2048)
    # Give it to h11 to convert back into events
    for event in c.receive_data(data):
        print(event)
        if type(event) is h11.EndOfMessage:
            done = True

s.close()
