# High level events that make up HTTP/1.1 conversations. Loosely inspired by
# the corresponding events in hyper-h2:
#
#     http://python-hyper.org/h2/en/stable/api.html#events
#
# Don't subclass these. Stuff will break.

from . import headers
from .util import bytesify, ProtocolError

# Everything in __all__ gets re-exported as part of the h11 public API.
__all__ = [
    "Request",
    "InformationalResponse",
    "Response",
    "Data",
    "EndOfMessage",
    "ConnectionClosed",
    "Paused",
]


class _EventBundle:
    _fields = []
    _defaults = {}

    def __init__(self, **kwargs):
        allowed = set(self._fields)
        for kwarg in kwargs:
            if kwarg not in allowed:
                raise TypeError(
                    "unrecognized kwarg {} for {}"
                    .format(kwarg, self.__class__.__name__))
        required = allowed.difference(self._defaults)
        for field in required:
            if field not in kwargs:
                raise TypeError(
                    "missing required kwarg {} for {}"
                    .format(kwarg, self.__class__.__name__))
        self.__dict__.update(self._defaults)
        self.__dict__.update(kwargs)

        # Special handling for some fields

        if "headers" in self.__dict__:
            self.headers = headers.normalize_and_validate(self.headers)

        for field in ["method", "target", "http_version"]:
            if field in self.__dict__:
                self.__dict__[field] = bytesify(self.__dict__[field])

        if "status_code" in self.__dict__:
            if not isinstance(self.status_code, int):
                raise ProtocolError("status code must be integer")

        self._validate()

    def _validate(self):
        pass

    def __repr__(self):
        name = self.__class__.__name__
        kwarg_strs = ["{}={}".format(field, self.__dict__[field])
                      for field in self._fields]
        kwarg_str = ", ".join(kwarg_strs)
        return "{}({})".format(name, kwarg_str)

    # Useful for tests
    def __eq__(self, other):
        return (self.__class__ == other.__class__
                and self.__dict__ == other.__dict__)

    def __hash__(self):
        return hash(self.__class__) ^ hash(tuple(self.items()))

class Request(_EventBundle):
    _fields = ["method", "target", "headers", "http_version"]
    _defaults = {"http_version": b"1.1"}

    def _validate(self):
        if self.http_version == b"1.1":
            for name, value in self.headers:
                if name.lower() == b"host":
                    break
            else:
                raise ProtocolError("Missing mandatory Host: header")


class _ResponseBase(_EventBundle):
    _fields = ["status_code", "headers", "http_version"]
    _defaults = {"http_version": b"1.1"}


class InformationalResponse(_ResponseBase):
    def _validate(self):
        if not (100 <= self.status_code < 200):
            raise ProtocolError(
                "InformationalResponse status_code should be in range "
                "[100, 200), not {}"
                .format(self.status_code))


class Response(_ResponseBase):
    def _validate(self):
        if not (200 <= self.status_code < 600):
            raise ProtocolError(
                "Response status_code should be in range [200, 600), not {}"
                .format(self.status_code))


class Data(_EventBundle):
    _fields = ["data"]


# XX FIXME: "A recipient MUST ignore (or consider as an error) any fields that
# are forbidden to be sent in a trailer, since processing them as if they were
# present in the header section might bypass external security filters."
# https://svn.tools.ietf.org/svn/wg/httpbis/specs/rfc7230.html#chunked.trailer.part
# Unfortunately, the list of forbidden fields is long and vague :-/
class EndOfMessage(_EventBundle):
    _fields = ["headers"]
    _defaults = {"headers": []}


class ConnectionClosed(_EventBundle):
    pass

class Paused(_EventBundle):
    _fields = ["reason"]