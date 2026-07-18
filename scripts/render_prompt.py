"""Render compiled agent prompt(s) for a client config to stdout.

Usage:
    python scripts/render_prompt.py <config.yaml> [--language en-US]

Prints the compiled Retell general_prompt for one language (with --language) or
all languages (default) to stdout, separated by a header line per language.

Privacy: for real client configs, keep this output in the terminal. Do NOT
redirect it into a tracked file — compiled prompts embed private client facts.
The config/clients/ directory (raw configs) and capture/ (dumps) are already
gitignored; compiled prompts should be treated the same way.
"""

from __future__ import annotations

import argparse
import sys

from dma_deploy_kit.agent import compile_all, compile_prompt
from dma_deploy_kit.config import ClientConfigError, load_client_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render compiled agent prompt(s) to stdout.")
    parser.add_argument("config", help="Path to a client config YAML file.")
    parser.add_argument(
        "--language",
        help="Language code to render (e.g. en-US). Omit to render all languages.",
    )
    args = parser.parse_args(argv)

    try:
        config = load_client_config(args.config)
    except ClientConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.language:
        match = next((lp for lp in config.languages if lp.code == args.language), None)
        if match is None:
            available = ", ".join(lp.code for lp in config.languages)
            print(
                f"Language '{args.language}' not in config. Available: {available}",
                file=sys.stderr,
            )
            return 1
        print(f"===== {config.client.slug} — {match.code} =====")
        print(compile_prompt(config, match))
        return 0

    for code, prompt in compile_all(config).items():
        print(f"===== {config.client.slug} — {code} =====")
        print(prompt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
