# Code to read HTTP data
#
# Strategy: each reader is a callable which takes a ReceiveBuffer object, and
# either:
# 1) consumes some of it and returns an Event
# 2) raises a LocalProtocolError (for consistency -- e.g. we call validate()
#    and it might raise a LocalProtocolError, so simpler just to always use
#    this)
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
from ._util import LocalProtocolError, validate
from ._state import *
from ._events import *

__all__ = ["READERS"]

# We use native strings for all the re patterns, to take advantage of string
# formatting, and then convert to bytestrings when compiling the final re
# objects.

# https://svn.tools.ietf.org/svn/wg/httpbis/specs/rfc7230.html#whitespace
#  OWS            = *( SP / HTAB )
#                 ; optional whitespace
OWS = r"[ \t]*"

# https://svn.tools.ietf.org/svn/wg/httpbis/specs/rfc7230.html#rule.token.separators
#   token          = 1*tchar
#
#   tchar          = "!" / "#" / "$" / "%" / "&" / "'" / "*"
#                  / "+" / "-" / "." / "^" / "_" / "`" / "|" / "~"
#                  / DIGIT / ALPHA
#                  ; any VCHAR, except delimiters
token = r"[-!#$%&'*+.^_`|~0-9a-zA-Z]+"

# https://svn.tools.ietf.org/svn/wg/httpbis/specs/rfc7230.html#header.fields
#  field-name     = token
field_name = token

# The standard says:
#
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
#
# However, the standard definition of field-content is WRONG! It disallows
# fields containing a single visible character surrounded by whitespace,
# e.g. "foo a bar".
#
# See: https://www.rfc-editor.org/errata_search.php?rfc=7230&eid=4189
#
# So our definition of field_content attempts to fix it up...
vchar_or_obs_text = r"[\x21-\xff]"
field_vchar = vchar_or_obs_text
field_content = r"{field_vchar}+(?:[ \t]+{field_vchar}+)*".format(**globals())

# We handle obs-fold at a different level, and our fixed-up field_content
# already grows to swallow the whole value, so ? instead of *
field_value = r"({field_content})?".format(**globals())

#  header-field   = field-name ":" OWS field-value OWS
header_field = (
    r"(?P<field_name>{field_name})"
    r":"
    r"{OWS}"
    r"(?P<field_value>{field_value})"
    r"{OWS}"
    .format(**globals()))
header_field_re = re.compile(header_field.encode("ascii"))

# Remember that this has to run in O(n) time -- so e.g. the bytearray cast is
# critical.
obs_fold_re = re.compile(br"[ \t]+")
def _obsolete_line_fold(lines):
    it = iter(lines)
    last = None
    for line in it:
        match = obs_fold_re.match(line)
        if match:
            if last is None:
                raise LocalProtocolError(
                    "continuation line at start of headers")
            if not isinstance(last, bytearray):
                last = bytearray(last)
            last += b" "
            last += line[match.end():]
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
request_target = r"[^ ]+"
http_version = r"HTTP/(?P<http_version>[0-9]\.[0-9])"
request_line = (
    r"(?P<method>{method})"
    r" "
    r"(?P<target>{request_target})"
    r" "
    r"{http_version}"
    .format(**globals()))
request_line_re = re.compile(request_line.encode("ascii"))

def maybe_read_from_IDLE_client(buf):
    lines = buf.maybe_extract_lines()
    if lines is None:
        return None
    if not lines:
        raise LocalProtocolError("no request line received")
    matches = validate(request_line_re, lines[0])
    return Request(headers=list(_decode_header_lines(lines[1:])), **matches)

# https://svn.tools.ietf.org/svn/wg/httpbis/specs/rfc7230.html#status.line
#
#   status-line = HTTP-version SP status-code SP reason-phrase CRLF
#   status-code    = 3DIGIT
#   reason-phrase  = *( HTAB / SP / VCHAR / obs-text )
status_code = r"[0-9]{3}"
reason_phrase = r"([ \t]|{vchar_or_obs_text})*".format(**globals())
status_line = (
    r"{http_version}"
    r" "
    r"(?P<status_code>{status_code})"
    r" "
    r"{reason_phrase}"
    .format(**globals()))
status_line_re = re.compile(status_line.encode("ascii"))

def maybe_read_from_SEND_RESPONSE_server(buf):
    lines = buf.maybe_extract_lines()
    if lines is None:
        return None
    if not lines:
        raise LocalProtocolError("no response line received")
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


HEXDIG = r"[0-9A-Fa-f]"
# Actually
#
#      chunk-size     = 1*HEXDIG
#
# but we impose an upper-limit to avoid ridiculosity. len(str(2**64)) == 20
chunk_size = r"({HEXDIG}){{1,20}}".format(**globals())
# Actually
#
#     chunk-ext      = *( ";" chunk-ext-name [ "=" chunk-ext-val ] )
#
# but we aren't parsing the things so we don't really care.
chunk_ext = r";.*"
chunk_header = (
    r"(?P<chunk_size>{chunk_size})"
    r"(?P<chunk_ext>{chunk_ext})?"
    r"\r\n"
    .format(**globals()))

chunk_header_re = re.compile(chunk_header.encode("ascii"))
class ChunkedReader(object):
    def __init__(self):
        self._bytes_in_chunk = 0
        # After reading a chunk, we have to throw away the trailing \r\n; if
        # this is >0 then we discard that many bytes before resuming regular
        # de-chunkification.
        self._bytes_to_discard = 0
        self._reading_trailer = False

    def __call__(self, buf):
        if self._reading_trailer:
            lines = buf.maybe_extract_lines()
            if lines is None:
                return None
            return EndOfMessage(headers=list(_decode_header_lines(lines)))
        if self._bytes_to_discard > 0:
            data = buf.maybe_extract_at_most(self._bytes_to_discard)
            if data is None:
                return None
            self._bytes_to_discard -= len(data)
            if self._bytes_to_discard > 0:
                return None
            # else, fall through and read some more
        assert self._bytes_to_discard == 0
        if self._bytes_in_chunk == 0:
            # We need to refill our chunk count
            chunk_header = buf.maybe_extract_until_next(b"\r\n")
            if chunk_header is None:
                return None
            matches = validate(chunk_header_re, chunk_header)
            # XX FIXME: we discard chunk extensions. Does anyone care?
            # We convert to bytes because Python 2's `int()` function doesn't
            # work properly on bytearray objects.
            self._bytes_in_chunk = int(bytes(matches["chunk_size"]), base=16)
            if self._bytes_in_chunk == 0:
                self._reading_trailer = True
                return self(buf)
        assert self._bytes_in_chunk > 0
        data = buf.maybe_extract_at_most(self._bytes_in_chunk)
        if data is None:
            return None
        self._bytes_in_chunk -= len(data)
        if self._bytes_in_chunk == 0:
            self._bytes_to_discard = 2
        return Data(data=data)


class Http10Reader(object):
    def __call__(self, buf):
        data = buf.maybe_extract_at_most(999999999)
        if data is None:
            return None
        return Data(data=data)

    def read_eof(self):
        return EndOfMessage()

def expect_nothing(buf):
    if buf:
        raise LocalProtocolError("Got data when expecting EOF")
    return None

READERS = {
    (CLIENT, IDLE): maybe_read_from_IDLE_client,
    (SERVER, IDLE): maybe_read_from_SEND_RESPONSE_server,
    (SERVER, SEND_RESPONSE): maybe_read_from_SEND_RESPONSE_server,

    (CLIENT, DONE): expect_nothing,
    (CLIENT, MUST_CLOSE): expect_nothing,
    (CLIENT, CLOSED): expect_nothing,
    (SERVER, DONE): expect_nothing,
    (SERVER, MUST_CLOSE): expect_nothing,
    (SERVER, CLOSED): expect_nothing,

    SEND_BODY: {
        "chunked": ChunkedReader,
        "content-length": ContentLengthReader,
        "http/1.0": Http10Reader,
    },
}
