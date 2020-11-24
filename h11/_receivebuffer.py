import sys

__all__ = ["ReceiveBuffer"]


# Operations we want to support:
# - find next \r\n or \r\n\r\n, or wait until there is one
# - read at-most-N bytes
# Goals:
# - on average, do this fast
# - worst case, do this in O(n) where n is the number of bytes processed
# Plan:
# - store bytearray, offset, how far we've searched for a separator token
# - use the how-far-we've-searched data to avoid rescanning
# - while doing a stream of uninterrupted processing, advance offset instead
#   of constantly copying
# WARNING:
# - I haven't benchmarked or profiled any of this yet.
class ReceiveBuffer(object):
    def __init__(self):
        self._data = bytearray()
        # These are both absolute offsets into self._data:
        self._looked_at = 0
        self._looked_for = b""

    def __bool__(self):
        return bool(len(self))

    # for @property unprocessed_data
    def __bytes__(self):
        return bytes(self._data)

    if sys.version_info[0] < 3:  # version specific: Python 2
        __str__ = __bytes__
        __nonzero__ = __bool__

    def __len__(self):
        return len(self._data)

    def __iadd__(self, byteslike):
        self._data += byteslike
        return self

    def maybe_extract_at_most(self, count):
        out = self._data[:count]
        if not out:
            return None
        # Note that front-truncation of bytesarray is amortized O(1), from
        # Python 3.4 onwards, thanks to some excellent work by Antoine Pitrou:
        #
        # https://bugs.python.org/issue19087
        del self._data[:count]
        return out

    def maybe_extract_until_next(self, needle):
        # Returns extracted bytes on success (advancing offset), or None on
        # failure
        if self._looked_for == needle:
            search_start = max(0, self._looked_at - len(needle) + 1)
        else:
            search_start = 0
        offset = self._data.find(needle, search_start)
        if offset == -1:
            self._looked_at = len(self._data)
            self._looked_for = needle
            return None
        new_start = offset + len(needle)
        out = self._data[:new_start]
        del self._data[:new_start]
        return out

    # HTTP/1.1 has a number of constructs where you keep reading lines until
    # you see a blank one. This does that, and then returns the lines.
    def maybe_extract_lines(self):
        if self._data[:2] == b"\r\n":
            del self._data[:2]
            return []
        else:
            data = self.maybe_extract_until_next(b"\r\n\r\n")
            if data is None:
                return None
            lines = data.split(b"\r\n")
            assert lines[-2] == lines[-1] == b""
            del lines[-2:]
            return lines
