# Code to read HTTP data
#
# Strategy: each reader is a callable which takes a ReceiveBuffer object, and
# either:
# 1) consumes some of it and returns an Event
# 2) raises a ProtocolError
# 3) returns None, meaning "I need more data"
#
# If they have a .read_eof attribute, then this will be called if an EOF is
# received -- but this is optional. Either way, the actual ConnectionClosed
# event will be generated afterwards.
#
# READERS is a dict describing how to pick a reader. It maps states to either:
# - a reader
# - or, for body readers, a dict of per-framing reader factories

import re
from .util import ProtocolError, validate
from .state import CLIENT, SERVER, IDLE, SEND_RESPONSE, SEND_BODY
from .events import *

__all__ = ["READERS"]

# https://svn.tools.ietf.org/svn/wg/httpbis/specs/rfc7230.html#whitespace
#  OWS            = *( SP / HTAB )
#                 ; optional whitespace
OWS = br"[ \t]*"

# https://svn.tools.ietf.org/svn/wg/httpbis/specs/rfc7230.html#rule.token.separators
#   token          = 1*tchar
#
#   tchar          = "!" / "#" / "$" / "%" / "&" / "'" / "*"
#                  / "+" / "-" / "." / "^" / "_" / "`" / "|" / "~"
#                  / DIGIT / ALPHA
#                  ; any VCHAR, except delimiters
token = rb"[-!#$%&%&'*+.^_`|~0-9a-zA-Z]+"

# https://svn.tools.ietf.org/svn/wg/httpbis/specs/rfc7230.html#header.fields
#  field-name     = token
field_name = token

#  field-value    = *( field-content / obs-fold )
#  field-content  = field-vchar [ 1*( SP / HTAB ) field-vchar ]
#  field-vchar    = VCHAR / obs-text
#  obs-fold       = CRLF 1*( SP / HTAB )
#                 ; obsolete line folding
#                 ; see Section 3.2.4
#
# https://tools.ietf.org/html/rfc5234#appendix-B.1
#
#   VCHAR          =  %x21-7E
#                  ; visible (printing) characters
#
# https://svn.tools.ietf.org/svn/wg/httpbis/specs/rfc7230.html#rule.quoted-string
#   obs-text       = %x80-FF
vchar_or_obs_text = rb"[\x21-\xff]"
field_vchar = vchar_or_obs_text
field_content = rb"%(field_vchar)s([ \t]+%(field_vchar)s)?" % {
    b"field_vchar": field_vchar,
}
field_value = rb"(%(field_content)s)*" % {b"field_content": field_content}

#  header-field   = field-name ":" OWS field-value OWS
header_field = (
    rb"^"
    rb"(?P<field_name>%(field_name)s)"
    rb":"
    rb"%(OWS)s"
    rb"(?P<field_value>%(field_value)s)"
    rb"%(OWS)s"
    rb"$"
    % {
        b"field_name": field_name,
        b"field_value": field_value,
        b"OWS": OWS,
    })
header_field_re = re.compile(header_field)

obs_fold_re = re.compile(rb"[ \t]+")
def _obsolete_line_fold(lines):
    it = iter(lines)
    last = None
    for line in it:
        match = obs_fold_re.match(line)
        if match:
            if last is None:
                raise ProtocolError("continuation line at start of headers")
            if not isinstance(last, bytearray):
                last = bytearray(last)
            last += b" "
            last += line[match.endpos:]
        else:
            if last is not None:
                yield last
            last = line
    if last is not None:
        yield last

def _decode_header_lines(lines):
    for line in _obsolete_line_fold(lines):
        matches = validate(header_field_re, line)
        yield (matches["field_name"], matches["field_value"])

# https://svn.tools.ietf.org/svn/wg/httpbis/specs/rfc7230.html#request.line
#
#   request-line   = method SP request-target SP HTTP-version CRLF
#   method         = token
#   HTTP-version   = HTTP-name "/" DIGIT "." DIGIT
#   HTTP-name      = %x48.54.54.50 ; "HTTP", case-sensitive
#
# request-target is complicated (see RFC 7230 sec 5.3) -- could be path, full
# URL, host+port (for connect), or even "*", but in any case we are guaranteed
# that it contains no spaces (see sec 3.1.1).
method = token
request_target = br"[^ ]+"
http_version = br"HTTP/(?P<http_version>[0-9]\.[0-9])"
request_line = (
    br"(?P<method>%(method)s)"
    br" "
    br"(?P<target>%(request_target)s)"
    br" "
    br"%(http_version)s"
    % {
        b"method": method,
        b"request_target": request_target,
        b"http_version": http_version,
    })
request_line_re = re.compile(request_line)

def maybe_read_from_IDLE_client(buf):
    lines = buf.maybe_extract_lines()
    if lines is None:
        return None
    matches = validate(request_line_re, lines[0])
    return Request(headers=list(_decode_header_lines(lines[1:])), **matches)

# https://svn.tools.ietf.org/svn/wg/httpbis/specs/rfc7230.html#status.line
#
#   status-line = HTTP-version SP status-code SP reason-phrase CRLF
#   status-code    = 3DIGIT
#   reason-phrase  = *( HTAB / SP / VCHAR / obs-text )
status_code = br"[0-9]{3}"
reason_phrase = br"([ \t]|%(vchar_or_obs_text)s)*" % {
    b"vchar_or_obs_text": vchar_or_obs_text}
status_line = (
    br"^"
    br"%(http_version)s"
    br" "
    br"(?P<status_code>%(status_code)s)"
    br" "
    br"%(reason_phrase)s"
    br"$"
    % {
        b"http_version": http_version,
        b"status_code": status_code,
        b"reason_phrase": reason_phrase,
    })
status_line_re = re.compile(status_line)

def maybe_read_from_SEND_RESPONSE_server(buf):
    lines = buf.maybe_extract_lines()
    if lines is None:
        return None
    matches = validate(status_line_re, lines[0])
    status_code = matches["status_code"] = int(matches["status_code"])
    class_ = InformationalResponse if status_code < 200 else Response
    return class_(headers=list(_decode_header_lines(lines[1:])), **matches)


class ContentLengthReader:
    def __init__(self, length):
        self._length = length

    def __call__(self, buf):
        if self._length == 0:
            return EndOfMessage()
        data = buf.maybe_extract_at_most(self._length)
        if data is None:
            return None
        self._length -= len(data)
        return Data(data=data)


class Http10Reader:
    def __call__(self, buf):
        data = buf.maybe_extract_at_most(999999999)
        if data is None:
            return None
        return Data(data)

    def read_eof(self):
        return EndOfMessage()


chunk_header_re = re.compile(br"(?P<count>[0-9]{1,20})\r\n")
class ChunkedReader:
    def __init__(self):
        self._bytes_in_chunk = 0
        self._reading_trailer = False

    def __call__(self, buf):
        if self._reading_trailer:
            lines = buf.maybe_extract_lines()
            if lines is None:
                return None
            return EndOfMessage(headers=list(_decode_header_lines(lines)))
        else:
            # Refill our chunk count
            if self._bytes_in_chunk == 0:
                chunk_header = buf.maybe_extract_until_next_new(b"\r\n")
                if chunk_header is None:
                    return None
                matches = validate(chunk_header_re, chunk_header)
                self._bytes_in_chunk = int(matches["count"], base=16)
                if self._bytes_in_chunk == 0:
                    self._reading_trailer = True
                    return self(buf)
            assert self._bytes_in_chunk > 0
            data = buf.maybe_extract_at_most(self._bytes_in_chunk)
            if data is None:
                return None
            self._bytes_in_chunk -= len(data)
            return Data(data=data)

BODY_READERS = {
    "chunked": ChunkedReader,
    "content-length": ContentLengthReader,
    "http/1.0": Http10Reader,
}

READERS = {
    (CLIENT, IDLE): maybe_read_from_IDLE_client,
    (SERVER, SEND_RESPONSE): maybe_read_from_SEND_RESPONSE_server,
    (CLIENT, SEND_BODY): BODY_READERS,
    (SERVER, SEND_BODY): BODY_READERS,
}
