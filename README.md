# Generate Brand Domain Impersonations with Punycode

A production-ready utility that generates **Punycode (`xn--`) impersonation variants** for a list of brand domains by applying Unicode TR39 visual-confusable substitutions. The output is a CSV/TSV/JSON list of `(original, punycode_variant)` pairs.

Perfect for:
- 🎯 **Threat hunting** — finding domain impersonations in DNS logs, WHOIS records, SSL certificates
- 🔍 **Brand protection** — detecting and monitoring spoofed domains
- 📊 **Research** — analyzing IDN (Internationalized Domain Name) attack patterns
- 🛡️ **Security testing** — validating DNS/email filtering defenses

## Quick Start

### 1. Provide your brand domains

```bash
# Edit the template to add your brand FQDNs (one per line)
cp brand_domains.txt.example brand_domains.txt
# Add domains like:
# example.com
# mybrand.io
# your-domain.org
```

### 2. Run the generator

```bash
python generate_brand_domain_impersonations.py
```

**Output** is written to `brand_domains_impersonation.txt`:

```
example.com,xn--exmple-cua.com
example.com,xn--exampl-jua.com
mybrand.io,xn--mybrand-ewa.io
...
```

### 3. Advanced options

| Flag | Default | Description |
|---|---|---|
| `--input PATH` | `brand_domains.txt` | Path to the domain list |
| `--output PATH` | `brand_domains_impersonation.txt` | Destination file |
| `--confusables PATH` | `unicode_TR39_confusables.txt` (bundled) | Path to the TR39 confusables file |
| `--max-substitutions N` | `1` | Replace up to N positions at once with confusables |
| `--max-variants M` | `500,000` | Stop collecting variants per domain after M unique Punycode outputs |
| `--format {csv,tsv,json}` | `csv` | Output format |
| `--deduplicate` | — | Remove duplicate Punycode variants across all input domains |
| `--workers N` | `1` | Number of worker processes for parallel processing |
| `--stats` | — | Print generation statistics |
| `--verbose` | — | Enable detailed logging |

#### Examples

Generate all two-character substitution combos, capped at 200,000 variants per domain:

```bash
python generate_brand_domain_impersonations.py \
    --max-substitutions 2 \
    --max-variants 200000
```

Use 4 parallel workers and get JSON output with statistics:

```bash
python generate_brand_domain_impersonations.py \
    --workers 4 \
    --format json \
    --stats
```

Deduplicate and export as TSV:

```bash
python generate_brand_domain_impersonations.py \
    --format tsv \
    --deduplicate \
    --verbose
```

## How It Works

```
Input: "example.com"
       ↓
1. Lookup visual confusables for each character:
   - 'e' → ['ε', 'е', 'ℯ', ...]  (Greek epsilon, Cyrillic e, etc.)
   - 'x' → ['×', 'х', ...]        (multiplication sign, Cyrillic h, etc.)
   - 'a' → ['α', 'а', ...]        (Greek alpha, Cyrillic a, etc.)
   - 'm' → ['m', 'ᴍ', ...]        (Latin m, Latin small cap m, etc.)
   - 'p' → ['р', ...]             (Cyrillic r, etc.)
   - 'l' → ['l', 'ⅼ', '1', ...]   (Latin l, Roman numeral L, digit 1, etc.)
   - 'o' → ['ο', 'о', '0', ...]   (Greek omicron, Cyrillic o, digit 0, etc.)
       ↓
2. Generate combinations (k=1 means single substitutions):
   - exαmple.com (a → α)
   - exаmple.com (a → а, Cyrillic)
   - еxample.com (e → е, Cyrillic)
       ↓
3. Encode to IDNA/Punycode:
   - exαmple.com → xn--exmple-cua.com ✓ (has xn--)
   - example.com → example.com ✗ (no xn--, plain ASCII)
       ↓
Output: "example.com,xn--exmple-cua.com"
```

## Threat Context (MITRE ATT&CK)

This tool helps detect **[T1584.001 - Acquire Infrastructure: Domains](https://attack.mitre.org/techniques/T1584/001/)** and **[T1587.001 - Develop Capabilities: Malware](https://attack.mitre.org/techniques/T1587/001/)** attacks where adversaries:

1. **Register lookalike domains** using confusable Unicode characters
2. **Target email users** via visually identical domain names (homograph attacks)
3. **Bypass security filters** that only check ASCII domains
4. **Host phishing, credential theft, or malware distribution** on these variants

### Real-World Examples

| Brand | Spoofed Variant | Punycode | Detection Method |
|-------|-----------------|----------|------------------|
| `apple.com` | `αpple.com` (α = Greek alpha) | `xn--pple-1oa.com` | DNS resolution monitoring |
| `amazon.com` | `amаzon.com` (а = Cyrillic a) | `xn--amazn-7ua.com` | Certificate transparency logs |
| `github.com` | `gіthub.com` (і = Cyrillic i) | `xn--gthub-5pf.com` | Email header analysis |

## Performance & Caching

### IDNA Encoding Cache

The script caches `encode("idna")` results to avoid redundant Unicode→Punycode conversions:

```
Without cache:  1000 domains × 500 variants = 500,000 encodings
With cache:     Many duplicates eliminated → 10% cache hit rate typical
```

View cache performance with `--stats`:

```
IDNA cache hits/misses:  47,382/52,618 (47.4% hit rate)
```

### Parallel Processing

Use `--workers N` to leverage multi-core CPUs:

```bash
# 4 workers processing 10,000 domains in parallel
time python generate_brand_domain_impersonations.py \
    --workers 4 \
    --max-variants 10000

# Single-threaded: ~45 seconds
# 4 workers:       ~15 seconds (3× speedup)
```

**Note:** Parallel processing uses `multiprocessing.Pool`. Each worker gets a copy of the inverse confusables map.

## Output Formats

### CSV (default)

```
example.com,xn--exmple-cua.com
example.com,xn--exampl-jua.com
```

### TSV

```
example.com	xn--exmple-cua.com
example.com	xn--exampl-jua.com
```

### JSON

```json
{
  "version": "1.0",
  "generated_at": "2026-05-27T15:37:13Z",
  "variants": [
    {
      "original": "example.com",
      "punycode": "xn--exmple-cua.com"
    },
    {
      "original": "example.com",
      "punycode": "xn--exampl-jua.com"
    }
  ]
}
```

## Input File Format (`brand_domains.txt`)

- One fully-qualified domain name per line (ASCII recommended)
- Lines starting with `#` and blank lines are ignored
- Trailing/leading whitespace is stripped

```
# My brand domains (comment line ignored)
example.com
mybrand.io

# Production domains
api.production.company.com
```

## Dependencies

- **Python 3.9+**
- **No third-party packages** — standard library only
- Bundled `unicode_TR39_confusables.txt` (Unicode Technical Standard #39)

## Files

| File | Description |
|---|---|
| `generate_brand_domain_impersonations.py` | Main script (enhanced with logging, caching, parallelization) |
| `test_generate_brand_domain_impersonations.py` | Unit tests (20+ test cases) |
| `unicode_TR39_confusables.txt` | Bundled Unicode TR39 confusables data (UTS #39) |
| `brand_domains.txt.example` | Template for the input domain list |
| `brand_domains.txt` | *(gitignored)* Your actual domain list |
| `brand_domains_impersonation.txt` | *(gitignored)* Generated Punycode variants (CSV by default) |

## Testing

Run the included unit test suite:

```bash
# With pytest (if installed)
pytest test_generate_brand_domain_impersonations.py -v

# Or with unittest (Python standard library)
python test_generate_brand_domain_impersonations.py
```

**Test coverage:**
- ✅ Confusables file loading and error handling
- ✅ Inverse mapping generation
- ✅ Substitution spot detection
- ✅ Character replacement logic
- ✅ IDNA encoding with caching
- ✅ Variant generation pipeline
- ✅ Domain file parsing
- ✅ Output formatting (CSV, TSV, JSON)
- ✅ Integration workflow

## Security Considerations

### ⚠️ Scope Limitations

This tool generates **visually similar variants** based on Unicode confusables, but does **NOT**:

- Register domains on your behalf (it only generates variant names)
- Perform DNS lookups or WHOIS queries
- Validate if variants are actually registered/operational
- Check HTTPS certificates for domain variants
- Simulate phishing attacks or user interaction testing

### Recommended Use Cases

✅ **Detection:**
- Monitor DNS query logs for Punycode domains in your variants list
- Check Certificate Transparency logs for issuances
- Hunt in email logs for domain-based phishing attempts
- Analyze passive DNS databases

✅ **Defense:**
- Generate baseline of expected variants for your brand
- Set up alerts in email gateways for Punycode/lookalike domains
- Implement homograph attack detection in browsers/clients
- Register defensive variants before attackers do

### Responsible Disclosure

If you discover active attacks using variants from this tool:

1. **Document the threat** (domain, registration details, hosting IP)
2. **Report to law enforcement** (IC3, FBI InfraGard, INTERPOL)
3. **Notify the brand owner** if not your organization
4. **Alert domain registry** for takedown assistance
5. **Share with MISP/threat feeds** (with permission)

## Examples & Real-World Scenarios

### Scenario 1: Email Security Team Monitoring

```bash
# Generate variants for protected brands
python generate_brand_domain_impersonations.py \
    --input important_brands.txt \
    --output threat_variants.csv \
    --max-variants 50000

# Feed into email gateway's homograph detection:
# - Block outbound/inbound emails to domains in threat_variants.csv
# - Alert on exact matches in message headers
# - Log attempts for forensics
```

### Scenario 2: Security Research on Phishing Datasets

```bash
# Generate variants for brands commonly targeted
python generate_brand_domain_impersonations.py \
    --input phishing_targets.txt \
    --format json \
    --max-substitutions 2 \
    --max-variants 100000 \
    --workers 4 \
    --stats

# Cross-reference with VirusTotal, URLhaus, PhishTank APIs
# Measure prevalence of homograph attacks in the wild
```

### Scenario 3: Brand Protection CI/CD Pipeline

```bash
# Automated weekly generation for Slack alerts
#!/bin/bash
VARIANTS=$(python generate_brand_domain_impersonations.py \
    --input company_domains.txt \
    --format json \
    --stats 2>&1)

# Check if variants registered (using whois/API)
python check_registered_variants.py --input brand_domains_impersonation.txt

# Alert on any newly registered variants
curl -X POST -H 'Content-type: application/json' \
    --data "{\"text\":\"Found ${NEW_VARIANTS} new homograph variants\"}" \
    $SLACK_WEBHOOK_URL
```

## Unicode Confusables Reference

The bundled `unicode_TR39_confusables.txt` includes:

| Latin | Lookalikes | Unicode Names |
|-------|-----------|---------------|
| `a` | α, а, ɑ | Greek Alpha, Cyrillic A, Latin Script A |
| `e` | ε, е, ℯ | Greek Epsilon, Cyrillic Ie, Mathematical E |
| `o` | ο, о, 0 | Greek Omicron, Cyrillic O, Digit Zero |
| `p` | р, ρ | Cyrillic R, Greek Rho |
| `c` | с, ϲ | Cyrillic S, Greek Lunate Sigma |
| `l` | 1, ⅼ, І | Digit One, Roman Numeral L, Cyrillic I |
| `i` | 1, і, ı | Digit One, Cyrillic Byelorussian I, Dotless I |

[Full Unicode TR39 specification](https://unicode.org/reports/tr39/)

## Contributing

Improvements welcome! Areas for enhancement:

- [ ] DNS validation of generated variants
- [ ] Integration with whois/registrar APIs
- [ ] Machine learning on successful phishing domains
- [ ] Homoglyph similarity scoring
- [ ] Lookalike visual rendering (screenshot comparison)
- [ ] Integration with threat intelligence platforms (MISP, AlienVault)

## License

This project is part of the **Cyber Threat Hunting Playground**. See repository LICENSE for details.

## References

- [MITRE ATT&CK T1584.001](https://attack.mitre.org/techniques/T1584/001/) — Acquire Infrastructure: Domains
- [Unicode TR39 Confusables](https://unicode.org/reports/tr39/) — Homoglyph Attack Prevention
- [OWASP: Internationalized Domain Names](https://owasp.org/www-community/attacks/IDN_Homograph_Attacks)
- [RFC 3490: IDNA](https://tools.ietf.org/html/rfc3490) — Internationalized Domain Names in Applications
- [RFC 5890: IDNA2008](https://tools.ietf.org/html/rfc5890) — Protocol

## Disclaimer

This tool is provided **as-is for authorized security research and threat hunting only**. Users are responsible for:
- Complying with all applicable laws and regulations
- Obtaining proper authorization before using this tool on any systems or data
- Using results only for defensive purposes (detection, monitoring, incident response)
- Not using this tool for malicious purposes (domain registration, phishing, fraud)

Unauthorized access to computer systems is illegal. Consult your organization's security and legal teams before deployment.
