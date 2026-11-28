#!/usr/bin/env python3
"""scripts/diagnose.sh 的回归测试：不需要 docker / GPU。

用 PATH 里的假 docker 模拟一个「服务 ready、容器 unhealthy、引擎退出过 3 次」的现场，
断言诊断输出确实收齐了这几处（之前的版本漏掉它们）：

- 精确路径 /data/state/strata/launch/*.log 的引擎日志（三层目录）；
- **全量**服务日志里的 `the engine stopped ... (exit code -9)`（不只末尾 500 行）；
- healthcheck 的 Status / FailingStreak 与每条检查的 ExitCode + 输出；
- 最近 24 小时的容器事件（die）；
- 容器内 NVML 失败与宿主侧 dmesg 的 root 命令提示。

    pixi run test
"""

from __future__ import annotations

import glob
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

FAKE_DOCKER = r'''#!/usr/bin/env bash
case "$1" in
  info) exit 0 ;;
  inspect)
    case "$*" in
      *"State.Health.Log"*) echo "exit=1 start=2026-11-28T08:05:00Z output=curl: (7) Failed to connect to 127.0.0.1 port 8080" ;;
      *"State.Health"*) echo "Status=unhealthy FailingStreak=3" ;;
      *"State.Status"*) echo "running restarts=0 oomkilled=false started=2026-11-27T01:00:00Z" ;;
      *"Memory="*) echo "Memory=0 MemorySwap=0 OomKillDisable=<no value> ShmSize=2147483648 NanoCpus=0" ;;
      *) exit 0 ;;
    esac
    ;;
  compose)
    case "$2" in
      config) echo '{"services":{"strata":{"mem_limit":null,"shm_size":"2147483648"},"idle-unload":{}}}' ;;
      ps)
        if [ "$3" = "--status" ]; then echo strata; else echo "qwen-strata  strata  Up 31 hours (unhealthy)"; fi
        ;;
      logs)
        if [ "$*" = "compose logs --no-color --tail=500 strata" ]; then
          echo "qwen-strata | Starting Strata: inkling-2-mini/SQ-10"
          echo "qwen-strata | Engine log /data/state/strata/launch/inkling-2-mini.engine.log"
        else
          echo "qwen-strata | Starting Strata: inkling-2-mini/SQ-10"
          echo "qwen-strata | [strata] the engine stopped 3 times in 10 minutes (exit code -9)"
          echo "qwen-strata | [strata] Its log: /data/state/strata/launch/inkling-2-mini.engine.log"
          echo "qwen-strata | [strata] the model unloads after 1800 s without requests"
        fi
        ;;
      exec)
        case "$*" in
          *"state/strata/launch 2>/dev/null"*) echo "total 12"; echo "-rw-r--r-- 1 ubuntu ubuntu 9990 Nov 28 08:05 inkling-2-mini.engine.log" ;;
          *".log"*) echo "--- /data/state/strata/launch/inkling-2-mini.engine.log"; echo "ENGINE-LOG-IN-CONTAINER"; echo "CUDA error 2 (out of memory)" ;;
          *".json"*) echo '{"args":["--max-context","131072","--ram-experts"]}' ;;
          *"ulimit"*) echo "rtprio 0"; echo "locked memory(kbytes) unlimited" ;;
          *"df -h"*) echo "shm 2.0G 0 2.0G 0% /dev/shm" ;;
          *"nvidia-smi"*) echo "Failed to initialize NVML: Unknown Error" ;;
          *"launch --status"*) echo "inkling-2-mini: ready at http://127.0.0.1:8080" ;;
          *) echo "ok" ;;
        esac
        ;;
      *) exit 0 ;;
    esac
    ;;
  events)
    # 与真实 docker 一样：参数先回显一份（测试据此断言 --filter event=...），
    # 然后输出 400 条 healthcheck 噪声 + 一条真正的 die。脚本若再 tail，die 会被挤掉。
    echo "ARGS: $*"
    i=0
    while [ "$i" -lt 400 ]; do echo "1795856000 exec_die name=qwen-strata"; i=$((i + 1)); done
    echo "1795856785 die name=qwen-strata exit=0"
    ;;
  *) exit 0 ;;
esac
'''


class DiagnoseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="diagnose-")
        shutil.copytree(HERE, os.path.join(self.tmp, "scripts"))
        shutil.copy(os.path.join(ROOT, ".env.example"), os.path.join(self.tmp, ".env.example"))
        # 宿主侧的同一份引擎日志（容器内读不到时的后备）
        engine_dir = pathlib.Path(self.tmp, "strata-data", "state", "strata", "launch")
        engine_dir.mkdir(parents=True)
        (engine_dir / "inkling-2-mini.engine.log").write_text(
            "ENGINE-LOG-ON-HOST\n", encoding="utf-8")
        fake_bin = pathlib.Path(self.tmp, "fakebin")
        fake_bin.mkdir()
        docker = fake_bin / "docker"
        docker.write_text(FAKE_DOCKER, encoding="utf-8")
        docker.chmod(0o755)
        # 固定 dmesg 的无权限分支（root / CAP_SYSLOG 的 CI 上真实 dmesg 会走另一条路）
        dmesg = fake_bin / "dmesg"
        dmesg.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
        dmesg.chmod(0o755)
        self.env = os.environ.copy()
        self.env.update({
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "STRATA_DATA_DIR": os.path.join(self.tmp, "strata-data"),
        })

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_diagnose(self) -> str:
        result = subprocess.run(
            ["bash", os.path.join(self.tmp, "scripts", "diagnose.sh")],
            cwd=self.tmp, env=self.env, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        files = sorted(glob.glob(os.path.join(self.tmp, "output", "diagnose-*.txt")))
        self.assertTrue(files, "diagnose 没有生成输出文件")
        return pathlib.Path(files[-1]).read_text(encoding="utf-8")

    def test_collects_the_engine_log_at_its_exact_path(self) -> None:
        text = self.run_diagnose()
        self.assertIn("/data/state/strata/launch", text)
        self.assertIn("inkling-2-mini.engine.log", text)
        self.assertIn("ENGINE-LOG-IN-CONTAINER", text)
        self.assertIn("ENGINE-LOG-ON-HOST", text)

    def test_finds_engine_stopped_in_the_full_service_log(self) -> None:
        text = self.run_diagnose()
        self.assertIn("全量服务日志里的关键错误", text)
        self.assertIn("the engine stopped 3 times in 10 minutes (exit code -9)", text)
        # 末尾 500 行那份也有，但全量段必须单独存在（旧实现只有 tail）
        self.assertIn("grep 全部历史", text)

    def test_reports_healthcheck_details(self) -> None:
        text = self.run_diagnose()
        self.assertIn("容器健康检查", text)
        self.assertIn("Status=unhealthy", text)
        self.assertIn("FailingStreak=3", text)
        self.assertIn("exit=1 start=", text)
        self.assertIn("Failed to connect to 127.0.0.1 port 8080", text)

    def test_events_are_filtered_and_survive_healthcheck_noise(self) -> None:
        # 回归：healthcheck 每 30 秒产生 exec_create/exec_start/exec_die，旧实现用 tail -60
        # 只装得下约 10 分钟，真正 9 小时前的 die 会被挤掉
        text = self.run_diagnose()
        self.assertIn("最近 24 小时的容器事件", text)
        events = text.split("最近 24 小时的容器事件", 1)[1]
        self.assertIn("--filter event=die", events)
        for event in ("event=oom", "event=kill", "event=destroy", "event=restart"):
            self.assertIn(event, events)
        self.assertIn("die name=qwen-strata exit=0", events)
        # 400 条噪声一行不少：说明脚本没有 tail 截断
        self.assertEqual(events.count("exec_die name=qwen-strata"), 400)

    def test_warns_to_keep_the_engine_log_before_recreating(self) -> None:
        text = self.run_diagnose()
        self.assertIn("复现 503 后先别重启 / 重建容器", text)
        self.assertIn("截断覆盖", text)
        self.assertIn("/data/state/strata/launch/*.engine.log", text)

    def test_reports_container_nvml_failure_and_kernel_command(self) -> None:
        text = self.run_diagnose()
        self.assertIn("Failed to initialize NVML", text)
        self.assertIn("sudo dmesg -T", text)
        self.assertIn("NVRM", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
