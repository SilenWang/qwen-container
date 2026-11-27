# 模型与档位

一个 Strata 服务，三套模型参数（切换方式见 [README](../README.md)）。这一页写各模型是什么、
需要多少资源、档位之间差多少。

## Qwen3.8-Flash-Next

| 项目 | 值 |
|---|---|
| 模型 | `Qwen3.8-Flash-Next`（MoE，每层 512 专家 / 每 token 激活约 10 个，带 MTP 投机解码头） |
| 权重来源 | `ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF`（首启自动下载并准备） |
| 启动参数 | `STRATA_FAMILY=qwen` + `STRATA_MODEL=IQ2_XS` |
| 分档 | `Q2_0` / `IQ2_XS` / `IQ3_XXS` / `IQ3_S`，默认 `IQ2_XS`；32 GiB 内存机器用 `coder` 档（`IQ1_M`） |
| 最大上下文 | 128K（`STRATA_CONTEXT=128k`，可到 256K） |
| 显存 / 内存 | 12 GB 显存 + `IQ2_XS` 约需 46 GiB 内存（建议 64 GiB） |
| 磁盘 | 66–84 GB 权重 + 约 2 GB 预处理包/草稿层 + 镜像 2.2 GB，建议 NVMe |

### 档位与资源

「启动需内存」≈ 首启要放进内存的专家 + 10 GB 系统余量（数字来自 Strata 文档）。

| 档位 | 权重（内存+显存） | 启动需内存 | 下载 | 评测均分 | 特点 |
|---|---:|---:|---:|---:|---|
| `Q2_0` | 37.6 GB | 44 GB | 66 GB | 89.07 | 最快，质量够用 |
| `IQ2_XS` | 39.2 GB | 46 GB | 68 GB | 89.16 | 默认：速度和质量的平衡点 |
| `IQ3_XXS` | 47.0 GB | 53 GB | 76 GB | 92.57 | 更慢，质量更好 |
| `IQ3_S` | 54.8 GB | 60 GB | 84 GB | 93.26 | 最慢，质量最好 |
| `coder`（`IQ1_M`） | — | 33 GB | — | — | 砍掉一半专家，偏代码；32 GiB 内存机器用这档 |

「评测均分」= AIME25 / GPQA-Diamond / LiveCodeBench v6 三项平均，取自 ISTA-DASLab 模型卡
（BF16 同口径 93.12）。四档在 zero-shot 五任务（arc/hellaswag/winogrande/piqa）上都与 BF16 持平（77–78），
差距主要出现在推理和代码生成上。`IQ2_XS → IQ3_S` 是 +4.1 分（GPQA-D +5.6、LCB v6 +3.4、AIME25 +3.3），
代价是 decode 约慢一倍。

### 12G 显卡能选到哪一档

显存不是门槛（Strata 最低要求就是 12 GB 卡），内存才是。3060 12G + 64 GB 实测跑过 `IQ3_S`，
128K / 260K 上下文召回都正常，decode 约 19–20 tok/s、32K prompt prefill 约 820 tok/s（128K 首轮约 160 s）；
官方另有一组 63 GB 机器上 128K 跑到 25–28 tok/s 的记录（内存装不下全部专家时，引擎会自动走
「内存副本 + 按需读盘」）。

选择建议：

- 64 GiB 内存、更在意质量 → `IQ3_S`
- 48 GiB 内存 → `IQ3_XXS`（约 26–27 tok/s）
- 32 GiB 内存 → `IQ2_XS`，或 coder 档
- 只想要速度 → `Q2_0` 或 `IQ2_XS`（3060 上 decode 约 40–46 tok/s）

### 实测参考

社区实测（RTX 3060 12G + i7-12700 + 64 GB，`IQ2_XS`，128K 上下文，Windows v0.1.41）：decode 约
40–46 tok/s，32K prompt 约 930 tok/s（128K 首次 prefill 约 2 分钟），上下文召回正常。

> 这些数字是参考值；实际速度取决于显卡、内存带宽、CPU 和磁盘，本项目不承诺具体数值。

## Qwen3.6-35B-A3B

| 项目 | 值 |
|---|---|
| 模型 | `Qwen3.6-35B-A3B`（MoE：35B 总参 / 每 token 激活约 3B，256 专家 / top-8） |
| 权重来源 | `unsloth/Qwen3.6-35B-A3B-MTP-GGUF`（首启自动下载，Strata 用它的 MTP 草稿层） |
| 启动参数 | `STRATA_FAMILY=qwen36` + `STRATA_MODEL=UD-Q4_K_XL` |
| 分档 | `UD-Q4_K_XL`（默认）/ `UD-Q6_K_XL` / `Q8_0` |
| 上下文 | 12G 卡上 Strata 建议 32K；显存够时可到 128K |
| 显存 / 内存 | 12 GB 显存 + 32 GiB 内存起 |
| 磁盘 | 22.9–37.8 GB 权重 + 预处理包/草稿层，建议 NVMe |

| 档位 | 文件大小 | 启动需内存 | 说明 |
|---|---:|---:|---|
| `UD-Q4_K_XL` | 22.9 GB | 32 GB | 4-bit（Unsloth Dynamic），最小，12G 卡推荐 |
| `UD-Q6_K_XL` | 32.6 GB | 44 GB | 6-bit，更接近原模型 |
| `Q8_0` | 37.8 GB | 52 GB | 8-bit，最接近原模型 |

这个家族走 Strata 自己的引擎路径（`src/qwen35`，按 GGUF 的架构自动切换），支持图片输入
（`STRATA_VISION=on` 会多下约 0.9 GB 的 `mmproj-F16.gguf`）和并发请求（`STRATA_PARALLEL=2..8`）。
它**不**走 Flash-Next 的 pack 机制：`MODEL` 直接对应 Unsloth 的档位名，`STRATA_FAMILY` 必须写成 `qwen36`。

> Intel Arc 与 Mac 引擎不支持这个家族；NVIDIA / AMD 可用。

## Inkling 2 Mini（Thinking Machines Lab）

Strata 0.2.21 起的镜像自带 `strata-inkling` **0.1.67**（容器 label `com.strata.engine` 可直接核对）。
0.2.20 起这个引擎把 Inkling 纳入 GPU + 内存 + CPU + SSD 的**四级 MoE**：显存装不下的冷专家自动放到内存/SSD，
32K 以上上下文还会自动把 KV 卸载到 host。0.2.19 镜像里只有 0.1.65，没有这层机制，12 GiB 卡跑 SQ-10 32K 会因
3072 个专家全部进显存而 CUDA OOM，所以本仓库默认镜像从 0.2.19 升到了 0.2.21。
26.4B 参数、每 token 激活 3.4B（中间层循环 3 次，
按 48 次虚拟访问算 5.4B），支持 1M 上下文、文字 + 图片输入、OpenAI 兼容的 tool calling。

| 项目 | 值 |
|---|---|
| 权重来源 | `strata-engine/Inkling-2-Mini-Strata`（HuggingFace / 魔搭均有，见 [strata.md](strata.md#国内网络modelscope-或-hf-镜像)） |
| 启动参数 | `STRATA_FAMILY=inkling-2-mini` + `STRATA_MODEL=SQ-10` |
| 分档 | `SQ-10`（默认）/ `SQ-15` / `SQ-20` |
| 上下文 | 默认 32K；可选 8K / 32K / 64K / 128K / 256K（模型本身最长 1M） |
| 显存 / 内存 | 12 GiB 卡 + 16 GiB 内存起；`SQ-10` 在 12 GiB 卡上 32K 可用（约 11.7 / 12.2 GiB 显存，少量冷专家落内存/SSD） |
| 磁盘 | 11–20 GB（含默认可选的 Vision 分片 0.64 GB） |
| 并发 | `STRATA_PARALLEL` 可到 64，默认 1 |
| 草稿层 | 内置 DSpark Block-8，无需另配 |

| 档位 | 下载 | 推荐显卡 | 启动需内存 | 与原模型一致率 |
|---|---:|---:|---:|---:|
| `SQ-10` | 10.47 GB | 12 GiB（也支持 8 GiB / 780M / Strix Point iGPU） | 16 GiB | 92.35% |
| `SQ-15` | 14.35 GB | 16 GiB；12 GiB 卡 + 24 GiB 内存走 hybrid | 24 GiB | 96.68% |
| `SQ-20` | 19.35 GB | 24 GiB 卡 / 32 GiB 统一内存 | 32 GiB | 100%（逐位一致） |

选择建议：

- 本项目默认的 12G 卡 → `SQ-10`：32K 上下文下显存约 11.7 / 12.2 GiB，引擎会把装不下的冷专家自动放到内存/SSD
- 16G 卡 → `SQ-15`，质量明显更好
- 24G 卡 / 统一内存 32 GiB 以上 → `SQ-20`，和原模型逐位一致
- 想看图片：`.env` 设 `STRATA_VISION=on`，多下 0.64 GB，12G 卡上图片读取器跑 CPU

> 显存内加载未在本仓库逐档实测（开发机 `nvidia-smi` 报 NVML 错误、没有可用 GPU）。
> 上表的显存 / 内存数字来自官方模型卡与 Strata `inkling_fit` 规则（0.2.21 / 引擎 0.1.67），
> 下载体积与 `strata launch --plan` 的输出一致（镜像内已核对）。

> Hy4-Flash（`strata-engine/Hy4-Flash-Strata`）需要 64 GiB 内存和约 130 GB 磁盘，暂未纳入本容器。
