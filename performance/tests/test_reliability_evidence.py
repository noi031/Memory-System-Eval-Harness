import copy
import json

from performance.targets.echomem.acceptance.reliability_evidence import recovery_counts, observability_counts
from performance.targets.echomem.acceptance.observability_timeline import timeline_counts


def recovery_sample():
    details = {
        "pending-before-kill": {},
        "commit-recovery": {"accepted_202": True, "autonomous_recovery_observed": True},
        "message-reconciliation": {"expected_server_message_ids": ["private-1", "private-2"],
                                   "missing_server_message_ids": []},
        "idempotency-replay": {"same_archive": True},
        "cursor-reconciliation": {}, "order-reconciliation": {},
    }
    return {"status": "PASS", "elapsed_s": 3,
            "checks": [{"name": name, "status": "PASS", "detail": json.dumps(detail)}
                       for name, detail in details.items()]}


def observability_sample():
    return {"status": "PASS", "expected_tenants": ["private-tenant"], "expected_lanes": ["commit"],
            "lane_count": 1, "lane_count_matches_observed": True, "expected_lanes_match_observed": True,
            "sample_count": 3, "missing": [], "invalid": [], "duplicate_rows": [],
            "rows": [{"tenant_id": "private-tenant", "lane": "commit", "queued": 0,
                      "wait_seconds_total": .2, "exec_seconds_total": 1, "rejected_total": 0,
                      "accepted_total": 2, "completed_total": 2, "failed_total": 0,
                      "accepted_delta": 2, "queued_peak_during_load": 1}]}


def test_recovery_keeps_unknown_counts_and_planned_samples_in_denominator():
    incomplete = recovery_sample()
    next(c for c in incomplete["checks"] if c["name"] == "message-reconciliation")["detail"] = '{}'
    public = recovery_counts({"status": "PASS", "expected_samples": 3,
                              "samples": [recovery_sample(), incomplete]})
    assert public["status"] == "INCONCLUSIVE"
    assert public["passed_samples"] == public["inconclusive_samples"] == 1
    assert public["expected_samples"] == 3 and public["unexecuted_samples"] == 1
    assert public["missing_messages"] is None and public["expected_messages"] is None
    assert public["reconciled_samples"] == 1 and public["known_missing_messages"] == 0
    assert "private-" not in json.dumps(public)


def test_failed_sample_overrides_stale_parent_pass():
    failed = recovery_sample()
    failed["checks"][-1]["status"] = "FAIL"
    public = recovery_counts({"status": "PASS", "samples": [recovery_sample(), failed]})
    assert public["status"] == "FAIL" and public["failed_samples"] == 1


def test_missing_required_recovery_checks_do_not_count_as_pass():
    incomplete = recovery_sample()
    incomplete["checks"] = incomplete["checks"][:-2]
    public = recovery_counts(incomplete)
    assert public["status"] == "INCONCLUSIVE"
    assert public["missing_required_checks"] == ["cursor-reconciliation", "order-reconciliation"]
    malformed = recovery_sample()
    malformed["checks"][1]["detail"] = '[1, 2]'
    assert recovery_counts(malformed)["status"] == "INCONCLUSIVE"


def test_missing_missing_ids_field_is_not_zero():
    item = recovery_sample()
    for check in item["checks"]:
        if check["name"] == "message-reconciliation":
            check["detail"] = json.dumps({"expected_server_message_ids": [1, 2]})
    assert recovery_counts(item)["missing_messages"] is None


def test_observability_revalidates_duplicates_and_nonfinite_values():
    value = observability_sample()
    value["rows"].append(copy.deepcopy(value["rows"][0]))
    public = observability_counts(value)
    assert public["status"] == "INCONCLUSIVE" and public["duplicate_cells"] == 1
    assert public["valid_cells"] == 0
    value = observability_sample()
    value["rows"][0]["wait_seconds_total"] = float('nan')
    public = observability_counts(value)
    assert public["invalid_cells"] == 1 and public["valid_cells"] == 0
    assert public["rows"][0]["wait_seconds_total"] is None
    json.dumps(public, allow_nan=False)


def timeline_evidence():
    frames = [observability_sample() for _ in range(4)]
    for frame, at in zip(frames, (8, 11, 13, 16)):
        frame.update(observed_at_s=at, boot_id="private-boot-id")
    return {"expected_tenants": ["private-tenant"], "expected_lanes": ["commit"],
            "before": frames[0], "during": frames[1:3], "after": frames[3],
            "window_start_s": 10, "window_end_s": 15, "max_sampling_gap_s": 3}


def test_timeline_checks_all_frames_without_exporting_private_identifiers():
    evidence = timeline_evidence()
    result = timeline_counts(evidence)
    assert result["status"] == "PASS"
    assert result["passed_snapshots"] == result["snapshot_count"] == 4
    assert result["counter_comparisons"] == 18
    assert result["max_gap_s"] == 2 and result["gaps_exceeded"] == 0
    assert "private-" not in json.dumps(result)


def test_missing_middle_snapshot_cannot_be_hidden_by_complete_final_frame():
    evidence = timeline_evidence()
    evidence["during"][0]["rows"] = []
    evidence["during"][0]["expected_tenants"] = []
    result = timeline_counts(evidence)
    assert result["status"] == "INCONCLUSIVE"
    assert result["snapshots"][1]["expected_cells"] == 1
    assert result["snapshots"][1]["missing_cells"] == 1


def test_queue_can_drop_but_same_process_cumulative_counter_cannot():
    evidence = timeline_evidence()
    evidence["before"]["rows"][0]["queued"] = 3
    assert timeline_counts(evidence)["status"] == "PASS"
    evidence["during"][0]["rows"][0]["rejected_total"] = 2
    result = timeline_counts(evidence)
    assert result["status"] == "FAIL"
    assert result["counter_regressions"][0]["counter"] == "rejected_total"
    assert result["counter_regressions"][0]["classification"] == "same_process"


def test_restart_and_unknown_process_are_separate_from_same_process_regression():
    evidence = timeline_evidence()
    evidence["during"][0]["rows"][0]["rejected_total"] = 2
    evidence["during"][1]["boot_id"] = "private-new-boot"
    evidence["after"]["boot_id"] = "private-new-boot"
    result = timeline_counts(evidence)
    assert result["status"] == "INCONCLUSIVE"
    assert len(result["restart_observations"]) == 1
    assert result["counter_regressions"][0]["classification"] == "restart"
    for frame in [evidence["before"], *evidence["during"], evidence["after"]]:
        frame.pop("boot_id")
    result = timeline_counts(evidence)
    assert result["counter_regressions"][0]["classification"] == "process_identity_unknown"
    assert result["status"] == "INCONCLUSIVE"


def test_gaps_no_during_samples_or_missing_timestamps_prevent_process_pass():
    evidence = timeline_evidence()
    evidence["window_end_s"] = 30
    result = timeline_counts(evidence)
    assert result["status"] == "INCONCLUSIVE" and result["gaps_exceeded"] == 1
    evidence = timeline_evidence()
    evidence["during"] = []
    assert timeline_counts(evidence)["status"] == "INCONCLUSIVE"
    evidence = timeline_evidence()
    evidence["during"][0].pop("observed_at_s")
    result = timeline_counts(evidence)
    assert result["status"] == "INCONCLUSIVE" and result["max_gap_s"] is None


def test_failed_monitor_and_reordered_samples_are_not_valid_coverage():
    evidence = timeline_evidence()
    evidence["monitor_errors"] = ["OSError"]
    result = timeline_counts(evidence)
    assert result["status"] == "INCONCLUSIVE" and result["monitor_failed"]
    evidence = timeline_evidence()
    evidence["during"].reverse()
    assert timeline_counts(evidence)["sampling_times_valid"] is False


def test_legacy_wall_clock_can_measure_internal_gap_but_not_unrecorded_boundaries():
    evidence = timeline_evidence()
    evidence.pop("window_start_s")
    evidence.pop("window_end_s")
    for i,frame in enumerate([evidence["before"], *evidence["during"], evidence["after"]]):
        frame.pop("observed_at_s")
        frame["created_at"] = f"2026-09-08T01:00:{2*i:02d}+00:00"
    result = timeline_counts(evidence)
    assert result["status"] == "INCONCLUSIVE"
    assert result["max_internal_gap_s"] == 2
    assert result["max_gap_s"] is None
    assert result["sampling_clock"] == "wall_clock"


def test_final_snapshot_pass_is_separate_from_overall_timeline_status():
    raw = observability_sample()
    raw["process_observations"] = timeline_evidence()
    raw["process_observations"]["during"][0]["rows"] = []
    public = observability_counts(raw)
    assert public["snapshot_status"] == "PASS"
    assert public["status"] == public["timeline"]["status"] == "INCONCLUSIVE"
