#!/usr/bin/env python3
"""scripts/ctx_budget.py 的单元测试：纯计算，不需要 GPU / docker。

    pixi run test
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "ctx_budget.py")
sys.path.insert(0, HERE)

import ctx_budget  # noqa: E402 - 单测与被测脚本同目录


class ParseContextTest(unittest.TestCase):
    def test_suffixes(self) -> None:
        self.assertEqual(ctx_budget.parse_context("32k"), 32768)
        self.assertEqual(ctx_budget.parse_context("128K"), 131072)
        self.assertEqual(ctx_budget.parse_context("256k"), 262144)
        self.assertEqual(ctx_budget.parse_context("8k"), 8192)
        self.assertEqual(ctx_budget.parse_context("1M"), 1024 * 1024)

    def test_plain_numbers(self) -> None:
        self.assertEqual(ctx_budget.parse_context("131072"), 131072)
        self.assertEqual(ctx_budget.parse_context(" 32768 "), 32768)
        self.assertEqual(ctx_budget.parse_context(65536), 65536)

    def test_empty_and_garbage(self) -> None:
        for raw in (None, "", "   ", "auto", "128k?", "0", "-5"):
            self.assertIsNone(ctx_budget.parse_context(raw), raw)

    def test_format_round_trip(self) -> None:
        self.assertEqual(ctx_budget.format_context(131072), "128k")
        self.assertEqual(ctx_budget.format_context(12345), "12345")


class InklingBudgetTest(unittest.TestCase):
    def test_host_kv_is_linear(self) -> None:
        self.assertAlmostEqual(ctx_budget.host_kv_gib(32768), 1.07, places=2)
        self.assertAlmostEqual(ctx_budget.host_kv_gib(131072), 4.28, places=2)

    def test_32k_on_official_floor_fits(self) -> None:
        # 16 GiB 是 SQ-10 的官方最低门槛（不是现场容量）：32K 在这个低配边界上刚好够
        out = ctx_budget.inkling_budget("SQ-10", 32768, 16)
        self.assertEqual(out["status"], "ok", out["message"])
        self.assertIsNone(out["suggested"])

    def test_128k_below_official_floor_is_rejected(self) -> None:
        # 低配边界：128K 的 host KV 超出官方最低门槛；这条只说「低于门槛」，不是任何现场机器的结论
        out = ctx_budget.inkling_budget("SQ-10", 131072, 16)
        self.assertEqual(out["status"], "bad", out["message"])
        self.assertEqual(out["suggested"], "32k")
        self.assertAlmostEqual(out["host_kv_gib"], 4.28, places=2)
        self.assertGreater(out["required_gib"], 16)
        self.assertIn("官方最低", out["message"])

    def test_128k_on_a_real_128_gib_host_fits(self) -> None:
        # 现场宿主是 128 GiB（SIL-991 审计 125.7 GiB）：128K 的 4.28 GiB host KV 完全够，
        # 不能因为官方门槛是 16 GiB 就把这类机器也降成 32K
        out = ctx_budget.inkling_budget("SQ-10", 131072, 128)
        self.assertEqual(out["status"], "ok", out["message"])
        self.assertIsNone(out["suggested"])
        self.assertIn("本机实际 128 GiB", out["message"])
        self.assertEqual(ctx_budget.inkling_budget("SQ-10", 65536, 128)["status"], "ok")

    def test_no_forced_downgrade_above_the_floor(self) -> None:
        # select_model 只在 bad/warn 时改写；48 GiB 以上 128K/256K 都应是 ok
        for ctx in (131072, 262144):
            self.assertEqual(ctx_budget.inkling_budget("SQ-10", ctx, 48)["status"], "ok", ctx)

    def test_128k_needs_more_memory(self) -> None:
        self.assertEqual(ctx_budget.inkling_budget("SQ-10", 131072, 24)["status"], "ok")
        self.assertEqual(ctx_budget.inkling_budget("SQ-10", 131072, 19)["status"], "warn")

    def test_256k_on_32_gib_fits(self) -> None:
        out = ctx_budget.inkling_budget("SQ-10", 262144, 32)
        self.assertEqual(out["status"], "ok", out["message"])

    def test_bigger_packs_need_more_ram(self) -> None:
        # SQ-20 的官方门槛是 32 GiB；24 GiB 机器下 32K 也不该判 ok
        self.assertEqual(ctx_budget.inkling_budget("SQ-20", 32768, 24)["status"], "bad")
        self.assertEqual(ctx_budget.inkling_budget("SQ-20", 32768, 31)["status"], "warn")
        self.assertEqual(ctx_budget.inkling_budget("SQ-20", 32768, 32)["status"], "ok")

    def test_unknown_memory_is_unknown_not_bad(self) -> None:
        out = ctx_budget.inkling_budget("SQ-10", 131072, None)
        self.assertEqual(out["status"], "unknown")

    def test_non_inkling_family_is_skipped(self) -> None:
        self.assertEqual(ctx_budget.assess("qwen", "IQ2_XS", "128k", 16)["status"], "skip")
        self.assertEqual(ctx_budget.assess("inkling-2-mini", "SQ-10", "128k", 16)["status"], "bad")

    def test_bad_context_string_is_unknown(self) -> None:
        self.assertEqual(ctx_budget.assess("inkling-2-mini", "SQ-10", "", 16)["status"], "unknown")


class CommandLineTest(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True, timeout=10)

    def test_kv_output_for_rejected_context(self) -> None:
        result = self.run_cli("--family", "inkling-2-mini", "--model", "SQ-10",
                              "--context", "128k", "--memory", "16")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("status=bad", result.stdout)
        self.assertIn("suggested=32k", result.stdout)

    def test_json_output(self) -> None:
        result = self.run_cli("--family", "inkling-2-mini", "--context", "32k",
                              "--memory", "16", "--format", "json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"status": "ok"', result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
