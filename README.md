# 本地 LLM 容器化推理（12G 显存 / 128K 上下文）

pixi 管依赖和任务，Docker 跑推理引擎，在一台带 NVIDIA 显卡（12G 显存起）的 Linux 机器上提供一个
OpenAI / Anthropic 兼容的本地模型服务。引擎是 [Strata](https://github.com/Niko1221/Strata)：
一块 12G 卡就能跑起来，专家权重按热度在显存 / 内存 / SSD 之间调度。

仓库里准备了三套模型，**共用一个服务**，按需切换（同一时间只加载一个）：

| 模型 | 启动参数 | 上下文 | 权重 | 内存 | 特点 |
|---|---|---|---|---|---|
| Qwen3.8-Flash-Next（默认） | `FAMILY=qwen` `MODEL=IQ2_XS` | 128K（可到 256K） | 66–84 GB，首启自动下载 | `IQ2_XS` 约 46 GiB，建议 64 GiB | 更大更强，MoE 512 专家/层 |
| Qwen3.6-35B-A3B | `FAMILY=qwen36` `MODEL=UD-Q4_K_XL` | 32K（12G 卡；显存够可到 128K） | 22.9 GB，首启自动下载 | 32 GiB 起 | 更小更快，MoE 256 专家/层，每 token 激活约 3B |
| Inkling 2 Mini | `FAMILY=inkling-2-mini` `MODEL=SQ-10` | 32K（可到 256K，最长 1M） | 10.5 GB，首启自动下载 | 16 GiB 起 | 26.4B / 3.4B 激活，12G 卡 32K 可用（冷专家自动放内存/SSD） |

地址统一是 `http://127.0.0.1:8080/v1`，**必须带 API key**（首启自动生成，`pixi run key` 查看）。

## 快速开始

```bash
cp .env.example .env      # 可选：改端口 / 档位 / 上下文 / 下载源
pixi run preflight        # 驱动 / 内存 / 磁盘 / 端口自检
pixi run up               # 启动（首次下载模型，进度看 pixi run logs）
pixi run ps               # 等到 STATUS 显示 healthy（首次下载 68 GB，可能要几小时）
pixi run key              # 没在 .env 里设 key 时，打印自动生成的 key（下载期间还没有 key）
pixi run smoke            # 健康检查 + 模型列表 + 一次补全
```

首次下载超过 1 小时的话，给需要等服务的命令放大超时：`STRATA_READY_TIMEOUT=21600 pixi run smoke`。

默认 30 分钟没有新请求，`idle-unload` 辅助容器会把模型卸载，释放显存和内存；下一次请求自动加载。
阈值在 `.env` 的 `STRATA_IDLE_UNLOAD_S`（秒，0 = 关闭），日志用 `pixi run idle-logs` 查看。

## 切换模型

三套模型只是同一服务的三组启动参数，切换不改 compose 结构：

```bash
pixi run model-inkling    # 切到 Inkling 2 Mini（12G 卡推荐：SQ-10，32K 可用）
pixi run model-35b        # 切到 Qwen3.6-35B-A3B
pixi run model-flash      # 切回 Qwen3.8-Flash-Next（默认）
```

也可以手改 `.env` 里的 `STRATA_FAMILY` / `STRATA_MODEL` / `STRATA_PRESET`，再 `pixi run recreate`。
12G 卡一次只加载一个模型，切换就是卸载旧模型、加载新模型；所有模型共用一个数据目录，
已下载的权重和各自的 preset（`flash-next` / `qwen36-35b` / `inkling-2-mini`）都保留，
切回来不会重新下载。

> Inkling 2 Mini 支持魔搭与 HuggingFace（国内推荐魔搭，注意 `resolve/master`），
> 下载源用 `STRATA_INKLING_SOURCE` 指定，见 [docs/strata.md](docs/strata.md)。

## 从旧版本升级

旧版本跑两个容器（`qwen36-35b-a3b` + `qwen38-flash-next`），新版只有一个 `qwen-strata`，默认都用 8080，
旧容器不删会撞端口：

```bash
docker rm -f qwen36-35b-a3b qwen38-flash-next   # 先停掉旧服务（数据在卷里，不会丢）
pixi run up
```

旧版的 `models/`、`cache/` 不再使用；模型、预处理包和 preset 都在 `strata-data/` 下。

## 项目里有什么

```
qwen-container/
├── pixi.toml            # 唯一依赖与任务声明（pixi task list 看全部任务）
├── pixi.lock
├── docker-compose.yml   # 单一 Strata 服务；模型切换写在 environment 段
├── .env.example         # 配置模板（含三套模型参数）
├── scripts/             # 切换模型（三个档位）/ 自检 / 冒烟 / 长上下文召回 / 公共客户端
├── docs/                # 详细文档，见下表
└── strata-data/         # 模型权重、预处理包、preset、API key（可用 STRATA_DATA_DIR 改；git 忽略）
```

## 文档

| 文档 | 内容 |
|---|---|
| [docs/models.md](docs/models.md) | 各模型的规格、档位与评测、内存与磁盘需求 |
| [docs/strata.md](docs/strata.md) | 服务参数、模型下载源（HuggingFace / ModelScope）、复用已有模型 |
| [docs/operations.md](docs/operations.md) | 前置条件、环境自检、常用任务、模型切换、数据落盘、排障 |
| [docs/api.md](docs/api.md) | 调用方式与端点 |

## 前置条件

Linux x86-64；NVIDIA 驱动（cuda13 镜像要 580+，旧卡/旧驱动用 cuda12 镜像）；Docker Engine +
compose v2 + nvidia-container-toolkit；pixi。内存 32 GiB 起，跑 Flash-Next 建议 64 GiB；
数据目录建议 NVMe（专家权重会按需读盘）。

`docker` 是宿主级服务，无法由 pixi 安装；python / curl / jq 由 pixi 提供。细节见
[docs/operations.md](docs/operations.md)。Inkling `SQ-10` 16 GiB 内存即可。

## License

- 本仓库的脚本与配置：Apache-2.0（见 [LICENSE](./LICENSE)）
- Qwen3.6-35B-A3B 权重：Apache-2.0；Qwen3.8-Flash-Next 权重：Qwen 社区许可（见各自 HuggingFace 模型页）
- Inkling 2 Mini 权重（`strata-engine/Inkling-2-Mini-Strata`）：Apache-2.0
- Strata：MIT
