__all__ = ["ProtocolError", "LocalProtocolError", "RemoteProtocolError",
           "validate", "Sentinel", "bytesify"]

class ProtocolError(Exception):
    """Exception indicating a violation of the HTTP/1.1 protocol.

    This as an abstract base class, with two concrete base classes:
    :exc:`LocalProtocolError`, which indicates that you tried to do something
    that HTTP/1.1 says is illegal, and :exc:`RemoteProtocolError`, which
    indicates that the remote peer tried to do something that HTTP/1.1 says is
    illegal. See :ref:`error-handling` for details.

    In addition to the normal :exc:`Exception` features, it has one attribute:

    .. attribute:: error_status_hint

       This gives a suggestion as to what status code a server might use if
       this error occurred as part of a request.

       For a :exc:`RemoteProtocolError`, this is useful as a suggestion for
       how you might want to respond to a misbehaving peer, if you're
       implementing a server.

       For a :exc:`LocalProtocolError`, this can be taken as a suggestion for
       how your peer might have responded to *you* if h11 had allowed you to
       continue.

       The default is 400 Bad Request, a generic catch-all for protocol
       violations.

    """
    def __init__(self, msg, error_status_hint=400):
        if type(self) is ProtocolError:
            raise TypeError("tried to directly instantiate ProtocolError")
        Exception.__init__(self, msg)
        self.error_status_hint = error_status_hint


# Strategy: there are a number of public APIs where a LocalProtocolError can
# be raised (send(), all the different event constructors, ...), and only one
# public API where RemoteProtocolError can be raised
# (receive_data()). Therefore we always raise LocalProtocolError internally,
# and then receive_data will translate this into a RemoteProtocolError.
#
# Internally:
#   LocalProtocolError is the generic "ProtocolError".
# Externally:
#   LocalProtocolError is for local errors and RemoteProtocolError is for
#   remote errors.
class LocalProtocolError(ProtocolError):
    pass

class RemoteProtocolError(ProtocolError):
    pass

# Equivalent to python 3.4's regex.fullmatch(data)
def _fullmatch(regex, data): # version specific: Python < 3.4
    match = regex.match(data)
    if match and match.end() != len(data):
        match = None
    return match

def validate(regex, data, msg="malformed data"):
    match = _fullmatch(regex, data)
    if not match:
        raise LocalProtocolError(msg)
    return match.groupdict()

# Sentinel values
# Inherits identity-based comparison and hashing from object
class Sentinel(object):
    def __init__(self, name):
        self._name = name

    def __repr__(self):
        return self._name

# Used for methods, request targets, HTTP versions, header names, and header
# values. Accepts ascii-strings, or bytes/bytearray/memoryview/..., and always
# returns bytes.
def bytesify(s):
    if isinstance(s, str):
        s = s.encode("ascii")
    if isinstance(s, int):
        raise TypeError("expected bytes-like object, not int")
    return bytes(s)
