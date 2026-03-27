# BLB Shared Memory IPC Contract

## Overview

Two named shared memory regions connect Sierra Chart (C++) and
the Python live scorer. C++ writes tick data, Python reads it,
scores it, and writes the signal back.

Both processes run on the same machine under the same user.

---

## Tick Ring Buffer

**Name:** `Local\BLB_TickRing`
**Size:** 16 (header) + 1024 x 184 (slots) = 188,432 bytes
**Creator:** BLB_ConsolidationTickCollector.cpp (on first run)
**Writer:** C++ (tick collector)
**Reader:** Python (live scorer)

### Layout

```
Offset 0x0000: Header (16 bytes)
  uint64_t  write_index       // atomically incremented after each slot write
  uint64_t  reserved          // alignment padding

Offset 0x0010: Slot[0] (184 bytes)
Offset 0x00C8: Slot[1] (184 bytes)
...
Offset 0x2DFF0: Slot[1023] (184 bytes)
```

### Slot Structure (184 bytes)

```
double  TradePrice            //  0: last trade price
double  TradeSize             //  8: last trade volume
double  PriceSpeed_pts_sec    // 16: points per second velocity
double  PriceDelta_5tick      // 24: net price change over 5 ticks
double  PriceDelta_20tick     // 32: net price change over 20 ticks
double  RotationSize_A        // 40: rotation size (short window)
double  RotationSize_B        // 48: rotation size (long window)
double  DirReversals_20tick   // 56: direction reversals in 20 ticks
double  AbsorbLevel           // 64: price level being absorbed (0=none)
double  AbsorbVolHit          // 72: volume that hit absorb level
double  AbsorbOrigSize        // 80: original resting size
double  AbsorbCurSize         // 88: current remaining size
double  DOMImbalance          // 96: bid-ask imbalance
double  TotalBidDepth         //104: sum of all bid depth
double  TotalAskDepth         //112: sum of all ask depth
double  Bid1Size              //120: best bid size
double  Ask1Size              //128: best ask size
double  Spread                //136: current bid-ask spread
double  CumDelta              //144: cumulative delta
double  TimeSinceLast_ms      //152: ms since previous tick
uint64_t TickIndex            //160: sequential tick counter
uint64_t TimestampMs          //168: epoch milliseconds
double  reserved              //176: future use
```

### Write Protocol (C++ side)

1. Compute slot index: `write_index % 1024`
2. Write all fields to slot
3. Memory fence (`_ReadWriteBarrier()` or `MemoryBarrier()`)
4. Atomically increment write_index: `InterlockedIncrement64(&header->write_index)`

### Read Protocol (Python side)

1. Track local `read_index` (starts at 0, or at current `write_index` on connect)
2. Poll: if `read_index < write_index`, read slot at `read_index % 1024`
3. Increment local `read_index`
4. If `write_index - read_index > 900`, log warning (falling behind)
5. If `write_index - read_index > 1024`, slots were overwritten — reset `read_index = write_index - 512`

---

## Signal Block

**Name:** `Local\BLB_Signal`
**Size:** 48 bytes
**Creator:** BLB_ConsolidationTickCollector.cpp (created alongside tick ring)
**Writer:** Python (live scorer)
**Reader:** C++ (signal reader study)

### Layout

```
Offset 0x00: float   consolidation_prob   // 0.0-1.0, from predict_proba class 1
Offset 0x04: float   rotation_size_mean   // current rotation size (points)
Offset 0x08: float   breakout_score       // 0.0-1.0, from predict_proba class 2
Offset 0x0C: uint32_t ready              // 0 = warming up, 1 = scoring active
Offset 0x10: uint64_t tick_index         // last tick_index that was scored
Offset 0x18: uint64_t timestamp_ms       // when Python last wrote this
Offset 0x20: float   reserved_1          // future use (Phase 6 strategy signals)
Offset 0x24: float   reserved_2          // future use
Offset 0x28: uint64_t reserved_3         // future use
```

### Write Protocol (Python side)

1. Write all fields except `tick_index`
2. Write `tick_index` last (acts as implicit sequence number)
3. Set `ready = 1` once warm-up is complete

### Read Protocol (C++ side)

1. Read `tick_index` — if unchanged since last read, signal hasn't updated
2. Staleness check: if `sc.Index - signal.tick_index > 100`, signal is stale
3. Time staleness: if `current_time_ms - signal.timestamp_ms > 500`, signal is stale
4. When stale, set `sg_Stale = 1.0` and use fallback behavior

---

## Fallback: File-Based IPC

If shared memory creation fails (permissions, etc.), both sides
fall back to memory-mapped files on disk:

- `C:\SierraChart\SierraChartInstance_2\Data\blb_tick_ring.bin`
- `C:\SierraChart\SierraChartInstance_2\Data\blb_signal.bin`

Same layout, same protocols. Only difference: backed by a file
instead of the system pagefile.

---

## Notes

- `Local\` prefix scopes to the current user session (no admin needed)
- All multi-byte fields are little-endian (x86 native)
- No locks — single writer per region, atomics for sequencing
- Python uses `ctypes` + `mmap` to access named shared memory on Windows
