"""Smoke test: characterize sg13g2_inv_1 on a 2x2 grid with .ci/smoke_test.sh."""
import os
import subprocess
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PDK_ROOT = os.environ.get("TULIP_TEST_PDK_ROOT",
                          "/usr/local/share/pdk/IHP-Open-PDK")
MODELS = os.path.join(PDK_ROOT, "ihp-sg13g2", "libs.tech", "ngspice", "models")


class TestSmoke(unittest.TestCase):
    """Run the CI smoke test script against the SG13G2 models of PDK_ROOT."""

    @unittest.skipUnless(os.path.isdir(MODELS), "no IHP-Open-PDK SG13G2 models")
    def test_inv_1(self):
        """inv_1 characterizes without failed simulations."""
        proc = subprocess.run(
            [os.path.join(ROOT, ".ci", "smoke_test.sh"), PDK_ROOT],
            cwd=ROOT, capture_output=True, text=True, check=False,
            timeout=900)
        self.assertEqual(proc.returncode, 0,
                         proc.stdout[-3000:] + proc.stderr[-3000:])
        self.assertIn("SMOKE TEST PASSED", proc.stdout)


if __name__ == "__main__":
    unittest.main()
