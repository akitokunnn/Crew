"use strict";
(() => {
  window.HeaderLabScenarios = window.HeaderLabScenarios || {};
  window.HeaderLabScenarios.manyTasks = [
    {
      id:"audio-clock-policy", title:"确定音频时钟漂移的补偿策略", shortTitle:"时钟漂移策略", status:"waiting",
      summary:"长时间回放中音频采样时钟与动作时钟逐渐偏离，需要确定补偿频率与允许误差。",
      acceptance:["明确重采样与动作对齐的取舍", "记录允许漂移范围及触发补偿的条件"]
    },
    {
      id:"npu-detail-priority", title:"拍板低功耗模式下的微姿态细节保留范围", shortTitle:"低功耗细节取舍", status:"waiting",
      summary:"降低 NPU 频率后，动作细节与响应速度难以同时保留，需要确定本轮验收优先级。",
      acceptance:["选定优先保留的动作维度", "形成低功耗模式的验收口径"]
    },
    {
      id:"replay-sample-approval", title:"确认多语速实机回放样本与验收顺序", shortTitle:"回放样本确认", status:"waiting",
      summary:"已整理正常语速、快速连读与停顿较多的样本，等待确认代表性和首轮回放顺序。",
      acceptance:["确认每类样本覆盖的使用情境", "确定首轮回放清单与观察重点"]
    },
    {
      id:"silent-pose-choice", title:"确定静音片段结束后的姿态回归方式", shortTitle:"静音姿态回归", status:"waiting",
      summary:"静音后可以缓慢回到中性姿态，也可以保留最后一个自然动作，需要选定更符合舱体表现的方案。",
      acceptance:["比较两种回归方式的实机片段", "确认回归时间与突发语音的衔接规则"]
    },
    {
      id:"thermal-acceptance-window", title:"确认高温持续运行时的性能降级与验收窗口", shortTitle:"高温验收窗口", status:"waiting",
      summary:"热稳定后的推理时延与冷启动不同，需要确定正式记录时段及可接受的降级范围。",
      acceptance:["确定预热时长和连续观测窗口", "确认温度、时延与丢帧的联合判定规则"]
    },
    {
      id:"npu-stream-buffer", title:"优化 NPU 流式推理的输入缓冲与帧边界", shortTitle:"流式缓冲优化", status:"running",
      summary:"对齐音频块与推理窗口，减少边界处的重复计算和动作不连续。",
      acceptance:["测量不同缓冲长度的首帧时延", "验证跨音频块的姿态输出连续性"]
    },
    {
      id:"audio-front-end-alignment", title:"对齐音频前处理参数与训练侧约定", shortTitle:"前处理对齐", status:"running",
      summary:"核对采样率、窗长与归一化方式，定位实机输入和离线输入之间的差异。",
      acceptance:["输出可复核的参数对照表", "同一段音频的前处理结果满足误差要求"]
    },
    {
      id:"pose-jitter-smoothing", title:"修正快速辅音段的微姿态抖动", shortTitle:"快速语音抖动", status:"running",
      summary:"在快速辅音与短停顿处检查姿态突变，尝试保持细节的平滑策略。",
      acceptance:["复现并标记抖动出现的片段", "比较平滑前后的连续性与额外时延"]
    },
    {
      id:"replay-timestamp-observation", title:"补齐音频输入到舱体动作输出的时间戳观测", shortTitle:"端到端时间戳", status:"running",
      summary:"让采集、前处理、推理和动作下发使用可关联的时间戳，支撑延迟定位。",
      acceptance:["一次回放可以串联各阶段时间点", "观测开销不明显干扰回放结果"]
    },
    {
      id:"quantization-pose-regression", title:"核对量化模型在轻声与气声片段中的姿态偏差", shortTitle:"量化姿态回归", status:"running",
      summary:"以浮点输出为参照，检查轻声输入在量化后是否丢失小幅动作。",
      acceptance:["给出轻声与气声样本的偏差分布", "定位超出动作容差的具体维度"]
    },
    {
      id:"long-audio-recovery", title:"验证连续长音频回放中断、设备重新连接与推理上下文恢复后的动作连续性及剩余片段衔接", shortTitle:"长回放中断恢复", status:"running",
      summary:"沿真实回放路径模拟音频暂停和设备短暂断开，核对恢复后是否重复动作或跳过片段。",
      acceptance:["覆盖音频块边界内外的中断恢复", "恢复后时间线清楚且不重复下发已播放动作"]
    },
    {
      id:"npu-memory-watermark", title:"跟踪多轮回放后的 NPU 内存水位", shortTitle:"NPU 内存水位", status:"running",
      summary:"连续运行多轮短回放，观察模型缓冲释放及内存水位是否逐轮增长。",
      acceptance:["记录每轮前后主机与 NPU 内存", "定位无法回落的分配及其持有路径"]
    },
    {
      id:"cabin-motion-limits", title:"梳理舱体各动作维度的安全幅度与速度边界", shortTitle:"动作边界梳理", status:"queued",
      summary:"整理现有舱体动作约束，为模型输出映射和实机回归提供一致的边界。",
      acceptance:["列出各维度幅度、速度与组合限制", "与动作控制接口中的现有限制逐项对照"]
    },
    {
      id:"mixed-language-corpus", title:"建立中英混合语音的回归样本集", shortTitle:"中英混合样本", status:"queued",
      summary:"收集句内切换语言、数字读法和缩写等片段，补足现有回归集的覆盖。",
      acceptance:["样本包含语言切换及特殊读法标记", "每段样本能独立复现并用于重复回放"]
    },
    {
      id:"background-noise-baseline", title:"测量风扇与舱内背景声对微姿态输出的影响", shortTitle:"舱内噪声基线", status:"queued",
      summary:"比较安静环境与典型舱内噪声，检查背景声是否触发明显的无效动作。",
      acceptance:["使用同一语音样本比较不同噪声条件", "统计静音误动作及有效语音动作变化"]
    },
    {
      id:"cold-start-timeline", title:"拆解模型加载到首段动作的冷启动时间", shortTitle:"冷启动拆解", status:"queued",
      summary:"分别记录模型读取、NPU 初始化、首块音频和首次动作下发的耗时。",
      acceptance:["区分首次启动与模型已缓存的情况", "给出各阶段耗时和主要等待来源"]
    },
    {
      id:"audio-rate-matrix", title:"覆盖不同输入采样率的重采样回归", shortTitle:"采样率回归", status:"queued",
      summary:"验证常见音频输入规格经统一重采样后，时长、响度及姿态节奏保持一致。",
      acceptance:["覆盖产品实际接受的输入规格", "比较重采样前后时长与动作对齐误差"]
    },
    {
      id:"type5-mips-comparison", title:"建立 Type5 与 MiPS 在相同音频、相同舱体动作约束和相同温度条件下的并排回放及差异复核流程", shortTitle:"新旧链路对照", status:"queued",
      summary:"固定影响结果的条件，保留新旧链路可重复的动作与时延对照，支持替换验收。",
      acceptance:["同一批样本可在两条链路重复回放", "差异记录能追溯到样本、配置与设备状态"]
    },
    {
      id:"pose-range-calibration", title:"校准小幅动作在不同舱体上的映射增益", shortTitle:"舱体增益校准", status:"queued",
      summary:"对比舱体个体差异，检查同样的模型输出是否出现幅度过小或局部饱和。",
      acceptance:["记录设备与动作维度的映射参数", "用统一样本复核校准后的动作幅度"]
    },
    {
      id:"replay-operator-guide", title:"整理实机回放的准备、记录与异常标注步骤", shortTitle:"回放操作说明", status:"queued",
      summary:"把设备准备、样本选择和现象标注串成可照着执行的回放流程。",
      acceptance:["首次使用者可依据步骤完成一轮回放", "异常记录包含定位所需的样本和时间点"]
    },
    {
      id:"stream-backpressure", title:"验证动作下发变慢时的音频背压与过期帧处理", shortTitle:"下发背压验证", status:"queued",
      summary:"模拟动作通道短时变慢，明确积压帧的保留、丢弃与恢复策略。",
      acceptance:["动作通道恢复后积压可有界消退", "过期帧处理不导致大幅动作跳变"]
    },
    {
      id:"model-io-contract", title:"整理 MiPS 模型输入输出与张量维度约定", shortTitle:"模型接口约定", status:"delivered",
      summary:"已整理音频特征输入、上下文缓存及动作输出的形状和数据类型，供适配核对。",
      acceptance:["文档与当前模型文件的实际接口一致", "附一组可复核的输入输出样例"]
    },
    {
      id:"npu-runtime-probe", title:"完成舱体 NPU 运行时与算子支持探测", shortTitle:"NPU 能力探测", status:"delivered",
      summary:"已记录设备运行时版本、模型加载结果和不支持算子的检查结果。",
      acceptance:["探测过程与目标设备信息可追溯", "加载失败项保留原始诊断信息"]
    },
    {
      id:"audio-fixture-cleanup", title:"清理回归音频的削波、空文件与重复样本", shortTitle:"音频样本清理", status:"delivered",
      summary:"已检查现有回归样本的完整性，移出无法使用的文件并记录处理原因。",
      acceptance:["有效样本都有时长、规格和内容标签", "被移出样本有明确原因及原始来源记录"]
    },
    {
      id:"pose-axis-convention", title:"统一模型姿态轴与舱体动作轴的方向约定", shortTitle:"姿态轴方向统一", status:"delivered",
      summary:"已逐维核对正负方向、单位和中性点，消除模型输出到动作接口之间的歧义。",
      acceptance:["每个姿态维度对应明确的舱体动作", "单维测试可观察到符合约定的动作方向"]
    },
    {
      id:"baseline-trace-format", title:"定义回放性能记录的统一字段", shortTitle:"性能记录格式", status:"delivered",
      summary:"已统一样本、设备、模型配置、温度和阶段耗时字段，方便横向比较。",
      acceptance:["记录可关联同一轮回放的各阶段", "缺失字段与测量失败有明确表示"]
    },
    {
      id:"type5-fallback-check", title:"核对切回 Type5 的配置与动作通路", shortTitle:"Type5 回退核对", status:"delivered",
      summary:"已在隔离回放配置中核对旧链路入口与参数，保留替换过程中的回退依据。",
      acceptance:["切换后实际使用的链路可辨认", "回退不沿用不兼容的 MiPS 上下文"]
    },
    {
      id:"silence-detection-baseline", title:"建立静音与低响度输入的识别基线", shortTitle:"静音识别基线", status:"delivered",
      summary:"已记录静音、轻声和气声的输入特征，给静音回归和误动作检查提供基线。",
      acceptance:["基线样本区分静音与有效低响度语音", "阈值与测量方式可在同一设备复现"]
    },
    {
      id:"replay-manifest-format", title:"补齐回放清单中的模型、音频与配置版本", shortTitle:"回放版本清单", status:"delivered",
      summary:"已让一次回放清单同时标识模型文件、音频样本和动作映射配置，便于结果复核。",
      acceptance:["回放结果能定位到完整输入组合", "同名文件变化不会被误认为同一版本"]
    },
    {
      id:"device-reconnect-observation", title:"完成舱体音频回放在设备短时断连、模型上下文重建和动作通道异常退出后的恢复与长时间稳定性回归验收", shortTitle:"设备恢复回归", status:"delivered",
      summary:"已整理设备断连、动作通道退出和推理上下文恢复后的回放记录，复核恢复后的动作及持续运行表现。",
      acceptance:["区分音频暂停、推理停顿与设备断连", "恢复过程与长时间回放均保留可定位的观察记录"]
    },
    {
      id:"short-utterance-baseline", title:"建立短句与单字语音的首帧动作基线", shortTitle:"短句首帧基线", status:"delivered",
      summary:"已测量短句和单字片段，检查起始动作是否因上下文不足而迟到或幅度失真。",
      acceptance:["记录输入起点到首段有效动作的时间", "基线覆盖短促输入和句首停顿"]
    },
    {
      id:"cabin-neutral-reference", title:"确认舱体中性姿态与回放前复位基准", shortTitle:"中性姿态基准", status:"delivered",
      summary:"已核对回放开始前的中性姿态与复位顺序，减少不同轮次的初始状态差异。",
      acceptance:["复位后的姿态可重复并可观察", "回放记录包含是否完成复位的状态"]
    },
    {
      id:"comparison-observation-sheet", title:"整理动作连续性、语音贴合度与延迟的观察记录表", shortTitle:"效果观察记录表", status:"delivered",
      summary:"已为回放评审准备统一记录方式，让主观现象能对应到样本和时间点。",
      acceptance:["每条观察能定位到具体回放片段", "连续性、贴合度和延迟分别记录"]
    }
  ];
})();
