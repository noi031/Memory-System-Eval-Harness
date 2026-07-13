#!/usr/bin/env python3
"""Runtime metrics client for pulling Prometheus metrics from EchoAgent/EchoMem.

This module provides a black-box interface to collect runtime metrics from
EchoAgent and EchoMem via their /metrics Prometheus endpoints.

Usage:
    from runtime_metrics_client import RuntimeMetricsClient

    client = RuntimeMetricsClient(
        echoagent_url="http://127.0.0.1:31020",
        echomem_url="http://127.0.0.1:8010"
    )
    metrics = client.fetch_metrics()
    turn_metrics = client.extract_turn_metrics(metrics)
"""
from __future__ import annotations

import re
import time
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


# ---------------------------------------------------------------------------
# Prometheus text format parser
# ---------------------------------------------------------------------------

def parse_prometheus_text(text: str) -> dict[str, Any]:
    """Parse Prometheus text format into a structured dict.

    Returns:
        {
            "metric_name": {
                "type": "counter" | "gauge" | "histogram" | "summary",
                "samples": [
                    {"labels": {"label1": "value1", ...}, "value": 123.0},
                    ...
                ],
                # For histograms:
                "buckets": {"le": value, ...},
                "sum": float,
                "count": int,
            },
            ...
        }
    """
    result: dict[str, Any] = {}
    current_metric: str | None = None
    current_type: str | None = None

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            # Parse TYPE and HELP comments
            if line.startswith("# TYPE "):
                parts = line.split()
                if len(parts) >= 4:
                    metric_name = parts[2]
                    metric_type = parts[3]
                    current_metric = metric_name
                    current_type = metric_type
                    if metric_name not in result:
                        result[metric_name] = {"type": metric_type, "samples": []}
            elif line.startswith("# HELP "):
                parts = line.split(None, 3)
                if len(parts) >= 3:
                    metric_name = parts[2]
                    if metric_name not in result:
                        result[metric_name] = {"type": "unknown", "samples": []}
            continue

        # Parse metric line: metric_name{labels} value or metric_name value
        match = re.match(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)\{([^}]*)\}\s+([^\s]+)$', line)
        if match:
            metric_name = match.group(1)
            labels_str = match.group(2)
            value_str = match.group(3)
        else:
            match2 = re.match(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)\s+([^\s]+)$', line)
            if match2:
                metric_name = match2.group(1)
                labels_str = ""
                value_str = match2.group(2)
            else:
                continue

        # Parse labels
        labels: dict[str, str] = {}
        if labels_str:
            for label_match in re.finditer(r'([a-zA-Z_][a-zA-Z0-9_]*)="([^"]*)"', labels_str):
                labels[label_match.group(1)] = label_match.group(2)

        # Parse value
        try:
            value = float(value_str)
        except ValueError:
            continue

        # Store sample
        if metric_name not in result:
            result[metric_name] = {"type": "unknown", "samples": []}

        sample = {"labels": labels, "value": value}
        result[metric_name]["samples"].append(sample)

        # For histograms, extract bucket/sum/count
        if result[metric_name].get("type") == "histogram":
            if "le" in labels:
                # Bucket sample
                if "buckets" not in result[metric_name]:
                    result[metric_name]["buckets"] = {}
                le_value = labels["le"]
                result[metric_name]["buckets"][le_value] = value
            elif "_sum" in metric_name or "sum" in labels:
                result[metric_name]["sum"] = value
            elif "_count" in metric_name or "count" in labels:
                result[metric_name]["count"] = int(value)

    return result


def histogram_quantile(metrics: dict[str, Any], metric_name: str, quantile: float,
                       labels_filter: dict[str, str] | None = None) -> float | None:
    """Compute quantile from histogram buckets.

    Args:
        metrics: Parsed metrics dict
        metric_name: Base metric name (without _bucket suffix)
        quantile: Quantile to compute (0.0 - 1.0)
        labels_filter: Optional labels to filter by

    Returns:
        Quantile value or None if not available
    """
    bucket_name = f"{metric_name}_bucket"
    if bucket_name not in metrics:
        return None

    histogram = metrics[bucket_name]
    buckets = histogram.get("buckets", {})

    if not buckets:
        return None

    # Filter by labels if provided
    if labels_filter:
        # Re-parse with label filtering
        filtered_buckets = {}
        for sample in histogram.get("samples", []):
            sample_labels = sample.get("labels", {})
            if all(sample_labels.get(k) == v for k, v in labels_filter.items()):
                le = sample_labels.get("le")
                if le:
                    filtered_buckets[le] = sample["value"]
        buckets = filtered_buckets

    # Find total count
    total_count = buckets.get("+Inf", 0)
    if total_count == 0:
        return None

    target = quantile * total_count

    # Sort buckets by le value
    sorted_buckets = sorted(
        [(float(le) if le != "+Inf" else float("inf"), count) for le, count in buckets.items()],
        key=lambda x: x[0]
    )

    # Find bucket containing quantile
    cumulative = 0.0
    prev_bound = 0.0
    prev_count = 0.0

    for bound, count in sorted_buckets:
        if cumulative >= target:
            # Interpolate within bucket
            if cumulative == prev_count:
                return prev_bound
            fraction = (target - prev_count) / (cumulative - prev_count)
            return prev_bound + fraction * (bound - prev_bound)
        prev_bound = bound
        prev_count = cumulative
        cumulative = count

    return sorted_buckets[-1][0] if sorted_buckets else None


def histogram_sum(metrics: dict[str, Any], metric_name: str,
                  labels_filter: dict[str, str] | None = None) -> float:
    """Get sum from histogram.

    Args:
        metrics: Parsed metrics dict
        metric_name: Base metric name
        labels_filter: Optional labels to filter by

    Returns:
        Sum value or 0 if not available
    """
    sum_name = f"{metric_name}_sum"
    if sum_name not in metrics:
        return 0.0

    for sample in metrics[sum_name].get("samples", []):
        if labels_filter:
            sample_labels = sample.get("labels", {})
            if all(sample_labels.get(k) == v for k, v in labels_filter.items()):
                return sample["value"]
        else:
            return sample["value"]

    return 0.0


def histogram_count(metrics: dict[str, Any], metric_name: str,
                    labels_filter: dict[str, str] | None = None) -> int:
    """Get count from histogram.

    Args:
        metrics: Parsed metrics dict
        metric_name: Base metric name
        labels_filter: Optional labels to filter by

    Returns:
        Count value or 0 if not available
    """
    count_name = f"{metric_name}_count"
    if count_name not in metrics:
        return 0

    for sample in metrics[count_name].get("samples", []):
        if labels_filter:
            sample_labels = sample.get("labels", {})
            if all(sample_labels.get(k) == v for k, v in labels_filter.items()):
                return int(sample["value"])
        else:
            return int(sample["value"])

    return 0


def histogram_mean(metrics: dict[str, Any], metric_name: str,
                   labels_filter: dict[str, str] | None = None) -> float:
    """Compute mean from histogram sum/count.

    Args:
        metrics: Parsed metrics dict
        metric_name: Base metric name
        labels_filter: Optional labels to filter by

    Returns:
        Mean value or 0 if not available
    """
    total_sum = histogram_sum(metrics, metric_name, labels_filter)
    total_count = histogram_count(metrics, metric_name, labels_filter)
    if total_count == 0:
        return 0.0
    return total_sum / total_count


def counter_value(metrics: dict[str, Any], metric_name: str,
                  labels_filter: dict[str, str] | None = None) -> float:
    """Get counter value.

    Args:
        metrics: Parsed metrics dict
        metric_name: Metric name
        labels_filter: Optional labels to filter by

    Returns:
        Counter value or 0 if not available
    """
    if metric_name not in metrics:
        return 0.0

    for sample in metrics[metric_name].get("samples", []):
        if labels_filter:
            sample_labels = sample.get("labels", {})
            if all(sample_labels.get(k) == v for k, v in labels_filter.items()):
                return sample["value"]
        else:
            return sample["value"]

    return 0.0


# ---------------------------------------------------------------------------
# RuntimeMetricsClient
# ---------------------------------------------------------------------------

class RuntimeMetricsClient:
    """Client for fetching runtime metrics from EchoAgent and EchoMem."""

    def __init__(
        self,
        echoagent_url: str = "http://127.0.0.1:31020",
        echomem_url: str = "http://127.0.0.1:8010",
        timeout: int = 10,
    ):
        """Initialize the metrics client.

        Args:
            echoagent_url: EchoAgent backend URL
            echomem_url: EchoMem service URL
            timeout: Request timeout in seconds
        """
        self.echoagent_metrics_url = f"{echoagent_url.rstrip('/')}/metrics"
        self.echomem_metrics_url = f"{echomem_url.rstrip('/')}/metrics"
        self.timeout = timeout

    def _fetch_prometheus(self, url: str) -> dict[str, Any]:
        """Fetch and parse Prometheus metrics from URL.

        Args:
            url: Prometheus /metrics endpoint URL

        Returns:
            Parsed metrics dict
        """
        try:
            req = Request(url)
            with urlopen(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8", "replace")
            return parse_prometheus_text(text)
        except HTTPError as exc:
            return {"error": f"HTTP {exc.code}: {exc.reason}", "samples": []}
        except URLError as exc:
            return {"error": f"URL error: {exc.reason}", "samples": []}
        except Exception as exc:
            return {"error": str(exc), "samples": []}

    def fetch_metrics(self) -> dict[str, Any]:
        """Fetch metrics from both EchoAgent and EchoMem.

        Returns:
            {
                "echoagent": {...parsed metrics...},
                "echomem": {...parsed metrics...},
                "timestamp": float,
                "errors": {"echoagent": str | None, "echomem": str | None}
            }
        """
        timestamp = time.time()
        errors: dict[str, str | None] = {"echoagent": None, "echomem": None}

        echoagent_metrics = self._fetch_prometheus(self.echoagent_metrics_url)
        if "error" in echoagent_metrics and echoagent_metrics.get("samples") == []:
            errors["echoagent"] = echoagent_metrics.get("error")

        echomem_metrics = self._fetch_prometheus(self.echomem_metrics_url)
        if "error" in echomem_metrics and echomem_metrics.get("samples") == []:
            errors["echomem"] = echomem_metrics.get("error")

        return {
            "echoagent": echoagent_metrics,
            "echomem": echomem_metrics,
            "timestamp": timestamp,
            "errors": errors,
        }

    def extract_turn_metrics(
        self,
        metrics: dict[str, Any],
        pipeline: str = "memory_prefetch_prefill",
        status: str = "completed",
    ) -> dict[str, Any]:
        """Extract turn-level metrics snapshot.

        Args:
            metrics: Metrics dict from fetch_metrics()
            pipeline: Pipeline label (memory_prefetch_prefill | baseline)
            status: Status label (completed | aborted | failed)

        Returns:
            Dict with key metrics for a turn
        """
        echoagent = metrics.get("echoagent", {})
        echomem = metrics.get("echomem", {})

        labels_filter = {"pipeline": pipeline, "status": status}

        # TTFT metrics
        ttft_p50 = histogram_quantile(echoagent, "echoagent_turn_ttft_seconds", 0.5, labels_filter)
        ttft_p95 = histogram_quantile(echoagent, "echoagent_turn_ttft_seconds", 0.95, labels_filter)
        ttft_mean = histogram_mean(echoagent, "echoagent_turn_ttft_seconds", labels_filter)

        # Token metrics
        cached_tokens_sum = histogram_sum(echoagent, "echoagent_generate_cached_tokens", labels_filter)
        prompt_tokens_sum = histogram_sum(echoagent, "echoagent_generate_prompt_tokens", labels_filter)

        # Prefill warmup metrics
        prefill_duration_mean = histogram_mean(echoagent, "echoagent_prefill_warmup_duration_seconds")
        prefill_cached_tokens = histogram_sum(echoagent, "echoagent_prefill_warmup_cached_tokens")

        # EchoMem API duration
        echomem_api_duration_p50 = histogram_quantile(
            echoagent, "echoagent_echomem_api_duration_seconds", 0.5
        )
        echomem_api_duration_p95 = histogram_quantile(
            echoagent, "echoagent_echomem_api_duration_seconds", 0.95
        )

        # EchoMem retrieval metrics
        retrieval_duration_p50 = histogram_quantile(echomem, "echomem_retrieval_duration_seconds", 0.5)
        retrieval_duration_p95 = histogram_quantile(echomem, "echomem_retrieval_duration_seconds", 0.95)
        retrieval_count = histogram_count(echomem, "echomem_retrieval_duration_seconds")

        return {
            # TTFT
            "ttft_p50_seconds": ttft_p50,
            "ttft_p95_seconds": ttft_p95,
            "ttft_mean_seconds": ttft_mean,

            # Tokens
            "cached_tokens_sum": cached_tokens_sum,
            "prompt_tokens_sum": prompt_tokens_sum,

            # Prefill
            "prefill_duration_mean_seconds": prefill_duration_mean,
            "prefill_cached_tokens": prefill_cached_tokens,

            # EchoMem API
            "echomem_api_duration_p50_seconds": echomem_api_duration_p50,
            "echomem_api_duration_p95_seconds": echomem_api_duration_p95,

            # EchoMem retrieval
            "retrieval_duration_p50_seconds": retrieval_duration_p50,
            "retrieval_duration_p95_seconds": retrieval_duration_p95,
            "retrieval_count": retrieval_count,
        }

    def diff_metrics(
        self,
        before: dict[str, Any],
        after: dict[str, Any],
        metric_names: list[str] | None = None,
    ) -> dict[str, Any]:
        """Compute delta between two metric snapshots.

        Args:
            before: Earlier metrics snapshot
            after: Later metrics snapshot
            metric_names: Optional list of metric names to diff (default: all histograms)

        Returns:
            Dict with delta values for each metric
        """
        if metric_names is None:
            metric_names = [
                "echoagent_turn_ttft_seconds",
                "echoagent_generate_cached_tokens",
                "echoagent_generate_prompt_tokens",
                "echoagent_prefill_warmup_duration_seconds",
                "echoagent_echomem_api_duration_seconds",
                "echomem_retrieval_duration_seconds",
            ]

        deltas: dict[str, Any] = {}

        for name in metric_names:
            before_sum = histogram_sum(before.get("echoagent", {}), name) or histogram_sum(before.get("echomem", {}), name)
            after_sum = histogram_sum(after.get("echoagent", {}), name) or histogram_sum(after.get("echomem", {}), name)
            before_count = histogram_count(before.get("echoagent", {}), name) or histogram_count(before.get("echomem", {}), name)
            after_count = histogram_count(after.get("echoagent", {}), name) or histogram_count(after.get("echomem", {}), name)

            deltas[name] = {
                "sum_delta": after_sum - before_sum,
                "count_delta": after_count - before_count,
            }

        return deltas

    def get_counter_delta(
        self,
        before: dict[str, Any],
        after: dict[str, Any],
        metric_name: str,
        labels_filter: dict[str, str] | None = None,
    ) -> float:
        """Get delta for a counter metric.

        Args:
            before: Earlier metrics snapshot
            after: Later metrics snapshot
            metric_name: Counter metric name
            labels_filter: Optional labels to filter by

        Returns:
            Delta value
        """
        before_val = counter_value(before.get("echoagent", {}), metric_name, labels_filter) or \
                     counter_value(before.get("echomem", {}), metric_name, labels_filter)
        after_val = counter_value(after.get("echoagent", {}), metric_name, labels_filter) or \
                    counter_value(after.get("echomem", {}), metric_name, labels_filter)
        return after_val - before_val


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

def format_metrics_summary(metrics: dict[str, Any]) -> str:
    """Format metrics dict as human-readable summary.

    Args:
        metrics: Metrics dict from fetch_metrics()

    Returns:
        Formatted string summary
    """
    lines = []
    lines.append(f"Timestamp: {metrics.get('timestamp', 'N/A')}")

    errors = metrics.get("errors", {})
    if errors.get("echoagent"):
        lines.append(f"EchoAgent error: {errors['echoagent']}")
    if errors.get("echomem"):
        lines.append(f"EchoMem error: {errors['echomem']}")

    echoagent = metrics.get("echoagent", {})
    if echoagent and not errors.get("echoagent"):
        lines.append("\n=== EchoAgent Metrics ===")
        lines.append(f"  TTFT p50: {histogram_quantile(echoagent, 'echoagent_turn_ttft_seconds', 0.5) or 'N/A'}s")
        lines.append(f"  TTFT p95: {histogram_quantile(echoagent, 'echoagent_turn_ttft_seconds', 0.95) or 'N/A'}s")
        lines.append(f"  Cached tokens: {histogram_sum(echoagent, 'echoagent_generate_cached_tokens') or 0}")
        lines.append(f"  Prompt tokens: {histogram_sum(echoagent, 'echoagent_generate_prompt_tokens') or 0}")

    echomem = metrics.get("echomem", {})
    if echomem and not errors.get("echomem"):
        lines.append("\n=== EchoMem Metrics ===")
        lines.append(f"  Retrieval p50: {histogram_quantile(echomem, 'echomem_retrieval_duration_seconds', 0.5) or 'N/A'}s")
        lines.append(f"  Retrieval p95: {histogram_quantile(echomem, 'echomem_retrieval_duration_seconds', 0.95) or 'N/A'}s")
        lines.append(f"  Retrieval count: {histogram_count(echomem, 'echomem_retrieval_duration_seconds') or 0}")

    return "\n".join(lines)


if __name__ == "__main__":
    # Quick test
    import argparse

    parser = argparse.ArgumentParser(description="Fetch and display runtime metrics")
    parser.add_argument("--echoagent-url", default="http://127.0.0.1:31020")
    parser.add_argument("--echomem-url", default="http://127.0.0.1:8010")
    args = parser.parse_args()

    client = RuntimeMetricsClient(args.echoagent_url, args.echomem_url)
    metrics = client.fetch_metrics()
    print(format_metrics_summary(metrics))
