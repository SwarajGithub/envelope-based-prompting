"""Command-line entry point.

Commands:
  smoke   Run the controller on a few clean validation records against the live model (M5).
  run     Run a configured block: each condition and each approved seed (M6).
  report  Summarise every run database under runs/ into runs/reports/summary.csv.

Usage (from src/, venv active, Ollama running, envelope approved):
  python -m fenced_retry.cli smoke --n 3
  python -m fenced_retry.cli run --config configs/train-block-01.toml
  python -m fenced_retry.cli report
"""

from __future__ import annotations

import argparse
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fenced_retry import __version__

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENVELOPE = ROOT / "envelopes" / "receipt-extract-v3.json"
DEFAULT_DATA = ROOT.parent / "data" / "cord"


def clean_records(records: list, limit: int) -> list:
    """First `limit` records whose reference values all appear in the input text (D-040)."""
    from fenced_retry.validator.checks import check_output

    out = []
    for rec in records:
        if not check_output(rec.reference, rec.input_text):
            out.append(rec)
            if len(out) == limit:
                break
    return out


def settings_from_envelope(env: dict[str, Any], usage_db: Path, store_full_text: bool,
                           stage_temperatures: dict[str, float] | None = None):
    from fenced_retry.llm.client import LLMSettings

    th = env["theta"]
    return LLMSettings(
        model=th["model"]["name"],
        endpoint=th["model"]["endpoint"],
        num_ctx=th["num_ctx"],
        num_predict=th["num_predict"],
        temperature=th["temperature"],
        seed=th["seeds"][0],
        think=th["think_enabled"],
        call_timeout_s=th["call_timeout_s"],
        expected_digest=th["model"]["digest"],
        max_total_tokens=th["max_total_tokens"],
        max_wall_seconds=th["max_wall_seconds"],
        store_full_text=store_full_text,
        usage_db=usage_db,
        stage_temperatures=tuple(sorted((stage_temperatures or {}).items())),
    )


def cmd_smoke(args: argparse.Namespace) -> int:
    from fenced_retry.adapters.cord import load_split
    from fenced_retry.controller.batch import run_batch
    from fenced_retry.envelope.model import load_envelope
    from fenced_retry.llm.client import LLMClient
    from fenced_retry.store.run_db import RunStore

    env = load_envelope(args.envelope)
    records = clean_records(load_split(args.data / "validation", "validation"), args.n)
    if not records:
        print("no clean validation records found; run scripts/audit_references.py first")
        return 1

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"smoke-{stamp}"
    store = RunStore(ROOT / "runs" / f"{run_id}.sqlite")
    client = LLMClient(settings_from_envelope(env, ROOT / "runs" / "usage.sqlite", store_full_text=True))
    try:
        outcomes = run_batch(
            env_path=args.envelope,
            records=[(r.record_id, r.input_text) for r in records],
            caller=client,
            store=store,
            run_id=run_id,
            shadow=args.shadow,
            project=args.project,
        )
        totals = client.tracker.totals()
    finally:
        client.close()
        store.close()

    latencies = []
    print(f"run {run_id}  envelope {env['envelope_id']} {env['version']}  records {len(outcomes)}")
    for out in outcomes:
        print(f"\n{out.record_id}: {out.state} ({out.reason})")
        for a in out.attempts:
            if a.latency_ms is not None:
                latencies.append(a.latency_ms)
            failing = ",".join(sorted(a.failing)) or "-"
            print(f"  attempt {a.index}  plan {a.plan_id}  status {a.status}  failing {failing}  tier {a.tier or '-'}")
    print("\nusage totals (shared file):", totals)
    if latencies:
        print(f"call latency ms: median {statistics.median(latencies):.0f}, max {max(latencies):.0f} (for budgets, D-032)")
    print(f"run database: {ROOT / 'runs' / (run_id + '.sqlite')}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    import dataclasses

    from fenced_retry.adapters.cord import load_split
    from fenced_retry.controller.batch import run_batch
    from fenced_retry.envelope.model import load_envelope
    from fenced_retry.harness.config import ConfigError, load_config
    from fenced_retry.harness.report import write_run_report
    from fenced_retry.llm.client import LLMClient
    from fenced_retry.store.run_db import RunStore

    env = load_envelope(args.envelope if args.envelope else DEFAULT_ENVELOPE)
    try:
        cfg = load_config(args.config, envelope_seeds=env["theta"]["seeds"])
    except ConfigError as exc:
        print("CONFIG ERROR:", exc)
        return 1
    base = ROOT / cfg.runs_dir
    reports = base / "reports"
    from fenced_retry.llm.sampling import SamplingError, load_sampling

    try:
        stage_temps = load_sampling(ROOT / "configs" / "sampling.toml")
    except (SamplingError, OSError) as exc:
        print("SAMPLING CONFIG ERROR:", exc)
        return 1
    print("temperatures: executor", env["theta"]["temperature"], "(envelope)", stage_temps or "(defaults)")
    from fenced_retry.assurance.breaker import make_fleet_check

    th = env["theta"]
    fleet_check = make_fleet_check(w_d=th["W_d"], phi=th["phi"], phi_s=th["phi_s"], gamma=th["gamma"])
    records = load_split(cfg.data / cfg.split, cfg.split)
    clean = clean_records(records, len(records))
    block = clean[cfg.offset: cfg.offset + cfg.limit]
    print(f"block: {cfg.split} records {cfg.offset}..{cfg.offset + len(block) - 1} ({len(block)} clean)")

    for condition in cfg.conditions:
        for seed in cfg.seeds:
            run_id = f"{cfg.name}-{cfg.split}-{cfg.offset:04d}-{len(block)}-{condition}-s{seed}"
            store = RunStore(base / f"{run_id}.sqlite")
            settings = dataclasses.replace(
                settings_from_envelope(env, base / "usage.sqlite", cfg.store_full_text, stage_temps), seed=seed
            )
            client = LLMClient(settings)
            try:
                run_batch(
                    env_path=args.envelope if args.envelope else DEFAULT_ENVELOPE,
                    records=[(r.record_id, r.input_text) for r in block],
                    caller=client,
                    store=store,
                    run_id=run_id,
                    condition=condition,
                    shadow=cfg.shadow,
                    project=cfg.project,
                    breaker=fleet_check,
                )
            except Exception as exc:  # a crash leaves the run resumable (M6)
                print(f"STOPPED {run_id}: {exc!r}. Re-run the same command to resume.")
                return 1
            finally:
                client.close()
                store.close()
            summary = write_run_report(base / f"{run_id}.sqlite", reports)
            rate = summary["accept_rate"]
            print(f"{run_id}: accepted {summary['accepted']}/{summary['records']} "
                  f"(rate {rate:.3f}), calls {summary['calls']}")
    return 0


def _assurance_config() -> dict:
    import tomllib

    with (ROOT / "configs" / "assurance.toml").open("rb") as fh:
        return tomllib.load(fh)


def _open_run(run_id: str):
    from fenced_retry.store.run_db import RunStore

    path = ROOT / "runs" / f"{run_id}.sqlite"
    if not path.exists():
        print(f"no run database: {path}")
        return None
    return RunStore(path)


def cmd_assure(args: argparse.Namespace) -> int:
    from fenced_retry.assurance.sampler import plan_samples
    from fenced_retry.assurance.stats import required_clusters, required_independent
    from fenced_retry.envelope.model import load_envelope

    a = _assurance_config()["sampling"]
    store = _open_run(args.run)
    if store is None:
        return 1
    th = load_envelope(DEFAULT_ENVELOPE)["theta"]
    c = a["c"]
    print(f"clusters per block c={c} (rule needs c >= {required_clusters(a['alpha'], a['eps_c'])}), j={a['j']}")
    n_min = required_independent(a["alpha"], a["eps"])
    samples = plan_samples(store, args.run, w_a=th["W_a"], c=c, j=a["j"], n_min=n_min, include_tail=True)
    store.close()
    print(f"{len(samples)} samples in {len({s.block_index for s in samples})} blocks "
          f"(W_a={th['W_a']}, final partial block included, D-060)")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    from fenced_retry.adapters.cord import load_split
    from fenced_retry.assurance.review import review_session

    store = _open_run(args.run)
    if store is None:
        return 1
    records = {r.record_id: r for r in load_split(args.data, args.data.name)}
    n = review_session(store, args.run, records)
    store.close()
    print(f"{n} verdicts recorded")
    return 0


def cmd_judge(args: argparse.Namespace) -> int:
    from fenced_retry.adapters.cord import load_split
    from fenced_retry.assurance.judge import judge_samples

    store = _open_run(args.run)
    if store is None:
        return 1
    records = {r.record_id: r for r in load_split(args.data, args.data.name)}
    counts = judge_samples(store, args.run, records)
    store.close()
    print(f"reference verdicts: {counts['correct']} correct, {counts['incorrect']} incorrect, "
          f"{counts['no_reference']} without reference")
    return 0


def cmd_metrics(args: argparse.Namespace) -> int:
    import json

    from fenced_retry.harness.metrics import compute

    path = ROOT / "runs" / f"{args.run}.sqlite"
    if not path.exists():
        print(f"no run database: {path}")
        return 1
    out = compute(path)
    dest = ROOT / "runs" / "reports" / f"{args.run}_metrics.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    print(f"written: {dest}")
    return 0


def cmd_breaker(args: argparse.Namespace) -> int:
    from fenced_retry.assurance.breaker import evaluate
    from fenced_retry.envelope.model import load_envelope as _load

    th = _load(DEFAULT_ENVELOPE)["theta"]
    store = _open_run(args.run)
    if store is None:
        return 1
    st = evaluate(store, args.run, w_d=th["W_d"], phi=th["phi"], phi_s=th["phi_s"], gamma=th["gamma"])
    store.add_breaker_event(args.run, "check", "; ".join(st.reasons) or "closed")
    store.close()
    frac = "n/a" if st.dl_fraction is None else f"{st.dl_fraction:.3f}"
    lcb = "n/a" if st.silent_lcb is None else f"{st.silent_lcb:.3f}"
    print(f"dead-letter window: {st.dl_dead}/{st.dl_window} (fraction {frac}, full={st.dl_full}, "
          f"armed={st.dl_armed})")
    print(f"silent failures: {st.silent_k}/{st.silent_n} reviewed (LCB {lcb}, armed={st.silent_armed})")
    print("state: " + ("OPEN, batch paused: " + "; ".join(st.reasons) if st.open else "closed"))
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    store = _open_run(args.run)
    if store is None:
        return 1
    store.add_breaker_event(args.run, "decision", args.note)
    store.close()
    print(f"decision recorded for {args.run}; the batch can resume")
    return 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    """Analytic phi for the dead-letter arm (paper §9.4). Writes a report; does not edit the envelope."""
    import json

    from fenced_retry.assurance.stats import phi_from_null
    from fenced_retry.envelope.model import load_envelope
    from fenced_retry.store.run_db import RunStore

    th = load_envelope(DEFAULT_ENVELOPE)["theta"]
    runs = sorted((ROOT / "runs").glob(f"{args.prefix}*.sqlite"))
    dead = total = 0
    for path in runs:
        st = RunStore(path)
        rows = st.terminal_states_in_order(path.stem)
        total += len(rows)
        dead += sum(1 for _, s in rows if s == "DEAD_LETTER")
        st.close()
    observed = dead / total if total else None
    p0 = args.p0 if args.p0 is not None else observed
    if p0 is None:
        print("no terminal records found and no --p0 given")
        return 1
    k, phi = phi_from_null(th["W_d"], p0, th["alpha"])
    out = {"runs": [p.stem for p in runs], "terminal_records": total, "dead_letters": dead,
           "observed_rate": observed, "p0": p0, "W_d": th["W_d"], "alpha": th["alpha"],
           "k": k, "phi": phi}
    dest = ROOT / "runs" / "reports" / "calibration_dead_letter.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"p0={p0:.4f} W_d={th['W_d']} alpha={th['alpha']} -> k={k}, phi={phi:.3f}")
    print(f"written: {dest} (not applied: phi is set only by a new approved envelope version, D-046)")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from fenced_retry.harness.report import summarize_all

    base = ROOT / "runs"
    rows = summarize_all(base, base / "reports")
    for r in rows:
        rate = f"{r['accept_rate']:.3f}" if r["accept_rate"] is not None else "n/a"
        print(f"{r['run_id']}: records {r['records']}, accept rate {rate}, calls {r['calls']}")
    print(f"written: {base / 'reports' / 'summary.csv'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fenced-retry", description=f"fenced-retry {__version__}")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    smoke = sub.add_parser("smoke", help="run a few clean validation records against the live model")
    smoke.add_argument("--n", type=int, default=3)
    smoke.add_argument("--envelope", type=Path, default=DEFAULT_ENVELOPE)
    smoke.add_argument("--data", type=Path, default=DEFAULT_DATA)
    smoke.add_argument("--shadow", action="store_true", help="mark outcomes as shadow (not for use)")
    smoke.add_argument("--project", default="pilot", help="project name recorded in the usage file")
    smoke.set_defaults(func=cmd_smoke)
    run = sub.add_parser("run", help="run a configured block (conditions x seeds)")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--envelope", type=Path, default=None)
    run.set_defaults(func=cmd_run)
    rep = sub.add_parser("report", help="summarise all run databases")
    rep.set_defaults(func=cmd_report)
    asr = sub.add_parser("assure", help="draw the assurance sample for a run (M7)")
    asr.add_argument("--run", required=True)
    asr.set_defaults(func=cmd_assure)
    rev = sub.add_parser("review", help="review sampled accepted outputs against reference fields (M7)")
    rev.add_argument("--run", required=True)
    rev.add_argument("--data", type=Path, default=DEFAULT_DATA / "train",
                     help="split folder holding gt/ and images (default data/cord/train)")
    rev.set_defaults(func=cmd_review)
    jdg = sub.add_parser("judge", help="automatic verdict per sample against the CORD reference (D-061)")
    jdg.add_argument("--run", required=True)
    jdg.add_argument("--data", type=Path, default=DEFAULT_DATA / "train")
    jdg.set_defaults(func=cmd_judge)
    met = sub.add_parser("metrics", help="M8 metrics for one run (paper §9.3, §9.5)")
    met.add_argument("--run", required=True)
    met.set_defaults(func=cmd_metrics)
    brk = sub.add_parser("breaker", help="evaluate the fleet breaker for a run (M7)")
    brk.add_argument("--run", required=True)
    brk.set_defaults(func=cmd_breaker)
    dec = sub.add_parser("decide", help="record a human decision that lets a paused batch resume (M7)")
    dec.add_argument("--run", required=True)
    dec.add_argument("--note", required=True)
    dec.set_defaults(func=cmd_decide)
    cal = sub.add_parser("calibrate", help="analytic dead-letter threshold phi from train runs (M7)")
    cal.add_argument("--prefix", default="pilot-train-0000-100-controller-s0",
                     help="run id prefix to pool. The default is one run: the nine block-1 runs "
                          "share the same 100 records, so pooling them would count each receipt nine times.")
    cal.add_argument("--p0", type=float, default=None, help="nominal dead-letter rate (default: observed)")
    cal.set_defaults(func=cmd_calibrate)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
