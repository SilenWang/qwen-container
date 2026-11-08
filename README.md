# 本地 LLM 容器化推理（12G 显存 / 128K 上下文）

在一台带 NVIDIA 显卡（12G 显存起步）的 Linux 机器上跑本地大模型服务：pixi 管依赖和任务，Docker 跑推理引擎，
对外提供 OpenAI 兼容接口。同一个仓库里放两个模型，按需切换：

| | 模型一 | 模型二 |
|---|---|---|
| 模型 | `Qwen3.6-35B-A3B` | `Qwen3.8-Flash-Next` |
| 引擎 | llama.cpp（`llama-server`） | [Strata](https://github.com/Niko1221/Strata) |
| 上下文 | 128K | 128K |
| 显存 / 内存 | 12G / 32G 起（建议 48–64G） | 12G / 48G 起（建议 64G） |
| 磁盘 | 约 22 GB 权重 | 约 66–84 GB 权重（首启自动下载，看档位） |
| 端口 | `127.0.0.1:8080` | `127.0.0.1:8081` |
| 启动 | `pixi run up` | `pixi run strata-up` |

Strata 是另一套推理引擎：把 MoE 的专家权重按热度分散在显存 / 内存 / SSD 上，所以 12G 卡也能跑
`Qwen3.8-Flash-Next` 这种 125B 级的模型。两个引擎的接口都是 OpenAI 兼容，客户端只改 base URL 和 key。

一块 12G 卡同一时间只能跑一个模型，所以 `pixi run up` 和 `pixi run strata-up` 会先停掉另一个。

---

## 模型一：Qwen3.6-35B-A3B（llama.cpp）

```bash
cp .env.example .env     # 可选：改端口 / 上下文 / 卸载档位
pixi run download        # 下载 GGUF 权重（约 22.4 GB）
pixi run up              # 启动容器
pixi run smoke           # 自检：健康检查 + n_ctx 核对 + 一次补全 + 吞吐
```

服务起来后是 OpenAI 兼容接口：`http://127.0.0.1:8080/v1`（无鉴权，只绑本机回环）。

### 模型基本配置

| 项目 | 值 |
|---|---|
| 模型 | `Qwen3.6-35B-A3B`（MoE：35B 总参 / 每 token 激活约 3B） |
| 权重来源 | `unsloth/Qwen3.6-35B-A3B-GGUF` |
| 权重文件 | `Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf`（约 22.4 GB） |
| 对外模型名 | `qwen3.6-35b-a3b`（OpenAI API 的 `model` 字段） |
| 最大上下文 | 131072（128K）；模型原生 256K |
| 推理镜像 | `ghcr.io/ggml-org/llama.cpp:server-cuda-b11146`（固定 build） |
| 架构 | 40 层，其中 10 层全注意力 + 30 层 GDN 线性注意力 |

关键运行参数（全部在 `.env`，改完要 `pixi run recreate` 才生效）：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CTX_SIZE` | `131072` | 最大上下文 128K |
| `N_CPU_MOE` | `28` | 前 28 层的 MoE 专家留在系统内存（12G 卡唯一真正省显存的旋钮） |
| `N_GPU_LAYERS` | `99` | 全部层都参与 GPU 卸载计算 |
| `FIT` | `off` | 关掉 `--fit` 自动调参，显存按显式参数算 |
| `FLASH_ATTN` | `on` | 必须开 |
| `CACHE_TYPE_K` / `CACHE_TYPE_V` | `q8_0` / `q8_0` | 必须同类型 |
| `LOAD_MODE` | `none` | 不走 mmap |
| `UBATCH_SIZE` | `1024` | prefill 批大小 |
| `THREADS` | `-1` | 自动（物理核心数） |
| `PARALLEL` | `1` | 1 个 slot 独占 128K KV |
| `BIND_ADDR` / `PORT` | `127.0.0.1` / `8080` | 只绑本机；服务无鉴权，暴露到公网前请自行加认证 |

模型默认是文本推理。该模型带视觉塔，需要多模态时按 `.env.example` 里的说明下载 `mmproj` 并取消 `docker-compose.yml` 中 `--mmproj` 两行的注释。

### 调参与注意事项

- **`N_CPU_MOE` 要在自己的卡上扫一遍**：值越大越省显存、越慢。
  `pixi run tune-ncmoe` 会逐档重建容器并记录显存与吞吐，给出可用档位；再 `pixi run recreate && pixi run longctx` 用真实长上下文复核。
- **K/V 量化类型必须一致**（都用 `q8_0` 或都用 `q4_0`），否则 CUDA 端 FlashAttention 内核会静默回落到 CPU 后端，prefill 掉一个数量级且日志不报错。
- **不要用 `--cpu-moe` 一刀切**：把全部专家赶进内存会浪费显存、速度接近腰斩。
- **量化不要低于 Q4_K_M**：MoE router 对低位量化敏感。
- **128K 的 prefill 是真正的瓶颈**：decode 有几十 tok/s，但 128K prompt 首次 prefill 是分钟级，长会话请复用上下文。
- **`LOAD_MODE=none` 的代价是内存**：32 GiB 会偏紧，不够可改成 `mmap`。
- **端口 8080 是容器内部约定**，不要改；换对外端口改 `PORT`。

---

## 模型二：Qwen3.8-Flash-Next（Strata）

```bash
cp .env.example .env     # 可选：改档位 / 上下文 / key
pixi run strata-preflight  # 环境自检：驱动版本 / 内存 / 磁盘 / 端口
pixi run strata-up         # 启动（首次启动自动下载 66–84 GB 模型，进度看 strata-logs）
pixi run strata-key        # 没在 .env 里设 key 时，查看自动生成的 key
pixi run strata-smoke      # 自检：健康检查 + 模型列表 + 一次补全 + 吞吐
```

服务是 OpenAI 兼容接口：`http://127.0.0.1:8081/v1`，**必须带 API key**；另外也提供 Anthropic 兼容的 `/v1/messages`。
`/health` 不需要 key。

```bash
# key：自己设的，或 `pixi run strata-key` 打印出来的那个
export STRATA_API_KEY=...
curl http://127.0.0.1:8081/v1/chat/completions \
  -H "Authorization: Bearer $STRATA_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
        "model": "strata",
        "messages": [{"role": "user", "content": "你好"}],
        "max_tokens": 256
      }'
```

model 字段写 `strata` 即可，准确的名字看 `GET /v1/models`（冒烟脚本会自动取）。

### 模型基本配置

| 项目 | 值 |
|---|---|
| 模型 | `Qwen3.8-Flash-Next`（MoE，每层 512 专家 / 每 token 激活约 10 个，带 MTP 投机解码头） |
| 权重来源 | `ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF`（Strata 首启自动下载并准备） |
| 分档 | `Q2_0` / `IQ2_XS` / `IQ3_XXS` / `IQ3_S`，默认 `IQ2_XS` |
| 最大上下文 | 128K（`STRATA_CONTEXT=128k`，可到 256k） |
| 推理镜像 | `ghcr.io/niko1221/strata:0.2.11-cuda13`（固定版本 tag） |
| 引擎 | Strata 0.1.57（镜像自带，专家权重按需在显存 / 内存 / SSD 间调度） |
| 对外端口 | `127.0.0.1:8081` → 容器内 8080 |
| 数据目录 | 宿主机 `./strata-data/`（模型、preset、API key，git 忽略） |

档位与内存（数字来自 Strata 文档；「启动需内存」≈ 首启要放进内存的专家 + 10 GB 系统余量）：

| 档位 | 权重（内存+显存） | 启动需内存 | 下载 | 评测均分 | 特点 |
|---|---:|---:|---:|---:|---|
| `Q2_0` | 37.6 GB | 44 GB | 66 GB | 89.07 | 最快，质量够用 |
| `IQ2_XS` | 39.2 GB | 46 GB | 68 GB | 89.16 | 默认：速度和质量的平衡点 |
| `IQ3_XXS` | 47.0 GB | 53 GB | 76 GB | 92.57 | 更慢，质量更好 |
| `IQ3_S` | 54.8 GB | 60 GB | 84 GB | 93.26 | 最慢，质量最好 |
| `coder`（`IQ1_M`） | — | 33 GB | — | — | 砍掉一半专家，偏代码；32 GB 内存机器用这档 |

「评测均分」= AIME25 / GPQA-Diamond / LiveCodeBench v6 三项平均，取自 ISTA-DASLab 模型卡（BF16 同口径 93.12）；
四档在 zero-shot 五任务（arc/hellaswag/winogrande/piqa）上都与 BF16 持平（77–78），差距主要出现在推理和代码生成上。
`IQ2_XS → IQ3_S` 是 +4.1 分（GPQA-D +5.6、LCB v6 +3.4、AIME25 +3.3），代价是 decode 约慢一倍。

**12G 显卡能选到哪一档？** 显存不是门槛（Strata 最低要求就是 12 GB 卡），内存才是。
3060 12G + 64 GB 实测跑过 `IQ3_S`，128K / 260K 上下文召回都正常，decode 约 19–20 tok/s、
32K prompt prefill 约 820 tok/s（128K 首轮约 160 s）；官方另有一组 63 GB 机器上 128K 跑到 25–28 tok/s 的记录
（内存装不下全部专家时，引擎会自动走「内存副本 + 按需读盘」）。48 GB 内存建议停在 `IQ3_XXS`（约 26–27 tok/s），
32 GB 用 `IQ2_XS` 或 coder。

关键运行参数（全部在 `.env`，改完要 `pixi run strata-recreate` 才生效）：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `STRATA_IMAGE` | `ghcr.io/niko1221/strata:0.2.11-cuda13` | cuda13 要驱动 580+；GTX 10 / P100 / V100 或驱动 575–579 用 `cuda12` |
| `STRATA_MODEL` | `IQ2_XS` | 上面的档位；留空由 Strata 按显卡自选 |
| `STRATA_CONTEXT` | `128k` | 32k / 64k / 128k / 256k，或直接写 token 数 |
| `STRATA_KV` | `int8` | 会话 KV 格式；`q4_0` 省一半内存 |
| `STRATA_VISION` | `off` | 图片输入；`on` 会多下约 0.9 GB 的读取器（12G 卡上跑 CPU） |
| `STRATA_TUNE` | `skip` | `quick` 首启做一次硬件校准（5–20 分钟，只做一次） |
| `STRATA_PARALLEL` | 空 | 并发数 1–8；空 = 按显卡自动 |
| `STRATA_RAM_KEEP_GIB` | `0` | 给系统留出的空闲内存（GiB）；跑 `IQ3_S` 或机器还要干别的时设 6–8 |
| `STRATA_SET` | 空 | 高级引擎参数，空格分隔 `KEY=VALUE`（等价于 `strata launch --set`） |
| `STRATA_SOURCE` | 空 | 模型下载源：空 = HuggingFace，`modelscope` = 魔搭（国内） |
| `HF_ENDPOINT` | 空 | HuggingFace 镜像站，如 `https://hf-mirror.com` |
| `STRATA_API_KEY` | 空 | 16 字符以上；空 = 首启自动生成并存进 `./strata-data` |
| `STRATA_BIND_ADDR` / `STRATA_PORT` | `127.0.0.1` / `8081` | 只绑本机；对外暴露时务必换成自己设的 key |

实测参考（社区实测：RTX 3060 12G + i7-12700 + 64 GB，`IQ2_XS`，128K 上下文，Windows v0.1.41）：decode 约 40–46 tok/s，
32K prompt 约 930 tok/s（128K 首次 prefill 约 2 分钟），上下文召回正常。

### 模型从哪下载

不用手动下：容器首启时由 Strata 自己下载到 `./strata-data/`，两样东西：

| 内容 | 来源 | 体积 |
|---|---|---:|
| GGUF 分片（按档位选） | HuggingFace `ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF` 的 `IQ2_XS/` 等子目录 | 66–84 GB |
| MTP 草稿层 | HuggingFace `Qwen/Qwen3.8-Flash-Next` 的 `mtp` 张量 | 约 6 GB |

国内网络二选一（都写在 `.env` 里，改完 `pixi run strata-recreate`）：

- **魔搭 ModelScope**（推荐）：同名仓库、同路径，Strata 原生支持 —— `STRATA_SOURCE=modelscope`。
  `https://www.modelscope.cn/models/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF` 上 `IQ2_XS/` 两个分片都在（39.2 + 28.8 GB），MTP 取自 `Qwen/Qwen3.8-Flash-Next`。ModelScope 提供的是仓库当前文件，下载后会按它公布的 SHA-256 校验。
- **HF 镜像站**：`HF_ENDPOINT=https://hf-mirror.com`（仍按 pinned revision 下载并校验）。

两个都留空就是官方 HuggingFace。下载支持断点续传；已下好的文件不会因为换源重下，但换档位会另下。

已经手动下好了？在 compose 里给 strata 服务加一行挂载（例如 `- /your/gguf/IQ2_XS:/models:ro`），
再设 `MODEL_PATH=/models`、`MODEL=IQ2_XS`；MTP 草稿层仍会从上面的源下载（约 6 GB）。

### 注意事项

- **首次启动会下载 66–84 GB（看档位）到 `./strata-data/`**，这期间 `docker ps` 显示 `starting`（不是 unhealthy）；进度看 `pixi run strata-logs`。
  下载可断点续传，中断后重新 `strata-up` 会接着下。磁盘建议 SSD/NVMe —— 专家权重会按需从盘上读。
- **模型在宿主机，不在容器里**：`./strata-data` 以 bind mount 挂到容器的 `/data`，删容器 / 换镜像都不丢权重。
- **单卡只能跑一个模型**：`pixi run up` 会先停模型二，`pixi run strata-up` 会先停模型一；分开停用 `strata-down`。
- **内存不够时会自动降级**：Strata 改成从 SSD 读专家（low-RAM 模式），能跑但明显变慢；32 GB 内存建议直接用 `coder` 档。
- **首次加载慢**：要往内存里装 30–50 GB，1–3 分钟内机器可能很卡，属正常。
- **128K 上下文的第一轮很慢**（prefill 是瓶颈），后续轮次只读增量，几秒内开始。
- **换 `.env` 后要 `strata-recreate`**；`strata-restart` 不会重新读配置。
- **镜像升级**：`pixi run strata-pull && pixi run strata-recreate`，模型和 preset 保留在 `./strata-data`。

---

## 前置条件

| 项目 | 模型一（llama.cpp） | 模型二（Strata） |
|---|---|---|
| 系统 | Linux（WSL2 也可） | Linux x86-64（WSL2 可用） |
| GPU | NVIDIA，12G 显存起步 | NVIDIA RTX 20/30/40/50，12G 显存起（GTX 10 / P100 / V100 用 cuda12 镜像） |
| 驱动 | 支持 CUDA 12 的驱动 | 580+（cuda13 镜像）/ 575+（cuda12 镜像） |
| Docker | Docker Engine + `docker compose` v2 + nvidia-container-toolkit | 同左 |
| pixi | 用于安装依赖与跑任务 | 同左 |
| CPU | 多核（MoE 专家在内存侧靠 CPU 读） | 支持 AVX2，核心越快越好（专家在 CPU 侧算） |
| 内存 | 32 GiB 起，建议 48–64 GiB | `IQ2_XS` 需 48 GiB，建议 64 GiB；32 GiB 用 `coder` 档 |
| 磁盘 | 权重 22.4 GB + 镜像 | 权重 66–84 GB + 镜像 2.2 GB，建议 NVMe |

`docker` 是宿主级服务，无法由 pixi 安装；其余依赖（python / curl / jq）都由 pixi 提供。

先跑一次环境自检：

```bash
pixi run preflight          # 模型一：docker / GPU 运行时 / 内存 / 磁盘 / 模型文件
pixi run strata-preflight   # 模型二：驱动版本 / 内存 / 磁盘 / 端口 / compose 配置
```

---

## 常用任务

模型一：

```bash
pixi run download      # 下载 GGUF（断点续传 + 校验大小）
pixi run up            # 启动容器（会先停模型二）
pixi run logs          # 跟随日志
pixi run ps            # 容器状态 / 健康检查
pixi run smoke         # 冒烟自检（依赖 up）
pixi run longctx       # 长上下文阶梯实测（needle 召回 + prefill/decode 吞吐）
pixi run tune-ncmoe    # 扫 --n-cpu-moe 卸载档位
pixi run recreate      # 改了 .env 后按新配置重建容器
pixi run config        # 只看解析后的 compose 配置
pixi run pin-image     # 打印镜像 digest，便于锁定版本
pixi run build-image   # 可选：自编译 llama.cpp CUDA 镜像
```

模型二：

```bash
pixi run strata-preflight  # 环境自检
pixi run strata-up         # 启动（首次会下载模型；会先停模型一）
pixi run strata-logs       # 跟随日志
pixi run strata-ps         # 容器状态 / 健康检查
pixi run strata-key        # 打印 API key
pixi run strata-smoke      # 冒烟自检
pixi run strata-recreate   # 改了 .env 后重建
pixi run strata-config     # 只看渲染后的配置
pixi run strata-pull       # 更新镜像
pixi run strata-shell      # 进容器
pixi run strata-down       # 停止（卸载模型，释放显存与内存）
```

两个都停 / 都删：`pixi run down`。

## 调用方式

```bash
# 模型一（无鉴权）
curl http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model": "qwen3.6-35b-a3b", "messages": [{"role": "user", "content": "你好"}], "max_tokens": 256}'

# 模型二（要 key）
curl http://127.0.0.1:8081/v1/chat/completions \
  -H "Authorization: Bearer $STRATA_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model": "strata", "messages": [{"role": "user", "content": "你好"}], "max_tokens": 256}'
```

模型一的其他端点：`/health`（就绪）、`/props`（`n_ctx` / slot 数）、`/metrics`（Prometheus）、`/tokenize`、`/slots`、`/v1/models`。
模型二的其他端点：`/health`（免 key）、`/v1/models`（要 key）、`/v1/messages`（Anthropic 兼容）。

---

## 目录结构

```
qwen-container/
├── pixi.toml               # 唯一依赖与任务声明（两个模型的任务都在这里）
├── pixi.lock               # 锁定版本，提交进 git
├── docker-compose.yml      # llama-server + strata 两个服务（参数都从 .env 读）
├── .env.example            # 配置模板
├── scripts/
│   ├── download_model.sh   # 下载模型一的 GGUF（断点续传 + 大小校验）
│   ├── preflight.sh        # 模型一环境自检
│   ├── llama_client.py     # 极简 HTTP 客户端（仅标准库）
│   ├── smoke_test.py       # 模型一冒烟自检
│   ├── longctx_test.py     # 长上下文阶梯实测（needle 召回 + 吞吐）
│   ├── bench_once.py       # 单次请求基准（tune 脚本用）
│   ├── tune_ncmoe.sh       # 扫 --n-cpu-moe
│   ├── pin_image.sh        # 锁定镜像 digest
│   ├── build_llamacpp_image.sh  # 可选：自编译 CUDA 镜像
│   ├── strata_preflight.sh # 模型二环境自检
│   └── strata_smoke.py     # 模型二冒烟自检
├── models/                 # 模型一权重（git 忽略）
├── cache/                  # 模型一 slot KV cache（git 忽略）
└── strata-data/            # 模型二数据：权重 + preset + key（git 忽略）
```

## License

- 本仓库的脚本与配置：Apache-2.0（见 [LICENSE](./LICENSE)）
- Qwen3.6-35B-A3B 模型权重：Apache-2.0（见 HuggingFace 模型页）
- Qwen3.8-Flash-Next 模型权重：Qwen 社区许可（见 HuggingFace 模型页）
- llama.cpp：MIT
- Strata：MIT
