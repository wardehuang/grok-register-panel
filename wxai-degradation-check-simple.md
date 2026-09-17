# Grok-4.6 降智检测最简复刻

## 1. 请求

### 端点

```text
POST https://cli-chat-proxy.grok.com/v1/responses
```

### 请求头

```http
Authorization: Bearer [REDACTED]
Accept: text/event-stream
Content-Type: application/json
X-XAI-Token-Auth: xai-grok-cli
x-grok-client-version: 1.0.4
User-Agent: xai-grok-workspace/1.0.4
```

### 请求体

```json
{
  "model": "grok-4.6",
  "input": "用中文回答：17 × 23 等于多少？只输出计算过程和答案。",
  "stream": true,
  "reasoning": {
    "effort": "high",
    "summary": "detailed"
  },
  "max_output_tokens": 96,
  "temperature": 0
}
```

## 2. SSE 返回示例

下面是精简后的结构示例。真实返回会包含更多字段。

```text
data: {"type":"response.reasoning_summary_text.delta","item_id":"rs_1","delta":"把 23 拆成 20 加 3，分别计算后相加。"}

data: {"type":"response.output_text.delta","delta":"17 × 23 = 17 × (20 + 3) = 340 + 51 = 391\n答案：391"}

data: {"type":"response.output_item.done","item":{"id":"rs_1","type":"reasoning","status":"completed","encrypted_content":"[至少 256 字节的加密思考内容]","summary":[{"type":"summary_text","text":"把 23 拆分成 20 和 3，计算 17×20 与 17×3，再把两个结果相加得到最终答案。"}]}}

data: {"type":"response.completed","response":{"usage":{"output_tokens":65,"output_tokens_details":{"reasoning_tokens":40}}}}

data: [DONE]
```

程序还必须记录 SSE 的实际到达时间。只看返回 JSON，不能计算 TPS。

## 3. 计算

```text
evaluatedTokens = outputTokens + reasoningTokens
generationMs = 首个有效 data JSON 到 response.completed 的时间
TPS = evaluatedTokens × 1000 ÷ generationMs
```

使用上面的示例：

```text
evaluatedTokens = 65 + 40 = 105
```

真实思考证据满足任意一项即可：

- 有效 summary 不少于 32 个字符。
- reasoning item 已完成，并且 `encrypted_content` 不少于 `max(256, reasoningTokens × 4)` 字节。

`burst_dump` 当前不判降智；命中后视为有真实思考。

## 4. 得出结论

当前阈值：

```text
soft TPS = 0.1
hard TPS = 500
TTFB = 5 秒
generation = 1 秒
token threshold = 100
```

### 正常示例

```text
generationMs = 5000
TPS = 105 × 1000 ÷ 5000 = 21
isRealThinking = true
```

结论：

```json
{
  "classification": "normal",
  "qualityLevel": "healthy",
  "reason": "within_threshold"
}
```

### 降智示例

相同返回内容，但在 `100ms` 内集中返回：

```text
generationMs = 100
TPS = 105 × 1000 ÷ 100 = 1050
```

`1050 >= hard TPS 500`，结论：

```json
{
  "classification": "suspected_degradation",
  "qualityLevel": "hard",
  "reason": "hard_tps"
}
```

另外两种降智条件：

```text
0.1 < TPS < 500，并且没有真实思考证据
=> soft_tps_missing_real_thinking

TTFB > 5 秒，并且 generationMs < 1000，并且 evaluatedTokens > 100
=> ttfb_downgrade
```

判定顺序：

```text
hard_tps
soft_tps_missing_real_thinking
ttfb_downgrade
否则 normal
```
