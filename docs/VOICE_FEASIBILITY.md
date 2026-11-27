# 手机端与全局助手全双工语音：可行性评估（R7）

调研与撰写日期：2026-11-27。对应 [总计划](REBUILD_PLAN_2026-11-27.md) R7。本文是选型与方案依据，不代表已实现；原型见 `frontend/voice.html`（只用浏览器原生能力和假数据，不调用任何后端接口）。

来源一律注明查阅日期（均为 2026-11-27）。标“经验估计”的数字没有在本机实测；标“待实测”的结论需要真机验证后才能写进正式设计。

## 结论摘要

1. **可行，但“全双工”要分层实现。** 全局助手本身是 Claude Code / Codex 会话，只收发文字；Claude 当前所有模型都是“文字和图片输入、文字输出”（[Anthropic 模型概览](https://platform.claude.com/docs/en/models/overview)）。所以语音层只能套在助手外面：端侧负责采集、回声消除、说话检测和打断（毫秒级）；识别和合成把语音与文字互转；助手仍按“一条带来源的普通输入”工作。打断、插话都在端侧完成，不需要助手本身支持音频。
2. **手机浏览器经现有局域网地址访问时，麦克风根本拿不到。** 局域网访问是 `http://<局域网IP>:47812`，不是安全上下文，`navigator.mediaDevices` 为 `undefined`（见第 3 节）。手机网页要用语音，必须先有 HTTPS；原生 App 不受这条限制。
3. **推荐路线：先做“端侧识别 / 合成 + 统一协议只传文字”。** 原生 App 用系统识别与合成，浏览器在安全上下文里用 Web Speech；Host 只多一个“语音话轮”事件类型，不引入新依赖、不需要用户密钥、带宽最低。宿主侧本地识别（sherpa-onnx）和云端实时语音模型作为后续可选项，前者要引入新依赖，后者要用户自备密钥，都需要用户拍板。
4. **汇报走快路径，派活必须确认。** 汇报由宿主直接用操作清单的只读操作拼出卡片和口播稿，不等大模型；派活一律生成结构化确认卡片，语音“确认”只绑定当前那张卡片，高风险操作只接受屏幕点按。

## 1. 采集、播放、回声消除与打断

| 环节 | 浏览器 | 原生 App | 结论 |
| --- | --- | --- | --- |
| 采集 | `getUserMedia({audio:{echoCancellation, noiseSuppression, autoGainControl}})`；只在安全上下文可用 | iOS `AVAudioEngine`，Android `AudioRecord` | 都可行；浏览器受 HTTPS 限制 |
| 回声消除 | `echoCancellation:true` 时浏览器“至少要消除 remote-only”，即来自 `RTCPeerConnection` 的远端声音，“应尽量”消除全部系统声音；`"all"` 才明确要求消除全部本机播放 | iOS `setVoiceProcessingEnabled(_:)`（iOS 13+）启用系统语音处理；Android `AcousticEchoCanceler` 挂到 `AudioRecord` 会话 | **浏览器里用 `speechSynthesis` 或 `<audio>` 播放助手语音时，不保证被消掉** |
| 打断（barge-in） | 端侧 VAD 检测到用户开口 → 立即 `speechSynthesis.cancel()` / 停止音频 → 记录已播放到的字位置 | 同左，用系统播放器 | 必须在端侧做；不能等服务端 |

来源：[MDN getUserMedia](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia)（页面更新于 2025-11-30）、[MDN echoCancellation](https://developer.mozilla.org/en-US/docs/Web/API/MediaTrackConstraints/echoCancellation)（2026-10-08）、[Apple setVoiceProcessingEnabled](https://developer.apple.com/documentation/avfaudio/avaudioionode/setvoiceprocessingenabled(_:))、[Android AcousticEchoCanceler](https://developer.android.com/reference/android/media/audiofx/AcousticEchoCanceler)（2026-08-03）。

浏览器免提模式的现实问题：外放时助手的声音可能被麦克风收回去，被识别成“用户在说话”，把自己打断。对策按代价从低到高：

1. 助手说话期间提高 VAD 门限，要求连续 ≥ 250ms 的人声才算打断（经验估计，待实测）；
2. 提示戴耳机，或在原型里把“免提”标成实验；
3. 把合成音频经一对本机 `RTCPeerConnection` 回环播放，让浏览器把它当成远端声音来消（待实测，各浏览器表现不一）；
4. 原生 App 用系统语音处理，这是最可靠的路径。

打断以后的语义：端侧上报“已播放到第 N 字”，宿主把助手这条回复标成“被打断，用户只听到前 N 字”，下一轮输入附上这个事实，避免助手以为用户已经听完了。

## 2. 识别与合成候选

### 2.1 浏览器原生 Web Speech

| 能力 | 实际可用性 | 隐私 |
| --- | --- | --- |
| `SpeechRecognition` | Chrome 桌面 / Android、Samsung Internet：部分支持（带前缀，部分属性缺失）；**iOS / macOS Safari：部分支持，前缀 `webkitSpeechRecognition`，需要开启 Siri，在 SafariViewController 里不可用**；Firefox：不支持（藏在 about:config 开关后）；Edge：对象存在但事件不触发 | MDN：部分浏览器默认把音频发到服务端识别，除非用 `processLocally` 要求本机识别；规范新增 `available()` / `install()` 管理本机语言包 |
| `speechSynthesis` | Chrome、Android Chrome、iOS Safari、Android Firefox、Samsung Internet 支持；Chrome 在部分桌面系统上播放约 15 秒后会停 | 系统语音在本机合成，部分“在线”音色走云端 |

来源：[MDN SpeechRecognition](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition)（2026-11-16）、caniuse 数据 [speech-recognition](https://caniuse.com/speech-recognition) 与 [speech-synthesis](https://caniuse.com/speech-synthesis)（原始数据 [GitHub](https://github.com/Fyrd/caniuse/blob/main/features-json/speech-recognition.json) 最后更新 2026-11-24）。

结论：浏览器原生能力**够做原型和“有就用”的第一版**，但不能作为唯一方案：Firefox 和 Edge 不可用；识别默认可能上传到浏览器厂商；`continuous` 长时间识别在移动端会被系统中断，需要自动重启；中文标点、专有名词（工作区名、Leader 名）准确率无法控制（`phrases` 上下文偏置是新属性，支持面未核实）。

### 2.2 本机开源

| 方案 | 能力 | 许可 | 现状 | 适合放在 |
| --- | --- | --- | --- | --- |
| [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) | 流式识别、合成、VAD、说话人分离，离线运行；有 WebSocket 服务端、Android / iOS / HarmonyOS 绑定 | Apache-2.0 | v2.0.3，2026-11-22 发布 | 宿主侧统一识别 / 合成，或原生 App 内嵌 |
| [whisper.cpp](https://github.com/ggml-org/whisper.cpp) | Whisper 的 C/C++ 实现，准确但偏批处理 | MIT | v1.9.6，2026-11-23 发布 | 定稿复核、长语音转写 |
| [Silero VAD](https://github.com/snakers4/silero-vad) | 说话检测 | MIT | 2026-11-26 仍在更新 | 端侧或宿主侧 VAD |

优点：零费用、音频不出本机、准确率可控（可以用工作区名、Leader 名做热词）。代价：要下载模型（几十到几百 MB，经验估计）、占宿主 CPU，并且属于**新依赖**，按总计划决策日志需要硬理由和用户拍板。

### 2.3 云端实时语音

价格均取自官方价目页（2026-11-27 查阅）：

| 服务 | 形态 | 价格 | 备注 |
| --- | --- | --- | --- |
| OpenAI `gpt-realtime-2.1` / `-mini` | 语音到语音，WebRTC / WebSocket | 音频输入 $32 / $10，输出 $64 / $20（每百万 token） | 官方推荐浏览器与移动端用 WebRTC，由应用服务器用标准密钥建会话 |
| OpenAI GPT-Live `gpt-live-1.1` | 语音会话 | 活跃 $0.05/分钟，待机 $0.02/分钟，按秒计费 | |
| OpenAI `gpt-live-transcribe` | 实时转写 | 约 $0.017/分钟 | |
| Google `gemini-3.8-live` 等 | 语音到语音 | 音频输入约 $0.005/分钟，输出约 $0.018/分钟 | **免费档数据会用于改进产品，付费档不会** |
| Google `gemini-3.5-transcribe-live` | WebSocket 双向流式转写 | 综合约 $0.009/分钟 | 同上 |
| Google `gemini-3.8-flash-tts` | 合成 | 2026 年内约 $0.00225 / 10 秒，2027-01-01 起翻倍 | |
| 阿里云百炼 | 实时识别 `qwen-audio-3.1-asr-flash-streaming`；合成 `qwen-audio-3.1-tts-*`；双工 `qwen-audio-3.1-live`（“实时双工语音对话，支持委派后台任务”） | 按量计费，未逐项摘录 | 中文场景与国内网络友好 |

来源：[OpenAI 定价](https://developers.openai.com/zh-Hans/api/docs/pricing)、[OpenAI WebRTC 指南](https://developers.openai.com/zh-Hans/api/docs/guides/voice-webrtc?voice-api=realtime)、[Gemini 定价](https://ai.google.dev/gemini-api/docs/pricing)、[阿里云百炼模型大全](https://help.aliyun.com/zh/model-studio/models)（页面更新于 2026-11-25）。

注意：语音到语音模型自己就是“大脑”，**不是** Crew 的全局助手。直接用它会出现两个助手，记忆、权限各一套。可行用法是让它当“前台”：只负责自然话轮、寒暄和朗读，所有查询与操作都通过工具调用转给全局助手或操作清单。阿里云的“委派后台任务”正是这种模式。都需要用户自备密钥并付费，**本轮按红线不接入**。

## 3. 安全上下文与局域网访问

现状（`plugin/crew/remote_access.py`）：

- 局域网入口是独立监听，默认 `0.0.0.0:47812`，**明文 HTTP**；设备用配对码登录，浏览器拿 `HttpOnly; SameSite=Strict` Cookie，桌面客户端拿 Bearer 令牌。
- 请求校验写死了 `Origin` 必须等于 `'http://' + Host`（第 364 行）；只放行私有地址来源。

MDN：`getUserMedia()` 只在安全上下文可用（HTTPS、`file:///` 或 localhost），**不安全的上下文里 `navigator.mediaDevices` 是 `undefined`**。手机打开 `http://192.168.x.x:47812` 不属于安全上下文，所以：

| 场景 | 麦克风 | 结论 |
| --- | --- | --- |
| 电脑本机 `http://localhost:<端口>` | 可用 | localhost 算安全上下文，可在电脑上直接体验 |
| 手机经局域网 HTTP | **不可用** | 原型检测到后自动切模拟演示，并提示原因 |
| 手机经 HTTPS | 可用 | 需要下面任一方案 |
| 原生 App 经局域网 HTTP | 可用（走系统麦克风权限） | 但 iOS 要处理本地网络权限和 ATS 明文例外、Android 要放行明文流量（平台配置，待原生 App 立项时核实） |

HTTPS 方案对比：

| 方案 | 做法 | 代价 | 建议 |
| --- | --- | --- | --- |
| A. 固定 Cloudflare Tunnel + Access | 已有决策“有域名后采用”（[decisions-remote-access.md](decisions-remote-access.md)），公网 HTTPS、正规证书 | 需要域名和 Cloudflare 账号；音频经第三方转发 | 有域名后的手机网页正式路径 |
| B. 局域网 HTTPS + 本地根证书 | 用 [mkcert](https://github.com/FiloSottile/mkcert)（BSD-3-Clause）为局域网 IP 签证书，监听 TLS | 每台手机都要安装并信任根证书，iOS 还要在设置里单独打开信任，步骤多；Python 标准库能加载证书，但不能生成证书，要引入外部程序 | 只作开发者选项，需用户拍板 |
| C. 原生 App | 不依赖浏览器安全上下文 | 要开发 App | 终局主路径 |
| D. Chrome 开发者开关把指定 HTTP 地址当安全来源 | 只适合开发调试 | 普通用户不可用 | 只写进开发说明 |

无论选 A 还是 B，`remote_access.py` 的 `Origin` 校验都要改成接受实际协议（`https://` + Host）。这条改动属于阶段二以后（`web.py` 拆分完成后），本轮不改。

## 4. 延迟

经验估计，未实测；真机实测要等原生 App 或 HTTPS 就绪：

| 段 | 典型耗时 | 说明 |
| --- | --- | --- |
| 端侧 VAD 检测到开口 / 停顿 | 开口约 30–100ms；判定说完需要 300–700ms 静音 | 端点检测是“说完到开始回应”的主要固定成本 |
| 流式识别 | 临时字幕约 200–500ms 跟上；定稿在端点后 | 端侧和云端相近 |
| 全局助手（CC / Codex） | **数秒到数十秒**，调用工具时更长 | 最大头，语音层改变不了 |
| 合成首音 | 系统合成接近即时；云端约 200–500ms | 按句流式合成 |
| 打断生效 | < 100ms | 只在端侧停播 |

对策：
1. 用户说完立即给本地提示音或短句（“好，我看一下”），不等助手；
2. 汇报类请求走宿主快路径（第 6 节），不经过大模型，亚秒级出卡片；
3. 助手回复按句流式送合成，第一句说完前就开始播；
4. 长任务不让用户干等：助手确认已派出就结束这一轮，结果完成后用通知或下次汇报告知。

## 5. 费用与隐私

以每天 20 分钟语音（用户说 8 分钟、助手说 6 分钟）为例，按第 2.3 节价目估算：

| 方案 | 每天 | 每月（30 天） | 音频去向 |
| --- | --- | --- | --- |
| 端侧原生识别 / 合成（推荐第一版） | $0 | $0 | iOS 走系统 / Siri；Chrome 默认可能上传 Google，除非 `processLocally` |
| 宿主侧 sherpa-onnx | $0（耗电脑 CPU） | $0 | 不出本机 |
| 云端识别 + 云端合成（如 Gemini 转写 + TTS） | 约 $0.26 | 约 $8 | 经服务商；免费档会被用于改进产品 |
| GPT-Live 全程活跃 | 约 $1.00 | 约 $30 | 经服务商 |

助手本身的推理沿用用户已有的 CC / Codex 订阅，不额外计费。原则：**默认不让音频离开用户设备和电脑**；云端方案只作用户自选、自带密钥的选项，设置里写清楚音频去向。宿主不保存原始音频，只保存识别后的文字，并在消息上标明来源为语音、识别置信度和是否被打断。

## 6. 接入统一协议与操作清单

### 6.1 放在哪一层

```
手机 / 网页端                     Host（Crew 后台）                          全局助手
采集 → AEC → VAD → 识别 ──文字话轮──▶ 语音网关 ──普通输入（来源=voice）──▶ CC / Codex 会话
播放 ◀─ 合成 ◀── 文字增量 + 卡片 ◀── 事件流 ◀────── 回复 / 操作清单调用 ◀──┘
打断：端侧立即停播 ──barge_in(played_chars)──▶ 标注“用户只听到前 N 字”
```

第一版语音网关只收发**文字话轮**和**卡片**，不收音频：

- 上行（命令，走统一协议的写操作，带命令 ID，只执行一次）：`voice.utterance`（定稿文字、置信度、语言、是否打断了上一条回复）、`voice.barge_in`（被打断的回复 ID、已播放字数）、`voice.confirm`（卡片 ID、确认 / 修改 / 取消）。
- 下行（事件流，沿用 R0 的“快照 + 序号”增量推送，断线补发；补不上就重拿快照；重连不重发写操作）：`assistant.text.delta`（按句切分，便于流式合成）、`voice.card.report`、`voice.card.confirm`、`voice.card.resolved`、`voice.state`（听 / 想 / 说）。
- 现有聊天事件流已经是 `text/event-stream` 加 `seq`（`web.py` `_chat_events`），语音事件可以并入同一条流，不另开连接。

以后要在宿主侧做识别或合成（第 2.2、2.3 节），才需要音频流：

- 上行音频用 WebSocket 二进制帧（16kHz PCM 或 Opus，每帧 20–40ms）。标准库没有 WebSocket 服务端，但 Crew 已经用标准库实现过 Lark 的 WebSocket 客户端（`lark.py`），服务端握手和分帧可以同样实现，不必引入第三方库。在 R0 定协议时预留 `voice.audio` 通道即可。
- 若改用分段 POST 上行音频，额外延迟约一个分段长度（经验估计 200–500ms），实现最简单，可作过渡。
- WebRTC 需要 DTLS / SRTP，标准库做不了，只在“浏览器直连云端实时模型、宿主只签发临时会话”时使用。

### 6.2 与全局助手和操作清单（方案 C）结合

- **身份与权限**：语音只是输入方式，不是新身份。语音话轮以用户身份进入全局助手，与 QQ / Lark 渠道一样“带上原始来源”（见 [REMOTE_CHANNELS.md](REMOTE_CHANNELS.md)）。助手调用操作清单时用自己会话的 MCP 令牌，权限按范围在后端卡死，语音来源**不提升**任何权限。
- **汇报（只读快路径）**：操作清单里的只读操作（待拍板、待办、各工作区目标状态、最近交付）由宿主直接组装成 `voice.card.report`，并按模板生成口播稿，不经过大模型。用户追问细节时才把问题交给助手。卡片每一行带操作清单条目的引用，点击可跳到对应页面。
- **派活（写操作一律确认）**：助手要执行写操作（例如向某个 Leader 发消息、创建目标）时，不直接执行，而是先调用清单里的“提议”操作，宿主据此生成 `voice.card.confirm`：操作名、参数、目标工作区与负责人、风险级别（来自清单的只读 / 破坏性 / 可重复标注）。
  - 语音“确认 / 取消”只作用于**当前展示的那张卡片**（卡片 ID 由端侧随话轮一起上报），不允许语音去确认其他卡片；
  - 清单标为破坏性的操作只接受屏幕点按，语音“确认”只会把卡片高亮并提示点按；
  - 宿主校验卡片仍有效（参数快照未变）后才执行，沿用 QQ 拍板按钮的快照检查思路。
- **`crew_search` 按需加载**：语音对话里助手要用到非核心工具时，照常通过 `crew_search` 加载，语音层无需关心。

## 7. 分阶段建议

| 阶段 | 内容 | 依赖 | 需要用户拍板 |
| --- | --- | --- | --- |
| 原型（本次） | `frontend/voice.html`：按住说话 / 免提、实时字幕、打断、汇报卡片、派活确认；浏览器原生能力或模拟；不连后端 | 无 | 否 |
| 第一版 | 语音网关只传文字话轮和卡片（第 6.1 节），挂在 R0 统一协议与操作清单上；浏览器在 localhost 或 HTTPS 下用 Web Speech；原生 App 用系统识别 / 合成与语音处理 | R0 协议、R5 操作清单 | 否（零新依赖） |
| 手机网页 HTTPS | 方案 A（有域名后）或方案 B（开发者选项） | 域名 / mkcert | 是 |
| 宿主侧本地识别 / 合成 | sherpa-onnx + Silero VAD，WebSocket 音频通道，热词用工作区名和 Leader 名 | 新依赖、模型下载 | 是 |
| 云端“前台”实时模型 | 用户自带密钥，前台模型只做话轮，所有操作经全局助手 / 操作清单 | 付费服务 | 是 |

## 8. 原型说明与未覆盖之处

原型 `frontend/voice.html`（访问 `/voice.html`，没有入口链接；桌面浏览器加 `?sim` 可强制模拟演示）采用“声场舞台”方向：上部同心环声场随状态与电平呼吸，下方大字显示当前这一句并逐字跟读，更早的对话和汇报 / 派活卡片收进时间线，底部是沿用 `#m-tabbar` 语言的悬浮胶囊（按住 / 免提切换、麦克风、打断、挂断）。视觉稿由 CLI Claude Code 出了两个方向，另一版“胶囊即声音”（逐字稿式排版）未采用，已删除。

判断顺序：安全上下文且有 `SpeechRecognition` 就用浏览器识别，否则模拟；有 `speechSynthesis` 和中文音色就朗读（朗读不受安全上下文限制，局域网 HTTP 下也会出声），否则只做字幕动画；安全上下文且有 `getUserMedia` 就取真实电平，否则模拟电平。原型不发起任何网络请求，只读 `localStorage` 的 `crew.theme`，派活确认后只显示“假数据，未真正发送”。

尚未覆盖：真机延迟、浏览器外放时的自打断率、iOS Safari 长时间连续识别的稳定性、中文专有名词识别率。这些要等 HTTPS 或原生 App 就绪后在真机上测。
