# 本地 LLM 容器化推理（12G 显存 / 128K 上下文）

pixi 管依赖和任务，Docker 跑推理引擎，在一台带 NVIDIA 显卡（12G 显存起）的 Linux 机器上提供一个
OpenAI / Anthropic 兼容的本地模型服务。引擎是 [Strata](https://github.com/Niko1221/Strata)：
一块 12G 卡就能跑起来，专家权重按热度在显存 / 内存 / SSD 之间调度。

仓库里准备了两套模型，**共用一个服务**，按需切换（同一时间只加载一个）：

| | Qwen3.8-Flash-Next | Qwen3.6-35B-A3B |
|---|---|---|
| 启动参数 | `FAMILY=qwen` `MODEL=IQ2_XS` | `FAMILY=qwen36` `MODEL=UD-Q4_K_XL` |
| 上下文 | 128K（可到 256K） | 32K（12G 卡；足够显存可到 128K） |
| 权重 | 66–84 GB，首启自动下载 | 22.9 GB，首启自动下载 |
| 内存 | `IQ2_XS` 约 46 GiB，建议 64 GiB | 32 GiB 起 |
| 特点 | 更大更强，MoE 512 专家/层 | 更小更快，MoE 256 专家/层，每 token 激活约 3B |

地址统一是 `http://127.0.0.1:8080/v1`，**必须带 API key**（首启自动生成，`pixi run key` 查看）。

## 快速开始

```bash
cp .env.example .env      # 可选：改端口 / 档位 / 上下文 / 下载源
pixi run preflight        # 驱动 / 内存 / 磁盘 / 端口自检
pixi run up               # 启动（首次下载模型，进度看 pixi run logs）
pixi run key              # 没在 .env 里设 key 时，打印自动生成的 key
pixi run smoke            # 健康检查 + 模型列表 + 一次补全
```

## 切换模型

两个模型只是同一服务的两组启动参数，切换不改 compose 结构：

```bash
pixi run model-35b        # 切到 Qwen3.6-35B-A3B（改写 .env 并重建容器）
pixi run model-flash      # 切回 Qwen3.8-Flash-Next
```

也可以手改 `.env` 里的 `STRATA_FAMILY` / `STRATA_MODEL` / `STRATA_PRESET`，再 `pixi run recreate`。
12G 卡一次只加载一个模型，切换就是卸载旧模型、加载新模型；两个模型共用一个数据目录，
已下载的权重和各自的 preset 都保留，切回来不会重新下载。

## 项目里有什么

```
qwen-container/
├── pixi.toml            # 唯一依赖与任务声明（pixi task list 看全部任务）
├── pixi.lock
├── docker-compose.yml   # 单一 Strata 服务；模型切换写在 environment 段
├── .env.example         # 配置模板（含两套模型参数）
├── scripts/             # 切换模型 / 自检 / 冒烟 / 长上下文召回 / 公共客户端
├── docs/                # 详细文档，见下表
└── strata-data/         # 模型权重、预处理包、preset、API key（可用 STRATA_DATA_DIR 改；git 忽略）
```

## 文档

| 文档 | 内容 |
|---|---|
| [docs/models.md](docs/models.md) | 两个模型的规格、档位与评测、内存与磁盘需求 |
| [docs/strata.md](docs/strata.md) | 服务参数、模型下载源（HuggingFace / ModelScope）、复用已有模型 |
| [docs/operations.md](docs/operations.md) | 前置条件、环境自检、常用任务、模型切换、数据落盘、排障 |
| [docs/api.md](docs/api.md) | 调用方式与端点 |

## 前置条件

Linux x86-64；NVIDIA 驱动（cuda13 镜像要 580+，旧卡/旧驱动用 cuda12 镜像）；Docker Engine +
compose v2 + nvidia-container-toolkit；pixi。内存 32 GiB 起，跑 Flash-Next 建议 64 GiB；
数据目录建议 NVMe（专家权重会按需读盘）。

`docker` 是宿主级服务，无法由 pixi 安装；python / curl / jq 由 pixi 提供。细节见
[docs/operations.md](docs/operations.md)。

## License

- 本仓库的脚本与配置：Apache-2.0（见 [LICENSE](./LICENSE)）
- Qwen3.6-35B-A3B 权重：Apache-2.0；Qwen3.8-Flash-Next 权重：Qwen 社区许可（见各自 HuggingFace 模型页）
- Strata：MIT
