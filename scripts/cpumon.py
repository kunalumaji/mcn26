#!/usr/bin/env python3
"""Sample CPU utilisation to CSV until interrupted (SIGINT/SIGTERM).

    scripts/cpumon.py --out cpu.csv [--interval 0.2] [--proc quicfile]

Columns:
  ts             Unix time (s)
  sys_cpu_pct    whole-machine utilisation, 0-100 (all cores)
  proc_cpu_pct   CPU of processes named --proc, in % of one core (can exceed 100)
  proc_count     number of such processes
  loadavg1       1-minute load average
"""
import argparse
import os
import signal
import time

TICKS = os.sysconf("SC_CLK_TCK")


def system_ticks():
    with open("/proc/stat") as f:
        values = [int(v) for v in f.readline().split()[1:9]]
    total = sum(values)
    return total - values[3] - values[4], total  # busy (minus idle, iowait), total


def process_ticks(name):
    ticks, count = 0, 0
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/comm") as f:
                if f.read().strip() != name:
                    continue
            with open(f"/proc/{pid}/stat") as f:
                fields = f.read().rsplit(")", 1)[1].split()
            ticks += int(fields[11]) + int(fields[12])  # utime + stime
            count += 1
        except (FileNotFoundError, ProcessLookupError, IndexError):
            continue
    return ticks, count


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--interval", type=float, default=0.2)
    parser.add_argument("--proc", default="quicfile")
    args = parser.parse_args()

    running = True

    def stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    with open(args.out, "w", buffering=1) as out:
        out.write("ts,sys_cpu_pct,proc_cpu_pct,proc_count,loadavg1\n")
        busy0, total0 = system_ticks()
        proc0, _ = process_ticks(args.proc)
        t0 = time.time()
        while running:
            time.sleep(args.interval)
            busy1, total1 = system_ticks()
            proc1, count = process_ticks(args.proc)
            t1 = time.time()
            sys_pct = 100.0 * (busy1 - busy0) / max(total1 - total0, 1)
            # A process that exited makes the delta negative; report 0 then.
            proc_pct = max(0.0, 100.0 * (proc1 - proc0) / TICKS / (t1 - t0))
            out.write(f"{t1:.3f},{sys_pct:.1f},{proc_pct:.1f},{count},"
                      f"{os.getloadavg()[0]:.2f}\n")
            busy0, total0, proc0, t0 = busy1, total1, proc1, t1


if __name__ == "__main__":
    main()
