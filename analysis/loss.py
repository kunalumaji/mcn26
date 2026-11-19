#!/usr/bin/env python3
"""Tables and the report figure for the lossy-path experiments (run_all.sh 4-6).

    analysis/loss.py results/<run> [results/<run> ...]                     # Markdown tables
    analysis/loss.py --fig out.pdf results/<exp4> results/<exp6> [...]     # also the figure

With --fig, several ceiling-sweep runs (repeats of experiment 6) are pooled into one.

Reads runs.csv (analysis/parse_results.py) of runs made with NETEM_PROFILES. Values are
medians over completed downloads, with interquartile ranges; ACK reduction and ACK-path
times are relative to fixed:2 on the same path and server load. Kinds of run:
  threshold sweep  every policy is fixed:N (experiment 4)
  ceiling sweep    fixed:2 plus adaptive:N with modifiers, e.g. adaptive:16+net (experiment 6)
  policy comparison  anything else (experiment 5)

Figure: (a) goodput vs fixed threshold on the clean and the lossy path, idle server
(experiment 4); (b) goodput and (c) ACK reduction vs adaptive's ceiling, one line per
modifier, on the lossy path under CPU load (experiment 6).
"""
import argparse
import csv
import statistics
from pathlib import Path

MB = 1024 * 1024
LOAD_NAMES = {"0": "none"}

# Lines of panels (b)-(c): colour, marker, label. Colours as in the report's other figures
# where the policy appears there (adaptive orange, adaptive+all vermillion, fixed:2 blue).
LINES = {
    "adaptive": ("#e69f00", "o", "adaptive"),
    "adaptive+phase": ("#56b4e9", "^", "adaptive+phase"),
    "adaptive+delay": ("#009e73", "v", "adaptive+delay"),
    "adaptive+net": ("#cc79a7", "s", "adaptive+net"),
    "adaptive+net+phase+reorder+delay": ("#d55e00", "D", "adaptive+all"),
}
BASELINE_COLOR = "#2878b5"


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


def errorbar_line(ax, xs, stats, offset=1.0, **style):
    """Median line with IQR whiskers; xs on a log axis, shifted by a factor to keep
    overlapping whiskers apart."""
    pts = [(x * offset, m, q1, q3) for x, (m, q1, q3) in zip(xs, stats) if m is not None]
    ax.errorbar([p[0] for p in pts], [p[1] for p in pts],
                yerr=[[p[1] - p[2] if p[2] is not None else 0 for p in pts],
                      [p[3] - p[1] if p[3] is not None else 0 for p in pts]],
                markersize=5.5, lw=1.6, capsize=2.2, elinewidth=0.9,
                markeredgecolor="white", markeredgewidth=0.7, zorder=3, **style)


def threshold_axis(ax, ts, label):
    ax.set_xscale("log", base=2)
    ax.set_xticks(ts)
    ax.set_xticklabels([str(t) for t in ts])
    ax.minorticks_off()
    ax.set_xlim(ts[0] / 1.25, ts[-1] * 1.25)
    ax.set_xlabel(label)


def figure(sweep, ceiling, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    plt.rcParams.update({
        "font.family": "serif", "font.serif": ["Nimbus Roman", "Times New Roman", "Times"],
        "mathtext.fontset": "stix", "font.size": 10, "axes.labelsize": 10,
        "xtick.labelsize": 9, "ytick.labelsize": 9, "legend.fontsize": 9,
        "axes.linewidth": 0.8, "xtick.direction": "in", "ytick.direction": "in",
        "pdf.fonttype": 42,
    })
    fig, axes = plt.subplots(1, 3, figsize=(8.6, 3.0), gridspec_kw={"wspace": 0.34})

    # (a) Fixed-threshold sweep on the clean and the lossy path (idle server).
    ax = axes[0]
    load = sweep.loads[0]
    (_, points), = sweep.lines.items()
    ts = [n for n, _ in points]
    for (color, marker), netem in zip([("#222222", "o"), ("#6f8fa6", "s")], sweep.paths):
        errorbar_line(ax, ts, [med_iqr(sweep.metric(s, load, netem, "goodput_mbps"))
                               for _, s in points],
                      color=color, marker=marker, label=f"{loss_of(netem):g}% loss")
    threshold_axis(ax, ts, "ACK threshold $T$ (packets)")
    ax.set_ylabel("Goodput (Mb/s)")
    ax.set_ylim(0, 105)
    ax.legend(loc="lower left", frameon=False, title="fixed:$T$, idle server", title_fontsize=9)

    # (b), (c) Ceiling sweep of adaptive with each modifier (lossy path, CPU load).
    load = ceiling.loads[0]
    netem = ceiling.paths[-1]
    families = [f for f in LINES if f in ceiling.lines]
    offsets = [1 + 0.035 * (i - (len(families) - 1) / 2) for i in range(len(families))]
    base = med_iqr(ceiling.metric("fixed:2", load, netem, "goodput_mbps"))
    for ax, key in [(axes[1], "goodput"), (axes[2], "reduction")]:
        for family, offset in zip(families, offsets):
            color, marker, label = LINES[family]
            points = ceiling.lines[family]
            if key == "goodput":
                stats = [med_iqr(ceiling.metric(s, load, netem, "goodput_mbps")) for _, s in points]
            else:
                stats = [ceiling.reduction(s, load, netem) for _, s in points]
            errorbar_line(ax, [n for n, _ in points], stats, offset=offset,
                          color=color, marker=marker, label=label)
        cs = sorted({n for pts in ceiling.lines.values() for n, _ in pts})
        threshold_axis(ax, cs, "Ceiling $C$ of adaptive (packets)")
        if key == "goodput":
            ax.axhline(base[0], color=BASELINE_COLOR, lw=1.3, ls="--", zorder=2)
            ax.set_ylabel("Goodput (Mb/s)")
            ax.set_ylim(0, 105)
        else:
            ax.axhline(0, color=BASELINE_COLOR, lw=1.3, ls="--", zorder=2)
            ax.set_ylabel("ACK reduction vs fixed:2 (%)")
            ax.set_ylim(-10, 100)
        # Condition label in the empty corner: top right for goodput (falls with C),
        # top left for the ACK reduction (rises with C).
        ax.text(0.97 if key == "goodput" else 0.04, 0.96,
                f"{loss_of(netem):g}% loss, {LOAD_NAMES.get(load, load)} CPU load",
                transform=ax.transAxes, ha="right" if key == "goodput" else "left", va="top",
                fontsize=9)

    for ax, label in zip(axes, "abc"):
        ax.grid(axis="y", linestyle=":", color="#b0b0b0", lw=0.7, zorder=0)
        ax.set_axisbelow(True)
        ax.tick_params(top=True, right=True)
        ax.text(0.5, -0.27, f"({label})", transform=ax.transAxes, ha="center", va="top",
                fontsize=10)

    handles = [Line2D([], [], color=LINES[f][0], marker=LINES[f][1], lw=1.6, markersize=5.5,
                      markeredgecolor="white", markeredgewidth=0.7, label=LINES[f][2])
               for f in families]
    handles.append(Line2D([], [], color=BASELINE_COLOR, lw=1.3, ls="--",
                          label="fixed:2 (baseline)"))
    fig.legend(handles=handles, loc="upper center", ncol=len(handles), frameon=False,
               bbox_to_anchor=(0.5, 1.06), columnspacing=1.0, handlelength=2.0,
               handletextpad=0.5)
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
        figure(sweeps[0], pooled, args.fig)


if __name__ == "__main__":
    main()
