# 模型二：Qwen3.8-Flash-Next（Strata）

服务名 `strata`，容器名 `qwen38-flash-next`，默认端口 `127.0.0.1:8081`。
它只在 compose 的 `strata` profile 下启动（`pixi run strata-up`），不会跟着 `pixi run up` 起来。

## 快速开始

```bash
cp .env.example .env       # 可选：改档位 / 上下文 / 下载源 / key
pixi run strata-preflight  # 驱动 / 内存 / 磁盘 / 端口自检
pixi run strata-up         # 启动（首次下载 66–84 GB，进度看 strata-logs）
pixi run strata-key        # 没在 .env 里设 key 时查看自动生成的 key
pixi run strata-smoke      # 健康检查 + 模型列表 + 一次补全 + 吞吐
```

接口是 OpenAI 兼容的 `http://127.0.0.1:8081/v1`，**必须带 API key**；另有 Anthropic 兼容的
`/v1/messages`。`/health` 不需要 key。调用示例见 [api.md](api.md)。

## 关键运行参数

全部在 `.env`，改完要 `pixi run strata-recreate` 才生效。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `STRATA_IMAGE` | `ghcr.io/niko1221/strata:0.2.11-cuda13` | cuda13 要驱动 580+；GTX 10 / P100 / V100 或驱动 575–579 用 `cuda12` |
| `STRATA_MODEL` | `IQ2_XS` | 档位；留空由 Strata 按显卡自选 |
| `STRATA_CONTEXT` | `128k` | 32k / 64k / 128k / 256k，或直接写 token 数 |
| `STRATA_KV` | `int8` | 会话 KV 格式；`q4_0` 省一半内存 |
| `STRATA_VISION` | `off` | 图片输入；`on` 会多下约 0.9 GB 的读取器（12G 卡上跑 CPU） |
| `STRATA_TUNE` | `skip` | `quick` 首启做一次硬件校准（5–20 分钟，只做一次） |
| `STRATA_PARALLEL` | 空 | 并发数 1–8；空 = 按显卡自动 |
| `STRATA_RAM_KEEP_GIB` | `0` | 给系统留出的空闲内存（GiB）；跑 `IQ3_S` 或机器还要干别的时设 6–8 |
| `STRATA_SET` | 空 | 高级引擎参数，空格分隔 `KEY=VALUE`（等价于 `strata launch --set`） |
| `STRATA_SOURCE` | 空 | 模型下载源：空 = HuggingFace，`modelscope` = 魔搭（国内） |
| `HF_ENDPOINT` | 空 | HuggingFace 镜像站，如 `https://hf-mirror.com` |
| `STRATA_API_KEY` | 空 | 16 字符以上；空 = 首启自动生成并存进数据目录 |
| `STRATA_DATA_DIR` | `./strata-data` | 宿主机数据目录（挂到容器 `/data`）；已下好模型时指过去可避免重复下载 |
| `STRATA_BIND_ADDR` / `STRATA_PORT` | `127.0.0.1` / `8081` | 只绑本机；对外暴露时务必换成自己设的 key |

## 模型从哪下载

不用手动下：容器首启时由 Strata 自己下载到数据目录（默认 `./strata-data/`），主要是两样：

| 内容 | 来源 | 体积 |
|---|---|---:|
| GGUF 分片（按档位选） | HuggingFace `ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF` 的 `IQ2_XS/` 等子目录 | 66–84 GB |
| 预处理包 + 草稿层 | 本地由上面分片生成（`packs/`），草稿层默认是 DFlash2 | 约 2 GB |

草稿层默认用 **DFlash2**（约 0.8 GB），**不会**下 MTP 那 6 GB；只有显式 `--drafting mtp` 才会去取 MTP 张量。

### 国内网络：ModelScope 或 HF 镜像

两选一，都写在 `.env` 里，改完 `pixi run strata-recreate`：

- **魔搭 ModelScope**（推荐）：同名仓库、同路径，Strata 原生支持 —— `STRATA_SOURCE=modelscope`。
  `https://www.modelscope.cn/models/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF` 上 `IQ2_XS/` 两个分片
  都在（39.2 + 28.8 GB）。ModelScope 提供的是仓库当前文件，下载后会按它公布的 SHA-256 校验。
- **HF 镜像站**：`HF_ENDPOINT=https://hf-mirror.com`（仍按 pinned revision 下载并校验）。

两个都留空就是官方 HuggingFace。下载支持断点续传；已下好的文件不会因为换源重下，但换档位会另下。

### 复用已经下载好的模型

把 `STRATA_DATA_DIR` 指向那份数据目录即可：

```bash
# .env
STRATA_DATA_DIR=/mnt/raid/llm_models
```

目录里应是 Strata 自己的布局（`Strata-data/models/<档位>/…gguf`、`Strata-data/packs/…`），
这样 `pixi run strata-up` 直接加载、不再下载。

也可以只挂一份 GGUF 分片目录：compose 里加 `- /your/gguf/IQ2_XS:/models:ro`，再设
`MODEL_PATH=/models`、`MODEL=IQ2_XS`。

## 注意事项

- **首次启动会下载 66–84 GB（看档位）+ 约 2 GB 预处理包/草稿层**（到数据目录，默认 `./strata-data/`），
  这期间 `docker ps` 显示 `starting`（不是 unhealthy）；进度看 `pixi run strata-logs`。
  下载可断点续传，中断后重新 `strata-up` 会接着下。磁盘建议 SSD/NVMe —— 专家权重会按需从盘上读。
- **模型在宿主机，不在容器里**：数据目录以 bind mount 挂到容器的 `/data`，删容器 / 换镜像都不丢权重。
- **单卡只能跑一个模型**：`pixi run up` 会先停模型二，`pixi run strata-up` 会先停模型一；
  只停模型二用 `pixi run strata-down`。
- **内存不够时会自动降级**：Strata 改成从 SSD 读专家（low-RAM 模式），能跑但明显变慢；
  32 GiB 内存建议直接用 `coder` 档。
- **首次加载慢**：要往内存里装 30–50 GB，1–3 分钟内机器可能很卡，属正常。
- **128K 上下文的第一轮很慢**（prefill 是瓶颈），后续轮次只读增量，几秒内开始。
- **换 `.env` 后要 `strata-recreate`**；`strata-restart` 不会重新读配置。
- **镜像升级**：`pixi run strata-pull && pixi run strata-recreate`，模型和 preset 保留在数据目录。

## 相关任务

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

档位选择、内存与评测数据见 [models.md](models.md)。
