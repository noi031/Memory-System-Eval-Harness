"""CLI defaults for the current EchoMem observation entry point."""

from performance.targets.echomem.observation_run import build_parser


def test_observation_defaults_to_first_three_metrics() -> None:
    args = build_parser().parse_args(["--profiles", "profile.json", "--out-dir", "results"])
    assert args.metrics == "M1,M2,M3"
    assert args.scenarios == ""
    assert args.probes == ""


def test_full_run_explicitly_selects_all_six_metrics() -> None:
    args = build_parser().parse_args(
        ["--profiles", "profile.json", "--out-dir", "results", "--metrics", "M1,M2,M3,M4,M5,M6"]
    )
    assert args.metrics == "M1,M2,M3,M4,M5,M6"
