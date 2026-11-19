#!/usr/bin/env python3
"""Tables and the report figure for the lossy-path experiments (run_all.sh 4-6).

    analysis/loss.py results/<run> [results/<run> ...]                     # Markdown tables
    analysis/loss.py --fig out.pdf results/<exp4> results/<exp6> [...]     # also the figure

With --fig, all ceiling-sweep runs given (repeats, clean and lossy halves of experiment 6)
are pooled into one.

Reads runs.csv (analysis/parse_results.py) of runs made with NETEM_PROFILES. Values are
medians over completed downloads, with interquartile ranges; ACK reduction and ACK-path
times are relative to fixed:2 on the same path and server load. Kinds of run:
  threshold sweep  every policy is fixed:N (experiment 4)
  ceiling sweep    fixed:2 plus adaptive:N with modifiers, e.g. adaptive:16+net (experiment 6)
  policy comparison  anything else (experiment 5)

Figure: goodput against the threshold, one panel per policy, each with a line for the clean
and one for the lossy path: (a) fixed:T on an idle server (experiment 4); (b)-(f) adaptive
with no modifier, +phase, +delay, +net and all of them under CPU load, against its ceiling C
(experiment 6; under this load adaptive sits at C unless a modifier caps T).
"""
import argparse
import csv
import statistics
from pathlib import Path

MB = 1024 * 1024
LOAD_NAMES = {"0": "none"}

# Panels (b)-(f): the modifier sets of adaptive, in order, with their labels.
FAMILIES = {
    "adaptive": "adaptive",
    "adaptive+phase": "adaptive+phase",
    "adaptive+delay": "adaptive+delay",
    "adaptive+net": "adaptive+net",
    "adaptive+net+phase+reorder+delay": "adaptive+all",
}


def num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def med_iqr(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None, None, None
    if len(values) < 3:
        return statistics.median(values), None, None
    q = statistics.quantiles(values, n=4, method="inclusive")
    return statistics.median(values), q[0], q[2]


def med(values):
    return med_iqr(values)[0]


def fmt(value, spec=".0f"):
    return "–" if value is None else format(value, spec)


def loss_of(netem):
    """'100mbit:10ms:0.1' -> 0.1 (percent)."""
    parts = netem.split(":")
    return float(parts[2]) if len(parts) > 2 else 0.0


def acks_per_mb(r):
    frames, bytes_ = num(r["server_recv_ack_frames"]), num(r["bytes"])
    return frames / (bytes_ / MB) if frames and bytes_ else None


def split_spec(spec):
    """'adaptive:16+net' -> ('adaptive+net', 16); 'fixed:8' -> ('fixed', 8); 'litequic' -> (.., None)."""
    base, _, modifiers = spec.partition("+")
    name, _, n = base.partition(":")
    family = name + ("+" + modifiers if modifiers else "")
    return family, int(n) if n else None


class Run:
    def __init__(self, *outs):
        self.outs = [Path(o) for o in outs]
        self.name = " + ".join(o.name for o in self.outs)
        rows = [r for o in self.outs for r in csv.DictReader(open(o / "runs.csv"))]
        self.rows = [r for r in rows if r["ok"] == "1"]
        self.failed = [r for r in rows if r["ok"] != "1"]
        self.specs = list(dict.fromkeys(r["spec"] for r in rows))
        self.loads = list(dict.fromkeys(r["server_load"] for r in rows))
        self.paths = sorted(dict.fromkeys(r["netem"] for r in rows), key=loss_of)
        parsed = {s: split_spec(s) for s in self.specs}
        if all(f == "fixed" and n for f, n in parsed.values()):
            self.kind = "threshold sweep"
        elif all(s == "fixed:2" or (f.startswith("adaptive") and n) for s, (f, n) in parsed.items()):
            self.kind = "ceiling sweep"
        else:
            self.kind = "policy comparison"
        if self.kind != "policy comparison":
            families = list(dict.fromkeys(f for f, _ in parsed.values()))
            self.specs.sort(key=lambda s: (families.index(parsed[s][0]), parsed[s][1]))
        # family -> [(N, spec)] for the sweeps
        self.lines = {}
        for s in self.specs:
            family, n = parsed[s]
            if s != "fixed:2" or self.kind == "threshold sweep":
                self.lines.setdefault(family, []).append((n, s))

    def select(self, spec, load, netem, failed=False):
        return [r for r in (self.failed if failed else self.rows)
                if r["spec"] == spec and r["server_load"] == load and r["netem"] == netem]

    def metric(self, spec, load, netem, key):
        fn = acks_per_mb if key == "acks_per_mb" else (lambda r: num(r[key]))
        return [fn(r) for r in self.select(spec, load, netem)]

    def reduction(self, spec, load, netem):
        """ACK frames per MiB removed relative to fixed:2 (%), median and IQR."""
        base = med(self.metric("fixed:2", load, netem, "acks_per_mb"))
        if not base:
            return None, None, None
        values = [100 * (1 - v / base) for v in self.metric(spec, load, netem, "acks_per_mb")
                  if v is not None]
        return med_iqr(values)


def print_tables(run):
    print(f"Run `{run.name}` ({run.kind}): {len(run.rows)} completed downloads, "
          f"{len(run.failed)} stalled.\n")
    print("Medians [IQR]; ACK reduction and ACK-path times relative to `fixed:2` on the same "
          "path and load.\n")
    print("| Load | Path | Policy | n | Stalled | Goodput Mb/s | p10 Mb/s | Time s | ACKs/MiB "
          "| ACK reduction | Mean T | Server ACK µs/MiB | vs fixed:2 | Client ACK µs/MiB "
          "| vs fixed:2 | Lost pkts | Congestion events |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for load in run.loads:
        for netem in run.paths:
            b_srv = med(run.metric("fixed:2", load, netem, "server_recv_us_per_mb"))
            b_cli = med(run.metric("fixed:2", load, netem, "client_send_us_per_mb"))
            for spec in run.specs:
                rs = run.select(spec, load, netem)
                stalled = len(run.select(spec, load, netem, failed=True))
                if not rs and not stalled:
                    continue
                g, g1, g3 = med_iqr(run.metric(spec, load, netem, "goodput_mbps"))
                srv = med(run.metric(spec, load, netem, "server_recv_us_per_mb"))
                cli = med(run.metric(spec, load, netem, "client_send_us_per_mb"))
                iqr = f" [{fmt(g1)}–{fmt(g3)}]" if g1 is not None else ""
                print(f"| {LOAD_NAMES.get(load, load)} | {loss_of(netem):g}% loss | `{spec}` "
                      f"| {len(rs)} | {stalled} | {fmt(g, '.1f')}{iqr} "
                      f"| {fmt(med(run.metric(spec, load, netem, 'tput_p10_mbps')), '.1f')} "
                      f"| {fmt(med(run.metric(spec, load, netem, 'download_s')), '.1f')} "
                      f"| {fmt(med(run.metric(spec, load, netem, 'acks_per_mb')), '.1f')} "
                      f"| {fmt(run.reduction(spec, load, netem)[0])}% "
                      f"| {fmt(med(run.metric(spec, load, netem, 'threshold_mean')))} "
                      f"| {fmt(srv)} | {fmt(srv / b_srv if srv and b_srv else None, '.2f')} "
                      f"| {fmt(cli)} | {fmt(cli / b_cli if cli and b_cli else None, '.2f')} "
                      f"| {fmt(med(run.metric(spec, load, netem, 'server_lost_pkts')))} "
                      f"| {fmt(med(run.metric(spec, load, netem, 'server_congestion_events')))} |")
    print()


def errorbar_line(ax, xs, stats, **style):
    """Median line with IQR whiskers."""
    pts = [(x, m, q1, q3) for x, (m, q1, q3) in zip(xs, stats) if m is not None]
    if not pts:
        return
    ax.errorbar([p[0] for p in pts], [p[1] for p in pts],
                yerr=[[p[1] - p[2] if p[2] is not None else 0 for p in pts],
                      [p[3] - p[1] if p[3] is not None else 0 for p in pts]],
                markersize=5, lw=1.6, capsize=2.2, elinewidth=0.9,
                markeredgecolor="white", markeredgewidth=0.7, zorder=3, **style)


# The two paths, styled as in the report's earlier fixed-threshold figure: clean, lossy.
PATH_STYLE = [("#222222", "o"), ("#6f8fa6", "s")]


def path_style(netem):
    return PATH_STYLE[0] if loss_of(netem) == 0 else PATH_STYLE[1]


def figure(sweep, ceiling, path):
    """One panel per policy: goodput against the threshold, a line per path (0% / 0.1% loss).
    (a) fixed:T on an idle server (threshold sweep); (b)-(f) adaptive with each modifier set
    under CPU load, against its ceiling C (ceiling sweep)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    plt.rcParams.update({
        "font.family": "serif", "font.serif": ["Nimbus Roman", "Times New Roman", "Times"],
        "mathtext.fontset": "stix", "font.size": 10, "axes.labelsize": 10,
        "axes.titlesize": 10, "xtick.labelsize": 9, "ytick.labelsize": 9, "legend.fontsize": 9,
        "axes.linewidth": 0.8, "xtick.direction": "in", "ytick.direction": "in",
        "pdf.fonttype": 42,
    })
    panels = [("fixed", sweep, "fixed:$T$, idle server", "ACK threshold $T$ (packets)")]
    load = LOAD_NAMES.get(ceiling.loads[0], ceiling.loads[0])
    for family, name in FAMILIES.items():
        if family in ceiling.lines:
            panels.append((family, ceiling, f"{name}, {load} CPU load",
                           "Threshold ceiling $C$ (packets)"))
    cols = 3
    rows = -(-len(panels) // cols)
    fig, axes = plt.subplots(rows, cols, figsize=(8.6, 2.3 * rows), sharey=True,
                             gridspec_kw={"wspace": 0.12, "hspace": 0.6})
    axes = axes.flatten()
    for i, (ax, (family, run, title, xlabel)) in enumerate(zip(axes, panels)):
        points = run.lines[family]
        xs = [n for n, _ in points]
        for netem in run.paths:
            color, marker = path_style(netem)
            stats = [med_iqr(run.metric(s, run.loads[0], netem, "goodput_mbps"))
                     for _, s in points]
            errorbar_line(ax, xs, stats, color=color, marker=marker)
        ax.set_xscale("log", base=2)
        ax.set_xticks(xs)
        ax.set_xticklabels([str(x) for x in xs])
        ax.minorticks_off()
        ax.set_xlim(xs[0] / 1.25, xs[-1] * 1.25)
        ax.set_xlabel(xlabel)
        ax.set_ylim(0, 105)
        if i % cols == 0:
            ax.set_ylabel("Goodput (Mb/s)")
        ax.set_title(title, pad=4)
        ax.grid(axis="y", linestyle=":", color="#b0b0b0", lw=0.7, zorder=0)
        ax.set_axisbelow(True)
        ax.tick_params(top=True, right=True)
        ax.text(0.5, -0.33, f"({'abcdef'[i]})", transform=ax.transAxes, ha="center",
                va="top", fontsize=10)
    for ax in axes[len(panels):]:
        ax.set_visible(False)

    paths = sorted(set(sweep.paths) | set(ceiling.paths), key=loss_of)
    handles = [Line2D([], [], color=path_style(n)[0], marker=path_style(n)[1], lw=1.6,
                      markersize=5, markeredgecolor="white", markeredgewidth=0.7,
                      label=f"{loss_of(n):g}% loss") for n in paths]
    fig.legend(handles=handles, loc="upper center", ncol=len(handles), frameon=False,
               bbox_to_anchor=(0.5, 1.0), columnspacing=1.6, handlelength=2.2)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight", pad_inches=0.02)
    print(f"wrote {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("runs", nargs="+")
    parser.add_argument("--fig", help="write the report figure (needs a threshold sweep and a "
                        "ceiling sweep)")
    args = parser.parse_args()
    runs = [Run(r) for r in args.runs]
    for run in runs:
        print_tables(run)
    if args.fig:
        sweeps = [r for r in runs if r.kind == "threshold sweep"]
        ceilings = [r for r in runs if r.kind == "ceiling sweep"]
        if not sweeps or not ceilings:
            parser.error("--fig needs a threshold-sweep run and a ceiling-sweep run")
        pooled = Run(*[o for r in ceilings for o in r.outs]) if len(ceilings) > 1 else ceilings[0]
        pooled.paths = sorted(dict.fromkeys(r["netem"] for r in pooled.rows), key=loss_of)
        figure(sweeps[0], pooled, args.fig)


if __name__ == "__main__":
    main()
