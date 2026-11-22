# 第三方组件

「稿」本身以 GPL-3.0 发布。它依赖、调用或内置了下面这些组件，各自保留原有的许可证与版权。

## 运行时依赖（不随仓库分发，由安装脚本获取）

| 组件 | 用途 | 许可证 |
|---|---|---|
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | 取视频信息、字幕、音频 | Unlicense |
| [whisper.cpp](https://github.com/ggml-org/whisper.cpp) | 语音转写引擎 | MIT |
| [Flask](https://github.com/pallets/flask) | 本地 HTTP 服务 | BSD-3-Clause |
| [OpenAI Whisper 模型](https://huggingface.co/ggerganov/whisper.cpp) | 识别模型权重（ggml 格式） | MIT |

## 随仓库分发（`app/vendor/`）

这几个文件是直接内置的，因为 pip 装不上或版本对不上时它们必须仍然可用。

| 文件 | 来源 | 许可证 |
|---|---|---|
| `socks.py`, `sockshandler.py` | [PySocks](https://github.com/Anorov/PySocks) | BSD-3-Clause |
| `socksio/` | [socksio](https://github.com/sethmlarson/socksio) | MIT |
| `sitecustomize.py` | 本项目自带 | GPL-3.0 |

`sitecustomize.py` 是本项目自己的兼容补丁，不是第三方代码：给旧版 PyAV 的 `av.open` 套一层，只有对方确实不认识 `metadata_errors` 参数时才去掉重试。

## 字体与视觉

界面只用系统字体（宋体 / 苹方 / Helvetica），不内嵌任何字体文件，因此不涉及字体授权问题。
