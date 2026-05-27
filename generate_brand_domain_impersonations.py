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

Features:
  - Error handling & detailed logging (--verbose)
  - IDNA encoding result caching for performance
  - Parallel processing (--workers)
  - Output format options: CSV, JSON, TSV (--format)
  - Global deduplication (--deduplicate)
  - Generation statistics (--stats)

Usage:
    python generate_brand_domain_impersonations.py
    python generate_brand_domain_impersonations.py --max-substitutions 2 --max-variants 200000 --workers 4
    python generate_brand_domain_impersonations.py --format json --stats
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from multiprocessing import Manager, Pool
from pathlib import Path
from typing import Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_CONFUSABLES = _SCRIPT_DIR / "unicode_TR39_confusables.txt"

# Configure logging
logger = logging.getLogger(__name__)


@dataclass
class GenerationStats:
    """Statistics for a generation run."""
    total_domains: int = 0
    total_variants: int = 0
    domains_processed: int = 0
    domains_with_variants: int = 0
    time_elapsed: float = 0.0
    errors: list[str] = field(default_factory=list)
    idna_cache_hits: int = 0
    idna_cache_misses: int = 0

    def add_error(self, domain: str, error: str):
        self.errors.append(f"  {domain}: {error}")


def _setup_logging(verbose: bool) -> None:
    """Configure logging level."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _load_tr39_confusable_map(path: Path) -> dict[int, str]:
    """
    Parse a Unicode TR39 confusables.txt file and return a map of
    source codepoint (int) → skeleton/target string.

    Expected line format (non-comment, non-blank):
        <src_hex> ; <tgt_hex> [<tgt_hex> ...] ; <type> # optional comment

    Raises:
        FileNotFoundError: If the confusables file is not found.
        ValueError: If the confusables file is malformed.
    """
    if not path.is_file():
        raise FileNotFoundError(f"Confusables file not found: {path}")

    conf: dict[int, str] = {}
    error_count = 0
    line_count = 0

    try:
        for line_num, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            line_count += 1
            parts = line.split(";")
            if len(parts) < 2:
                logger.warning(f"Skipping malformed line {line_num}: {line[:60]}")
                error_count += 1
                continue

            src_hex = parts[0].strip()
            # Target may be several space-separated hex codepoints; strip inline comment first.
            tgt_field = parts[1].split("#")[0].strip()

            try:
                src_cp = int(src_hex, 16)
                tgt_str = "".join(chr(int(h, 16)) for h in tgt_field.split())
                conf[src_cp] = tgt_str
            except (ValueError, OverflowError) as e:
                logger.debug(f"Error parsing line {line_num}: {e}")
                error_count += 1
                continue

    except UnicodeDecodeError as e:
        raise ValueError(f"Unicode decode error in confusables file: {e}")

    logger.info(f"Loaded {len(conf)} confusable mappings from {path.name} ({line_count} lines, {error_count} errors)")
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

    result = {k: tuple(sorted(v)) for k, v in buckets.items()}
    logger.debug(f"Created inverse map with {len(result)} substitutable characters")
    return result


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


def _idna_encode_hostname(unicode_host: str, cache: Optional[dict] = None, stats: Optional[GenerationStats] = None) -> str | None:
    """
    Encode hostname to IDNA/Punycode with optional caching.

    Args:
        unicode_host: Unicode hostname to encode
        cache: Optional dict for caching results
        stats: Optional stats object to track cache hits/misses

    Returns:
        ASCII IDNA-encoded hostname or None if encoding fails
    """
    if cache is not None and unicode_host in cache:
        if stats:
            stats.idna_cache_hits += 1
        return cache[unicode_host]

    try:
        result = unicode_host.encode("idna").decode("ascii")
        if cache is not None:
            cache[unicode_host] = result
        if stats:
            stats.idna_cache_misses += 1
        return result
    except (UnicodeError, UnicodeDecodeError) as e:
        logger.debug(f"Failed to encode '{unicode_host}': {e}")
        if cache is not None:
            cache[unicode_host] = None
        return None


def _variants_for_domain(
    hostname: str,
    inverse: dict[str, tuple[str, ...]],
    max_substitutions: int,
    max_variants: int,
    idna_cache: Optional[dict] = None,
    stats: Optional[GenerationStats] = None,
) -> list[str]:
    """
    Return sorted unique Punycode ASCII hostnames (each contains 'xn--') derived from hostname.

    Args:
        hostname: Input domain hostname
        inverse: Inverse confusables map
        max_substitutions: Maximum simultaneous substitutions
        max_variants: Maximum variants to generate per domain
        idna_cache: Optional IDNA encoding cache
        stats: Optional statistics object

    Returns:
        Sorted list of Punycode variants
    """
    host = hostname.strip().lower()
    if not host:
        return []
    labels = host.split(".")
    if any(not lab for lab in labels):
        return []

    spots = _substitutable_spots(labels, inverse)
    if not spots:
        logger.debug(f"No substitutable spots found for {hostname}")
        return []

    seen: set[str] = set()
    out: list[str] = []

    def try_add(unicode_host: str) -> None:
        nonlocal out
        if len(seen) >= max_variants:
            return
        ascii_host = _idna_encode_hostname(unicode_host, cache=idna_cache, stats=stats)
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
    """
    Return (line_as_in_file_stripped, lowercase_for_generation) per domain.

    Raises:
        FileNotFoundError: If input file is not found.
    """
    if not path.is_file():
        raise FileNotFoundError(f"Input file not found: {path}")

    lines: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        lines.append((s, s.lower()))

    logger.info(f"Loaded {len(lines)} domains from {path.name}")
    return lines


def _process_domain_worker(args: tuple) -> tuple[str, list[str], Optional[str]]:
    """Worker function for multiprocessing."""
    domain_display, domain_key, inverse, max_subs, max_vars, idna_cache = args
    try:
        variants = _variants_for_domain(
            domain_key,
            inverse,
            max_substitutions=max_subs,
            max_variants=max_vars,
            idna_cache=idna_cache,
        )
        return (domain_display, variants, None)
    except Exception as e:
        logger.error(f"Error processing domain {domain_display}: {e}")
        return (domain_display, [], str(e))


def _format_output_csv(domain_display: str, variants: list[str]) -> list[str]:
    """Format output as CSV lines."""
    return [f"{domain_display},{puny}" for puny in variants]


def _format_output_tsv(domain_display: str, variants: list[str]) -> list[str]:
    """Format output as TSV lines."""
    return [f"{domain_display}\t{puny}" for puny in variants]


def _format_output_json(all_results: list[tuple[str, list[str]]]) -> str:
    """Format all results as JSON."""
    output = {
        "version": "1.0",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "variants": []
    }
    for domain, variants in all_results:
        for variant in variants:
            output["variants"].append({
                "original": domain,
                "punycode": variant
            })
    return json.dumps(output, indent=2, ensure_ascii=False)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=_SCRIPT_DIR / "brand_domains.txt",
        help="Path to brand_domains.txt",
    )
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
    parser.add_argument(
        "--format",
        choices=["csv", "tsv", "json"],
        default="csv",
        help="Output format (default: csv)",
    )
    parser.add_argument(
        "--deduplicate",
        action="store_true",
        help="Remove duplicate Punycode variants across all input domains",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        metavar="N",
        help="Number of worker processes for parallel processing (default: 1)",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print generation statistics",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()
    _setup_logging(args.verbose)

    start_time = time.time()
    stats = GenerationStats()

    # Validate inputs
    try:
        if not args.confusables.is_file():
            logger.error(f"Confusables file not found: {args.confusables}")
            return 1

        if not args.input.is_file():
            logger.error(f"Input file not found: {args.input}")
            return 1

        logger.info("Loading Unicode TR39 confusables...")
        conf_map = _load_tr39_confusable_map(args.confusables)
        inverse = _inverse_singlechar_confusables(conf_map)

        logger.info("Reading input domains...")
        domains = _read_domains(args.input)
        if not domains:
            logger.error(f"No domains in {args.input}")
            return 1

        stats.total_domains = len(domains)
        logger.info(f"Processing {stats.total_domains} domains (max_substitutions={args.max_substitutions}, max_variants={args.max_variants})")

    except (FileNotFoundError, ValueError) as e:
        logger.error(f"Setup failed: {e}")
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)

    all_results: list[tuple[str, list[str]]] = []
    global_seen: set[str] = set() if args.deduplicate else None

    try:
        if args.workers > 1:
            logger.info(f"Using {args.workers} worker processes")
            with Manager() as manager:
                idna_cache = manager.dict()
                worker_args = [
                    (domain_display, domain_key, inverse, args.max_substitutions, args.max_variants, idna_cache)
                    for domain_display, domain_key in domains
                ]
                with Pool(args.workers) as pool:
                    for domain_display, variants, error in pool.imap_unordered(_process_domain_worker, worker_args):
                        if error:
                            stats.add_error(domain_display, error)
                        else:
                            if variants:
                                stats.domains_with_variants += 1
                            stats.total_variants += len(variants)
                            all_results.append((domain_display, variants))
                            stats.domains_processed += 1
                            if (stats.domains_processed % max(1, stats.total_domains // 10)) == 0:
                                logger.info(f"Progress: {stats.domains_processed}/{stats.total_domains} domains processed")
        else:
            idna_cache: dict[str, Optional[str]] = {}
            for domain_display, domain_key in domains:
                try:
                    variants = _variants_for_domain(
                        domain_key,
                        inverse,
                        max_substitutions=max(1, args.max_substitutions),
                        max_variants=args.max_variants,
                        idna_cache=idna_cache,
                        stats=stats,
                    )
                    if variants:
                        stats.domains_with_variants += 1
                    stats.total_variants += len(variants)
                    all_results.append((domain_display, variants))
                    stats.domains_processed += 1
                except Exception as e:
                    logger.error(f"Error processing domain {domain_display}: {e}")
                    stats.add_error(domain_display, str(e))

        # Write output
        logger.info(f"Writing output to {args.output.name}")
        total_lines = 0

        if args.format == "json":
            output_content = _format_output_json(all_results)
            args.output.write_text(output_content, encoding="utf-8")
            total_lines = stats.total_variants
        else:
            formatter = _format_output_tsv if args.format == "tsv" else _format_output_csv
            with args.output.open("w", encoding="utf-8", newline="\n") as fout:
                for domain_display, variants in all_results:
                    for line in formatter(domain_display, variants):
                        if args.deduplicate:
                            separator = "\t" if args.format == "tsv" else ","
                            punycode = line.split(separator, 1)[1]
                            if punycode in global_seen:
                                continue
                            global_seen.add(punycode)
                        fout.write(f"{line}\n")
                        total_lines += 1

        elapsed = time.time() - start_time
        stats.time_elapsed = elapsed

        logger.info(f"✓ Wrote {total_lines} lines to {args.output}")
        logger.info(f"  Domains with variants: {stats.domains_with_variants}/{stats.total_domains}")

        if args.stats:
            print("\n" + "="*60)
            print("GENERATION STATISTICS")
            print("="*60)
            print(f"Total domains:           {stats.total_domains}")
            print(f"Domains with variants:   {stats.domains_with_variants} ({100*stats.domains_with_variants/stats.total_domains:.1f}%)")
            print(f"Total variants generated: {stats.total_variants}")
            if args.workers == 1:
                hit_rate = 100 * stats.idna_cache_hits / (stats.idna_cache_hits + stats.idna_cache_misses) if (stats.idna_cache_hits + stats.idna_cache_misses) > 0 else 0
                print(f"IDNA cache hits/misses:  {stats.idna_cache_hits}/{stats.idna_cache_misses} ({hit_rate:.1f}% hit rate)")
            print(f"Time elapsed:            {elapsed:.2f}s")
            if stats.errors:
                print(f"Errors ({len(stats.errors)}):")
                for error in stats.errors[:10]:  # Show first 10 errors
                    print(error)
                if len(stats.errors) > 10:
                    print(f"  ... and {len(stats.errors) - 10} more errors")
            print("="*60 + "\n")

        return 0

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=args.verbose)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
