Getting started: Writing your own HTTP/1.1 client
=================================================

.. currentmodule:: h11

h11 can be used to implement both HTTP/1.1 clients and servers. To
give a flavor for how the API works, we'll demonstrate a small
client.


HTTP basics
-----------

An HTTP interaction always starts with a client sending a *request*,
optionally some *data* (e.g., a POST body); and then the server
responds with a *response* and optionally some *data* (e.g. the
requested document). Requests and responses have some data associated
with them: for requests, this is a method (e.g. ``GET``), a target
(e.g. ``/index.html``), and a collection of headers
(e.g. ``User-agent: demo-clent``). For responses, it's a status code
(e.g. 404 Not Found) and a collection of headers.

Of course, as far as the network is concerned, there's no such thing
as "requests" and "responses" -- there's just bytes being sent from
one computer to another. Let's see what this looks like, by fetching
https://httpbin.org/xml:

.. ipython:: python

   import ssl, socket

   ctx = ssl.create_default_context()
   sock = ctx.wrap_socket(socket.create_connection(("httpbin.org", 443)),
                          server_hostname="httpbin.org")

   # Send request
   sock.sendall(b"GET /xml HTTP/1.1\r\nhost: httpbin.org\r\n\r\n")
   # Read response
   response_data = sock.recv(1024)
   # Let's see what we got!
   print(response_data)

.. warning::

   If you try to reproduce these examples interactively, then you'll
   have the most luck if you paste them in all at once. Remember we're
   talking to a remote server here – if you type them in one at a
   time, and you're too slow, then the server might give up on waiting
   for you and close the connection. One way to recognize that this
   has happened is if ``response_data`` comes back as an empty string,
   or later on when we're working with h11 this might cause errors
   that mention ``ConnectionClosed``.

So that's, uh, very convenient and readable. It's a little more
understandable if we print the bytes as text:

.. ipython:: python

   print(response_data.decode("ascii"))

Here we can see the status code at the top (200, which is the code for
"OK"), followed by the headers, followed by the data (a silly little
XML document). But we can already see that working with bytes by hand
like this is really cumbersome. What we need to do is to move up to a
higher level of abstraction.

This is what h11 does. Instead of talking in bytes, it lets you talk
in high-level HTTP "events". To see what this means, let's repeat the
above exercise, but using h11. We start by making a TLS connection
like before, but now we'll also import :mod:`h11`, and create a
:class:`h11.Connection` object:

.. ipython:: python

   import ssl, socket
   import h11

   ctx = ssl.create_default_context()
   sock = ctx.wrap_socket(socket.create_connection(("httpbin.org", 443)),
                          server_hostname="httpbin.org")

   conn = h11.Connection(our_role=h11.CLIENT)

Next, to send an event to the server, there are three steps we have to
take. First, we create an object representing the event we want to
send -- in this case, a :class:`h11.Request`:

.. ipython:: python

   request = h11.Request(method="GET",
                         target="/xml",
                         headers=[("Host", "httpbin.org")])

Next, we pass this to our connection's :meth:`~Connection.send`
method, which gives us back the bytes corresponding to this message:

.. ipython:: python

   bytes_to_send = conn.send(request)

And then we send these bytes across the network:

.. ipython:: python

   sock.sendall(bytes_to_send)

There's nothing magical here -- these are the same bytes that we sent
up above:

.. ipython:: python

   bytes_to_send

Why doesn't h11 go ahead and send the bytes for you? Because it's
designed to be usable no matter what socket API you're using --
doesn't matter if it's synchronous like this, asynchronous,
callback-based, whatever; if you can read and write bytes from the
network, then you can use h11.

In this case, we're not quite done yet -- we have to send another
event to tell the other side that we're finished, which we do by
sending an :class:`EndOfMessage` event:

.. ipython:: python

   end_of_message_bytes_to_send = conn.send(h11.EndOfMessage())
   sock.sendall(end_of_message_bytes_to_send)

Of course, it turns out that in this case, the HTTP/1.1 specification
tells us that any request that doesn't contain either a
``Content-Length`` or ``Transfer-Encoding`` header automatically has a
0 length body, and h11 knows that, and h11 knows that the server knows
that, so it actually encoded the :class:`EndOfMessage` event as the
empty string:

.. ipython:: python

   end_of_message_bytes_to_send

But there are other cases where it might not, depending on what
headers are set, what message is being responded to, the HTTP version
of the remote peer, etc. etc. So for consistency, h11 requires that
you *always* finish your messages by sending an explicit
:class:`EndOfMessage` event; then it keeps track of the details of
what that actually means in any given situation, so that you don't
have to.

Finally, we have to read the server's reply. By now you can probably
guess how this is done, at least in the general outline: we read some
bytes from the network, then we hand them to the connection (using
:meth:`Connection.receive_data`) and it converts them into events
(using :meth:`Connection.next_event`).

.. ipython:: python

   bytes_received = sock.recv(1024)
   conn.receive_data(bytes_received)
   conn.next_event()
   conn.next_event()
   conn.next_event()

(Remember, if you're following along and get an error here mentioning
``ConnectionClosed``, then try again, but going through the steps
faster!)

Here the server sent us three events: a :class:`Response` object,
which is similar to the :class:`Request` object that we created
earlier and has the response's status code (200 OK) and headers; a
:class:`Data` object containing the response data; and another
:class:`EndOfMessage` object. This similarity between what we send and
what we receive isn't accidental: if we were using h11 to write an HTTP
server, then these are the objects we would have created and passed to
:meth:`~Connection.send` -- h11 in client and server mode has an API
that's almost exactly symmetric.

One thing we have to deal with, though, is that an entire response
doesn't always arrive in a single call to :meth:`socket.recv` --
sometimes the network will decide to trickle it in at its own pace, in
multiple pieces. Let's try that again:

.. ipython:: python

   import ssl, socket
   import h11

   ctx = ssl.create_default_context()
   sock = ctx.wrap_socket(socket.create_connection(("httpbin.org", 443)),
                          server_hostname="httpbin.org")

   conn = h11.Connection(our_role=h11.CLIENT)
   request = h11.Request(method="GET",
                         target="/xml",
                         headers=[("Host", "httpbin.org")])
   sock.sendall(conn.send(request))

and this time, we'll read in chunks of 200 bytes, to see how h11
handles it:

.. ipython:: python

   bytes_received = sock.recv(200)
   conn.receive_data(bytes_received)
   conn.next_event()

:data:`NEED_DATA` is a special value that indicates that we, well,
need more data. h11 has buffered the first chunk of data; let's read
some more:

.. ipython:: python

   bytes_received = sock.recv(200)
   conn.receive_data(bytes_received)
   conn.next_event()

Now it's managed to read a complete :class:`Request`.


A basic client object
---------------------

Now let's use what we've learned to wrap up our socket and
:class:`Connection` into a single object with some convenience
methods:

.. literalinclude:: _examples/myclient.py

.. ipython:: python
   :suppress:

    import sys
    with open(sys._h11_hack_docs_source_path + "/_examples/myclient.py") as f:
        exec(f.read())

And then we can send requests:

.. ipython:: python

   client = MyHttpClient("httpbin.org", 443)

   client.send(h11.Request(method="GET", target="/xml",
                           headers=[("Host", "httpbin.org")]))
   client.send(h11.EndOfMessage())

And read back the events:

.. ipython:: python

   client.next_event()
   client.next_event()

Note here that we received a :class:`Data` event that only has *part*
of the response body -- this is another consequence of our reading in
small chunks. h11 tries to buffer as little as it can, so it streams
out data as it arrives, which might mean that a message body might be
split up into multiple :class:`Data` events. (Of course, if you're the
one sending data, you can do the same thing: instead of buffering all
your data in one giant :class:`Data` event, you can send multiple
:class:`Data` events yourself to stream the data out incrementally;
just make sure that you set the appropriate ``Content-Length`` /
``Transfer-Encoding`` headers.) If we keep reading, we'll see more
:class:`Data` events, and then eventually the :class:`EndOfMessage`:

.. ipython:: python

   client.next_event()
   client.next_event()
   client.next_event()

Now we can see why :class:`EndOfMessage` is so important -- otherwise,
we can't tell when we've received the end of the data. And since
that's the end of this response, the server won't send us anything
more until we make another request -- if we try, then the socket read
will just hang forever, unless we set a timeout or interrupt it:

.. ipython:: python
   :okexcept:

   client.sock.settimeout(2)
   client.next_event()


Keep-alive
----------

For some servers, we'd have to stop here, because they require a new
connection for every request/response. But, this server is smarter
than that -- it supports `keep-alive
<https://en.wikipedia.org/wiki/HTTP_persistent_connection>`_, so we
can re-use this connection to send another request. There's a few ways
we can tell. First, if it didn't, then it would have closed the
connection already, and we would have gotten a
:class:`ConnectionClosed` event on our last call to
:meth:`~Connection.next_event`. We can also tell by checking h11's
internal idea of what state the two sides of the conversation are in:

.. ipython:: python

   client.conn.our_state, client.conn.their_state

If the server didn't support keep-alive, then these would be
:data:`MUST_CLOSE` and either :data:`MUST_CLOSE` or :data:`CLOSED`,
respectively (depending on whether we'd seen the socket actually close
yet). :data:`DONE` / :data:`DONE`, on the other hand, means that this
request/response cycle has totally finished, but the connection itself
is still viable, and we can start over and send a new request on this
same connection.

To do this, we tell h11 to get ready (this is needed as a safety
measure to make sure different requests/responses on the same
connection don't get accidentally mixed up):

.. ipython:: python

   client.conn.start_next_cycle()

This resets both sides back to their initial :data:`IDLE` state,
allowing us to send another :class:`Request`:

.. ipython:: python

   client.conn.our_state, client.conn.their_state

   client.send(h11.Request(method="GET", target="/get",
                           headers=[("Host", "httpbin.org")]))
   client.send(h11.EndOfMessage())
   client.next_event()


What's next?
------------

Here's some ideas of things you might try:

* Adapt the above examples to make a POST request. (Don't forget to
  set the ``Content-Length`` header -- but don't worry, if you do
  forget, then h11 will give you an error when you try to send data):

  .. code-block:: python

     client.send(h11.Request(method="POST", target="/post",
                             headers=[("Host", "httpbin.org"),
                                      ("Content-Length", "10")]))
     client.send(h11.Data(data=b"1234567890"))
     client.send(h11.EndOfMessage())

* Experiment with what happens if you try to violate the HTTP protocol
  by sending a :class:`Response` as a client, or sending two
  :class:`Request`\s in a row.

* Write your own basic ``http_get`` function that takes a URL, parses
  out the host/port/path, then connects to the server, does a ``GET``
  request, and then collects up all the resulting :class:`Data`
  objects, concatenates their payloads, and returns it.

* Adapt the above code to use your favorite non-blocking API

* Use h11 to write a simple HTTP server. (If you get stuck, `here's an
  example
  <https://github.com/python-hyper/h11/blob/master/examples/trio-server.py>`_.)

And of course, you'll want to read the :ref:`API-documentation` for
all the details.
