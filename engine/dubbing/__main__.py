import json
import os
import sys
import traceback
from pathlib import Path

from .jobs import exclusive
from .pipeline import Cancelled, Engine, describe


def main():
    # Reserve stdout for JSON Lines even if an ML library writes native diagnostics.
    stream = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdin.reconfigure(encoding="utf-8-sig")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    def emit(event):
        stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")

    cancel_path = None
    pause_path = None
    try:
        request = json.load(sys.stdin)
        data_dir = Path(request["data_dir"])
        if not data_dir.is_absolute():
            raise ValueError("Thiếu đường dẫn tuyệt đối tới dữ liệu ứng dụng.")
        data_dir = data_dir.resolve()
        if request.get("cancel_path"):
            marker = Path(request["cancel_path"])
            if marker.is_absolute() and marker.parent.resolve() == data_dir / "cancel":
                cancel_path = marker
        if request.get("pause_path"):
            marker = Path(request["pause_path"])
            if marker.is_absolute() and marker.parent.resolve() == data_dir / "cancel" and marker.suffix == ".pause":
                pause_path = marker
            else:
                raise ValueError("Đường dẫn yêu cầu tạm dừng không hợp lệ.")
        request["pause_path"] = str(pause_path) if pause_path else None
        with exclusive(data_dir):
            engine = Engine(data_dir, request.get("settings") or {}, cancel_path, emit)
            engine.pause_path = pause_path
            result = engine.dispatch(request)
            emit({"type": "result", "data": result})
        return 0
    except Exception as error:
        if not isinstance(error, Cancelled):
            traceback.print_exc(file=sys.stderr)
        emit({"type": "error", "message": describe(error)})
        return 130 if isinstance(error, Cancelled) else 1
    finally:
        if cancel_path:
            cancel_path.unlink(missing_ok=True)
        if pause_path:
            pause_path.unlink(missing_ok=True)
        stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
