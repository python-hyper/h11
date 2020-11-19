import re
import sys

__all__ = ["ReceiveBuffer"]


# Operations we want to support:
# - find next \r\n or \r\n\r\n (\n or \n\n are also acceptable),
#   or wait until there is one
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
#
# Note that starting in Python 3.4, deleting the initial n bytes from a
# bytearray is amortized O(n), thanks to some excellent work by Antoine
# Martin:
#
#     https://bugs.python.org/issue19087
#
# This means that if we only supported 3.4+, we could get rid of the code here
# involving self._start and self.compress, because it's doing exactly the same
# thing that bytearray now does internally.
#
# BUT unfortunately, we still support 2.7, and reading short segments out of a
# long buffer MUST be O(bytes read) to avoid DoS issues, so we can't actually
# delete this code. Yet:
#
#     https://pythonclock.org/
#
# (Two things to double-check first though: make sure PyPy also has the
# optimization, and benchmark to make sure it's a win, since we do have a
# slightly clever thing where we delay calling compress() until we've
# processed a whole event, which could in theory be slightly more efficient
# than the internal bytearray support.)

blank_line_delimiter_regex = re.compile(b"\n\r?\n", re.MULTILINE)
line_delimiter_regex = re.compile(b"\r?\n", re.MULTILINE)


class ReceiveBuffer(object):
    def __init__(self):
        self._data = bytearray()
        # These are both absolute offsets into self._data:
        self._start = 0
        self._looked_at = 0

        self._looked_for_regex = blank_line_delimiter_regex

    def __bool__(self):
        return bool(len(self))

    # for @property unprocessed_data
    def __bytes__(self):
        return bytes(self._data[self._start :])

    if sys.version_info[0] < 3:  # version specific: Python 2
        __str__ = __bytes__
        __nonzero__ = __bool__

    def __len__(self):
        return len(self._data) - self._start

    def compress(self):
        # Heuristic: only compress if it lets us reduce size by a factor
        # of 2
        if self._start > len(self._data) // 2:
            del self._data[: self._start]
            self._looked_at -= self._start
            self._start -= self._start

    def __iadd__(self, byteslike):
        self._data += byteslike
        return self

    def maybe_extract_at_most(self, count):
        out = self._data[self._start : self._start + count]
        if not out:
            return None
        self._start += len(out)
        return out

    def maybe_extract_until_next(self, needle_regex, max_needle_length):
        # Returns extracted bytes on success (advancing offset), or None on
        # failure
        if self._looked_for_regex == needle_regex:
            looked_at = max(self._start, self._looked_at - max_needle_length)
        else:
            looked_at = self._start
            self._looked_for_regex = needle_regex

        delimiter_match = next(
            self._looked_for_regex.finditer(self._data, looked_at), None
        )

        if delimiter_match is None:
            self._looked_at = len(self._data)
            return None

        _, end = delimiter_match.span(0)

        out = self._data[self._start : end]

        self._start = end

        return out

    def _get_fields_delimiter(self, data, lines_delimiter_regex):
        delimiter_match = next(lines_delimiter_regex.finditer(data), None)

        if delimiter_match is not None:
            begin, end = delimiter_match.span(0)
            result = data[begin:end]
        else:
            result = b"\r\n"

        return bytes(result)

    # HTTP/1.1 has a number of constructs where you keep reading lines until
    # you see a blank one. This does that, and then returns the lines.
    def maybe_extract_lines(self):
        start_chunk = self._data[self._start : self._start + 2]
        if start_chunk in [b"\r\n", b"\n"]:
            self._start += len(start_chunk)
            return []
        else:
            data = self.maybe_extract_until_next(blank_line_delimiter_regex, 3)
            if data is None:
                return None

            real_lines_delimiter = self._get_fields_delimiter(data, line_delimiter_regex)
            lines = data.rstrip(b"\r\n").split(real_lines_delimiter)

            return lines
