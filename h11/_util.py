__all__ = ["ProtocolError", "validate", "Sentinel", "bytesify"]

# This indicates either that you tried to do something that HTTP/1.1 says is
# illegal, or that your peer did. Either way, you should probably close the
# connection and think things over.
class ProtocolError(Exception):
    """This exception indicates a violation of the HTTP/1.1 protocol.

    This might be because your perr tried to do something that HTTP/1.1 says
    is illegal (if it's raised by :meth:`Connection.receive_data`), or that
    you did. Either way, you should probably close the connection and think
    things over.

    In addition to the normal Exception features, it has one attribute:

    .. attribute:: error_status_hint

       If you're a server and you want to send an error response back to a
       naughty client, then this gives a suggestion as to which status code
       you might want to use. The default is 400 Bad Request, a generic
       catch-all for protocol violations.
    """
    def __init__(self, msg, error_status_hint=400):
        Exception.__init__(self, msg)
        self.error_status_hint = error_status_hint

def validate(regex, data, msg="malformed data"):
    match = regex.match(data)
    if not match:
        raise ProtocolError(msg)
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
