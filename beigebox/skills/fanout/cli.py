"""CLI for the fan-out skill.

Invoke as ``python3 -m beigebox.skills.fan_out`` (or via scripts/fan-out.sh).

Loaders for items (pick exactly one):
  --items-file PATH     one item per line (lines stripped, blanks dropped)
  --items-glob PATTERN  shell glob; each matching path is loaded as `{path, contents}`
  --items-json PATH     JSON file containing a list (of strings or dicts)
  --items-stdin         read JSON list from stdin

Templates use ``{item}`` (or ``{item.field}`` for dict items) and ``{index}``.
The reduce template uses ``{responses}`` (joined with separators) and ``{count}``.
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import sys
from pathlib import Path
from typing import Any

from .pipeline import DEFAULT_BASE_URL, DEFAULT_API_KEY, DEFAULT_TIMEOUT, fan_out


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="beigebox-fan-out",
        description="Fan a list of inputs out to N parallel model calls; optionally reduce.",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--items-file", type=Path, help="One item per line (whitespace stripped).")
    src.add_argument("--items-glob", type=str, help="Shell glob; each match becomes {path, contents}.")
    src.add_argument("--items-json", type=Path, help="JSON file containing a list.")
    src.add_argument(
        "--items-stdin",
        action="store_true",
        help="Read a JSON list of items from stdin.",
    )

    p.add_argument(
        "--template",
        type=str,
        required=True,
        help="Per-item prompt template. Supports {item}, {item.field}, {index}.",
    )
    p.add_argument("--model", type=str, required=True, help="Model id to call per item.")
    p.add_argument("--concurrency", type=int, default=4, help="Max parallel calls. Default 4.")
    p.add_argument("--system", type=str, default=None, help="Optional system message for each item.")
    p.add_argument("--temperature", type=float, default=0.2)
    p.add_argument("--max-tokens", type=int, default=None, help="Optional cap per item call.")
    p.add_argument("--base-url", type=str, default=DEFAULT_BASE_URL, help="OpenAI-compat base URL.")
    p.add_argument("--api-key", type=str, default=DEFAULT_API_KEY, help="Bearer token (anything works for the BeigeBox proxy).")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)

    p.add_argument(
        "--reduce",
        type=str,
        default=None,
        help="Reduce prompt template. Supports {responses}, {count}. Triggers a final merge call.",
    )
    p.add_argument("--reduce-model", type=str, default=None, help="Override model for reduce step.")
    p.add_argument("--reduce-system", type=str, default=None)
    p.add_argument("--reduce-max-tokens", type=int, default=None)
    p.add_argument(
        "--reduce-on-partial",
        action="store_true",
        help="Run reduce even if some items failed.",
    )

    p.add_argument(
        "--format",
        choices=("json", "summary"),
        default="json",
        help="Output format. json: full result. summary: human-readable.",
    )
    p.add_argument("--out", type=Path, default=None, help="Write JSON output to this file.")
    return p


def _load_items(args: argparse.Namespace) -> list[Any]:
    if args.items_file:
        text = args.items_file.read_text(encoding="utf-8")
        return [ln.strip() for ln in text.splitlines() if ln.strip()]
    if args.items_glob:
        matches = sorted(glob.glob(args.items_glob, recursive=True))
        out: list[dict[str, Any]] = []
        for path in matches:
            p = Path(path)
            if not p.is_file():
                continue
            try:
                contents = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            out.append({"path": str(p), "contents": contents})
        return out
    if args.items_json:
        data = json.loads(args.items_json.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise SystemExit(f"--items-json must contain a list, got {type(data).__name__}")
        return data
    if args.items_stdin:
        data = json.loads(sys.stdin.read())
        if not isinstance(data, list):
            raise SystemExit(f"stdin must contain a JSON list, got {type(data).__name__}")
        return data
    raise SystemExit("no item source given")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    items = _load_items(args)

    result = asyncio.run(
        fan_out(
            items,
            args.template,
            model=args.model,
            concurrency=args.concurrency,
            base_url=args.base_url,
            api_key=args.api_key,
            system=args.system,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            reduce_prompt=args.reduce,
            reduce_model=args.reduce_model,
            reduce_system=args.reduce_system,
            reduce_max_tokens=args.reduce_max_tokens,
            reduce_on_partial=args.reduce_on_partial,
            timeout=args.timeout,
        )
    )

    if args.out:
        args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False))

    if args.format == "json":
        if not args.out:
            json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
            sys.stdout.write("\n")
    else:
        _print_summary(result)
    return 0 if result["stats"]["failed"] == 0 else 1


def _print_summary(result: dict[str, Any]) -> None:
    s = result["stats"]
    print("Fan-out summary")
    print("-" * 60)
    print(f"  Items:                {s['items']}")
    print(f"  Succeeded:            {s['succeeded']}")
    print(f"  Failed:               {s['failed']}")
    print(f"  Prompt tokens (sum):  {s['total_prompt_tokens']}")
    print(f"  Compl. tokens (sum):  {s['total_completion_tokens']}")
    print(f"  Wall-clock seconds:   {s['total_duration_seconds']}")
    print()
    for i, r in enumerate(result["responses"]):
        item_label = (
            r["item"]["path"] if isinstance(r["item"], dict) and "path" in r["item"]
            else (str(r["item"])[:60] + ("…" if len(str(r["item"])) > 60 else ""))
        )
        if r["error"]:
            print(f"  [{i:02d}] FAIL  {item_label}  — {r['error']}")
        else:
            preview = (r["content"][:80] + "…") if len(r["content"]) > 80 else r["content"]
            preview = preview.replace("\n", " ")
            print(f"  [{i:02d}] OK    {item_label}  — {preview}")
    if result["reduce"]:
        print()
        print("Reduce:")
        print("-" * 60)
        if result["reduce"].get("error"):
            print(f"  FAILED — {result['reduce']['error']}")
        else:
            print(result["reduce"]["content"])
