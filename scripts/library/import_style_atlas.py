"""Compatibility entry point for the source-specific editorial compiler.

Fetch research inputs with fetch_style_sources.py, then explicitly provide ALL
editorial shards to this command. Generic keyword-generated copy is retired.
"""

from build_style_content import main


if __name__ == "__main__":
    raise SystemExit(main())
