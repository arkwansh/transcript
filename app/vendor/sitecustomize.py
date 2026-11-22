"""稿 — 兼容补丁。这个文件不是垃圾，别删。

有些机器上装的 PyAV（av）版本偏旧，不认识 metadata_errors 这个参数，
而 faster-whisper 每解一次音频都会传它，于是整个转写直接报
    TypeError: open() got an unexpected keyword argument 'metadata_errors'

这里给 av.open 套一层：只有对方确实不认这个参数时，才把它去掉再试一次。
PyAV 本身支持这个参数时，行为完全不变，等于没这层。

放在 app/vendor 里，由 launch.sh 通过 PYTHONPATH 挂上，
Python 启动时会自动 import 本模块（sitecustomize 是标准机制）。
"""

try:
    import av

    _original_open = av.open

    def _open(*args, **kwargs):
        try:
            return _original_open(*args, **kwargs)
        except TypeError as exc:
            if "metadata_errors" in kwargs and "metadata_errors" in str(exc):
                kwargs.pop("metadata_errors", None)
                return _original_open(*args, **kwargs)
            raise

    _open.__doc__ = getattr(_original_open, "__doc__", None)
    av.open = _open
except Exception:
    # 补丁本身绝不能拖累程序启动
    pass
