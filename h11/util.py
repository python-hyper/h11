__all__ = ["bytesify"]

# Strategy: most of our error-checking code is used both to catch usage errors
# by whoever's using this library, and also to catch naughtiness on behalf of
# remote hosts. But it's useful to be able to distinguish these. So,
# internally we raise ProtocolError whenever something goes wrong. And then
# when processing data from the remote host, we catch these and convert them
# to RemoteProtocolError. So from the user point of view, ProtocolError -> you
# screwed up, RemoteProtocolError -> someone else screwed up, maybe you should
# send a 400 Invalid Request or something like that. (If possible. I mean,
# obviously not if you're a client, or the error was "connection closed
# unexpectedly" or whatever.)
class ProtocolError(Exception):
    def __init__(self, msg, error_status_hint=400):
        Exception.__init__(self, msg)
        self.error_status_hint = error_status_hint

class RemoteProtocolError(Exception):
    def __init__(self, base):
        Exception.__init__(*base.args)
        self.error_status_hint = base.error_status_hint

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
    return bytes(s)
