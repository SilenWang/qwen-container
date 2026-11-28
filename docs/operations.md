# 前置条件、任务与排障

## 前置条件

| 项目 | 要求 |
|---|---|
| 系统 | Linux x86-64（WSL2 可用） |
| GPU | NVIDIA RTX 20/30/40/50，12G 显存起；GTX 10 / P100 / V100 用 cuda12 镜像 |
| 驱动 | cuda13 镜像要 580+；cuda12 镜像 575+ |
| Docker | Docker Engine + `docker compose` v2 + nvidia-container-toolkit |
| pixi | 用于安装依赖与跑任务 |
| CPU | 支持 AVX2，核心越快越好（专家在 CPU 侧算） |
| 内存 | 32 GiB 起；Flash-Next `IQ2_XS` 约需 46 GiB，建议 64 GiB；35B-A3B `UD-Q4_K_XL` 32 GiB 起；Inkling `SQ-10` 16 GiB 起 |
| 磁盘 | 数据目录建议 NVMe：Flash-Next 66–84 GB，35B-A3B 约 23 GB，Inkling 11–20 GB（另加预处理包与镜像） |

`docker` 是宿主级服务，无法由 pixi 安装；其余依赖（python / curl / jq）都由 pixi 提供。

## 环境自检

```bash
pixi run preflight
```

检查 docker / GPU 运行时 / 驱动版本 / AVX2 / 内存 / 磁盘 / 端口 / 已下载的模型 / 解析后的容器参数。
自检会按 `.env` 里当前的模型和档位核算内存与磁盘需求；切换模型后再跑一次即可。

## 常用任务

```bash
pixi run up            # 启动（首次下载当前模型）
pixi run logs          # 跟随日志
pixi run ps            # 容器状态 / 健康检查
pixi run key           # 打印 API key（要等容器 healthy 之后才有）
pixi run smoke         # 冒烟：健康检查 + 模型列表 + 一次补全 + 吞吐
pixi run longctx       # 长上下文召回实测（needle）
pixi run idle-logs     # 空闲释放看门狗日志
pixi run test          # 空闲释放看门狗单元测试
pixi run recreate      # 改了 .env 后按新配置重建容器
pixi run restart       # 重启（不重读 .env）
pixi run stop          # 停止（卸载模型，释放显存与内存）
pixi run down          # 停止并移除容器
pixi run config        # 只看解析后的 compose 配置
pixi run pull          # 更新镜像
pixi run shell         # 进容器
```

`pixi run longctx` 默认按 `.env` 的上下文挑深度（8K / 16K / 32K / 64K 中放得下的），也可以指定：

```bash
pixi run longctx -- --depths 4096,16384,32768
```

## 模型切换

一个服务，两套参数。切换只改 `.env` 的三行并重建容器：

| 想跑 | 命令 | 结果 |
|---|---|---|
| Qwen3.8-Flash-Next（默认） | `pixi run model-flash` | `FAMILY=qwen` / `MODEL=IQ2_XS` / `PRESET=flash-next` |
| Qwen3.6-35B-A3B | `pixi run model-35b` | `FAMILY=qwen36` / `MODEL=UD-Q4_K_XL` / `PRESET=qwen36-35b` |
| Inkling 2 Mini | `pixi run model-inkling` | `FAMILY=inkling-2-mini` / `MODEL=SQ-10` / `PRESET=inkling-2-mini` |

所有模型共用一个数据目录：权重、预处理包、各自的 preset 都保留，切回来不用重新下载。
Inkling 2 Mini 走魔搭（`resolve/master`）或 HuggingFace，下载源配置见
[strata.md](strata.md#国内网络modelscope-或-hf-镜像)。
12G 卡一次只加载一个模型，重建容器会先卸载旧模型、释放显存。

只想换档位（例如 Flash-Next 的 `IQ2_XS` → `IQ3_S`）：改 `.env` 的 `STRATA_MODEL`，`pixi run recreate`。

## 空闲释放

默认开启：30 分钟没有新请求就卸载模型，把显存和内存还回去；下一次请求由 Strata 自动重新加载。

实现方式是 `idle-unload` 辅助容器：它每 60 秒查一次 `/v1/status`，如果模型已加载、没有请求在跑，
且距最后一次请求超过 `STRATA_IDLE_UNLOAD_S` 秒，就调用 Strata 的 `POST /unload`。辅助容器和主服务
用同一个镜像，不占 GPU，只读数据目录里的 API key（或 `.env` 的 `STRATA_API_KEY`）。

```bash
# .env
STRATA_IDLE_UNLOAD_S=1800   # 秒；0 = 关闭
STRATA_IDLE_POLL_S=60       # 检查间隔；阈值误差不超过这个值
STRATA_IDLE_UNLOAD_COOLDOWN_S=600   # 两次卸载的最小间隔（秒）；0 = 不限制
```

一次空闲只卸载一次：卸载成功后记下当时的 `last_request_at`，在它前进（真的有新请求）之前不会再发
`/unload`，两次卸载之间还有 `STRATA_IDLE_UNLOAD_COOLDOWN_S`（默认 600 秒）的冷却。这是为了避开
Strata 0.2.21 起的熔断——同一个引擎 10 分钟内死 3 次就停止启动并返回 503
（`The engine stopped 3 times in 10 minutes`）；重复卸载、或者刚重载就再卸，会把一次重载失败叠成熔断。
「这段空闲已经卸过」记在容器的 `/tmp/idle-unload-state.json`，看门狗自己被重启也不会立刻再卸一次。

改完 `pixi run recreate`。看它做了什么：`pixi run idle-logs`；`pixi run ps` 里会多一个
`qwen-idle-unload`。卸载后 `/v1/models` 仍会列出模型（状态 `unloaded`），第一次请求会等模型重新加载。
释放的是引擎进程的显存和匿名内存；模型文件可能仍留在内核页缓存里（可回收内存），系统需要时会自动让出。

不想用这个辅助容器就设 `STRATA_IDLE_UNLOAD_S=0`；主服务本身不受影响。

## 数据落在哪

| 内容 | 路径 | 说明 |
|---|---|---|
| 模型权重（所有模型） | Qwen：`./strata-data/Strata-data/models/<档位>/`；Inkling：`./strata-data/Strata-data/models/inkling-2-mini/` | 首启自动下载；可用 `STRATA_DATA_DIR` 换位置 |
| 预处理包 + 草稿层 | `./strata-data/Strata-data/packs/` | 本地生成，约 2 GB |
| preset（运行参数 / 调参结果） | `./strata-data/config/strata/launch-presets/` | 每个模型一个 preset，切换互不覆盖 |
| API key | `./strata-data/state/api-key` | 首启自动生成，`pixi run key` 查看（要等 healthy） |

这些都在 `.gitignore` 里，不会提交；删容器或换镜像都不会丢。

## 排障

**起不来，日志说找不到模型文件**：数据目录里没有对应档位的 GGUF。如果是手动放的模型，按
[docs/strata.md](strata.md#复用已经下载好的模型) 指 `STRATA_DATA_DIR` 或用 `MODEL_PATH`；否则让它自己下载。

**端口被占用**：改 `.env` 的 `STRATA_PORT`，改完 `pixi run recreate`。

**报 “The engine in this image does not match this computer's graphics card” 或启动即退出**：镜像档位与驱动不匹配 ——
cuda13 要驱动 580+；驱动 575–579 或 GTX 10 / P100 / V100 换成 `cuda12` 镜像
（`STRATA_IMAGE=ghcr.io/niko1221/strata:0.2.21-cuda12`）。

**一直 `starting`**：首次启动在下载模型，看 `pixi run logs` 的进度；下载可断点续传，中断后重新
`pixi run up` 会接着下。

**`pixi run key` 报 “There is no API key for that launch”**：容器还在下载 / 加载，key 要到服务跑起来才生成。
等 `pixi run ps` 显示 `healthy` 再取；想自己定 key 就在 `.env` 里设 `STRATA_API_KEY`（16 字符以上）。

**`pixi run smoke` 报“服务在 3600s 内没有就绪”**：首启下载可能超过 1 小时。
`STRATA_READY_TIMEOUT=21600 pixi run smoke` 放大等待时间，或者先 `pixi run ps` 等到 healthy 再跑。

**`smoke` / `longctx` 说“返回内容为空”**：模型把 max_tokens 用在了思考段。脚本默认已用
`chat_template_kwargs.enable_thinking=false` 关掉思考，并在正文为空时自动放大预算重试一次；
仍为空就调大 `SMOKE_MAX_TOKENS` / `LONGCTX_MAX_TOKENS`。

**切了模型但服务还是旧的**：`.env` 改了没重建容器。`pixi run recreate`；`restart` 不重读配置。

**镜像升到 0.2.21 后第一次切到 Inkling 2 Mini**：镜像自带 `strata-inkling` 0.1.67，
不需要另装，也不需要首启升级（组件目录只读不影响）；切换只会下载模型包。老镜像（0.2.11）不认这个 family，`FAMILY` 写 `inkling-2-mini` 会报模型不存在 ——
先 `pixi run pull` 再 `pixi run recreate`。

**Inkling 在 12 GiB 卡上 32K 就 CUDA OOM**：引擎要 ≥ 0.1.66 才会把装不下的冷专家放到内存/SSD。
0.2.19 镜像自带的是 0.1.65，它会把 3072 个专家全塞进显存（日志里 `experts 8.60 GB` 一个不少），
12 GiB 必然爆。用 0.2.21 及以上镜像（内置 0.1.67）即可，日志会显示
`Engine for Inkling-2-Mini` 为 0.1.67 且专家数少于全量；不需要手动 `strata update`。

**整机卡住 / 内存被吃满**：Strata 要往内存里装几十 GB，首次加载 1–3 分钟机器很卡属正常；
长期不够就换小档（Flash-Next 用 `Q2_0` / `coder`，35B-A3B 用 `UD-Q4_K_XL`）或在 `.env` 设
`STRATA_RAM_KEEP_GIB=8` 给系统留内存。

**闲置卸载后第一次请求 503 `The engine stopped 3 times in 10 minutes`**：这是 Strata 0.2.21 起的熔断
保护——同一个引擎 10 分钟内停了 3 次就不再拉起，等 10 分钟会自动重试，`pixi run recreate` 立刻清空计数。
先看现场：`pixi run logs | tail -100`（引擎最后一句常常是 `engine started: ...` 之后就没输出），
再 `dmesg -T | grep -i -E 'killed process|out of memory'`。Inkling 最常见的原因是**上下文超过机器内存**：
它从 32K 起把 KV 放到 host（内存），32K 约 1.1 GiB、128K 约 4.3 GiB；16 GiB 内存的机器用 128K，
卸载后重新加载的峰值会顶到内存上限，引擎被 OOM killer 结束，重试 3 次就熔断。
修法：`STRATA_CONTEXT=32k` 后 `pixi run recreate`（`pixi run model-inkling` 会按内存自动降）。
`pixi run preflight` 现在会在启动前按机器内存核算，装不下直接报错并给出建议值。

**很慢、磁盘灯常亮**：内存不足，专家在从 SSD 读。换小档，或确认数据目录（`STRATA_DATA_DIR`）在 NVMe 上。

**长上下文首轮特别慢**：prefill 是瓶颈，属预期；长会话尽量复用上下文，或用 `pixi run longctx`
实测召回与端到端速度。
