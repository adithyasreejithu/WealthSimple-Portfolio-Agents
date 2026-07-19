from __future__ import annotations

import argparse
import sys

from classification_workflow import classify_portfolio, write_output

def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Classify the configured portfolio safely.")
    parser.add_argument("--output", help="JSON path inside exports/portfolio-classification.")
    parser.add_argument("--pretty", action="store_true", help="Indent and sort the JSON output.")
    return parser.parse_args(argv)

def main(argv=None):
    args = parse_args(argv)
    try:
        path = write_output(classify_portfolio(), args.output, args.pretty)
    except Exception as exc:
        print(f"portfolio classification failed: {exc}", file=sys.stderr)
        return 1
    print(path)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
