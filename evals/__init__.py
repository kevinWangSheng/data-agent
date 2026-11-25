"""Evaluation pipeline: sampling, scoring, comparison and reports (spec seam C).

`evals/manifest.json` is the committed dev/holdout split (ADR-0004), produced from a
data-eng-bench checkout at commit a3278ad102829a6084dde086244a0ef665a8011c with:

    python -m evals sample --tasks-dir data-eng-bench/tasks \
        --dev-list data-eng-bench/configs/fast-30.txt --size 30 --seed 20261015 \
        --out evals/manifest.json

LiveSQLBench's `lsb-` ids sit in the same `dev` and `holdout` lists after data-eng-bench's, with
`livesqlbench_seed` and `livesqlbench_source` (ticket 128), appended by

    python -m evals livesqlbench split --public <livesqlbench_data.jsonl> --seed 20261124

which also writes evals/livesqlbench_databases.json. `sample` rewrites the whole manifest, so run
that split command again after it.
"""
