#!/usr/bin/env python3
"""
Generate Punycode (xn--) impersonation variants for brand domains using the
bundled Unicode TR39 confusables file (unicode_TR39_confusables.txt).

Reads ASCII (or mixed) FQDNs from brand_domains.txt, emits lines:
    <original_domain>,<idna_ascii_with_xn-->

Only variants whose IDNA encoding contains at least one ``xn--`` label are kept
(non-ASCII substitutions in at least one label).

By default, every *single*-character TR39 inverse substitution is emitted for each
substitutable codepoint. Use --max-substitutions > 1 for multi-position combinations
(capped by --max-variants per input domain).

Usage:
    python generate_brand_domain_impersonations.py
    python generate_brand_domain_impersonations.py --max-substitutions 2 --max-variants 200000
"""

from __future__ import annotations

import argparse
import itertools
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_CONFUSABLES = _SCRIPT_DIR / "unicode_TR39_confusables.txt"


def _load_tr39_confusable_map(path: Path) -> dict[int, str]:
    """
    Parse a Unicode TR39 confusables.txt file and return a map of
    source codepoint (int) → skeleton/target string.

    Expected line format (non-comment, non-blank):
        <src_hex> ; <tgt_hex> [<tgt_hex> ...] ; <type> # optional comment
    """
    conf: dict[int, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(";")
        if len(parts) < 2:
            continue
        src_hex = parts[0].strip()
        # Target may be several space-separated hex codepoints; strip inline comment first.
        tgt_field = parts[1].split("#")[0].strip()
        try:
            src_cp = int(src_hex, 16)
            tgt_str = "".join(chr(int(h, 16)) for h in tgt_field.split())
            conf[src_cp] = tgt_str
        except ValueError:
            continue
    return conf


def _inverse_singlechar_confusables(conf_map: dict[int, str]) -> dict[str, tuple[str, ...]]:
    """
    TR39 file maps source codepoint -> skeleton/target string.
    For each ASCII target character T, collect every source character S (len 1)
    such that conf_map[ord(S)] == T and S is not identical to T.
    """
    buckets: dict[str, set[str]] = {}
    for src_cp, tgt in conf_map.items():
        if len(tgt) != 1:
            continue
        if ord(tgt) == src_cp:
            continue
        buckets.setdefault(tgt, set()).add(chr(src_cp))
    return {k: tuple(sorted(v)) for k, v in buckets.items()}


def _substitutable_spots(labels: list[str], inverse: dict[str, tuple[str, ...]]) -> list[tuple[int, int, str, tuple[str, ...]]]:
    """List of (label_index, char_index, original_char, substitutes) for spots with ≥1 substitute."""
    spots: list[tuple[int, int, str, tuple[str, ...]]] = []
    for li, lab in enumerate(labels):
        for ci, ch in enumerate(lab):
            subs = inverse.get(ch)
            if subs:
                spots.append((li, ci, ch, subs))
    return spots


def _apply_spot_replacements(
    labels: list[str],
    replacements: list[tuple[int, int, str]],
) -> str:
    """replacements: list of (label_idx, char_idx, new_char) — must not double-book a spot."""
    out_labels: list[str] = []
    for li, lab in enumerate(labels):
        chars = list(lab)
        for (rli, rci, new_ch) in replacements:
            if rli == li:
                chars[rci] = new_ch
        out_labels.append("".join(chars))
    return ".".join(out_labels)


def _idna_encode_hostname(unicode_host: str) -> str | None:
    try:
        return unicode_host.encode("idna").decode("ascii")
    except UnicodeError:
        return None


def _variants_for_domain(
    hostname: str,
    inverse: dict[str, tuple[str, ...]],
    max_substitutions: int,
    max_variants: int,
) -> list[str]:
    """
    Return sorted unique Punycode ASCII hostnames (each contains 'xn--') derived from hostname.
    """
    host = hostname.strip().lower()
    if not host:
        return []
    labels = host.split(".")
    if any(not lab for lab in labels):
        return []

    spots = _substitutable_spots(labels, inverse)
    seen: set[str] = set()
    out: list[str] = []

    def try_add(unicode_host: str) -> None:
        nonlocal out
        if len(seen) >= max_variants:
            return
        ascii_host = _idna_encode_hostname(unicode_host)
        if not ascii_host or "xn--" not in ascii_host:
            return
        if ascii_host in seen:
            return
        seen.add(ascii_host)
        out.append(ascii_host)

    # k = number of simultaneous character replacements (distinct spots)
    for k in range(1, min(max_substitutions, len(spots)) + 1):
        for spot_combo in itertools.combinations(spots, k):
            sub_lists = [s[3] for s in spot_combo]
            for choice in itertools.product(*sub_lists):
                reps = [(spot_combo[i][0], spot_combo[i][1], choice[i]) for i in range(k)]
                unicode_host = _apply_spot_replacements(labels, reps)
                try_add(unicode_host)
                if len(seen) >= max_variants:
                    return sorted(out)

    return sorted(out)


def _read_domains(path: Path) -> list[tuple[str, str]]:
    """Return (line_as_in_file_stripped, lowercase_for_generation) per domain."""
    lines: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        lines.append((s, s.lower()))
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=_SCRIPT_DIR / "brand_domains.txt", help="Path to brand_domains.txt")
    parser.add_argument(
        "--output",
        type=Path,
        default=_SCRIPT_DIR / "brand_domains_impersonation.txt",
        help="Destination file (default: brand_domains_impersonation.txt next to the script)",
    )
    parser.add_argument(
        "--confusables",
        type=Path,
        default=_DEFAULT_CONFUSABLES,
        help="Path to unicode_TR39_confusables.txt (default: bundled copy next to the script)",
    )
    parser.add_argument(
        "--max-substitutions",
        type=int,
        default=1,
        metavar="N",
        help="Replace up to N positions at once with TR39 confusables (default: 1).",
    )
    parser.add_argument(
        "--max-variants",
        type=int,
        default=500_000,
        metavar="M",
        help="Stop collecting variants per input domain after M unique Punycode outputs.",
    )
    args = parser.parse_args()

    if not args.confusables.is_file():
        print(f"Confusables file not found: {args.confusables}", file=sys.stderr)
        return 1

    if not args.input.is_file():
        print(f"Input file not found: {args.input}", file=sys.stderr)
        return 1

    conf_map = _load_tr39_confusable_map(args.confusables)
    inverse = _inverse_singlechar_confusables(conf_map)

    domains = _read_domains(args.input)
    if not domains:
        print(f"No domains in {args.input}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)

    total_lines = 0
    with args.output.open("w", encoding="utf-8", newline="\n") as fout:
        for domain_display, domain_key in domains:
            variants = _variants_for_domain(
                domain_key,
                inverse,
                max_substitutions=max(1, args.max_substitutions),
                max_variants=args.max_variants,
            )
            for puny in variants:
                fout.write(f"{domain_display},{puny}\n")
                total_lines += 1

    print(f"Wrote {total_lines} lines to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
