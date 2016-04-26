# High level events that we emit as our external interface for reading HTTP
# streams, loosely modelled on the corresponding events in hyper-h2:
#     http://python-hyper.org/h2/en/stable/api.html#events
#
# The most noticeable difference is that I use the same objects for sending,
# so have dropped the Receive prefix.

from .util import asciify, asciify_headers

__all__ = [
    "Request",
    "Response",
    "InformationalResponse",
    "Data",
    "EndOfMessage",
]

class _EventBundle:
    _required = []
    _optional = []

    def __init__(self, **kwargs):
        allowed = set(self._required + self._optional)
        for kwarg in kwargs:
            if kwarg not in allowed:
                raise TypeError(
                    "unrecognized kwarg {} for {}"
                    .format(kwarg, self.__class__.__name__))
        for field in self._required:
            if field not in kwargs:
                raise TypeError(
                    "missing required kwarg {} for {}"
                    .format(kwarg, self.__class__.__name__))
        self.__dict__.update(kwargs)

        if "headers" in self.__dict__:
            self.headers = _asciify_headers(self.headers)
        for field in ["method", "client_method", "url"]:
            if field in self.__dict__:
                self.__dict__[field] = _asciify(self.__dict__[field])

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

class Request(_EventBundle):
    _required = ["method", "url", "headers"]
    _optional = ["http_version", "keep_alive"]

class _ResponseBase(_EventBundle):
    _required = ["status_code", "headers"]
    _optional = ["http_version", "request_method", "keep_alive"]

class Response(_ResponseBase):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not (200 <= self.status_code):
            raise ValueError(
                "Response status_code should be >= 200, but got {}"
                .format(self.status_code))

class InformationalResponse(_ResponseBase):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not (100 <= self.status_code < 200):
            raise ValueError(
                "InformationalResponse status_code should be in range "
                "[200, 300), but got {}"
                .format(self.status_code))

class Data(_EventBundle):
    _required = ["data"]

# XX FIXME: "A recipient MUST ignore (or consider as an error) any fields that
# are forbidden to be sent in a trailer, since processing them as if they were
# present in the header section might bypass external security filters."
# https://svn.tools.ietf.org/svn/wg/httpbis/specs/rfc7230.html#chunked.trailer.part
class EndOfMessage:
    _optional = ["headers", "keep_alive", "upgrade", "trailing_data"]
