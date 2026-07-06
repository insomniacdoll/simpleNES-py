"""Scheduler / end-to-end benchmarks."""


def test_bench_run_frame(benchmark, nes_machine):
    """End-to-end: measure one full frame via Scheduler.run_frame()."""
    benchmark(nes_machine.run_frame)
