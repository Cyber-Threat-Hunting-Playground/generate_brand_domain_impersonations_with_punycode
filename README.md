# Generate Brand Domain Impersonations with Punycode

A utility that generates **Punycode (`xn--`) impersonation variants** for a list of brand domains by applying Unicode TR39 visual-confusable substitutions. The output is a CSV-style list of `(original_domain, punycode_variant)` pairs suitable for use as threat-hunting input data.

## How it works

1. For every ASCII character in each input domain label, the script looks up all Unicode code-points that are visually confusable with it using the bundled `unicode_TR39_confusables.txt` file.
2. It builds all combinations of single (or multi-position, see options) confusable substitutions.
3. Each candidate Unicode hostname is encoded to IDNA/Punycode with Python's `str.encode("idna")`.
4. Only variants whose Punycode form contains at least one `xn--` label are kept — these are the domains that look like the brand but resolve differently.

## Quick start

### 1. Provide your brand domains

Copy the example file and add one FQDN per line:

```bash
cp brand_domains.txt.example brand_domains.txt
# then edit brand_domains.txt
```

`brand_domains.txt` is excluded from version control by `.gitignore`; only the `.example` template is tracked.

### 2. Run the generator

```bash
python generate_brand_domain_impersonations.py
```

**Output** is written to `brand_domains_impersonation.txt` next to the script:

```
example.com,xn--xmple-cua.com
example.com,xn--exampl-jua.com
...
```

### 3. Advanced options

| Flag | Default | Description |
|---|---|---|
| `--input PATH` | `brand_domains.txt` (next to script) | Path to the domain list |
| `--output PATH` | `brand_domains_impersonation.txt` (next to script) | Destination file |
| `--confusables PATH` | `unicode_TR39_confusables.txt` (bundled) | Path to the TR39 confusables file |
| `--max-substitutions N` | `1` | Replace up to N positions at once with confusables |
| `--max-variants M` | `500 000` | Stop collecting variants per domain after M unique Punycode outputs |

Example — generate all two-character substitution combos, capped at 200 000 variants per domain:

```bash
python generate_brand_domain_impersonations.py \
    --max-substitutions 2 \
    --max-variants 200000
```

## Input file format (`brand_domains.txt`)

- One fully-qualified domain name per line (ASCII recommended).
- Lines starting with `#` and blank lines are ignored.

```
# My brand domains
example.com
mybrand.io
```

## Dependencies

- Python 3.9+
- No third-party packages — standard library only. The TR39 confusable data is bundled as `unicode_TR39_confusables.txt`.

## Files

| File | Description |
|---|---|
| `generate_brand_domain_impersonations.py` | Main script |
| `unicode_TR39_confusables.txt` | Bundled Unicode TR39 confusables data (UTS #39) |
| `brand_domains.txt.example` | Template for the input domain list |
| `brand_domains.txt` | *(gitignored)* Your actual domain list |
| `brand_domains_impersonation.txt` | *(gitignored)* Generated Punycode variants |
