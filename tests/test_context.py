import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("cli", Path(__file__).parents[1] / "scripts" / "cli.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("unable to load installer module")
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


class ContextTests(unittest.TestCase):
    def test_upstream_shift_formula(self):
        self.assertEqual(cli.usable_prompt_limit(4096, 4), 2050)
        self.assertEqual(cli.usable_prompt_limit(32768, 4), 16386)
        self.assertEqual(cli.usable_prompt_limit(65536, 4), 32770)

    def test_default_budgets(self):
        c = cli.defaults()
        self.assertEqual(cli.runtime_context(c), 128000)
        self.assertEqual(cli.usable_prompt_limit(cli.runtime_context(c)), 64002)

    def test_lfm3_model_context_cap(self):
        c = cli.defaults()
        c["input_context_tokens"] = 64512
        with self.assertRaisesRegex(ValueError, "cannot exceed 64000"):
            cli.validate(c, Path("/tmp/config.json"))

    def test_render_has_cpu_and_gateway(self):
        c = cli.defaults()
        docs = cli.render(c, "x" * 48)
        payload = str(docs)
        self.assertIn("OLLAMA_LLM_LIBRARY", payload)
        self.assertIn("cpu", payload)
        self.assertIn("WWW-Authenticate", payload)
        self.assertIn("lfm3-vl-hermes:2b", payload)


if __name__ == "__main__":
    unittest.main()
