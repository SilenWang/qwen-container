# Qwen3.6-35B-A3B 容器化推理（12G 显存 / 128K 上下文）

这个仓库用来在**一台带 NVIDIA 显卡（12G 显存起步）的 Linux 机器**上跑起一个本地大模型服务：
pixi 管依赖和任务，Docker 里跑 [llama.cpp](https://github.com/ggml-org/llama.cpp) 的 `llama-server`，
对外提供 OpenAI 兼容接口。

适合的场景：显存不大（12G 级）、但需要**长上下文（128K）**、并且希望生成速度可用（几十 tok/s）的本地部署。

```bash
cp .env.example .env     # 可选：改端口 / 上下文 / 卸载档位
pixi run download        # 下载 GGUF 权重（约 22.4 GB）
pixi run up              # 启动容器
pixi run smoke           # 自检：健康检查 + n_ctx 核对 + 一次补全 + 吞吐
```

服务起来后是 OpenAI 兼容接口：`http://127.0.0.1:8080/v1`。

---

## 模型基本配置

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

---

## 前置条件

| 项目 | 要求 |
|---|---|
| 系统 | Linux（WSL2 也可） |
| GPU | NVIDIA，12G 显存起步 |
| 驱动 | 支持 CUDA 12 的驱动 + **nvidia-container-toolkit**（`docker run --gpus all` 可用） |
| Docker | Docker Engine + `docker compose` v2 |
| pixi | 用于安装依赖与跑任务 |
| CPU | 多核（MoE 专家在内存侧靠 CPU 读） |
| 内存 | **32 GiB 起步，建议 48–64 GiB**（`LOAD_MODE=none` 下整份权重都要进内存） |
| 磁盘 | 模型 22.4 GB + 镜像若干 GB，建议 NVMe |

`docker` 是宿主级服务，无法由 pixi 安装；其余依赖（python / curl / jq）都由 pixi 提供。

先跑一次环境自检：

```bash
pixi run preflight
```

它会检查 docker、nvidia 运行时、内存/磁盘、模型文件，并回显 `docker compose config` 解析出的关键参数。

---

## 常用任务

```bash
pixi run download      # 下载 GGUF（断点续传 + 校验大小）
pixi run up            # 启动容器
pixi run logs          # 跟随日志
pixi run ps            # 容器状态 / 健康检查
pixi run smoke         # 冒烟自检（依赖 up）
pixi run longctx       # 长上下文阶梯实测（needle 召回 + prefill/decode 吞吐）
pixi run tune-ncmoe    # 扫 --n-cpu-moe 卸载档位
pixi run recreate      # 改了 .env 后按新配置重建容器
pixi run config        # 只看解析后的 compose 配置
pixi run pin-image     # 打印镜像 digest，便于锁定版本
pixi run build-image   # 可选：自编译 llama.cpp CUDA 镜像
pixi run down          # 停止
```

## 调用方式

任何 OpenAI SDK 指向 `http://127.0.0.1:8080/v1` 即可：

```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
        "model": "qwen3.6-35b-a3b",
        "messages": [{"role": "user", "content": "你好"}],
        "max_tokens": 256
      }'
```

其他端点：`/health`（就绪）、`/props`（`n_ctx` / slot 数）、`/metrics`（Prometheus）、`/tokenize`、`/slots`、`/v1/models`。

---

## 调参与注意事项

- **`N_CPU_MOE` 要在自己的卡上扫一遍**：值越大越省显存、越慢。
  `pixi run tune-ncmoe` 会逐档重建容器并记录显存与吞吐，给出可用档位；再 `pixi run recreate && pixi run longctx` 用真实长上下文复核。
- **K/V 量化类型必须一致**（都用 `q8_0` 或都用 `q4_0`），否则 CUDA 端 FlashAttention 内核会静默回落到 CPU 后端，prefill 掉一个数量级且日志不报错。
- **不要用 `--cpu-moe` 一刀切**：把全部专家赶进内存会浪费显存、速度接近腰斩。
- **量化不要低于 Q4_K_M**：MoE router 对低位量化敏感。
- **128K 的 prefill 是真正的瓶颈**：decode 有几十 tok/s，但 128K prompt 首次 prefill 是分钟级，长会话请复用上下文。
- **`LOAD_MODE=none` 的代价是内存**：32 GiB 会偏紧，不够可改成 `mmap`。
- **端口 8080 是容器内部约定**，不要改；换对外端口改 `PORT`。

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

## License

- 本仓库的脚本与配置：Apache-2.0（见 [LICENSE](./LICENSE)）
- Qwen3.6-35B-A3B 模型权重：Apache-2.0（见 HuggingFace 模型页）
- llama.cpp：MIT
