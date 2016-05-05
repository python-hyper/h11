################################################################
# The core state machine
################################################################
#
# Rule 1: everything that affects the state machine and state transitions must
# live here in this file. As much as possible goes into the table-based
# representation, but for the bits that don't quite fit, the actual code and
# state must nonetheless live here.
#
# Rule 2: this file does not know about what role we're playing; it only knows
# about HTTP request/response cycles in the abstract. This ensures that we
# don't cheat and apply different rules to local and remote parties.
#
#
# Theory of operation
# ===================
#
# Possibly the simplest way to think about this is that we actually have 5
# different state machines here. Yes, 5. These are:
#
# 1) The client state, with its complicated automaton (see the docs)
# 2) The server state, with its complicated automaton (see the docs)
# 3) The keep-alive state, with possible states {True, False}
# 4) The SWITCH_CONNECT state, with possible states {False, True}
# 5) The SWITCH_UPGRADE state, with possible states {False, True}
#
# For (3)-(5), the first state listed is the initial state.
#
# (1)-(3) are stored explicitly in member variables. The last
# two are stored implicitly in the pending_switch_proposals set as:
#   (state of 4) == (SWITCH_CONNECT in pending_switch_proposals)
#   (state of 5) == (SWITCH_UPGRADE in pending_switch_proposals)
#
# And each of these machines has two different kinds of transitions:
#
# a) Event-triggered
# b) State-triggered
#
# Event triggered is the obvious thing that you'd think it is: some event
# happens, and if it's the right event at the right time then a transition
# happens. But there are somewhat complicated rules for which machines can
# "see" which events. (As a rule of thumb, if a machine "sees" an event, this
# means two things: the event can affect the machine, and if the machine is
# not in a state where it expects that event then it's an error.) These rules
# are:
#
# 1) The client machine sees all h11.events objects emitted by the client.
#
# 2) The server machine sees all h11.events objects emitted by the server.
#
#    It also sees the client's Request event.
#
#    And sometimes, server events are annotated with a SWITCH_* event. For
#    example, we can have a (Response, SWITCH_CONNECT) event, which is
#    different from a regular Response event.
#
# 3) The keep-alive machine sees the process_keep_alive_disabled() event
#    (which is derived from Request/Response events), and this event
#    transitions it from True -> False, or from False -> False. There's no way
#    to transition back.
#
# 4&5) The SWITCH_* machines transition from False->True when we get a Request
#    that proposes the relevant type of switch (via
#    process_client_switch_proposals), and they go from True->False when we
#    get a Response that has no SWITCH_* annotation.
#
# So that's event-triggered transitions.
#
# State-triggered transitions are less standard. What they do here is couple
# the machines together. The way this works is, when certain *joint*
# configurations of states are achieved, then we automatically transition to a
# new *joint* state. So, for example, if we're ever in a joint state with
#
#   client: DONE
#   keep-alive: False
#
# then the client state immediately transitions to:
#
#   client: MUST_CLOSE
#
# This is fundamentally different from an event-based transition, because it
# doesn't matter how we arrived at the {client: DONE, keep-alive: False} state
# -- maybe the client transitioned SEND_BODY -> DONE, or keep-alive
# transitioned True -> False. Either way, once this precondition is satisfied,
# this transition is immediately triggered.
#
# What if two conflicting state-based transitions get enabled at the same
# time?  In practice there's only one case where this arises (client DONE ->
# MIGHT_SWITCH_PROTOCOL versus DONE -> MUST_CLOSE), and we resolve it by
# explicitly prioritizing the DONE -> MIGHT_SWITCH_PROTOCOL transition.
#
# Implementation
# --------------
#
# The event-triggered transitions for the server and client machines are all
# stored explicitly in a table. Ditto for the state-triggered transitions that
# involve just the server and client state.
#
# The transitions for the other machines, and the state-triggered transitions
# that involve the other machines, are written out as explicit Python code.
#
# It'd be nice if there were some cleaner way to do all this. This isn't
# *too* terrible, but I feel like it could probably be better.
#
# WARNING
# -------
#
# The script that generates the state machine diagrams for the docs knows how
# to read out the EVENT_TRIGGERED_TRANSITIONS and STATE_TRIGGERED_TRANSITIONS
# tables. But it can't automatically read the transitions that are written
# directly in Python code. So if you touch those, you need to also update the
# script to keep it in sync!

from .events import *
from .util import ProtocolError, Sentinel

# Everything in __all__ gets re-exported as part of the h11 public API.
__all__ = []

# Be careful of trailing whitespace here:
sentinels = ("CLIENT SERVER "
             # States
             "IDLE SEND_RESPONSE SEND_BODY DONE MUST_CLOSE CLOSED "
             "MIGHT_SWITCH_PROTOCOL SWITCHED_PROTOCOL "
             # Switch types
             "SWITCH_UPGRADE SWITCH_CONNECT").split()
for token in sentinels:
    globals()[token] = Sentinel(token)

__all__ += sentinels

EVENT_TRIGGERED_TRANSITIONS = {
    CLIENT: {
        IDLE: {
            Request: SEND_BODY,
            ConnectionClosed: CLOSED,
        },
        SEND_BODY: {
            Data: SEND_BODY,
            EndOfMessage: DONE,
        },
        DONE: {
            ConnectionClosed: CLOSED,
        },
        MUST_CLOSE: {
            ConnectionClosed: CLOSED,
        },
        CLOSED: {
            ConnectionClosed: CLOSED,
        },
        MIGHT_SWITCH_PROTOCOL: {},
        SWITCHED_PROTOCOL: {},
    },

    SERVER: {
        IDLE: {
            ConnectionClosed: CLOSED,
            # Special case: server sees client Request events, in this form
            (Request, CLIENT): SEND_RESPONSE,
            # This is needed solely to allow for 400 Bad Request responses to
            # requests that we errored out on, and thus never made it through
            # the state machine.
            Response: SEND_BODY,
        },
        SEND_RESPONSE: {
            InformationalResponse: SEND_RESPONSE,
            Response: SEND_BODY,
            (InformationalResponse, SWITCH_UPGRADE): SWITCHED_PROTOCOL,
            (Response, SWITCH_CONNECT): SWITCHED_PROTOCOL,
        },
        SEND_BODY: {
            Data: SEND_BODY,
            EndOfMessage: DONE,
        },
        DONE: {
            ConnectionClosed: CLOSED,
        },
        MUST_CLOSE: {
            ConnectionClosed: CLOSED,
        },
        CLOSED: {
            ConnectionClosed: CLOSED,
        },
        SWITCHED_PROTOCOL: {},
    },
}

# NB: there are also some special-case state-triggered transitions hard-coded
# into _fire_state_triggered_transitions below.
STATE_TRIGGERED_TRANSITIONS = {
    # (Client state, Server state) -> new states
    # Protocol negotiation
    (MIGHT_SWITCH_PROTOCOL, SWITCHED_PROTOCOL): {CLIENT: SWITCHED_PROTOCOL},
    # Socket shutdown
    (CLOSED, DONE): {SERVER: MUST_CLOSE},
    (CLOSED, IDLE): {SERVER: MUST_CLOSE},
    (DONE, CLOSED): {CLIENT: MUST_CLOSE},
    (IDLE, CLOSED): {CLIENT: MUST_CLOSE},
}

class ConnectionState:
    def __init__(self):
        # Extra bits of state that don't quite fit into the state model.

        # If this is False then it enables the automatic DONE -> MUST_CLOSE
        # transition. Don't set this directly; call .keep_alive_disabled()
        self.keep_alive = True

        # This is a subset of {UPGRADE, CONNECT}, containing the proposals
        # made by the client for switching protocols.
        self.pending_switch_proposals = set()

        self.states = {CLIENT: IDLE, SERVER: IDLE}

    def process_keep_alive_disabled(self):
        self.keep_alive = False
        self._fire_state_triggered_transitions()

    def process_client_switch_proposals(self, switch_events):
        assert self.states == {CLIENT: IDLE, SERVER: IDLE}
        assert not self.pending_switch_proposals
        self.pending_switch_proposals.update(switch_events)
        self._fire_state_triggered_transitions()

    def process_event(self, role, event_type, server_switch_event=None):
        if server_switch_event is not None:
            assert role is SERVER
            if server_switch_event not in self.pending_switch_proposals:
                raise ProtocolError(
                    "Received server {} event without a pending proposal"
                    .format(server_switch_event))
            event_type = (event_type, server_switch_event)
        if server_switch_event is None and event_type is Response:
            self.pending_switch_proposals = set()
        self._fire_event_triggered_transitions(role, event_type)
        # Special case: the server state does get to see Request
        # events.
        if event_type is Request:
            assert role is CLIENT
            self._fire_event_triggered_transitions(SERVER, (Request, CLIENT))
        self._fire_state_triggered_transitions()

    def _fire_event_triggered_transitions(self, role, event_type):
        state = self.states[role]
        try:
            new_state = EVENT_TRIGGERED_TRANSITIONS[role][state][event_type]
        except KeyError:
            raise ProtocolError(
                "can't handle event type {} for {} in state {}"
                .format(event_type, role, self.states[role]))
        self.states[role] = new_state

    def _fire_state_triggered_transitions(self):
        # We apply these rules repeatedly until converging on a fixed point
        while True:
            start_states = dict(self.states)

            # It could happen that both these special-case transitions are
            # enabled at the same time:
            #
            #    DONE -> MIGHT_SWITCH_PROTOCOL
            #    DONE -> MUST_CLOSE
            #
            # For example, this will always be true of a HTTP/1.0 client
            # requesting CONNECT.  If this happens, the protocol switch takes
            # priority. From there the client will either go to
            # SWITCHED_PROTOCOL, in which case it's none of our business when
            # they close the connection, or else the server will deny the
            # request, in which case the client will go back to DONE and then
            # from there to MUST_CLOSE.
            if self.pending_switch_proposals:
                if self.states[CLIENT] is DONE:
                    self.states[CLIENT] = MIGHT_SWITCH_PROTOCOL

            if not self.pending_switch_proposals:
                if self.states[CLIENT] is MIGHT_SWITCH_PROTOCOL:
                    self.states[CLIENT] = DONE

            if not self.keep_alive:
                for role in (CLIENT, SERVER):
                    if self.states[role] is DONE:
                        self.states[role] = MUST_CLOSE

            # Tabular state-triggered transitions
            joint_state = (self.states[CLIENT], self.states[SERVER])
            changes = STATE_TRIGGERED_TRANSITIONS.get(joint_state, {})
            self.states.update(changes)

            if self.states == start_states:
                # Fixed point reached
                return

    def prepare_to_reuse(self):
        if self.states != {CLIENT: DONE, SERVER: DONE}:
            raise ProtocolError("not in a reusable state")
        # Can't reach DONE/DONE with any of these active, but still, let's be
        # sure.
        assert self.keep_alive
        assert not self.pending_switch_proposals
        self.states = {CLIENT: IDLE, SERVER: IDLE}


_EVENT_COLOR = "#002092"
_STATE_COLOR = "#017517"
_SPECIAL_COLOR = "#7600a1"
def _make_dot(role, out_path):
    with open(out_path, "w") as f:
        f.write(
"""digraph {
  graph [fontname = "Lato"]
  node [fontname = "Lato"]
  edge [fontname = "Lato"]

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
                quoted_label = '"{}"'.format(label)
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
                    italicize = True
                    if event_type == (Request, CLIENT):
                        name = "client Request"
                        weight = 10
                    elif event_type[1] is SWITCH_UPGRADE:
                        name = "101 Switching Protocols"
                        weight = 1
                    elif event_type[1] is SWITCH_CONNECT:
                        name = "2xx CONNECT"
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
                 "peer in {}".format(their_state),
                 _STATE_COLOR)

        if role is CLIENT:
            edge(DONE, MIGHT_SWITCH_PROTOCOL,
                 "Request was CONNECT or Upgrade",
                 _STATE_COLOR,
                 italicize=True)

        edge(DONE, MUST_CLOSE, "keep-alive<BR/>disabled", _STATE_COLOR,
             italicize=True)
        edge(DONE, IDLE, "prepare_to_reuse()", _SPECIAL_COLOR,
             italicize=True)

        edges.sort()

        f.write("".join(edges))
        f.write("\n}\n")
