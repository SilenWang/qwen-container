# Strata 服务

服务名 `strata`，容器名 `qwen-strata`，默认端口 `127.0.0.1:8080`。一个服务里跑一个模型：
用 `STRATA_FAMILY` / `STRATA_MODEL` 选哪一个（各档位见 [README](../README.md) 和 [models.md](models.md)）。

## 关键运行参数

全部在 `.env`，改完要 `pixi run recreate` 才生效。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `STRATA_FAMILY` | `qwen` | `qwen` = Flash-Next 的 ISTA-DASLab 原版分档；`qwen36` = Qwen3.6-35B-A3B；`inkling-2-mini` = Inkling 2 Mini；`hy4-flash` = Tencent Hy4-Flash；另有 `unsloth` / `coder` / `swift` 等 |
| `STRATA_MODEL` | `IQ2_XS` | 档位名；Flash-Next 用 `Q2_0` / `IQ2_XS` / `IQ3_XXS` / `IQ3_S`，35B-A3B 用 `UD-Q4_K_XL` / `UD-Q6_K_XL` / `Q8_0`，Inkling 用 `SQ-10` / `SQ-15` / `SQ-20`，Hy4-Flash 用 `SQ-64` / `SQ-96` / `SQ-128`；留空由 Strata 按显卡自选 |
| `STRATA_PRESET` | `flash-next` | Strata 在数据目录里保存运行参数的槽位名；每个模型各用一个，切换不会互相覆盖 |
| `STRATA_IMAGE` | `ghcr.io/niko1221/strata:0.2.19-cuda13` | cuda13 要驱动 580+；GTX 10 / P100 / V100 或驱动 575–579 用 `cuda12`。0.2.19 起镜像带 `strata-hy4` / `strata-inkling` 引擎 |
| `STRATA_CONTEXT` | `128k` | 32k / 64k / 128k / 256k，或直接写 token 数；35B-A3B 在 12G 卡上建议 32k，Hy4-Flash 常用 128k（8k–128k），Inkling 常用 32k（可到 256k） |
| `STRATA_KV` | `int8` | 会话 KV 格式；`q4_0` 省一半内存 |
| `STRATA_VISION` | `off` | 图片输入；`on` 会多下约 0.9 GB 的读取器（12G 卡上跑 CPU） |
| `STRATA_TUNE` | `skip` | `quick` 首启做一次硬件校准（5–20 分钟，只做一次） |
| `STRATA_PARALLEL` | 空 | 并发数 1–8；空 = 按显卡自动 |
| `STRATA_RAM_KEEP_GIB` | `0` | 给系统留出的空闲内存（GiB）；跑大档位或机器还要干别的时设 6–8 |
| `STRATA_IDLE_UNLOAD_S` | `1800` | 空闲释放：多少秒没有新请求就卸载模型（0 = 关闭）；由 `idle-unload` 辅助容器执行 |
| `STRATA_IDLE_POLL_S` | `60` | 空闲检查间隔（秒）；阈值误差不超过这个值 |
| `STRATA_SET` | 空 | 高级引擎参数，空格分隔 `KEY=VALUE`（等价于 `strata launch --set`） |
| `STRATA_SOURCE` | 空 | 模型下载源：空 = HuggingFace，`modelscope` = 魔搭（国内）。**只作用于 Qwen 家族** |
| `HF_ENDPOINT` | 空 | HuggingFace 镜像站，如 `https://hf-mirror.com`。**只作用于 Qwen 家族** |
| `STRATA_INKLING_SOURCE` | 空 | Inkling 2 Mini 的下载 base（完整 URL）；空 = `https://huggingface.co/strata-engine/Inkling-2-Mini-Strata/resolve/main` |
| `STRATA_HY4_SOURCE` | 空 | Hy4-Flash 的下载 base（完整 URL）；空 = `https://huggingface.co/strata-engine/Hy4-Flash-Strata/resolve/main` |
| `STRATA_API_KEY` | 空 | 16 字符以上；空 = 首启自动生成并存进数据目录 |
| `STRATA_DATA_DIR` | `./strata-data` | 宿主机数据目录（挂到容器 `/data`）；已下好模型时指过去可避免重复下载 |
| `STRATA_MODEL_HOST_DIR` | `./models` | 已经有一份 GGUF 分片目录时，宿主机上的目录（挂到容器 `/models`） |
| `STRATA_MODEL_PATH` | 空 | 容器内的分片目录（通常 `/models`）；留空 = 让 Strata 自己下载 |
| `STRATA_BIND_ADDR` / `STRATA_PORT` | `127.0.0.1` / `8080` | 只绑本机；对外暴露时务必换成自己设的 key |

## 模型从哪下载

不用手动下：容器首启时由 Strata 自己下载到数据目录（默认 `./strata-data/`）：

| 家族 | 内容 | 来源 | 体积 |
|---|---|---|---:|
| Flash-Next | GGUF 分片（按档位选） | `ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF` 的 `IQ2_XS/` 等子目录 | 66–84 GB |
| 35B-A3B | GGUF 分片 | `unsloth/Qwen3.6-35B-A3B-MTP-GGUF` | 约 23 GB |
| Inkling 2 Mini | `.strata` 包（按档位选） | `strata-engine/Inkling-2-Mini-Strata` | 10.5–19.4 GB |
| Hy4-Flash | `.strata` 包 + Engram | `strata-engine/Hy4-Flash-Strata` | 130–181 GB |

Qwen 两档另会在本地生成预处理包 / 草稿层（`packs/`，约 2 GB）。Flash-Next 的草稿层默认用 **DFlash2**（约 0.8 GB），
**不会**下 MTP 那 6 GB；只有显式 `--drafting mtp` 才会去取 MTP 张量。35B-A3B 自带 MTP 草稿层，由 Strata 自己准备。

Inkling / Hy4-Flash 是 Strata 自己的 `.strata` 打包格式，不再生成预处理包：

- Inkling 自带 DSpark 草稿层，开图另下 `Inkling-2-Mini-Vision.strata`（0.64 GB）。
- Hy4-Flash 每个档位都要配同一份 `Hy4-Flash-Engram-Q8.strata`（67.58 GB，常驻内存），开图另下 0.93 GB 的 Vision 分片。
  上面 130–181 GB 已包含 Engram 与 Vision。

### 国内网络：ModelScope 或 HF 镜像

Qwen 两档两选一，都写在 `.env` 里，改完 `pixi run recreate`：

- **魔搭 ModelScope**（推荐）：同名仓库、同路径，Strata 原生支持 —— `STRATA_SOURCE=modelscope`。
  Flash-Next 在 `https://www.modelscope.cn/models/ISTA-DASLab/Qwen3.8-Flash-Next-GSQ-RCO-GGUF`
  上 `IQ2_XS/` 两个分片都在（39.2 + 28.8 GB）。ModelScope 提供的是仓库当前文件，下载后会按它公布的 SHA-256 校验。
- **HF 镜像站**：`HF_ENDPOINT=https://hf-mirror.com`（仍按 pinned revision 下载并校验）。

**Inkling 2 Mini / Hy4-Flash 目前只有 HuggingFace**：`strata-engine/Inkling-2-Mini-Strata` 和
`strata-engine/Hy4-Flash-Strata` 两个仓库在魔搭不存在（2026-11 核对：`modelscope.cn` 返回 record not found），
`STRATA_SOURCE=modelscope` 与 `HF_ENDPOINT` 对它们都不生效。国内网络请给它们单独指一个 HF 镜像 base：

```bash
# .env
STRATA_INKLING_SOURCE=https://hf-mirror.com/strata-engine/Inkling-2-Mini-Strata/resolve/main
STRATA_HY4_SOURCE=https://hf-mirror.com/strata-engine/Hy4-Flash-Strata/resolve/main
```

不写这两个变量就从官方 `huggingface.co` 取（`strata-engine/...`）。下载支持断点续传；
已下好的文件不会因为换源重下，但换档位会另下。

### 复用已经下载好的模型

把 `STRATA_DATA_DIR` 指向那份数据目录即可：

```bash
# .env
STRATA_DATA_DIR=/mnt/raid/llm_models
```

目录里应是 Strata 自己的布局（Qwen：`Strata-data/models/<档位>/…gguf`；Inkling / Hy4-Flash：
`Strata-data/models/<family>/…strata`；另有 `Strata-data/packs/…`），这样 `pixi run up` 直接加载、不再下载。

也可以只挂一份模型目录（直接放在这个目录里，不是再套一层子目录）。Qwen 是 GGUF 分片，
Inkling / Hy4-Flash 是 `.strata` 包：

```bash
# .env
STRATA_MODEL_HOST_DIR=/srv/gguf/IQ2_XS    # 宿主机目录，挂到容器的 /models（只读）
STRATA_MODEL_PATH=/models                 # 容器内路径
STRATA_MODEL=IQ2_XS                       # 和数据目录 / 分片对应的档位
```

`docker-compose.yml` 已经透传 `MODEL_PATH`，改完 `.env` 直接 `pixi run recreate`。
`STRATA_MODEL_PATH` 不设而只设 `STRATA_MODEL_HOST_DIR` 的话，Strata 会忽略挂进来的目录、照常下载。

确认会不会被重新下载：

```bash
pixi run preflight
# 已下载 IQ2_XS：2 个分片，66G → 启动时不会再下载
# 或：数据目录里还没有模型分片 → 首次启动会下载 IQ2_XS
```

## 不用 compose，直接 docker run

```bash
docker run -d --name qwen-strata --gpus all \
  -p 127.0.0.1:8080:8080 \
  --ulimit memlock=-1:-1 --shm-size 2g --stop-timeout 120 \
  -v /mnt/raid/llm_models:/data \
  -e FAMILY=qwen36 -e MODEL=UD-Q4_K_XL -e PRESET=qwen36-35b \
  -e CONTEXT=32k -e KV=int8 \
  ghcr.io/niko1221/strata:0.2.19-cuda13
```

Inkling 2 Mini 用 `FAMILY=inkling-2-mini MODEL=SQ-10 PRESET=inkling-2-mini`（12G 卡整份进显存）；
Hy4-Flash 用 `FAMILY=hy4-flash MODEL=SQ-64 PRESET=hy4-flash`，需要 64 GiB 内存。

`MODEL` 必须和数据目录里已下载的档位一致。不传 `API_KEY` 时会自动生成并存在 `/data/state/api-key`，
用 `docker exec qwen-strata strata launch --show-key --preset qwen36-35b` 查看。

## 注意事项

- **key 要等服务跑起来**：首启下载 / 加载期间还没有 key，`pixi run key` 会报 “There is no API key for that launch”；
  等 `pixi run ps` 显示 `healthy` 再取。想固定 key 就在 `.env` 设 `STRATA_API_KEY`（16 字符以上），首启就会用它。
- **等服务的超时**：`pixi run smoke` / `pixi run longctx` 默认等 1 小时（`STRATA_READY_TIMEOUT` 秒）；
  首启下载更久就 `STRATA_READY_TIMEOUT=21600 pixi run smoke`。
- **首次启动会下载模型**（Flash-Next 66–84 GB / 35B-A3B 约 23 GB / Inkling 10.5–19.4 GB / Hy4-Flash 130–181 GB）
  + 预处理包/草稿层，到数据目录。
  这期间 `docker ps` 显示 `starting`（不是 unhealthy），进度看 `pixi run logs`。
  下载可断点续传，中断后重新 `pixi run up` 会接着下。磁盘建议 SSD/NVMe —— 专家权重会按需从盘上读。
- **模型在宿主机，不在容器里**：数据目录以 bind mount 挂到容器的 `/data`，删容器 / 换镜像都不丢权重。
  所有模型的文件和平共处，切换不会删掉另一个。
- **单卡只能跑一个模型**：切换模型会重建容器，旧模型的显存随之释放；不需要手动停两个服务。
- **内存不够时会自动降级**：Strata 改成从 SSD 读专家（low-RAM 模式），能跑但明显变慢；
  32 GiB 内存跑 Flash-Next 建议直接用 `coder` 档。
- **首次加载慢**：要往内存里装几十 GB，1–3 分钟内机器可能很卡，属正常。
- **长上下文的第一轮很慢**（prefill 是瓶颈），后续轮次只读增量，几秒内开始。
- **换 `.env` 后要 `pixi run recreate`**；`pixi run restart` 不会重新读配置。
- **镜像升级**：`pixi run pull && pixi run recreate`，模型和 preset 保留在数据目录。
- **空闲释放**：默认 30 分钟没有新请求就卸载模型（`STRATA_IDLE_UNLOAD_S`，0 = 关闭），
  由 `idle-unload` 辅助容器调用 `POST /unload`；下一次请求会自动加载。日志看 `pixi run idle-logs`。
