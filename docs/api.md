# 调用方式与端点

一个服务，两套接口，客户端只改 base URL 和 key：

| | 值 |
|---|---|
| Base URL（OpenAI 兼容） | `http://127.0.0.1:8080/v1` |
| Base URL（Anthropic 兼容） | `http://127.0.0.1:8080` |
| 鉴权 | `Authorization: Bearer <key>`（`/health` 不需要） |
| 模型名 | 从 `GET /v1/models` 取（当前加载的模型） |

key 在 `.env` 的 `STRATA_API_KEY`；留空时由首启自动生成，用 `pixi run key` 查看。

## curl 示例

```bash
export STRATA_API_KEY=...   # 或 `pixi run key` 打印出来的那个

# 模型名先查一下（切模型后会变）
curl -s http://127.0.0.1:8080/v1/models \
  -H "Authorization: Bearer $STRATA_API_KEY"

curl http://127.0.0.1:8080/v1/chat/completions \
  -H "Authorization: Bearer $STRATA_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
        "model": "strata",
        "messages": [{"role": "user", "content": "你好"}],
        "max_tokens": 256
      }'
```

`model` 字段写 `GET /v1/models` 返回的 id；不同模型 / 档位下它可能不同（脚本里用返回值而不是硬编码）。

## 端点

| 端点 | 说明 |
|---|---|
| `/health` | 就绪探针（不需要 key） |
| `/v1/models` | 模型列表（要 key） |
| `/v1/chat/completions` | 对话补全（OpenAI 兼容） |
| `/v1/messages` | Anthropic 兼容接口（Claude Code 等应用可用） |

## 第三方客户端

任何支持「OpenAI 兼容 / 自定义 base URL」的客户端都能接：base URL 填 `http://127.0.0.1:8080/v1`，
再填 API key 和 `GET /v1/models` 里的模型名。Claude Code 这类 Anthropic 兼容客户端把
`http://127.0.0.1:8080` 作为 base URL、同一个 key 即可。

容器内也可以让 Strata 直接生成配置：

```bash
docker compose exec strata strata connect opencode          # 打印配置
docker compose exec strata strata connect opencode --check  # 发一个 prompt + 一次 tool call 验证
```
