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

blank_line_regex = re.compile(b"\n\r?\n", re.MULTILINE)


class ReceiveBuffer(object):
    def __init__(self):
        self._data = bytearray()
        self._next_line_search = 0
        self._multiple_lines_search = 0

    def __iadd__(self, byteslike):
        self._data += byteslike
        return self

    def __bool__(self):
        return bool(len(self))

    def __len__(self):
        return len(self._data)

    # for @property unprocessed_data
    def __bytes__(self):
        return bytes(self._data)

    if sys.version_info[0] < 3:  # version specific: Python 2
        __str__ = __bytes__
        __nonzero__ = __bool__

    def maybe_extract_at_most(self, count):
        """
        Extract a fixed number of bytes from the buffer.
        """
        out = self._data[:count]
        if not out:
            return None

        self._data[:count] = b""
        self._next_line_search = 0
        self._multiple_lines_search = 0
        return out

    def maybe_extract_next_line(self):
        """
        Extract the first line, if it is completed in the buffer.
        """
        # Only search in buffer space that we've not already looked at.
        partial_buffer = self._data[self._next_line_search :]
        partial_idx = partial_buffer.find(b"\n")
        if partial_idx == -1:
            self._next_line_search = len(self._data)
            return None

        # Truncate the buffer and return it.
        idx = self._next_line_search + partial_idx + 1
        out = self._data[:idx]
        self._data[:idx] = b""
        self._next_line_search = 0
        self._multiple_lines_search = 0
        return out

    def maybe_extract_lines(self):
        """
        Extract everything up to the first blank line, and return a list of lines.
        """
        # Handle the case where we have an immediate empty line.
        if self._data[:1] == b"\n":
            self._data[:1] = b""
            self._next_line_search = 0
            self._multiple_lines_search = 0
            return []

        if self._data[:2] == b"\r\n":
            self._data[:2] = b""
            self._next_line_search = 0
            self._multiple_lines_search = 0
            return []

        # Only search in buffer space that we've not already looked at.
        partial_buffer = self._data[self._multiple_lines_search :]
        match = blank_line_regex.search(partial_buffer)
        if match is None:
            self._multiple_lines_search = max(0, len(self._data) - 2)
            return None

        # Truncate the buffer and return it.
        idx = self._multiple_lines_search + match.span(0)[-1]
        out = self._data[:idx]
        lines = [line.rstrip(b"\r") for line in out.split(b"\n")]

        self._data[:idx] = b""
        self._next_line_search = 0
        self._multiple_lines_search = 0

        assert lines[-2] == lines[-1] == b""

        return lines[:-2]
