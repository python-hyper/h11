import re

from ._abnf import field_name, field_value
from ._util import bytesify, LocalProtocolError, validate

# Facts
# -----
#
# Headers are:
#   keys: case-insensitive ascii
#   values: mixture of ascii and raw bytes
#
# "Historically, HTTP has allowed field content with text in the ISO-8859-1
# charset [ISO-8859-1], supporting other charsets only through use of
# [RFC2047] encoding.  In practice, most HTTP header field values use only a
# subset of the US-ASCII charset [USASCII]. Newly defined header fields SHOULD
# limit their field values to US-ASCII octets.  A recipient SHOULD treat other
# octets in field content (obs-text) as opaque data."
# And it deprecates all non-ascii values
#
# Leading/trailing whitespace in header names is forbidden
#
# Values get leading/trailing whitespace stripped
#
# Content-Disposition actually needs to contain unicode semantically; to
# accomplish this it has a terrifically weird way of encoding the filename
# itself as ascii (and even this still has lots of cross-browser
# incompatibilities)
#
# Order is important:
# "a proxy MUST NOT change the order of these field values when forwarding a
# message"
# (and there are several headers where the order indicates a preference)
#
# Multiple occurences of the same header:
# "A sender MUST NOT generate multiple header fields with the same field name
# in a message unless either the entire field value for that header field is
# defined as a comma-separated list [or the header is Set-Cookie which gets a
# special exception]" - RFC 7230. (cookies are in RFC 6265)
#
# So every header aside from Set-Cookie can be merged by b", ".join if it
# occurs repeatedly. But, of course, they can't necessarily be split by
# .split(b","), because quoting.
#
# Given all this mess (case insensitive, duplicates allowed, order is
# important, ...), there doesn't appear to be any standard way to handle
# headers in Python -- they're almost like dicts, but... actually just
# aren't. For now we punt and just use a super simple representation: headers
# are a list of pairs
#
#   [(name1, value1), (name2, value2), ...]
#
# where all entries are bytestrings, names are lowercase and have no
# leading/trailing whitespace, and values are bytestrings with no
# leading/trailing whitespace. Searching and updating are done via naive O(n)
# methods.
#
# Maybe a dict-of-lists would be better?

_content_length_re = re.compile(br"[0-9]+")
_field_name_re = re.compile(field_name.encode("ascii"))
_field_value_re = re.compile(field_value.encode("ascii"))


def normalize_and_validate(headers, _parsed=False):
    new_headers = []
    saw_content_length = False
    saw_transfer_encoding = False
    for name, value in headers:
        # For headers coming out of the parser, we can safely skip some steps,
        # because it always returns bytes and has already run these regexes
        # over the data:
        if not _parsed:
            name = bytesify(name)
            value = bytesify(value)
            validate(_field_name_re, name, "Illegal header name {!r}", name)
            validate(_field_value_re, value, "Illegal header value {!r}", value)
        name = name.lower()
        if name == b"content-length":
            if saw_content_length:
                raise LocalProtocolError("multiple Content-Length headers")
            validate(_content_length_re, value, "bad Content-Length")
            saw_content_length = True
        if name == b"transfer-encoding":
            # "A server that receives a request message with a transfer coding
            # it does not understand SHOULD respond with 501 (Not
            # Implemented)."
            # https://tools.ietf.org/html/rfc7230#section-3.3.1
            if saw_transfer_encoding:
                raise LocalProtocolError(
                    "multiple Transfer-Encoding headers", error_status_hint=501
                )
            # "All transfer-coding names are case-insensitive"
            # -- https://tools.ietf.org/html/rfc7230#section-4
            value = value.lower()
            if value != b"chunked":
                raise LocalProtocolError(
                    "Only Transfer-Encoding: chunked is supported",
                    error_status_hint=501,
                )
            saw_transfer_encoding = True
        new_headers.append((name, value))
    return new_headers


def get_comma_header(headers, name):
    # Should only be used for headers whose value is a list of
    # comma-separated, case-insensitive values.
    #
    # The header name `name` is expected to be lower-case bytes.
    #
    # Connection: meets these criteria (including cast insensitivity).
    #
    # Content-Length: technically is just a single value (1*DIGIT), but the
    # standard makes reference to implementations that do multiple values, and
    # using this doesn't hurt. Ditto, case insensitivity doesn't things either
    # way.
    #
    # Transfer-Encoding: is more complex (allows for quoted strings), so
    # splitting on , is actually wrong. For example, this is legal:
    #
    #    Transfer-Encoding: foo; options="1,2", chunked
    #
    # and should be parsed as
    #
    #    foo; options="1,2"
    #    chunked
    #
    # but this naive function will parse it as
    #
    #    foo; options="1
    #    2"
    #    chunked
    #
    # However, this is okay because the only thing we are going to do with
    # any Transfer-Encoding is reject ones that aren't just "chunked", so
    # both of these will be treated the same anyway.
    #
    # Expect: the only legal value is the literal string
    # "100-continue". Splitting on commas is harmless. Case insensitive.
    #
    out = []
    for found_name, found_raw_value in headers:
        if found_name == name:
            found_raw_value = found_raw_value.lower()
            for found_split_value in found_raw_value.split(b","):
                found_split_value = found_split_value.strip()
                if found_split_value:
                    out.append(found_split_value)
    return out


def set_comma_header(headers, name, new_values):
    # The header name `name` is expected to be lower-case bytes.
    new_headers = []
    for found_name, found_raw_value in headers:
        if found_name != name:
            new_headers.append((found_name, found_raw_value))
    for new_value in new_values:
        new_headers.append((name, new_value))
    headers[:] = normalize_and_validate(new_headers)


def has_expect_100_continue(request):
    # https://tools.ietf.org/html/rfc7231#section-5.1.1
    # "A server that receives a 100-continue expectation in an HTTP/1.0 request
    # MUST ignore that expectation."
    if request.http_version < b"1.1":
        return False
    expect = get_comma_header(request.headers, b"expect")
    return b"100-continue" in expect
