import socket
import ssl
import h11

conn = h11.Connection(our_role=h11.CLIENT)
ctx = ssl.create_default_context()
sock = ctx.wrap_socket(socket.create_connection(("httpbin.org", 443)),
                       server_hostname="httpbin.org")

def send(event):
    print("Sending event:")
    print(event)
    print()
    # Pass the event through h11's state machine and encoding machinery
    data = conn.send(event)
    # Send the resulting bytes on the wire
    sock.sendall(data)

send(h11.Request(method="GET",
                 target="/get",
                 headers=[("Host", "httpbin.org"),
                          ("Connection", "close")]))
send(h11.EndOfMessage())

done = False
while not done:
    # Fetch data from the socket
    data = sock.recv(2048)
    # Give it to h11 to convert back into events
    for event in conn.receive_data(data):
        print("Received event:")
        print(event)
        print()
        if type(event) is h11.EndOfMessage:
            done = True

sock.close()
