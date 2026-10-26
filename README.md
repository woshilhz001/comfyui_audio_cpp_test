# ComfyUI AudioCpp Server Nodes

连接本地 [audio.cpp](https://github.com/0xShug0/audio.cpp) `audiocpp_server`
HTTP 服务的 ComfyUI 自定义节点包。

## 设计理念

- **按任务拆分节点,模型驱动参数**:每个任务(TTS / ASR / 音频任务 / 卸载 / 状态)
  一个节点。任务节点内「模型」下拉用 `DynamicCombo`——选中模型族后**只显示该模型
  真正需要的参数**,杜绝"参数一大堆却分不清哪个有用"。
- **参数类型严格对齐 audio.cpp API**(源码 `model_specs/*.json` 提取):
  - 字符串型(voice、reference_text、instructions、lyrics)→ `STRING`
  - 数值型(seed、max_tokens、top_k、num_inference_steps…)→ `INT` / `FLOAT`
  - 枚举型(template_name、text_chunk_mode、route…)→ `COMBO`
  - 开关型(do_sample、voice_anonymization…)→ `BOOLEAN`
  - 服务器端文件路径(voice_ref)→ `STRING`(路径)
- **输出带错误判断与完整信息**:每个执行节点失败时抛出 `AudioCppError`,
  附带服务器原始错误消息;成功时除主输出外还有诊断输出(完整结果 JSON,
  含 `timing` / `words` / `named_audio_outputs` / `speaker_turns` 等)。
- **危险操作有确认开关**:卸载模型(释放显存/内存)是破坏性操作,
  默认不执行,必须勾选「确认执行卸载」开关。

## 前置要求

- 运行中的 `audiocpp_server`。用仓库脚本一键启动:

  ```
  D:\ai\llama\audio.cpp\启动audio.cpp服务器_ComfyUI.bat
  ```

  该脚本以 `--config server.json --ui` 启动,绑定 `127.0.0.1:8085`,
  与所有节点的默认 host/port 一致。`--ui` 是**音频任务节点
  (AudioCppAudioTask)上传音频所必需**的(经 `/v1/ui/upload`)。

- 新版 ComfyUI(带 `comfy_api.latest` 扩展 API)。
- Python 依赖:`requests`、`soundfile`、`numpy`、`torch`
  (ComfyUI 便携版自带,无需额外安装)。

## 停止任务按钮与生成后卸载

- **停止任务按钮(ComfyUI "删除/Cancel")**:三个生成类节点(TTS / 语音转文本 /
  音频任务)的 `execute` 都是可中断的 —— 按下停止后,节点立即响应 `InterruptProcessingException`(ComfyUI 标准取消,而不是报错),audio.cpp 请求不再等待。
  注意:已发出的 GPU 推理无法被中途取消(模型会在服务器上跑完),但节点不再
  返回结果,且会在停止时按下面的卸载开关释放显存。
- **生成后卸载开关 `unload_after_execute`**(每个生成节点都有,默认关闭):
  勾选后,该节点生成完成(或用户停止)时自动调用 `/v1/tasks/unload_all_models`,
  释放 audio.cpp 模型占用的显存/内存。适合"生成完就让出显存"的场景;
  不勾选则模型保留在服务器内存中,供后续多次调用快速复用。

## 节点(Add Node 菜单 → audio.cpp)

### AudioCpp 文本转语音 (AudioCppTextToSpeech)

调用 `POST /v1/audio/speech`。输出原生 `AUDIO`
(`{'waveform': [B,C,T] float32, 'sample_rate': int}`)。

| 输入 | 类型 | 说明 |
| --- | --- | --- |
| `text` | 文本 | 要合成的文本。 |
| `model` | 下拉 | 模型族;选择后下方只显示该模型真实需要的参数。 |
| `voice_ref` | **音频(AUDIO)** | 参考/克隆音频,连接上游音频节点即可(自动上传到服务器)。 |
| `reference_text` | 文本 | 参考音频对应的转写。 |
| `instructions` | 文本 | 音色设计描述(VoiceDesign)。 |
| `voice` | 文本 | 内置音色 id(如 Vivian)。 |
| `language` | **下拉** | 语言代码(数据源:audio.cpp 各家族 languages)。 |
| `seed` | 整数 | 随机种子;-1 由服务器决定。 |

Qwen3 系列用法:
- `qwen3_tts`(VoiceDesign):填 `instructions`;
- `qwen3_tts_base`(克隆):接 `voice_ref` + `reference_text`;
- `qwen3_tts_customvoice`:填 `voice`(如 Vivian)。

### AudioCpp 语音转文本 (AudioCppSpeechToText)

调用 `POST /v1/audio/transcriptions`(multipart)。输入 `AUDIO`,`language`
为下拉(auto / 具体语言),`prompt` 可选。输出 3 项:
转写文本(STRING)、语言(STRING)、完整结果 JSON(STRING,含 timing)。
模型下拉自动发现 spec 家族(qwen3_asr / sense_asr / moonshine_asr /
canary_asr / cohere_asr / confucius4_r2t2 / moss_transcribe_diarize 等)。

### AudioCpp 音频任务 (AudioCppAudioTask)

调用通用任务端点 `POST /v1/tasks/run`,覆盖非 TTS 任务:

| 模型族 | 任务 | 说明 |
| --- | --- | --- |
| `yue2` | 音乐生成(YuE2) | 歌词 + 风格 + 可选 ABC 乐谱规划(coT);必备「text(歌词)」,style 提升到顶层 |
| `heartmula` | 音乐生成(HeartMuLa) | 歌词驱动生成,可开 infinite_mode 生成长音频 |
| `minimax_music3` | 音乐生成 | 歌词 + 时长 |
| `midashenglm_gen` | 音乐生成(MiDaShengLM) | 时长/引导强度/停止阈值 |
| `sheetsage2` | 乐谱转写 | 输入音频 → ABC 乐谱文本(输出 text/artifacts) |
| `muscriptor_small` | MIDI 生成 | 输入音频 → MIDI/JSON 乐谱 |
| `seed_vc` | 声音转换(VC) | 源音频 + 参考音频(目标声音,接 voice_ref);`route` 选项:`v2_vc`(声音转换,默认)/ `v1_svc`(唱歌)/ `v1_whisper_bigvgan_vc` / `v1_xlsr_hift_vc`;Seed-VC **不需要参考文本** |
| `rvc` | 声音转换(RVC) | 内置音色或自备 `.pth` 检查点,支持半音移调/检索/消音保护 |
| `meanvc2` | 声音转换(MeanVC2) | 轻量转换,多说话人 id |
| `ace_step` | 音频编辑 / AI 歌曲生成 | 源音频 + 提示词 |
| `sortformer_diar` | 说话人分离 | 检测说话人个数与区间(输出 speaker_turns) |
| `mel_band_roformer` | 音源分离 | 人声/伴奏 |
| `audiosr` | 音频超分辨率 | 建议接 16 kHz 输入,输出高分辨率版本 |
| `universr` | 通用音频超分辨率 | 单模型多采样率(8k~44.1k),输入采样率可配置 |
| `controlfoley` | 音效生成 | 文本提示 + 可选视频条件 |
| `liveavatar` | 人像动画生成 | 参考人像图片 + 提示词 → 动态视频 |
| `pulsevad` | 语音活动检测 | 检测语音/静音区间(阈值/最短时长可调) |
| `apollo` | 流式音频处理 | 分块流式处理(分块时长/重叠可调) |

- 输入音频与参考音频(`voice_ref`)均为 **AUDIO 连接**,自动上传;
  `target_sample_rate` 可在输入采样率与模型要求不一致时重采样
  (如 sortformer_diar 需 16000)。
- 音乐家族(yue2 / heartmula / minimax_music3)的**歌词/风格提升到顶层
  `text` / `style` 输入**,执行时自动回填进 options —— 与 audio.cpp 契约一致
  (options.lyrics 优先于 text_input)。契约定义见 `model_contracts.py`。
- 输出:主音频(AUDIO,可选)、文本(STRING,MIDI/ABC 乐谱等)、完整结果 JSON(STRING)。

### AudioCpp 卸载模型(释放显存) (AudioCppUnloadModels)

**危险操作节点**。调用 `POST /v1/tasks/unload_all_models` / `unload_models`。

- `trigger`(可选输入):连接任意上游节点即随图执行,使节点像普通 ComfyUI
  节点一样「有输入、有输出、可执行」。
- `mode`:`unload_all` / `unload_selected` / `check_only`。
- `confirm_unload`(简单勾选):**未勾选时任何模式都只报告不卸载**
  (不打断工作流,报告会注明「未勾选确认」);勾选后 `mode` 才真正生效。

### AudioCpp 服务器信息 (AudioCppServerInfo)

只读诊断:/health 状态、后端类型、配置模型数、当前已加载模型清单。

## 代码结构

```
comfyui_audio.cpp_test/
├── __init__.py            # ComfyExtension 入口,注册 5 个节点
├── config.py              # 服务器地址/端口/超时集中配置
├── audio_cpp_client.py    # HTTP 客户端(不依赖 ComfyUI,可独立测试)
├── audio_codec.py         # WAV ↔ AUDIO 互转 + 重采样(不依赖 ComfyUI)
├── interruptible.py       # 可中断请求助手:响应 ComfyUI 停止按钮 + 中断自动卸载
├── model_profiles.py      # 模型族参数档案:DynamicCombo 的数据来源
├── model_contracts.py     # 模型请求契约:端点/必需输入/顶层提升/隐藏字段
└── nodes/
    ├── __init__.py
    ├── base.py            # 共享输入模板
    ├── tts.py             # AudioCppTextToSpeech
    ├── asr.py             # AudioCppSpeechToText
    ├── task.py            # AudioCppAudioTask
    ├── unload.py          # AudioCppUnloadModels
    └── info.py            # AudioCppServerInfo
```

**扩展新模型**:在 `model_profiles.py` 的对应档案(或新增档案类)
追加模型族与参数定义,节点即自动获得该模型的下拉选项——无需改节点代码。
需要特殊输入契约(必需音频/歌词、style/lyrics 提升到顶层)的家族,
在 `model_contracts.py` 的 `TASK_INPUT_REQS` / `TASK_LIFTS` 补一行即可。

## 新模型自动适配(面向未来的扩展接口)

audio.cpp 与 GitHub 上游持续新增模型(如 `yue2` 音乐生成、`sheetsage2` MIDI、
`rvc` 声音转换、`heartmula` 音乐等)。本节点包的适配层原理:

1. **数据源 = audio.cpp 自带 `model_specs/*.json`**。每个 spec 声明了家族的
   `tasks`(任务类型)与 `options.request`(参数名、类型、默认值、描述)。
2. **运行时自动发现**(`model_profiles.py` 的 `ModelSpecLoader`):
   - 启动时扫描 `config.py` 中 `MODEL_SPEC_DIRS` 列出的目录
     (源码目录 `audio.cpp/audio.cpp/model_specs` 优先,其次是程序目录);
   - 新家族只要带 spec,就**自动出现在 TTS / ASR / 音频任务节点的下拉里**;
   - 参数类型按 audio.cpp API 精确映射:
     `string/enum → STRING/COMBO`、`int → INT`、`float → FLOAT`、
     `bool → BOOLEAN`、`path → STRING(服务器路径)`;
   - 手工档案里已有同族模型时(如 muscriptor_small ← spec family muscriptor),
     自动发现的 family 版会被跳过,避免同一模型在下拉出现两份。
3. **手工档案覆盖**:`model_profiles.py` 里的 `TTS_PROFILES` / `TASK_PROFILES`
   优先于自动发现,用于补充中文提示、合理默认值,以及 spec 参数缺失的
   旧格式家族(如 qwen3_tts 系列内嵌于 GGUF)。
4. **请求契约层**(`model_contracts.py`):与「下拉长什么样」分离,
   `TASK_INPUT_REQS` 声明必需输入(audio / voice_ref / text)、
   `TASK_LIFTS` 声明哪些参数提升到节点顶层并回填进 options(如 yue2 的
   style/lyrics)。新增家族通常无需改动;特殊家族在契约表补一行即可。
5. **扩展零成本**:audio.cpp 升级新增模型后,只需重启 ComfyUI(或重新加载
   扩展),新模型即自动可用——**无需修改任何节点代码**。

### 新增模型的手动作业(仅在需要时)

若新家族需要中文参数名/默认值/特殊处理,在 `model_profiles.py` 对应档案追加:

```python
TASK_PROFILES["mi_model"] = (
    "task",            # 任务: tts / asr / vc / diar / sep / gen / midi ...
    "我的新模型",      # 下拉里的中文名
    {"参数名": ("类型", "中文说明", 默认值)},
)
```

无需触碰节点文件。

## audio.cpp server.json 说明

`D:\ai\llama\audio.cpp\server.json` 已优化:
- 为 cosyvoice3、seed_vc、sortformer_diar、minimax_music3、muscriptor 等
  补充了 `default_request_options`(服务器级默认参数,请求未指定时生效),
  让节点参数更简洁;
- **`task` 字段是模型能否工作的关键**:
  - `qwen3_tts` 是 **VoiceDesign 模型**,必须配置 `task: "vdes"`
    (源码只接受 VoiceDesign task;配置成 `tts` 会报
    "Qwen3 voice design model only supports the VoiceDesign task");
  - `qwen3_tts_base` / `qwen3_tts_customvoice` 用 `task: "tts"`;
- **新模型(YuE2 / SheetSage2 / RVC / HeartMuLa / AudioSR 等)接入**:
  把它们写进 `server.json` 的 `models` 数组(`id` / `family` / `path` /
  `task` 四字段,`task` 取 spec 里的任务类别,如 `music` / `midi` / `vc` /
  `s2s` / `sfx`),再重启服务器;节点下拉会自动发现并显示——无需改节点包。
- 其余字段维持官方格式(`load_options` / `session_options` /
  `lazy_load` / `busy_timeout_ms` 等),服务器可直接解析。