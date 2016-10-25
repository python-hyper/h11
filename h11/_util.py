import sys

__all__ = ["ProtocolError", "LocalProtocolError", "RemoteProtocolError",
           "validate", "Sentinel", "bytesify", "make_sentinel"]

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
    def _reraise_as_remote_protocol_error(self):
        # After catching a LocalProtocolError, use this method to re-raise it
        # as a RemoteProtocolError. This method must be called from inside an
        # except: block.
        #
        # An easy way to get an equivalent RemoteProtocolError is just to
        # modify 'self' in place.
        self.__class__ = RemoteProtocolError
        # But the re-raising is somewhat non-trivial -- you might think that
        # now that we've modified the in-flight exception object, that just
        # doing 'raise' to re-raise it would be enough. But it turns out that
        # this doesn't work, because Python tracks the exception type
        # (exc_info[0]) separately from the exception object (exc_info[1]),
        # and we only modified the latter. So we really do need to re-raise
        # the new type explicitly.
        if sys.version_info[0] >= 3:
            # On py3, the traceback is part of the exception object, so our
            # in-place modification preserved it and we can just re-raise:
            raise self
        else:
            # On py2, preserving the traceback requires 3-argument
            # raise... but on py3 this is a syntax error, so we have to hide
            # it inside an exec
            exec("raise RemoteProtocolError, self, sys.exc_info()[2]")

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

class Sentinel(object):
    """Sentinel type for constructed sentinel types to inherit from.

    The class inherits identity-based comparison and hashing from object.
    """

def make_sentinel(name):
    """Return a sentinel value of a newly constructed type.

    The constructed class is equivalent to the following:

    .. code-block:: python

        class <name>(Sentinel):
            def __repr__(self):
                return <name>
    """

    def __repr__(self):
        return name

    cls = type(name, (Sentinel,), dict(__repr__=__repr__))
    return cls()

# Used for methods, request targets, HTTP versions, header names, and header
# values. Accepts ascii-strings, or bytes/bytearray/memoryview/..., and always
# returns bytes.
def bytesify(s):
    # Fast-path:
    if type(s) is bytes:
        return s
    if isinstance(s, str):
        s = s.encode("ascii")
    if isinstance(s, int):
        raise TypeError("expected bytes-like object, not int")
    return bytes(s)
