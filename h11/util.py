__all__ = ["ProtocolError", "validate", "Sentinel", "bytesify"]

# This indicates either that you tried to do something that HTTP/1.1 says is
# illegal, or that your peer did. Either way, you should probably close the
# connection and think things over.
class ProtocolError(Exception):
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
class Sentinel:
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
