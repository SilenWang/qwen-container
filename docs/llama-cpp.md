# 模型一：Qwen3.6-35B-A3B（llama.cpp）

服务名 `llama-server`，容器名 `qwen36-35b-a3b`，默认端口 `127.0.0.1:8080`。

## 快速开始

```bash
cp .env.example .env     # 可选：改端口 / 上下文 / 卸载档位
pixi run preflight       # 环境自检
pixi run download        # 下载 GGUF 权重（约 22.4 GB，断点续传 + 大小校验）
pixi run up              # 启动容器（会先停掉模型二）
pixi run smoke           # 自检：健康检查 + n_ctx 核对 + 一次补全 + 吞吐
```

接口是 OpenAI 兼容的 `http://127.0.0.1:8080/v1`，无鉴权，默认只绑本机回环。

## 关键运行参数

全部在 `.env`，改完要 `pixi run recreate` 才生效。

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

## 调参与注意事项

- **`N_CPU_MOE` 要在自己的卡上扫一遍**：值越大越省显存、越慢。
  `pixi run tune-ncmoe` 会逐档重建容器并记录显存与吞吐，给出可用档位；再
  `pixi run recreate && pixi run longctx` 用真实长上下文复核。
- **K/V 量化类型必须一致**（都用 `q8_0` 或都用 `q4_0`），否则 CUDA 端 FlashAttention 内核会静默回落到
  CPU 后端，prefill 掉一个数量级且日志不报错。
- **不要用 `--cpu-moe` 一刀切**：把全部专家赶进内存会浪费显存、速度接近腰斩。
- **量化不要低于 Q4_K_M**：MoE router 对低位量化敏感。
- **128K 的 prefill 是真正的瓶颈**：decode 有几十 tok/s，但 128K prompt 首次 prefill 是分钟级，长会话请复用上下文。
- **`LOAD_MODE=none` 的代价是内存**：32 GiB 会偏紧，不够可改成 `mmap`。
- **端口 8080 是容器内部约定**，不要改；换对外端口改 `PORT`。

## 多模态

模型默认是文本推理。该模型带视觉塔，需要图片输入时：

1. 在 `.env` 里设 `MMPROJ_FILE`（例如 `mmproj-F16.gguf`），`pixi run download` 会一并下载；
2. 取消 `docker-compose.yml` 中 `--mmproj` 两行的注释；
3. `pixi run recreate`。

## 相关任务

```bash
pixi run download      # 下载 GGUF（断点续传 + 校验大小）
pixi run up            # 启动（会先停模型二）
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
