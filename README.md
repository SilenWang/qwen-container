# Qwen3.6-35B-A3B 容器化推理（12G 显存 / 128K 上下文）

用 **pixi** 管依赖、用 **Docker** 跑 [llama.cpp](https://github.com/ggml-org/llama.cpp) 的 `llama-server`，
默认就按 **12G 显存 + 128K 上下文 + 速度可用** 这一目标配置 **Qwen3.6-35B-A3B**（MoE，35B 总参 / 每 token 激活约 3B）。

```bash
cp .env.example .env     # 可选：改端口 / 上下文 / 调参档位
pixi run download        # 下载 GGUF 权重（约 22.4 GB）
pixi run up              # 启动容器
pixi run smoke           # 自检：健康检查 + n_ctx 核对 + 一次补全 + 吞吐
```

服务起来后是 OpenAI 兼容接口：`http://127.0.0.1:8080/v1`。

---

## 为什么是这套配置

### 框架只有 llama.cpp 这一条路

12G 卡放不下 22 GB 权重，必须把一部分权重放在系统内存里。能满足这个前提的框架只有 llama.cpp 生态：

| 要求 | llama.cpp 的能力 |
|---|---|
| 权重低位量化 | GGUF（Q4_K / IQ 系列） |
| 权重按层卸载到内存 | `--n-cpu-moe`（只把 MoE 专家挪到内存，非专家权重留在显存） |
| KV cache 量化 | `--cache-type-k/-v`，把 128K 的 KV 压到 1.3 GiB（q8_0）/ 2.5 GiB（f16） |
| 长上下文下的注意力加速 | `--flash-attn on` |

ExLlama / vLLM / SGLang 假定权重常驻显存，直接用不了；Ollama、LM Studio 只是 llama.cpp 的封装，KV/卸载控制反而更粗。

### 为什么是 A3B 而不是 27B dense

关键不是「模型多大」，而是**每生成一个 token 要从慢内存读多少字节**。dense 模型每个 token 都要读全部权重，12G 卡只能卸载，实测掉到 3–6 tok/s；
A3B 每 token 只激活约 3B，专家在内存侧只需读约 0.5 GB，同一张 12G 卡能跑到几十 tok/s。128K 的 KV 也更小。

> 这套选型来自 SIL-240 的调研结论（12G 显存 + 128K + 速度可用 → 换 A3B 级模型）。本仓库把它落成可运行的工程。

### 显存账（128K）

权重按 `UD-Q4_K_XL`（22.36 GB）算 —— 下面这组数字是**解析该 GGUF 的张量表实测**的（733 个张量，合计 22.35 GB）：

| 张量分组 | 大小 | 放哪 |
|---|---|---|
| MoE 专家（`*_exps*`，40 层合计） | **19.67 GB**（88%） | 按 `N_CPU_MOE` 拆分 |
| ↳ 前 N 层（`N_CPU_MOE` 控制） | 每层平均 **0.492 GB** | 系统内存 |
| ↳ 其余 40−N 层 | 例如 N=28 → **5.90 GB** | 显存 |
| 非专家权重（attention / GDN / shared expert / router / embedding / output） | **2.68 GB** | 显存（每 token 都要用，不能挪） |
| KV cache @128K, q8_0 | 1.33 GiB | 显存 |
| 计算缓冲 / 运行时 | 约 1 GB | 显存 |

把 12G 卡按「约 11.4 GiB 可用」（扣掉桌面占用）代入：

| `N_CPU_MOE` | 显存里的权重 | + KV + 缓冲 | 结论 |
|---|---|---|---|
| 24 | 2.50 + 16×0.458 = 9.83 GiB | ≈ 12.2 GiB | **大概率 OOM** |
| 28 | 2.50 + 12×0.458 = 8.00 GiB | ≈ 10.3 GiB | 约 1 GiB 余量，**默认值** |
| 32 | 2.50 + 8×0.458 = 6.16 GiB | ≈ 8.5 GiB | 更稳，更慢 |

也就是说：**专家张量占了权重的 88%，`N_CPU_MOE` 是唯一真正省显存的旋钮**；
而「理论上算得下」和「实际跑得住」之间就差 1 GiB 左右 —— 所以默认值给的是留有余量的 28，
并且必须按下面的调参流程在你的卡上扫一遍。

> 对照：调研里提到的 `-ncmoe 20 是海市蜃楼` 就是这张表的直接后果 —— 空上下文看着够用，
> 真实长上下文一来就 OOM。默认值取 28 而不是调研里那个 24，原因就在这里。

### KV cache 为什么这么小

`Qwen3.6-35B-A3B` 是混合架构：40 层里只有每 4 层的 1 层是真注意力（`full_attention_interval: 4` → 10 层全注意力），
其余 30 层是 GDN 线性注意力，用固定大小的循环状态而不是随长度增长的 KV。

所以 KV 只跟那 10 层有关：

```
每 token / 每层 = 2 (K+V) × num_key_value_heads(2) × head_dim(256) × 2 B(f16)
10 层 × 2 KiB = 20 KiB/token          → 128K ≈ 2.5 GiB (f16)
q8_0（8.5 bit）≈ 10.6 KiB/token       → 128K ≈ 1.33 GiB (q8_0)
```

对照同场景的 27B dense（约 64 KiB/token），128K 的 KV 直接少了 3 倍以上，
这就是 12G 卡还能留出显存的根本原因 —— 代价是权重那边 128K 几乎不额外花钱，
**真正贵的是 MoE 专家，不是上下文**。

---

## 前置条件

| 项目 | 要求 |
|---|---|
| 系统 | Linux（WSL2 也可） |
| GPU | NVIDIA，12G 显存起步 |
| 驱动 | 支持 CUDA 12 的驱动 + **nvidia-container-toolkit**（`docker run --gpus all` 可用） |
| Docker | Docker Engine + `docker compose` v2 |
| pixi | 用于安装依赖与跑任务（`pixi run xxx`） |
| CPU | 多核（MoE 专家在内存侧靠 CPU 读；`--threads` 后文可调） |
| 内存 | **32 GiB 起步，建议 48–64 GiB**：`LOAD_MODE=none` 下整份权重（22.4 GB）都要读进内存，MoE 专家本来就在里面 |
| 磁盘 | 模型 22.4 GB + 镜像若干 GB，建议 NVMe |

`docker` 本身是宿主级服务，无法由 pixi 安装；除它之外的所有依赖（python / curl / jq）都由 pixi 提供。

先跑一次自检：

```bash
pixi run preflight
```

它会检查 docker、nvidia 运行时、内存/磁盘、模型文件，并回显 `docker compose config` 解析出的关键参数。

---

## 任务一览

```bash
pixi run download      # 下载 GGUF（断点续传 + 校验大小）
pixi run up            # 启动容器
pixi run logs          # 跟随日志
pixi run ps            # 容器状态 / 健康检查
pixi run smoke         # 冒烟自检（依赖 up）
pixi run longctx       # 长上下文阶梯实测（needle 召回 + prefill/decode 吞吐）
pixi run tune-ncmoe    # 扫 --n-cpu-moe 调参
pixi run recreate      # 改了 .env 后按新配置重建容器
pixi run config        # 只看解析后的 compose 配置
pixi run pin-image     # 打印镜像 digest，便于锁定版本
pixi run build-image   # 可选：自编译 llama.cpp CUDA 镜像
pixi run down          # 停止
```

## 默认参数

改 `.env` 即可，改完要 `pixi run recreate` 才生效。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `MODEL_FILE` | `Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf` | 量化档位。**不要低于 Q4_K_M**：MoE router 对低位量化脆弱 |
| `CTX_SIZE` | `131072` | 最大上下文 128K。模型原生 256K（`max_position_embeddings=262144`） |
| `N_CPU_MOE` | `28` | 前 N 层的 MoE 专家留在内存。**12G 卡唯一真正省显存的旋钮**（专家占权重 88%），用 `pixi run tune-ncmoe` 扫 |
| `N_GPU_LAYERS` | `99` | 全部层都参与卸载计算 |
| `FIT` | `off` | 关掉 `--fit` 自动调参：显式算显存，避免「空上下文估得下、填满后 OOM」 |
| `FLASH_ATTN` | `on` | 必须开；关掉后长上下文 decode 会腰斩 |
| `CACHE_TYPE_K/V` | `q8_0` / `q8_0` | **必须同类型**，详见「已知坑」 |
| `LOAD_MODE` | `none` | 不走 mmap（旧版 `--no-mmap`） |
| `UBATCH_SIZE` | `1024` | prefill 批大小 |
| `THREADS` | `-1` | 自动（物理核心数）；prefill 偏慢时可显式指定 |
| `PARALLEL` | `1` | 1 个 slot 独占 128K KV；调大后每个 slot 只分到 `CTX_SIZE / PARALLEL` |
| `BIND_ADDR` | `127.0.0.1` | 只绑本机。局域网/远程访问改 `0.0.0.0`（服务无鉴权，注意风险） |

---

## 12G 显卡调参流程

`--n-cpu-moe` 是唯一需要实际扫的参数：**值越大越省显存、越慢**。

```bash
pixi run tune-ncmoe                                  # 默认从 36 往下扫：36 32 30 28 26 24 22
NCMOE_SWEEP="32 28 24" pixi run tune-ncmoe           # 自定义档位
```

每档会重建容器、等就绪、发一个固定请求，记录「是否活着 / 显存 / prefill / decode」。
因为 KV cache 是按整个 `n_ctx` 在启动时一次性分配的，**能起来就说明 128K 的 KV 已经放下了**，
所以「起不来」这一档就是你的显存墙。脚本按从大到小扫，碰到第一档失败就停，并直接给出建议值。

1. 把脚本给出的 `N_CPU_MOE` 写进 `.env`（它取的是「最低可用档位」；脚本还会告诉你退一档的值，
   那是留余量的保守选择，代价是更慢）。
2. 用真实长上下文复核，不要只看短请求速度：

   ```bash
   pixi run recreate
   pixi run longctx
   ```

   它会按 8K → 32K → 64K → 122K 逐档埋一个随机 passcode 并检查召回，同时打印每档的 prefill / decode 吞吐。
3. 换档位后建议同时开着 `nvidia-smi -l 2` 观察实占，别只看 llama.cpp 的日志。

## 调用方式

服务是 OpenAI 兼容接口，任何 OpenAI SDK 指向 `http://127.0.0.1:8080/v1` 即可：

```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
        "model": "qwen3.6-35b-a3b",
        "messages": [{"role": "user", "content": "你好"}],
        "max_tokens": 256
      }'
```

其他端点：`/health`（就绪）、`/props`（`n_ctx` / slot 数）、`/metrics`（Prometheus）、`/tokenize`、
`/slots`、`/v1/models`。

---

## 已知坑

这些都是这套组合上真实存在的坑，按重要性排序：

1. **K/V 量化类型必须一致。** CUDA 端 FlashAttention 默认只编译 `q4_0/q4_0`、`q8_0/q8_0`、`f16/f16` 这几组内核；
   K/V 用了不同组合会**静默回落到 CPU 后端**，prefill 掉 20–30 倍，日志里不报错。
   本项目的默认值是 `q8_0` + `q8_0`。要玩其它组合，用 `pixi run build-image` 自编译（脚本会加
   `-DGGML_CUDA_FA_ALL_QUANTS=ON`）。
2. **`--flash-attn` 必须开。** 关掉后 56K 深度 decode 大约腰斩（社区实测 20.7 → 9.3 tok/s）。
3. **`--n-cpu-moe` 要扫，不要用 `--cpu-moe` 一刀切。** 一刀切会把全部专家赶进内存、显存空着一大半，
   速度接近腰斩。分级卸载（本项目的默认做法）能快近一倍。
4. **量化不要低于 Q4_K_M。** MoE router 对低位量化很敏感，`Q4_0`/`IQ4_XS` 这种均匀低位在这个模型上质量掉得厉害。
5. **128K 的 prefill 是真正的痛点。** decode 能有几十 tok/s，但 128K prompt 的首次 prefill 是分钟级
   （专家在内存里，CPU 侧要把所有 token 过一遍）。所以：长会话务必复用上下文、避免重复 prefill，
   别把 128K 当成「每次都可以重来一遍」的用法。
6. **混合架构的前缀缓存不可靠。** 这个模型 30 层是 GDN 线性注意力，上游还在修上下文检查点 / 前缀失效的问题
   （recurrent snapshot 相关的一串 PR）。表现是多轮对话时可能整段重新 prefill。
   本项目保留了 llama.cpp 的上下文检查点默认值（`--ctx-checkpoints 32`），如需调整见 `--help`。
7. **`--slot-save-path` 的目录必须预先存在**，否则 llama-server 直接启动失败（实测：`not a directory`）。
   本项目的 compose 会挂载 `./cache`，所以正常路径没问题。
8. **`--load-mode none` 的代价是内存。** 不用 mmap 意味着权重整份进内存（约等于模型文件大小），
   32 GiB 内存会偏紧，48–64 GiB 更稳。如果内存不够，把 `LOAD_MODE` 改成 `mmap`。
9. **不要指望 `--fit` 帮你兜底。** 它按「上下文还没填满」估显存，长 prompt 一来就 OOM。本项目默认 `FIT=off`。
10. **MTP 投机解码（可选实验）。** 该模型带 MTP 头，`unsloth/Qwen3.6-35B-A3B-MTP-GGUF` 提供了带 MTP 块的权重，
    较新的 llama.cpp 可以用 `--model-draft` 接上做投机解码。本项目**没有**默认开启，需要自己验证：
    换 `MODEL_REPO`/`MODEL_FILE` 到 MTP 仓库，再给 compose 加 `--model-draft` 参数。
11. **端口 8080 是容器内部的约定端口**，别改：镜像的健康检查和 `LLAMA_ARG_HOST` 都按它来。
    要换对外端口改 `PORT`（只影响宿主映射）。

## 与 SIL-240 调研配置的对应关系

调研里给的参考命令基本原样落地，只有两处需要改动：

| 调研文本 | 本项目 | 说明 |
|---|---|---|
| `-ncmoe 24` | `N_CPU_MOE=28`（同一参数的长名 `--n-cpu-moe`） | 见下面「默认值为什么改了」 |
| `--no-mmap` | `--load-mode none` | **上游在 2026 年的版本里删掉了 `--no-mmap`**，改成了统一的 `--load-mode`（`none` 即原语义）。本仓库固定的 build 11146 上已确认没有 `--no-mmap` |
| `-ngl 99 -fa on -c 131072 --jinja -ub 1024` | 同名参数 | 全部保留 |
| `-ctk q8_0 -ctv q8_0` | `CACHE_TYPE_K/V` | 保留同类型约束 |
| `-t <物理核心数>` | `THREADS=-1` | 自动取值；需要精调再显式指定 |
| — | `--fit off`、`--parallel 1`、`--metrics` | 本项目新增：显式化显存、单 slot 独占 128K、暴露指标 |

**默认值为什么改了 `N_CPU_MOE`：** 调研记录的 `-ncmoe 24` 来自 `UD-Q4_K_M` 的社区实测，
而本项目的默认量化是略大一点的 `UD-Q4_K_XL`。解析 GGUF 张量表后可以精确算出来：
`N=24` 时显存里要放 9.83 GiB 的专家权重 + KV + 计算缓冲 ≈ 12.2 GiB，**在 12 GiB 卡上大概率 OOM**；
`N=28` 是 10.3 GiB 左右，留约 1 GiB 余量，所以默认取 28 更稳。
调研里那句「`-ncmoe 20` 是海市蜃楼」和这个算术是同一件事 —— 真正的档位请用 `pixi run tune-ncmoe` 在你的卡上定。

## 目录结构

```
qwen-container/
├── pixi.toml               # 唯一依赖与任务声明
├── pixi.lock               # 锁定版本，提交进 git
├── docker-compose.yml      # llama-server 服务定义（所有参数都从此处读 .env）
├── .env.example            # 配置模板
├── scripts/
│   ├── download_model.sh   # 下载 GGUF（断点续传 + 大小校验）
│   ├── preflight.sh        # 环境自检
│   ├── llama_client.py     # 极简 HTTP 客户端（仅标准库）
│   ├── smoke_test.py       # 冒烟自检
│   ├── longctx_test.py     # 长上下文阶梯实测（needle 召回 + 吞吐）
│   ├── bench_once.py       # 单次请求基准（tune 脚本用）
│   ├── tune_ncmoe.sh       # 扫 --n-cpu-moe
│   ├── pin_image.sh        # 锁定镜像 digest
│   └── build_llamacpp_image.sh  # 可选：自编译 CUDA 镜像
├── models/                 # 权重（git 忽略）
└── cache/                  # slot KV cache（git 忽略）
```

## 数据来源与可信度

| 数字 | 来源 | 可信度 |
|---|---|---|
| 张量分组大小（专家 19.67 GB / 非专家 2.68 GB / 每层专家 0.492 GB） | 直接解析 `Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf` 的 GGUF 张量表（733 个张量） | **实测** |
| 权重文件大小 22,360,456,160 B | HuggingFace 返回的文件大小 | **实测** |
| KV 每 token 字节数 / 128K 总量 | 官方 `config.json` 的 `full_attention_interval`、`num_key_value_heads`、`head_dim` 折算 | 计算值（公式在 README 里） |
| flag 语义与默认值 | 固定镜像 `ghcr.io/ggml-org/llama.cpp:server-cuda-b11146` 的 `llama-server --help` | **实测** |
| API 行为（`/health`、`/props`、`/tokenize`、`/v1/chat/completions`、`/metrics`） | 用 build 11146 的 `llama-server` 起服务逐个核对 | **实测** |
| 12G 卡上的 tok/s 区间 | SIL-237 / SIL-240 调研引用的社区实测 | 社区数据，随内存/CPU/卡型浮动 |
| 「N=28 留约 1 GiB 余量」 | 由上面的张量实测值 + 估算的计算缓冲（约 1 GB）推出的显存账 | 计算值；**最终档位以你卡上的 `pixi run tune-ncmoe` 为准** |

> 本项目的容器配置本身没有在 12G 显卡上端到端跑过（开发环境没有 GPU）：
> 已验证的是「compose 配置能渲染出预期的容器命令」「llama-server 接受全部参数并正常提供服务」
> 「自检/长上下文/扫参脚本在真实服务上跑通」。显卡上的实际显存与 tok/s 请以你自己的实测为准。

## License

- 本仓库的脚本与配置：Apache-2.0（见 [LICENSE](./LICENSE)）
- Qwen3.6-35B-A3B 模型权重：Apache-2.0（见 HuggingFace 模型页）
- llama.cpp：MIT
