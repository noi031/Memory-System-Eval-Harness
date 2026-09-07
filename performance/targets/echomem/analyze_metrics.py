"""从 EchoMem 压测 metrics_samples.csv 提取关键内部指标分位数（只读分析）。"""
import csv, json, sys, collections

def load_final(rows):
    """按 (metric, labels) 分组，取最大 ts 的样本（histogram 累积计数取最终态）。"""
    final = {}
    for r in rows:
        key = (r['metric'], r['labels'])
        final[key] = float(r['value'])
    return final

def percentile(buckets, total, p):
    """buckets: sorted [(le, count)]；le 为 '+Inf' 时视为极大。线性插值。"""
    if total <= 0:
        return None
    target = total * p
    cum = 0.0
    prev_le = 0.0
    prev_cum = 0.0
    for le, c in buckets:
        le_v = float('inf') if le == '+Inf' else float(le)
        if cum + c >= target:
            if c <= 0 or le_v == float('inf'):
                return le_v
            frac = (target - cum) / c
            return prev_le + (le_v - prev_le) * frac
        prev_le, prev_cum = le_v, cum + c
        cum += c
    return None

def metric_stats(final, metric):
    """汇总指定 metric 的直方图：按非 le 的 labels 分组分别算。返回 [(labels_str, count, mean, p50, p95, p99)]。"""
    groups = collections.defaultdict(dict)   # label_key -> {le: count}
    sums = collections.defaultdict(float)
    counts = collections.defaultdict(int)
    for (m, labels_json), v in final.items():
        if not m.startswith(metric):
            continue
        labels = json.loads(labels_json)
        if m.endswith('_bucket'):
            le = labels.pop('le', '')
            lk = json.dumps(labels, sort_keys=True)
            groups[lk][le] = v
        elif m.endswith('_sum'):
            lk = json.dumps(labels, sort_keys=True)
            sums[lk] = v
        elif m.endswith('_count'):
            lk = json.dumps(labels, sort_keys=True)
            counts[lk] = v
    out = []
    for lk, buckets in groups.items():
        total = counts.get(lk, 0)
        s = sums.get(lk, 0.0)
        mean = (s / total) if total else None
        b = sorted(buckets.items(), key=lambda kv: (float('inf') if kv[0]=='+Inf' else float(kv[0])))
        out.append((lk, total, mean, percentile(b, total, 0.5), percentile(b, total, 0.95), percentile(b, total, 0.99)))
    out.sort(key=lambda x: -x[1])
    return out

def main(cases):
    base = r'E:/personal files/Projects/Huawei/Echo/Memory-System-Eval-Harness/performance/targets/echomem/results/1788701530/4U8G'
    metrics = [
        'echomem_session_commit_duration_seconds',
        'echomem_recall_duration_seconds',
        'echomem_memrouter_planning_duration_seconds',
        'echomem_engine_model_duration_seconds',
        'echomem_engine_model_ttfb_seconds',
        'echomem_provider_budget_wait_seconds',
        'echomem_memrouter_recall_admission_queue_wait_seconds',
        'echomem_lane_exec_seconds',
        'echomem_lane_wait_seconds',
    ]
    for case in cases:
        path = f'{base}/{case}/metrics_samples.csv'
        try:
            rows = list(csv.DictReader(open(path, encoding='utf-8')))
        except FileNotFoundError:
            print(f'== {case}: no csv'); continue
        final = load_final(rows)
        print(f'== {case} (samples={len(rows)})')
        for metric in metrics:
            stats = metric_stats(final, metric)
            if not stats:
                continue
            for lk, total, mean, p50, p95, p99 in stats[:3]:
                if total <= 0:
                    continue
                fmt = lambda v: f'{v:.3f}' if v is not None else '-'
                print(f'  {metric.replace("echomem_","")} | {lk[:70]} | n={total} mean={fmt(mean)} p50={fmt(p50)} p95={fmt(p95)} p99={fmt(p99)}')

if __name__ == '__main__':
    main(sys.argv[1:] or ['baseline','commit-barrier','capacity-8','search-priority-blackbox'])
