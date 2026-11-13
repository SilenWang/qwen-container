# 调用方式与端点

两个服务都是 OpenAI 兼容接口，客户端只改 base URL（模型二再加 key）。

| | 模型一 | 模型二 |
|---|---|---|
| Base URL | `http://127.0.0.1:8080/v1` | `http://127.0.0.1:8081/v1` |
| 鉴权 | 无 | `Authorization: Bearer <key>` |
| 模型名 | `qwen3.6-35b-a3b` | `strata`（准确名字看 `GET /v1/models`） |

模型二的 key 在 `.env` 的 `STRATA_API_KEY`；留空时由首启自动生成，用 `pixi run strata-key` 查看。

## curl 示例

```bash
# 模型一（无鉴权）
curl http://127.0.0.1:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
        "model": "qwen3.6-35b-a3b",
        "messages": [{"role": "user", "content": "你好"}],
        "max_tokens": 256
      }'

# 模型二（要 key）
export STRATA_API_KEY=...   # 或 `pixi run strata-key` 打印出来的那个
curl http://127.0.0.1:8081/v1/chat/completions \
  -H "Authorization: Bearer $STRATA_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
        "model": "strata",
        "messages": [{"role": "user", "content": "你好"}],
        "max_tokens": 256
      }'
```

## 端点

模型一：

| 端点 | 说明 |
|---|---|
| `/health` | 就绪探针 |
| `/props` | `n_ctx`、slot 数、chat template |
| `/v1/models` | 模型列表 |
| `/v1/chat/completions` | 对话补全（返回 `timings`，含 prefill/decode 吞吐） |
| `/tokenize` | 分词 |
| `/slots` | slot 状态 |
| `/metrics` | Prometheus 指标 |

模型二：

| 端点 | 说明 |
|---|---|
| `/health` | 就绪探针（**不需要 key**） |
| `/v1/models` | 模型列表（要 key） |
| `/v1/chat/completions` | 对话补全 |
| `/v1/messages` | Anthropic 兼容接口（Claude Code 等应用可用） |

## 第三方客户端

任何支持「OpenAI 兼容 / 自定义 base URL」的客户端都能接：把 base URL 填上表里对应地址，
模型二再填 API key 和模型名即可。模型二也支持把 `http://127.0.0.1:8081` 作为 Anthropic 兼容地址使用。
