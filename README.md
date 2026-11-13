# 本地 LLM 容器化推理（12G 显存 / 128K 上下文）

pixi 管依赖和任务，Docker 跑推理引擎，在一台带 NVIDIA 显卡（12G 显存起）的 Linux 机器上提供两个
OpenAI 兼容的本地模型服务。两个模型互相独立、按需切换；一块 12G 卡同一时间只能跑一个。

| | 模型一 | 模型二 |
|---|---|---|
| 模型 | `Qwen3.6-35B-A3B` | `Qwen3.8-Flash-Next` |
| 引擎 | llama.cpp（`llama-server`） | [Strata](https://github.com/Niko1221/Strata) |
| 上下文 | 128K | 128K |
| 端口 | `127.0.0.1:8080`（无鉴权） | `127.0.0.1:8081`（要 API key） |
| 权重 | 22.4 GB，`pixi run download` | 66–84 GB，首启自动下载（看档位） |
| 启动 | `pixi run up` | `pixi run strata-up` |

## 快速开始

```bash
cp .env.example .env        # 可选：改端口 / 档位 / 上下文

# 模型二：Qwen3.8-Flash-Next（Strata）
pixi run strata-preflight   # 驱动 / 内存 / 磁盘 / 端口自检
pixi run strata-up          # 启动（首次下载 66–84 GB，进度看 strata-logs）
pixi run strata-key         # 没设 key 时查看自动生成的 key
pixi run strata-smoke       # 健康检查 + 一次补全

# 模型一：Qwen3.6-35B-A3B（llama.cpp）
pixi run download           # 下载约 22.4 GB 权重
pixi run up                 # 启动
pixi run smoke              # 健康检查 + 参数核对 + 一次补全
```

OpenAI 兼容地址：模型一 `http://127.0.0.1:8080/v1`，模型二 `http://127.0.0.1:8081/v1`。

## 项目里有什么

```
qwen-container/
├── pixi.toml            # 唯一依赖与任务声明（pixi task list 看全部任务）
├── pixi.lock
├── docker-compose.yml   # llama-server + strata 两个服务，参数都从 .env 读
├── .env.example         # 配置模板
├── scripts/             # 下载 / 自检 / 冒烟 / 长上下文实测 / 扫参
├── docs/                # 详细文档，见下表
├── models/              # 模型一权重（git 忽略）
├── cache/               # 模型一 slot KV cache（git 忽略）
└── strata-data/         # 模型二数据（可用 STRATA_DATA_DIR 改；git 忽略）
```

## 文档

| 文档 | 内容 |
|---|---|
| [docs/models.md](docs/models.md) | 两个模型的配置、档位与评测、内存与磁盘需求 |
| [docs/llama-cpp.md](docs/llama-cpp.md) | 模型一：参数、调参与注意事项 |
| [docs/strata.md](docs/strata.md) | 模型二：参数、模型下载源（HuggingFace / ModelScope）、注意事项 |
| [docs/operations.md](docs/operations.md) | 前置条件、自检、常用任务、模型切换、排障 |
| [docs/api.md](docs/api.md) | 调用方式与端点 |

## 前置条件

Linux x86-64；NVIDIA 驱动（模型一 CUDA 12 驱动，模型二 580+，旧卡/旧驱动用 cuda12 镜像）；
Docker Engine + compose v2 + nvidia-container-toolkit；pixi。内存 32 GiB 起，模型二 `IQ2_XS` 建议 64 GiB。

`docker` 是宿主级服务，无法由 pixi 安装；python / curl / jq 由 pixi 提供。细节见
[docs/operations.md](docs/operations.md)。

## License

- 本仓库的脚本与配置：Apache-2.0（见 [LICENSE](./LICENSE)）
- Qwen3.6-35B-A3B 权重：Apache-2.0；Qwen3.8-Flash-Next 权重：Qwen 社区许可（见各自 HuggingFace 模型页）
- llama.cpp：MIT；Strata：MIT
