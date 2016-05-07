#!python

import sys
sys.path.append("../..")

import os.path
import subprocess

from h11.events import *
from h11.state import *
from h11.state import (
    _SWITCH_UPGRADE, _SWITCH_CONNECT,
    EVENT_TRIGGERED_TRANSITIONS, STATE_TRIGGERED_TRANSITIONS,
)

_EVENT_COLOR = "#002092"
_STATE_COLOR = "#017517"
_SPECIAL_COLOR = "#7600a1"
def make_dot(role, out_path):
    with open(out_path, "w") as f:
        f.write(
"""digraph {
  graph [fontname = "Lato"]
  node  [fontname = "Lato"]
  edge  [fontname = "Lato"]

  IDLE [label=<IDLE<BR/><i>start state</i>>]
""")

        # Dot output is sensitive to the order in which the edges are listed.
        # We generate them in python's randomized dict iteration order.  So to
        # normalize order, we accumulate and then sort.  Fortunately, this
        # order happens to be one that produces a nice layout... with other
        # orders I've seen really terrible layouts, and had to do things like
        # move the server's IDLE->MUST_CLOSE to the top of the file to fix
        # them.
        edges = []
        def edge(source, target, label, color, italicize=False, weight=1):
            if italicize:
                quoted_label = "<<i>{}</i>>".format(label)
            else:
                quoted_label = '<{}>'.format(label)
            edges.append(
                '{source} -> {target} [\n'
                '  label={quoted_label},\n'
                '  color="{color}", fontcolor="{color}",\n'
                '  weight={weight},\n'
                ']\n'
                .format(**locals()))

        CORE_EVENTS = {Request, InformationalResponse,
                       Response, Data, EndOfMessage}

        for (source_state, t) in EVENT_TRIGGERED_TRANSITIONS[role].items():
            for (event_type, target_state) in t.items():
                weight = 1
                color = _EVENT_COLOR
                italicize = False
                if (event_type in CORE_EVENTS
                    and source_state is not target_state):
                    weight = 10
                # exception
                if (event_type is Response and source_state is IDLE):
                    weight = 1
                if isinstance(event_type, tuple):
                    # The weird special cases
                    #color = _SPECIAL_COLOR
                    if event_type == (Request, CLIENT):
                        name = "<i>client makes Request</i>"
                        weight = 10
                    elif event_type[1] is _SWITCH_UPGRADE:
                        name = "<i>101 Switching Protocols</i>"
                        weight = 1
                    elif event_type[1] is _SWITCH_CONNECT:
                        name = "<i>CONNECT accepted</i>"
                        weight = 1
                    else:
                        assert False
                else:
                    name = event_type.__name__
                edge(source_state, target_state, name, color,
                     weight=weight, italicize=italicize)

        for state_pair, updates in STATE_TRIGGERED_TRANSITIONS.items():
            if role not in updates:
                continue
            if role is CLIENT:
                (our_state, their_state) = state_pair
            else:
                (their_state, our_state) = state_pair
            edge(our_state, updates[role],
                 "<i>peer in</i><BR/>{}".format(their_state),
                 _STATE_COLOR)

        if role is CLIENT:
            edge(DONE, MIGHT_SWITCH_PROTOCOL,
                 "Upgrade: or CONNECT<BR/>request is pending",
                 _STATE_COLOR,
                 italicize=True)
            edge(MIGHT_SWITCH_PROTOCOL, DONE,
                 "Upgrade: or CONNECT<BR/>request was denied",
                 _STATE_COLOR,
                 italicize=True)

        edge(DONE, MUST_CLOSE, "keep-alive<BR/>is disabled", _STATE_COLOR,
             italicize=True)
        edge(DONE, IDLE, "prepare_to_reuse()", _SPECIAL_COLOR)

        edges.sort()

        f.write("".join(edges))

        # For some reason labelfontsize doesn't seem to do anything, but this
        # works
        f.write("""
  labelloc="t"
  labeljust="l"
  label=<<FONT POINT-SIZE="20">h11 state machine: {}</FONT>>
"""
                .format(role))


        f.write("\n}\n")

my_dir = os.path.dirname(__file__)
out_dir = os.path.join(my_dir, "_static")
if not os.path.exists(out_dir):
    os.path.mkdir(out_dir)
for role in (CLIENT, SERVER):
    dot_path = os.path.join(out_dir, str(role) + ".dot")
    svg_path = dot_path[:-3] + "svg"
    make_dot(role, dot_path)
    subprocess.check_call(["dot", "-Tsvg", dot_path, "-o", svg_path])
