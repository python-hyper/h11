def get_comma_header(headers, name, *, lowercase=True):
    # Should only be used for headers whose value is a list of comma-separated
    # values. Use lowercase=True for case-insensitive ones.
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
    # "100-continue". Splitting on commas is harmless. But, must set
    # lowercase=False.
    #
    name = bytesify(name).lower()
    for found_name, found_raw_value in headers:
        found_name = bytesify(found_name).lower()
        if found_name == name:
            found_raw_value = bytesify(found_raw_value)
            if lowercase:
                found_raw_value = found_raw_value.lower()
            for found_split_value in found_raw_value.split(b","):
                found_split_value = found_split_value.strip()
                if found_split_value:
                    yield found_split_value

def set_comma_header(headers, name, new_values):
    name = bytesify(name).lower()
    new_headers = []
    for found_name, found_raw_value in headers:
        if bytesify(found_name).lower() != name:
            new_headers.append((found_name, found_raw_value))
    for new_value in new_values:
        new_headers.append((name, new_value))
    headers[:] = new_headers


################################################################
# Facts:
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
# "A server MUST reject any received request message that contains whitespace
# between a header field-name and colon with a response code of 400 (Bad
# Request). A proxy MUST remove any such whitespace from a response message
# before forwarding the message downstream."
# libhttp_parser doesn't care though, if you give it
#   b"Hello : there\r\n"
# you get back {"b"Hello ": b"there"}
# (i.e. it strips the whitespace around the value, but not around the field
# name)
#
# Values get leading/trailing whitespace stripped
#
# Content-Disposition actually needs to contain unicode; it has a terrifically
#   weird way of encoding the filename itself as ascii (and even this still
#   has lots of cross-browser incompatibilities)
#
# Order is important:
# "a proxy MUST NOT change the order of these field values when forwarding a
# message."
# Sigh.
#
# Multiple occurences of the same header:
# "A sender MUST NOT generate multiple header fields with the same field name
# in a message unless either the entire field value for that header field is
# defined as a comma-separated list [or the header is Set-Cookie which gets a
# special exception]" - RFC 7230. (cookies are in RFC 6265)
#
# So every header aside from Set-Cookie can be merged by b", ".join if it
# occurs repeatedly. But, of course, they can't necessarily be spit by
# .split(b","), because quoting.

################################################################
# What operations do we actually care about?
#
# extracting Connection, Transfer-Encoding, Content-Length
# setting same
#
# Letting users do things like ["Content-Type"] = ...
#
# Maybe we should follow the HTTP rules and normalize repeated header values
# to "foo,bar,baz", and then have a special exception for Set-Cookie being
# always a set of values
# (cookies are RFC

from .util import bytesify

def _norm_key(key):
    return bytesify(key).lower()

def _norm_value(value):
    return bytesify(value).strip()

def _norm_both(key, value):
    return norm_header_key(key), norm_header_value(value)

# Loosely inspired by werkzeug.datastructures.Headers.
# Mostly intended for internal use.
class Headers:
    def __init__(self, initial_headers=[]):
        self._list = []
        self.extend(initial_pairs)

    def __iter__(self):
        return iter(self._list)

    def add(self, key, value):
        # Raise errors early
        _norm_both(key, value)
        self._list.append((key, value))

    def extend(self, entries):
        for key, value in entries:
            self.add(key, value)

    def __contains__(self, key_or_pair_needle):
        if isinstance(key_or_pair, tuple):
            needle = _norm_both(key_or_pair)
            for key, value in self:
                if _norm_both(key, value) == needle:
                    return True
            return False
        else:
            return bool(self.get_all(key_or_pair))

    def discard(self, key, value):
        "Discards the given entry if present"
        needle = _norm_both(key, value)
        self._list = [entry for entry in self if _norm_both(*entry) != needle]

    def get_all(self, key_needle, *, split_on_comma=False):
        "Gets all values associated with given key"
        key_needle = _norm_key(key_needle)
        values = [value for (key, value) in self
                  if _norm_key(key) == key_needle]
        if split_on_comma:
            new_values = []
            for entry in values:
                for value in asciify(entry).split(b","):
                    new_values.append(value.strip())
            values = new_values
        return values

    def discard_all(self, key_needle):
        "Discards all entries associated with given key"
        key_needle = _norm_key(needle_key)
        self._list = [entry for entry in self if _norm_key(key) != key_needle]

    def set_all(self, key, values):
        "Replaces all existing values for key with 'values', at the end"
        self.discard_all(key)
        for value in values:
            self.add(key, value)

    def set(self, key, value):
        "Replaces all existing values for key with value, at the end"
        self.set_all(key, [value])
