"""Feedback guard: every path from S/PDIF in to S/PDIF out, and every mix that reaches its own bus again (a bus whose
mix is patched into a mixer input live on that bus, through any number of hops); refuses changes that add or strengthen one."""
from collections import deque
from dataclasses import dataclass

from . import topology as catalogue
from .properties import PropertyId as P
from .status import LoopPath, PathNode, Refusal, RefusalKind
from .topology import BUS_NAMES, INPUT_NAMES, SourceKind
from .values import BusIndex, InputIndex, MixCell, OutputSlot, Source

_REVERB_BUSES = (14, 15)
_SHOWN_SOURCES = (SourceKind.SPDIF_IN, SourceKind.POST_FX)


@dataclass(frozen=True)
class Accepted:
    pass


class _Graph:
    """Where S/PDIF in can travel in one state: nodes are ('src', code), ('in', i), ('bus', b), ('out', slot)."""

    def __init__(self, state, topology):
        self.topology = topology
        self.state = state
        self.fed = {}  # source code -> the inputs and S/PDIF outs it is patched to, in slot order
        for i in range(44):
            self._patch(OutputSlot(topology.mixer_input_slot(i)), ("in", i))
        for slot in topology.SPDIF_OUT_SLOTS:
            self._patch(OutputSlot(slot), ("out", slot))
        self.classes = {code: topology.classify(Source(code)) for code in self.fed}
        self.post_fx = {}
        for code, cls in self.classes.items():
            if cls.kind is SourceKind.POST_FX:
                self.post_fx.setdefault(int(topology.post_fx_input(code)), []).append(code)
        self.mix_of_bus = {b: topology.bus_mix_source(b) for b in range(14)}
        self.reverb = tuple(topology.REVERB_SOURCES)
        self.starts = [("src", code) for code in topology.SPDIF_IN]
        self.outs = [("out", slot) for slot in topology.SPDIF_OUT_SLOTS]

    def _patch(self, slot, node):
        source = self.state.source(slot)
        if source is not None:
            self.fed.setdefault(source.code, []).append(node)

    def _carries(self, bus, inp):
        cell = MixCell(bus, inp)
        gain, muted = self.state.gain(cell), self.state.muted(cell)
        return gain is not None and not gain.is_silent and not muted

    def successors(self, node):
        kind, n = node
        if kind == "src":
            return self.fed.get(n, [])
        if kind == "in":
            buses = [("bus", b) for b in range(16) if self._carries(b, n)]
            return buses + [("src", code) for code in self.post_fx.get(n, [])]
        if kind == "bus":
            codes = self.reverb if n in _REVERB_BUSES else (self.mix_of_bus[n],)
            return [("src", code) for code in codes]
        return []

    def search(self, starts, step):
        """Breadth-first search: each reached node mapped to the node it was reached from."""
        came_from = {node: None for node in starts}
        queue = deque(starts)
        while queue:
            node = queue.popleft()
            for nxt in step(node):
                if nxt not in came_from:
                    came_from[nxt] = node
                    queue.append(nxt)
        return came_from

    def reach(self):
        """Nodes reachable from S/PDIF in, the reverse hops among them, and the way from each node on to S/PDIF out."""
        forward = self.search(self.starts, self.successors)
        reverse = {}
        for u in forward:
            for v in self.successors(u):
                reverse.setdefault(v, []).append(u)
        backward = self.search([o for o in self.outs if o in forward], lambda v: reverse.get(v, []))
        return forward, reverse, backward

    def edges(self):
        """Every hop (u, v) lying on some path from S/PDIF in to S/PDIF out."""
        _forward, reverse, backward = self.reach()
        return {(u, v) for v in backward for u in reverse.get(v, [])}

    def path(self, nodes):
        hops = []
        for kind, n in nodes:
            if kind == "src" and (nodes[0] == (kind, n) or self.classes[n].kind in _SHOWN_SOURCES):
                hops.append(PathNode(Source(n), self.classes[n].label))
            elif kind == "in":
                hops.append(PathNode(InputIndex(n), INPUT_NAMES[n]))
            elif kind == "bus":
                hops.append(PathNode(BusIndex(n), BUS_NAMES[n]))
            elif kind == "out":
                side = "LR"[list(self.topology.SPDIF_OUT_SLOTS).index(n)]
                hops.append(PathNode(OutputSlot(n), f"S/PDIF out {side}"))
        return LoopPath(tuple(hops))

    @staticmethod
    def walk(came_from, node):
        nodes = []
        while node is not None:
            nodes.append(node)
            node = came_from[node]
        return nodes

    def paths(self):
        found = set()
        for start in self.starts:
            came_from = self.search([start], self.successors)
            for out in self.outs:
                if out in came_from:
                    found.add(self.path(self.walk(came_from, out)[::-1]))
        return frozenset(found) | self.cycle_paths()

    # ---- a mix reaching its own bus again ----
    def cycle_path(self, nodes):
        """nodes: a closed walk starting and ending at the same bus node."""
        hops = []
        for kind, n in nodes:
            if kind == "bus":
                hops.append(PathNode(BusIndex(n), BUS_NAMES[n]))
            elif kind == "src":
                hops.append(PathNode(Source(n), self.classes[n].label))
            elif kind == "in":
                hops.append(PathNode(InputIndex(n), INPUT_NAMES[n]))
        return LoopPath(tuple(hops))

    def cycle_paths(self):
        found = set()
        for b in range(16):
            start = ("bus", b)
            came_from = self.search(self.successors(start), self.successors)
            if start in came_from:
                found.add(self.cycle_path([start] + self.walk(came_from, start)[::-1]))
        return frozenset(found)

    def nodes(self):
        seen = set(("bus", b) for b in range(16))
        queue = deque(seen)
        while queue:
            node = queue.popleft()
            for nxt in self.successors(node):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return seen

    def cycle_edges(self):
        """Every hop (u, v) lying on a cycle: v leads back to u."""
        edges = set()
        for u in self.nodes():
            for v in self.successors(u):
                if u in self.search([v], self.successors):
                    edges.add((u, v))
        return edges

    def cycle_through(self, edge):
        u, v = edge
        came_from = self.search([v], self.successors)
        nodes = [u] + self.walk(came_from, u)[::-1]          # u, v, …, u
        buses = [k for k, node in enumerate(nodes[:-1]) if node[0] == "bus"]
        k = buses[0] if buses else 0
        rotated = nodes[k:-1] + nodes[:k] + [nodes[k]]
        return self.cycle_path(rotated)

    def path_through(self, edge):
        u, v = edge
        forward, _reverse, backward = self.reach()
        return self.path(self.walk(forward, u)[::-1] + self.walk(backward, v))


class SpdifLoopGuard:
    def __init__(self, topology=catalogue):
        self._topology = topology

    def paths(self, state) -> frozenset[LoopPath]:
        return _Graph(state, self._topology).paths()

    def check(self, before, after, writes) -> Accepted | Refusal:
        graph = _Graph(after, self._topology)
        loop_edges = graph.edges()
        cycle_edges = graph.cycle_edges()
        if not loop_edges and not cycle_edges:
            return Accepted()
        previous = _Graph(before, self._topology)
        raised = list(self._raised_cells(before, after, writes))
        added = sorted(loop_edges - previous.edges()) + [edge for edge in raised if edge in loop_edges]
        for edge in added:
            path = graph.path_through(edge)
            return Refusal(RefusalKind.LOOP, path.label(), path)
        new_cycles = sorted(cycle_edges - previous.cycle_edges()) + [edge for edge in raised if edge in cycle_edges]
        for edge in new_cycles:
            path = graph.cycle_through(edge)
            return Refusal(RefusalKind.LOOP, path.label(), path)
        return Accepted()

    @staticmethod
    def _raised_cells(before, after, writes):
        for write in writes:
            if write.address.prop == P.MIX_FADER:
                cell = MixCell(*divmod(write.address.index, 44))
                old, new = before.gain(cell), after.gain(cell)
                if new is not None and new.linear > (old.linear if old else 0.0):
                    yield ("in", int(cell.input)), ("bus", int(cell.bus))
