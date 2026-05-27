#!/usr/bin/env python3
"""
Unit tests for generate_brand_domain_impersonations.py

Run with: python -m pytest test_generate_brand_domain_impersonations.py -v

Or without pytest: python test_generate_brand_domain_impersonations.py
"""

import unittest
import tempfile
from pathlib import Path
from generate_brand_domain_impersonations import (
    _load_tr39_confusable_map,
    _inverse_singlechar_confusables,
    _substitutable_spots,
    _apply_spot_replacements,
    _idna_encode_hostname,
    _variants_for_domain,
    _read_domains,
    _format_output_csv,
    _format_output_tsv,
    _format_output_json,
)


class TestConfusableLoading(unittest.TestCase):
    """Test confusables file loading and parsing."""

    def test_load_valid_confusables(self):
        """Test loading a valid confusables file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("# Unicode TR39 Confusables\n")
            f.write("0041 ; 0061 ; MA # LATIN CAPITAL LETTER A → a\n")
            f.write("0042 ; 0062 ; MA # LATIN CAPITAL LETTER B → b\n")
            f.flush()

            try:
                conf_map = _load_tr39_confusable_map(Path(f.name))
                self.assertEqual(len(conf_map), 2)
                self.assertEqual(conf_map[0x0041], chr(0x0061))
                self.assertEqual(conf_map[0x0042], chr(0x0062))
            finally:
                Path(f.name).unlink()

    def test_load_nonexistent_confusables(self):
        """Test loading a nonexistent confusables file."""
        with self.assertRaises(FileNotFoundError):
            _load_tr39_confusable_map(Path("/nonexistent/confusables.txt"))

    def test_skip_malformed_lines(self):
        """Test that malformed lines are skipped gracefully."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("# Valid line\n")
            f.write("0041 ; 0061 ; MA\n")
            f.write("INVALID LINE HERE\n")
            f.write("0042 ; 0062 ; MA\n")
            f.flush()

            try:
                conf_map = _load_tr39_confusable_map(Path(f.name))
                # Should load the valid lines and skip the invalid one
                self.assertGreaterEqual(len(conf_map), 2)
            finally:
                Path(f.name).unlink()


class TestInverseConfusables(unittest.TestCase):
    """Test inverse confusables mapping."""

    def test_inverse_singlechar_confusables(self):
        """Test creating inverse single-character confusables map."""
        conf_map = {
            ord("A"): "a",
            ord("B"): "b",
            ord("C"): "c",
        }
        inverse = _inverse_singlechar_confusables(conf_map)

        self.assertIn("a", inverse)
        self.assertIn("A", inverse["a"])
        self.assertIn("b", inverse)
        self.assertIn("B", inverse["b"])

    def test_inverse_skips_multichar_targets(self):
        """Test that multi-character targets are skipped."""
        conf_map = {
            ord("A"): "a",
            ord("B"): "bb",  # Multi-char target, should be skipped
        }
        inverse = _inverse_singlechar_confusables(conf_map)

        self.assertIn("a", inverse)
        self.assertNotIn("bb", inverse)

    def test_inverse_skips_identical_pairs(self):
        """Test that identical source/target pairs are skipped."""
        conf_map = {
            ord("A"): "a",
            ord("a"): "a",  # Identical, should be skipped
        }
        inverse = _inverse_singlechar_confusables(conf_map)

        # Should have only the A->a mapping, not a->a
        self.assertEqual(len(inverse), 1)


class TestSubstitutionSpots(unittest.TestCase):
    """Test finding substitutable spots in domains."""

    def test_substitutable_spots(self):
        """Test finding spots where substitutions can be made."""
        conf_map = {ord("a"): "α", ord("o"): "ο"}
        inverse = _inverse_singlechar_confusables(conf_map)

        labels = ["example"]
        spots = _substitutable_spots(labels, inverse)

        # Should find 'a' at position 0 and 'o' at position 4
        self.assertEqual(len(spots), 2)

    def test_no_substitutable_spots(self):
        """Test domain with no substitutable characters."""
        inverse = {
            "α": ("a",),
            "ο": ("o",),
        }
        labels = ["xyz123"]
        spots = _substitutable_spots(labels, inverse)

        self.assertEqual(len(spots), 0)


class TestSpotReplacements(unittest.TestCase):
    """Test applying spot replacements."""

    def test_apply_single_replacement(self):
        """Test applying a single character replacement."""
        labels = ["example"]
        replacements = [(0, 0, "α")]  # Replace 'e' with 'α'
        result = _apply_spot_replacements(labels, replacements)

        self.assertEqual(result, "αxample")

    def test_apply_multiple_replacements(self):
        """Test applying multiple replacements in same label."""
        labels = ["example"]
        replacements = [(0, 0, "α"), (0, 4, "ο")]  # Replace 'e' and 'e'
        result = _apply_spot_replacements(labels, replacements)

        self.assertEqual(result, "αxamplο")

    def test_apply_replacements_multiple_labels(self):
        """Test applying replacements across multiple labels."""
        labels = ["example", "com"]
        replacements = [(0, 0, "α"), (1, 0, "ᴄ")]  # Replace 'e' in first label, 'c' in second
        result = _apply_spot_replacements(labels, replacements)

        self.assertEqual(result, "αxample.ᴄom")


class TestIdnaEncoding(unittest.TestCase):
    """Test IDNA encoding with optional caching."""

    def test_valid_idna_encoding(self):
        """Test encoding a valid Unicode hostname."""
        result = _idna_encode_hostname("café.com")
        self.assertIsNotNone(result)
        self.assertIn("xn--", result)

    def test_invalid_idna_encoding(self):
        """Test that invalid Unicode returns None."""
        # This should fail or return None
        result = _idna_encode_hostname("\x00invalid")
        self.assertIsNone(result)

    def test_idna_caching(self):
        """Test IDNA encoding caching."""
        cache: dict = {}
        result1 = _idna_encode_hostname("café.com", cache=cache)
        result2 = _idna_encode_hostname("café.com", cache=cache)

        self.assertEqual(result1, result2)
        self.assertIn("café.com", cache)

    def test_cache_preserves_none(self):
        """Test that cache preserves None results."""
        cache: dict = {}
        result1 = _idna_encode_hostname("\x00invalid", cache=cache)
        result2 = _idna_encode_hostname("\x00invalid", cache=cache)

        self.assertIsNone(result1)
        self.assertIsNone(result2)


class TestVariantsGeneration(unittest.TestCase):
    """Test variant generation logic."""

    def test_variants_empty_domain(self):
        """Test that empty domains return empty variants."""
        inverse: dict = {}
        result = _variants_for_domain("", inverse, 1, 100)
        self.assertEqual(result, [])

    def test_variants_no_substitutions(self):
        """Test domain with no possible substitutions."""
        inverse: dict = {}
        result = _variants_for_domain("example.com", inverse, 1, 100)
        self.assertEqual(result, [])

    def test_variants_with_punycode(self):
        """Test that only Punycode variants are returned."""
        # Use actual confusables that produce Punycode
        # This is more of an integration test
        conf_map = {
            ord("e"): "ε",  # Greek epsilon
        }
        inverse = _inverse_singlechar_confusables(conf_map)
        result = _variants_for_domain("example.com", inverse, 1, 100)

        # Filter for actual xn-- results
        punycode_variants = [v for v in result if "xn--" in v]
        self.assertGreater(len(punycode_variants), 0)


class TestDomainReading(unittest.TestCase):
    """Test reading domains from file."""

    def test_read_valid_domains(self):
        """Test reading a valid domains file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("# Comment line\n")
            f.write("example.com\n")
            f.write("mybrand.io\n")
            f.write("\n")
            f.write("another.org\n")
            f.flush()

            try:
                domains = _read_domains(Path(f.name))
                self.assertEqual(len(domains), 3)
                self.assertEqual(domains[0][0], "example.com")
                self.assertEqual(domains[0][1], "example.com")
            finally:
                Path(f.name).unlink()

    def test_read_domains_preserves_case_in_display(self):
        """Test that original case is preserved for display."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Example.COM\n")
            f.write("MyBrand.IO\n")
            f.flush()

            try:
                domains = _read_domains(Path(f.name))
                self.assertEqual(domains[0][0], "Example.COM")
                self.assertEqual(domains[0][1], "example.com")
                self.assertEqual(domains[1][0], "MyBrand.IO")
                self.assertEqual(domains[1][1], "mybrand.io")
            finally:
                Path(f.name).unlink()

    def test_read_nonexistent_domains_file(self):
        """Test reading a nonexistent domains file."""
        with self.assertRaises(FileNotFoundError):
            _read_domains(Path("/nonexistent/domains.txt"))


class TestOutputFormatting(unittest.TestCase):
    """Test output formatting functions."""

    def test_format_csv(self):
        """Test CSV output formatting."""
        lines = _format_output_csv("example.com", ["xn--exmple-cua.com", "xn--exampl-jua.com"])
        self.assertEqual(len(lines), 2)
        self.assertIn("example.com,xn--exmple-cua.com", lines)
        self.assertIn("example.com,xn--exampl-jua.com", lines)

    def test_format_tsv(self):
        """Test TSV output formatting."""
        lines = _format_output_tsv("example.com", ["xn--exmple-cua.com"])
        self.assertEqual(len(lines), 1)
        self.assertIn("example.com\txn--exmple-cua.com", lines)

    def test_format_json(self):
        """Test JSON output formatting."""
        results = [("example.com", ["xn--exmple-cua.com"]), ("mybrand.io", [])]
        output = _format_output_json(results)

        import json
        parsed = json.loads(output)
        self.assertIn("version", parsed)
        self.assertIn("generated_at", parsed)
        self.assertIn("variants", parsed)
        self.assertEqual(len(parsed["variants"]), 1)


class TestIntegration(unittest.TestCase):
    """Integration tests for the full workflow."""

    def test_full_workflow_with_temp_files(self):
        """Test complete workflow with temporary files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create confusables file
            confusables_file = tmpdir_path / "confusables.txt"
            confusables_file.write_text("# Test confusables\n0065 ; 03B5 ; MA # e → ε\n")

            # Create domains file
            domains_file = tmpdir_path / "domains.txt"
            domains_file.write_text("# Test domains\nexample.com\n")

            # Load and process
            conf_map = _load_tr39_confusable_map(confusables_file)
            self.assertGreater(len(conf_map), 0)

            inverse = _inverse_singlechar_confusables(conf_map)
            self.assertGreater(len(inverse), 0)

            domains = _read_domains(domains_file)
            self.assertEqual(len(domains), 1)


if __name__ == "__main__":
    unittest.main()
