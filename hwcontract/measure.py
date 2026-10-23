"""Contract-driven measurement of a single-line 0/1 capture, for any protocol.

Each timing edge says what it times: `measure: high` (a high run), `low`, or
`period` (rising edge to rising edge). `after: EDGE` limits an edge to the run
right after that edge, so a stretched low after a 1-bit counts against T1L
however long it is. Edges named T0H/T1H/T0L/T1L/RESET need no annotation:
T0H/T1H are highs, T0L/T1L the lows after them, RESET a low.

Every run is explained by the contract or reported. A run goes to the edge
whose window holds it (the nearest typ if several do). A run outside every
window is unmatched when it is more than TOLERANCE times outside every window
of its measure: nothing in the contract accounts for it. Otherwise it is
blamed on the nearest edge allowed after the previous run, so a stretched low
is a violation of its own bit's low. Only width decides unmatched; `after`
only decides who is blamed.

A run cut off by either end of the capture has an unknown true width that is at
least what was seen. It counts only for an open-ended edge (max: null, such as
a reset gap), only once it reaches that edge's min, and only when the capture
holds no complete run of that edge; otherwise it is ignored. The idle a capture
starts in then stands in for a reset when there is no other, but never passes
for a good gap between frames when the real gaps are short.

Out-of-window pulses that continue an unmatched spread of widths (each within
CLUSTER_RATIO of the next) join it, so one stall population is one unmatched
row instead of a few violations on each edge plus an unmatched row split off at
TOLERANCE. An edge never gives up its last pulses this way.

Each pulse is located by its start time. When the contract has a frame
delimiter (an open-ended edge longer than every bounded edge of its measure,
like RESET), pulses also carry the frame number and how many highs of that
frame came before them; for an LED strip that is the bit index.
"""
from bisect import bisect_left

from hwcontract.judge import out_of_window
from hwcontract.sigrok_adapter import distribution, runs

TOLERANCE = 2.0
CLUSTER_RATIO = 1.5                 # widths further apart than this are separate problems
_CLASSIC = {"T0H": ("high", None), "T1H": ("high", None),
            "T0L": ("low", "T0H"), "T1L": ("low", "T1H"), "RESET": ("low", None)}


def measures(contract):
    """{edge: (measure, after)}, or ValueError naming every edge that does not
    say what it measures."""
    names = {e["name"] for e in contract["edges"]}
    out, missing = {}, []
    for e in contract["edges"]:
        if "measure" in e:
            out[e["name"]] = (e["measure"], e.get("after"))
        elif e["name"] in _CLASSIC:
            measure, after = _CLASSIC[e["name"]]
            out[e["name"]] = (measure, after if after in names else None)
        else:
            missing.append(e["name"])
    if missing:
        raise ValueError(f"edges {', '.join(missing)} of {contract['contract']!r} do not say "
                         "what they measure on a 0/1 capture: give each one measure: high, "
                         "low or period, or judge observations JSON instead")
    return out


def _distance(w, edge):
    """1 inside the window, else how many times too short or too long."""
    lo, hi = edge["min"], edge.get("max")
    if w < lo:
        return lo / w if w > 0 else float("inf")
    if hi is not None and w > hi:
        return w / hi
    return 1.0


def _assign(w, candidates, same_measure, edges):
    inside = [n for n in candidates if _distance(w, edges[n]) == 1.0]
    if inside:
        typ = lambda n: max(w, edges[n]["typ"]) / max(min(w, edges[n]["typ"]), 1e-9)
        return min(inside, key=typ)
    if min(_distance(w, edges[n]) for n in same_measure) > TOLERANCE:
        return None                                  # unmatched: no window anywhere near
    return min(candidates, key=lambda n: _distance(w, edges[n]), default=None)


def _joins(w, widths):
    """True when w is within CLUSTER_RATIO of any of the sorted widths."""
    i = bisect_left(widths, w)
    return any(max(w, u) <= CLUSTER_RATIO * min(w, u) for u in widths[max(i - 1, 0):i + 1])


def _absorb(pulses, unmatched, spec, edges, dt_ns):
    """Move out-of-window pulses that continue an unmatched spread into it,
    repeating until nothing more joins. An edge keeps its pulses when it would
    lose all of them: then it is the edge that is wrong, not a few outliers."""
    for kind, stray in unmatched.items():
        names = [n for n, (m, _) in spec.items() if m == kind]
        moved = True
        while moved:
            moved = False
            widths = sorted(p["width_ns"] for p in stray)
            for n in names:
                out = [p for p in pulses[n] if out_of_window(edges[n], p["width_ns"], dt_ns)
                       and _joins(p["width_ns"], widths)]
                if out and len(out) < len(pulses[n]):
                    gone = {id(p) for p in out}
                    pulses[n][:] = [p for p in pulses[n] if id(p) not in gone]
                    stray.extend(out)
                    moved = True


def _delimiters(spec, edges):
    """Open-ended edges longer than every bounded edge of the same measure."""
    out = set()
    for name, (measure, _) in spec.items():
        bounded = [edges[o]["max"] for o, (m, _) in spec.items()
                   if m == measure and edges[o].get("max") is not None]
        if edges[name].get("max") is None and bounded and edges[name]["min"] > max(bounded):
            out.add(name)
    return out


def measure(contract, samples, dt_ns):
    """(observations, pulses, unmatched). observations are what the judge takes;
    pulses maps each edge to its located pulses (cut_off ones not counted when
    the edge has whole ones), unmatched each measure to the
    located runs that no edge explains. A period that would span a frame
    delimiter is not measured: the gap is its own edge, not a long period."""
    spec = measures(contract)
    edges = {e["name"]: e for e in contract["edges"]}
    delimiters = _delimiters(spec, edges)
    by_measure = {m: [n for n, (mn, _) in spec.items() if mn == m] for m, _ in spec.values()}
    periods = by_measure.get("period", [])
    pulses = {name: [] for name in spec}
    unmatched = {m: [] for m, _ in spec.values()}
    t, frame, highs, prev, rise = 0.0, 0, 0, None, None

    def at(width, start, where=None):
        p = {"width_ns": round(width), "t_ns": round(start)}
        if delimiters:
            p["frame"], p["highs_before"] = where or (frame, highs)
        return p

    all_runs = runs(samples, dt_ns)
    for i, (level, w) in enumerate(all_runs):
        kind = "high" if level else "low"
        candidates = [n for n, (m, after) in spec.items()
                      if m == kind and (after is None or prev is None or after == prev)]
        name = _assign(w, candidates, by_measure[kind], edges) if candidates else None
        cut_off = i in (0, len(all_runs) - 1)
        if cut_off and (name is None or edges[name].get("max") is not None
                        or w < edges[name]["min"]):
            name = None                              # true width unknown, at least w
        elif candidates:
            p = at(w, t)
            if cut_off:
                p["cut_off"] = True                  # seen only in part
            (pulses[name] if name else unmatched[kind]).append(p)
        if level and i > 0:                          # a rising edge
            if periods and rise is not None:
                start, where = rise
                p = _assign(t - start, periods, periods, edges)
                (pulses[p] if p else unmatched["period"]).append(at(t - start, start, where))
            rise = (t, (frame, highs))
        if name in delimiters:
            frame, highs = (frame + 1 if highs else frame), 0
            rise = None                              # a period never spans a frame gap
        highs += level
        prev = name
        t += w

    _absorb(pulses, unmatched, spec, edges, dt_ns)
    observations = []
    for name, ps in pulses.items():
        # a partial run counts only when nothing better; it stays listed, flagged
        counted = [p for p in ps if not p.get("cut_off")] or ps
        if counted:
            d = distribution([p["width_ns"] for p in counted])
            d.update(name=name, unit="ns", value=d["p50"], resolution_ns=round(dt_ns, 3))
            observations.append(d)
    return observations, pulses, {m: ps for m, ps in unmatched.items() if ps}
