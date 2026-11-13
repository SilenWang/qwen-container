# 模型配置与档位

仓库里放了两个模型，各自跑在独立的容器服务里。这一页写它们是什么、需要多少资源、档位之间差多少。

- 模型一：Qwen3.6-35B-A3B（llama.cpp）→ 配置与调参见 [llama-cpp.md](llama-cpp.md)
- 模型二：Qwen3.8-Flash-Next（Strata）→ 配置与下载见 [strata.md](strata.md)

## 模型一：Qwen3.6-35B-A3B

| 项目 | 值 |
|---|---|
| 模型 | `Qwen3.6-35B-A3B`（MoE：35B 总参 / 每 token 激活约 3B） |
| 权重来源 | `unsloth/Qwen3.6-35B-A3B-GGUF` |
| 权重文件 | `Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf`（约 22.4 GB） |
| 对外模型名 | `qwen3.6-35b-a3b`（OpenAI API 的 `model` 字段） |
| 最大上下文 | 131072（128K）；模型原生 256K |
| 推理镜像 | `ghcr.io/ggml-org/llama.cpp:server-cuda-b11146`（固定 build） |
| 架构 | 40 层，其中 10 层全注意力 + 30 层 GDN 线性注意力 |
| 显存 / 内存 | 12 GB 显存 + 32 GiB 内存起（建议 48–64 GiB） |
| 磁盘 | 权重 22.4 GB + 镜像 |

服务默认是文本推理；该模型带视觉塔，需要多模态时按 `.env.example` 的说明下载 `mmproj` 并取消
`docker-compose.yml` 中 `--mmproj` 两行的注释。

## 模型二：Qwen3.8-Flash-Next

| 项目 | 值 |
|---|---|
| 模型 | `Qwen3.8-Flash-Next`（MoE，每层 512 专家 / 每 token 激活约 10 个，带 MTP 投机解码头） |
| 权重来源 | `ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF`（Strata 首启自动下载并准备） |
| 分档 | `Q2_0` / `IQ2_XS` / `IQ3_XXS` / `IQ3_S`，默认 `IQ2_XS` |
| 最大上下文 | 128K（`STRATA_CONTEXT=128k`，可到 256k） |
| 推理镜像 | `ghcr.io/niko1221/strata:0.2.11-cuda13`（固定版本 tag） |
| 引擎 | Strata 0.1.57（镜像自带，专家权重按需在显存 / 内存 / SSD 间调度） |
| 显存 / 内存 | 12 GB 显存 + `IQ2_XS` 需 48 GiB 内存（建议 64 GiB） |
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

> 这些数字是参考值；实际速度取决于显卡、内存带宽、CPU 和磁盘，README 不承诺具体数值。
