"""Side-by-side comparison of the two request parsers.

Shows where the deterministic fallback is enough and where Gemini earns its place.
Run with GEMINI_MODE=live in .env to exercise the API.

    python demo_parser.py
"""
import os
import sys

sys.path.insert(0, "agent")

from dotenv import load_dotenv

import parser

load_dotenv()

CASES = [
    ("ordinary request",
     "Hi Agent, Can you give me Other Documents files from M12205? Thanks!"),
    ("negation",
     "For M12205 I want the documents that aren't exhibits or key documents"),
    ("matter number typed with spaces",
     "Please pull the exhibits filed under matter M 1 2 3 8 3 for me"),
    ("indirect wording",
     "Need the audio files from the Amherst boundary matter, M12383, for review"),
]

GREEN, RED, DIM, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[0m"


def show(label, value, expected):
    mark = f"{GREEN}ok{OFF}" if value == expected else f"{RED}wrong{OFF}"
    print(f"    {label:<8} {str(value):<34} {mark}")


def main():
    live = parser.gemini_enabled()
    print(f"\nGEMINI_MODE={os.environ.get('GEMINI_MODE', 'mock')}  "
          f"(API calls: {'on' if live else 'off'})\n")

    for name, text in CASES:
        print(f"  {name}")
        print(f"{DIM}    {text!r}{OFF}")

        fb = parser.fallback_parse(text)

        if live:
            # Exactly one API call per case -- the free tier is small.
            raw = parser.gemini_parse(text)
            gm = (parser._normalise_matter(raw[0]), parser._normalise_category(raw[1]))
            show("regex", fb, gm)
            show("gemini", gm, gm)
        else:
            print(f"    regex    {fb}")
            print(f"{DIM}    (set GEMINI_MODE=live to compare against Gemini){OFF}")
        print()


if __name__ == "__main__":
    main()
