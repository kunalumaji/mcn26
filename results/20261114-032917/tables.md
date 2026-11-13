Run `20261114-032917`: 52 successful downloads, 4 failed.

**ACK work and cost** (pooled over file sizes; ratios relative to `fixed:2` at the same load)

| Server load | Policy | n | ACKs/MB | Data pkts/ACK | Mean T | Server ACK µs/MB | vs fixed:2 | Client ACK µs/MB | vs fixed:2 | Server CPU ms/MB | vs fixed:2 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | `fixed:2` | 2 | 11.8 | 61.7 | 2 | 344 | 1.00 | 169 | 1.00 | 1.74 | 1.00 |
| 0 | `adaptive` | 2 | 12.2 | 59.3 | 2 | 380 | 1.11 | 180 | 1.07 | 1.94 | 1.12 |
| 0 | `adaptive:255` | 2 | 13.0 | 55.8 | 2 | 352 | 1.03 | 200 | 1.19 | 1.75 | 1.01 |
| 0 | `adaptive+net+phase+reorder+delay` | 2 | 12.3 | 59.1 | 2 | 349 | 1.01 | 191 | 1.13 | 1.84 | 1.06 |
| 0.25x | `fixed:2` | 2 | 12.2 | 59.4 | 2 | 325 | 1.00 | 193 | 1.00 | 1.75 | 1.00 |
| 0.25x | `adaptive` | 2 | 12.3 | 59.0 | 2 | 333 | 1.03 | 181 | 0.94 | 1.78 | 1.02 |
| 0.25x | `adaptive:255` | 2 | 12.6 | 57.6 | 2 | 365 | 1.13 | 168 | 0.87 | 1.91 | 1.10 |
| 0.25x | `adaptive+net+phase+reorder+delay` | 2 | 12.1 | 59.9 | 2 | 350 | 1.08 | 202 | 1.05 | 1.78 | 1.02 |
| 0.5x | `fixed:2` | 2 | 13.4 | 54.3 | 2 | 412 | 1.00 | 193 | 1.00 | 2.30 | 1.00 |
| 0.5x | `adaptive` | 2 | 11.9 | 60.9 | 10 | 413 | 1.00 | 184 | 0.95 | 2.29 | 1.00 |
| 0.5x | `adaptive:255` | 2 | 10.0 | 72.4 | 30 | 461 | 1.12 | 175 | 0.91 | 2.62 | 1.14 |
| 0.5x | `adaptive+net+phase+reorder+delay` | 2 | 10.4 | 70.1 | 9 | 412 | 1.00 | 187 | 0.97 | 2.26 | 0.98 |
| 0.75x | `fixed:2` | 2 | 11.0 | 66.0 | 2 | 454 | 1.00 | 195 | 1.00 | 2.61 | 1.00 |
| 0.75x | `adaptive` | 2 | 6.2 | 116.1 | 69 | 460 | 1.01 | 156 | 0.80 | 2.64 | 1.01 |
| 0.75x | `adaptive:255` | 1 | 3.7 | 197.3 | 177 | 624 | 1.37 | 107 | 0.55 | 2.62 | 1.00 |
| 0.75x | `adaptive+net+phase+reorder+delay` | 2 | 7.4 | 98.8 | 61 | 416 | 0.91 | 138 | 0.71 | 2.39 | 0.91 |
| 1x | `fixed:2` | 2 | 12.9 | 56.2 | 2 | 498 | 1.00 | 206 | 1.00 | 2.67 | 1.00 |
| 1x | `adaptive` | 1 | 6.0 | 120.9 | 97 | 693 | 1.39 | 145 | 0.70 | 2.67 | 1.00 |
| 1x | `adaptive:255` | 1 | 3.0 | 237.9 | 249 | 738 | 1.48 | 82 | 0.40 | 2.82 | 1.06 |
| 1x | `adaptive+net+phase+reorder+delay` | 2 | 7.3 | 99.6 | 74 | 454 | 0.91 | 151 | 0.73 | 2.59 | 0.97 |
| 2x | `fixed:2` | 2 | 13.5 | 53.9 | 2 | 1144 | 1.00 | 200 | 1.00 | 5.09 | 1.00 |
| 2x | `adaptive` | 2 | 5.8 | 124.5 | 98 | 1031 | 0.90 | 140 | 0.70 | 4.79 | 0.94 |
| 2x | `adaptive:255` | 2 | 3.0 | 239.0 | 249 | 1006 | 0.88 | 84 | 0.42 | 5.38 | 1.06 |
| 2x | `adaptive+net+phase+reorder+delay` | 2 | 8.9 | 83.1 | 79 | 1044 | 0.91 | 199 | 1.00 | 5.24 | 1.03 |
| 4x | `fixed:2` | 2 | 19.2 | 38.0 | 2 | 1747 | 1.00 | 348 | 1.00 | 6.28 | 1.00 |
| 4x | `adaptive` | 1 | 5.8 | 124.4 | 98 | 1415 | 0.81 | 151 | 0.43 | 5.41 | 0.86 |
| 4x | `adaptive:255` | 2 | 3.0 | 242.5 | 248 | 1100 | 0.63 | 86 | 0.25 | 5.03 | 0.80 |
| 4x | `adaptive+net+phase+reorder+delay` | 2 | 10.4 | 73.0 | 79 | 1376 | 0.79 | 231 | 0.66 | 5.26 | 0.84 |

**Performance** (goodput and 100 ms throughput: 500 MB and 1 GB downloads; download time: all files, relative to `fixed:2` for the same file and load)

| Server load | Policy | Goodput Mbit/s [IQR] | vs fixed:2 | p10 of 100 ms throughput, Mbit/s | Download time vs fixed:2 [IQR] | Lost pkts/MB |
|---|---|---|---|---|---|---|
| 0 | `fixed:2` | 614 [595–633] | 1.00 | 318 | 1.000 [0.970–1.030] | 2.41 |
| 0 | `adaptive` | 645 [643–647] | 1.05 | 576 | 0.948 [0.946–0.951] | 0.77 |
| 0 | `adaptive:255` | 626 [615–637] | 1.02 | 450 | 0.978 [0.961–0.996] | 1.33 |
| 0 | `adaptive+net+phase+reorder+delay` | 643 [641–644] | 1.05 | 570 | 0.952 [0.950–0.955] | 0.56 |
| 0.25x | `fixed:2` | 644 [643–646] | 1.00 | 537 | 1.000 [0.998–1.002] | 0.87 |
| 0.25x | `adaptive` | 646 [645–647] | 1.00 | 542 | 0.997 [0.996–0.998] | 0.73 |
| 0.25x | `adaptive:255` | 649 [648–649] | 1.01 | 598 | 0.994 [0.993–0.994] | 0.92 |
| 0.25x | `adaptive+net+phase+reorder+delay` | 632 [623–641] | 0.98 | 557 | 1.020 [1.005–1.035] | 1.00 |
| 0.5x | `fixed:2` | 609 [589–629] | 1.00 | 566 | 1.000 [0.967–1.033] | 0.58 |
| 0.5x | `adaptive` | 609 [589–629] | 1.00 | 573 | 0.999 [0.966–1.032] | 0.54 |
| 0.5x | `adaptive:255` | 646 [645–647] | 1.06 | 570 | 0.938 [0.937–0.940] | 1.32 |
| 0.5x | `adaptive+net+phase+reorder+delay` | 647 [645–649] | 1.06 | 571 | 0.937 [0.935–0.939] | 1.03 |
| 0.75x | `fixed:2` | 644 [641–646] | 1.00 | 516 | 1.000 [0.996–1.004] | 1.23 |
| 0.75x | `adaptive` | 649 [647–651] | 1.01 | 599 | 0.991 [0.988–0.995] | 0.73 |
| 0.75x | `adaptive:255` | 662 [662–662] | 1.03 | 562 | 0.972 [0.972–0.972] | 1.28 |
| 0.75x | `adaptive+net+phase+reorder+delay` | 650 [650–651] | 1.01 | 602 | 0.989 [0.989–0.990] | 0.42 |
| 1x | `fixed:2` | 639 [636–642] | 1.00 | 554 | 1.000 [0.995–1.005] | 0.18 |
| 1x | `adaptive` | 651 [651–651] | 1.02 | 465 | 0.981 [0.981–0.981] | 0.45 |
| 1x | `adaptive:255` | 663 [663–663] | 1.04 | 322 | 0.963 [0.963–0.963] | 0.85 |
| 1x | `adaptive+net+phase+reorder+delay` | 616 [598–633] | 0.96 | 246 | 1.041 [1.012–1.070] | 1.68 |
| 2x | `fixed:2` | 608 [597–619] | 1.00 | 533 | 1.000 [0.981–1.019] | 0.08 |
| 2x | `adaptive` | 630 [627–633] | 1.04 | 491 | 0.964 [0.959–0.969] | 0.98 |
| 2x | `adaptive:255` | 618 [610–626] | 1.02 | 439 | 0.983 [0.970–0.995] | 1.54 |
| 2x | `adaptive+net+phase+reorder+delay` | 556 [535–578] | 0.92 | 441 | 1.098 [1.055–1.140] | 0.61 |
| 4x | `fixed:2` | 366 [348–385] | 1.00 | 273 | 1.000 [0.950–1.050] | 0.43 |
| 4x | `adaptive` | 421 [421–421] | 1.15 | 239 | 0.860 [0.860–0.860] | 1.41 |
| 4x | `adaptive:255` | 472 [469–476] | 1.29 | 247 | 0.768 [0.762–0.774] | 0.78 |
| 4x | `adaptive+net+phase+reorder+delay` | 449 [430–468] | 1.23 | 265 | 0.812 [0.778–0.846] | 1.00 |

Median download time (s), server load 0:

| Policy | 500MB |
|---|---|
| `fixed:2` | 6.86 |
| `adaptive` | 6.51 |
| `adaptive:255` | 6.71 |
| `adaptive+net+phase+reorder+delay` | 6.53 |

Median download time (s), server load 0.25x:

| Policy | 500MB |
|---|---|
| `fixed:2` | 6.51 |
| `adaptive` | 6.50 |
| `adaptive:255` | 6.47 |
| `adaptive+net+phase+reorder+delay` | 6.65 |

Median download time (s), server load 0.5x:

| Policy | 500MB |
|---|---|
| `fixed:2` | 6.93 |
| `adaptive` | 6.92 |
| `adaptive:255` | 6.50 |
| `adaptive+net+phase+reorder+delay` | 6.49 |

Median download time (s), server load 0.75x:

| Policy | 500MB |
|---|---|
| `fixed:2` | 6.52 |
| `adaptive` | 6.47 |
| `adaptive:255` | 6.34 |
| `adaptive+net+phase+reorder+delay` | 6.46 |

Median download time (s), server load 1x:

| Policy | 500MB |
|---|---|
| `fixed:2` | 6.57 |
| `adaptive` | 6.45 |
| `adaptive:255` | 6.33 |
| `adaptive+net+phase+reorder+delay` | 6.84 |

Median download time (s), server load 2x:

| Policy | 500MB |
|---|---|
| `fixed:2` | 6.92 |
| `adaptive` | 6.67 |
| `adaptive:255` | 6.80 |
| `adaptive+net+phase+reorder+delay` | 7.59 |

Median download time (s), server load 4x:

| Policy | 500MB |
|---|---|
| `fixed:2` | 11.59 |
| `adaptive` | 9.97 |
| `adaptive:255` | 8.90 |
| `adaptive+net+phase+reorder+delay` | 9.41 |

