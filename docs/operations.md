# 前置条件、任务与排障

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

## 环境自检

```bash
pixi run preflight          # 模型一：docker / GPU 运行时 / 内存 / 磁盘 / 模型文件
pixi run strata-preflight   # 模型二：驱动版本 / 内存 / 磁盘 / 端口 / compose 配置
```

两个自检互不影响：跑 `strata-preflight` 不会因为模型一的权重没下载而报错。

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
pixi run restart       # 重启（不重读 .env）
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
pixi run strata-restart    # 重启（不重读 .env）
pixi run strata-config     # 只看渲染后的配置
pixi run strata-pull       # 更新镜像
pixi run strata-shell      # 进容器
pixi run strata-down       # 停止（卸载模型，释放显存与内存）
```

两个都停 / 都删：`pixi run down`（带 `--profile strata`，会一并清理模型二）。

## 模型切换

一块 12G 卡同时跑不了两个模型，所以两条启动命令互相排他：

| 想跑 | 命令 | 会先停掉 |
|---|---|---|
| 模型一 | `pixi run up` | `strata` 容器 |
| 模型二 | `pixi run strata-up` | `llama-server` 容器 |

只停不停另一个：`pixi run down`（两个都停）、`pixi run strata-down`（只停模型二）。

## 数据落在哪

| 内容 | 路径 | 说明 |
|---|---|---|
| 模型一权重 | `./models/` | `pixi run download` 下载；容器只读挂载到 `/models` |
| 模型一 KV cache | `./cache/` | 容器内 `/cache`，`--slot-save-path` |
| 模型二数据 | `./strata-data/`（可用 `STRATA_DATA_DIR` 改） | 模型、预处理包、preset、API key；容器内 `/data` |

三者都在 `.gitignore` 里，不会提交；删容器或换镜像都不会丢。

## 排障

**模型一起不来，日志说找不到模型文件**：`./models` 里没有 GGUF，先 `pixi run download`。

**端口被占用**：模型一改 `PORT`，模型二改 `STRATA_PORT`，改完 `recreate`。

**模型二报 “The engine in this image does not match this computer's graphics card” 或启动即退出**：镜像档位与驱动不匹配 ——
cuda13 要驱动 580+；驱动 575–579 或 GTX 10 / P100 / V100 换成 `cuda12` 镜像
（`STRATA_IMAGE=ghcr.io/niko1221/strata:0.2.11-cuda12`）。

**模型二一直 `starting`**：首次启动在下载 66–84 GB 模型，看 `pixi run strata-logs` 的进度；
下载可断点续传，中断后重新 `strata-up` 会接着下。

**整机卡住 / 内存被吃满**：Strata 要往内存里装 30–50 GB，首次加载 1–3 分钟机器很卡属正常；
长期不够就换小档（`Q2_0` / coder）或在 `.env` 设 `STRATA_RAM_KEEP_GIB=8` 给系统留内存。

**模型二很慢、磁盘灯常亮**：内存不足，专家在从 SSD 读。换小档，或确认 `./strata-data`
（`STRATA_DATA_DIR`）在 NVMe 上。

**改了 `.env` 不生效**：两个服务都要 `recreate`（`pixi run recreate` / `pixi run strata-recreate`），
`restart` 不会重新读 `.env`。

**模型一 128K 首轮特别慢**：prefill 是瓶颈，属预期；长会话尽量复用上下文，或用
`pixi run longctx` 实测召回与吞吐。
