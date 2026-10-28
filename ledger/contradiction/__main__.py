from .cli import eval_t2

import argparse

parser = argparse.ArgumentParser(prog="python -m ledger.contradiction")
parser.add_argument("--eval", dest="eval_path", required=True, help="T2 fixture YAML")
args = parser.parse_args()
result = eval_t2(args.eval_path)
for lang, block in result.items():
    for t, m in block["thresholds"].items():
        p = "n/a" if m["precision"] is None else f"{m['precision']:.3f}"
        r = "n/a" if m["recall"] is None else f"{m['recall']:.3f}"
        print(f"lang={lang} pairs={block['pairs']} t={t} precision={p} recall={r} tp={m['tp']} fp={m['fp']} fn={m['fn']}")
