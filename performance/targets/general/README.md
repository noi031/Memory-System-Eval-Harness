# targets/general（通用被测系统）

不针对具体记忆系统（echomem / openviking 等）的临时压测统一放这里，入口：

```bash
python run.py --target general run      --scene scenes/<name>.py   [--profile ...]
python run.py --target general validate --scene scenes/<name>.py
python run.py --target general probe    --scene probes/<name>.py   [--profile ...]
python run.py --target general list
```

- `scenes/` — 压测场景（Python 文件，导出 `task` / `tasks`，契约见 `performance/docs/设计意图.md` §4）
- `probes/` — 一次性行为探针（Python 文件，导出 `run(ctx)`，契约见设计意图 §13）
- `profiles/` — 画像 YAML（可选）
- `results/` — 运行结果（默认按场景位置推导，gitignore 已忽略）
