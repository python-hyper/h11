from _http_parser import HttpParser

# backpressure for pipelining?
# it sounds like pipelining is just a bad idea that will never be used or
# supported.
#   https://www.chromium.org/developers/design-documents/network-stack/http-pipelining

# basically what we want to do on the server side is make it an error to feed
# any data into receive_data if the connection is not in a state where it's
# receiving stuff.
# this is like the super simple version of http/2's flow control rules

# states we need to care about:
#   connection dead (closed, errored)
#   transfer-encoding
#   tracking switch from one request/response pair to another
#     which means tracking whether we've read all of the request body

# keep-alive:
#

class HttpConnection:
    def __init__(self, *, client_side):
        self._client_side = client_side
        if self._client_side:
            raise NotImplementedError(
                "only server side is implemented so far")
        mode = "response" if self._client_side else "request"
        # XX FIXME: need to do something about header_only handling for
        # client-side HEAD requests...
        self._parser = HttpParser(mode=mode)

    def receive_data(self, data):
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")

        # returns list of events


    def data_to_send(self, amt=None):
        XX

    # special headers:
    #   :status, :path, :method, :scheme, :authority
    # as a server you can call send_headers:
    #   0 or more times with :status 1XX
    #   once with any other :status header
    #   zero or one time for trailers
    # clients send one request block, and optionally one trailer block
    def send_headers(self, headers):
        # encode to UTF-8
        XX

    def send_data(self, data, end_body=False):
        XX

    def send_headers_and_data(self, headers, data):
        XX
