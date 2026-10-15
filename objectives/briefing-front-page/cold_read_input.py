#!/usr/bin/env python3
"""P29: render the full briefing a cold reader gets, front page and folds.

The first cold read was handed only the terminal render, which summarizes the
folds into counts, so it could not name a folded item to skip even though the
item was the thing it was asked for. This writes what a person actually reads:
the front page, the business rows, and the folded ledger behind them.
"""
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tier2_env                                                # noqa: E402

OUT = Path(sys.argv[1] if len(sys.argv) > 1
           else Path.home() / "Downloads" / "proof-briefing-front-page" / "live")
OUT.mkdir(parents=True, exist_ok=True)

tmp = Path(tempfile.mkdtemp(prefix="van-gogh-cold-"))
try:
    ctx = tier2_env.build(tmp, n_items=100, front_page_cap=7)
    rc, out, err = tier2_env.run_script("morning_coffee.py", ctx["env"],
                                        ["--input", str(ctx["input"])])
    data = tier2_env.first_json(out)
    text = "\n".join([
        "=== MORNING COFFEE ===", "",
        data["front_page_md"].rstrip(), "",
        data["fold_rows_md"].rstrip(), "",
        "## The folds", "", data["folded_md"].rstrip(), ""])
    path = OUT / "p29_full_briefing.txt"
    path.write_text(text, encoding="utf-8")
    print(f"rc={rc} wrote {path} ({len(text.splitlines())} lines)")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
