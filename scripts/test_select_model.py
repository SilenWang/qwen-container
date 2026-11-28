#!/usr/bin/env python3
"""scripts/select_model.sh 的上下文行为测试：切换时只在「装不下」时降档。

回归点（PR #6 审计）：
- 128 GiB 机器上 `128k` 与 `131072` 都必须原样保留（不能被「档位常用值」逻辑改成 32k）；
- 16 GiB（SQ-10 官方最低门槛）上两种写法都降到 32k；
- 非 Inkling 档位仍按旧规则把模板默认值规范成该档位常用值。

测试用 `STRATA_SELECT_NO_UP=1` 只改写 .env，不会碰 docker / 容器。

    pixi run test
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


class SelectModelContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="select-model-")
        shutil.copytree(HERE, os.path.join(self.tmp, "scripts"))
        shutil.copy(os.path.join(ROOT, ".env.example"), os.path.join(self.tmp, ".env.example"))
        self.script = os.path.join(self.tmp, "scripts", "select_model.sh")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_env(self, ctx: str | None) -> None:
        """.env 以模板为底；ctx=None 表示整行删掉（没设置上下文）。"""
        src = pathlib.Path(self.tmp, ".env.example").read_text(encoding="utf-8")
        lines: list[str] = []
        for line in src.splitlines():
            if line.startswith("STRATA_CONTEXT="):
                if ctx is None:
                    continue
                line = f"STRATA_CONTEXT={ctx}"
            lines.append(line)
        pathlib.Path(self.tmp, ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def context(self) -> str | None:
        text = pathlib.Path(self.tmp, ".env").read_text(encoding="utf-8")
        match = re.search(r"^STRATA_CONTEXT=(.*)$", text, re.MULTILINE)
        return match.group(1).strip() if match else None

    def run_select(self, memory: int, ctx: str | None, model: str = "inkling") -> subprocess.CompletedProcess[str]:
        self.write_env(ctx)
        env = os.environ.copy()
        env.update({"STRATA_MEM_GIB": str(memory), "STRATA_SELECT_NO_UP": "1"})
        return subprocess.run(
            ["bash", self.script, model],
            cwd=self.tmp, env=env, capture_output=True, text=True, timeout=30,
        )

    # ---- 128 GiB：预算 ok，用户的值必须保留 ----

    def test_128_gib_keeps_128k(self) -> None:
        result = self.run_select(128, "128k")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.context(), "128k")
        self.assertIn("保留 STRATA_CONTEXT=128k", result.stdout)
        self.assertIn("够用", result.stdout)

    def test_128_gib_keeps_token_form(self) -> None:
        result = self.run_select(128, "131072")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.context(), "131072")
        self.assertIn("保留 STRATA_CONTEXT=131072", result.stdout)

    def test_48_gib_keeps_256k(self) -> None:
        result = self.run_select(48, "256k")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.context(), "256k")

    # ---- 16 GiB：官方最低门槛，两种写法都降档 ----

    def test_16_gib_downgrades_128k(self) -> None:
        result = self.run_select(16, "128k")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.context(), "32k")
        self.assertIn("已改写为 STRATA_CONTEXT=32k", result.stdout)

    def test_16_gib_downgrades_token_form(self) -> None:
        result = self.run_select(16, "131072")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.context(), "32k")
        self.assertIn("已改写为 STRATA_CONTEXT=32k", result.stdout)

    def test_missing_context_gets_the_hint(self) -> None:
        result = self.run_select(16, None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.context(), "32k")
        self.assertIn("上下文未设置", result.stdout)

    # ---- 非 Inkling：保持旧的「档位常用值」规则 ----

    def test_flash_still_normalises_the_template_value(self) -> None:
        result = self.run_select(128, "32k", model="flash")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.context(), "128k")

    # ---- 测试/手动切档不走 docker ----

    def test_no_up_switch_skips_the_container(self) -> None:
        result = self.run_select(128, "128k")
        self.assertIn("没有重建容器", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
