# AI Video Dubbing

Ứng dụng Windows x64 để dịch và lồng tiếng video sang tiếng Việt bằng model chạy trên máy. Mỗi video có một thư mục dự án chứa lời gốc, bản dịch, audio từng câu, âm thanh tổng, nhật ký và các bản xuất. Chạy tự động hoặc xem/sửa từng bước. FFmpeg sao chép luồng hình chính bằng `-c:v copy`, ghép AAC tiếng Việt và thử MKV khi cần giữ codec nguồn. Video gốc được giữ nguyên.

## Cài đặt từ repo trên máy Windows mới

Máy cần **Windows 10/11 x64**, Windows PowerShell 5.1 có sẵn trong Windows, Internet và dung lượng trống cho toolchain, thư viện AI và model. Lần đầu tải nhiều GB nên có thể mất thời gian. Chọn một thư mục trên ổ đĩa cục bộ; giữ toàn bộ mã nguồn trong thư mục đó. Không cần cài trước Node.js, Python, Rust hay Visual Studio.

Chạy các lệnh dưới đây từ PowerShell thông thường, bằng quyền tài khoản của bạn. Nếu máy thiếu WebView2, trình cài Microsoft được gọi ở chế độ người dùng; Microsoft Edge Updater có thể quản lý runtime ở cấp máy theo [cơ chế phân phối WebView2](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution).

Clone repo bằng Git, hoặc tải ZIP từ trang repo rồi giải nén:

```powershell
git clone https://github.com/hoangson27/ai-video-dubbing.git
cd ai-video-dubbing
.\setup.cmd
.\dev.cmd
```

Nếu tải ZIP, mở thư mục vừa giải nén bằng VS Code rồi chạy hai lệnh `setup.cmd` và `dev.cmd` ở terminal PowerShell trong thư mục có `package.json`. Đường dẫn thư mục có khoảng trắng vẫn được hỗ trợ. Không cần kích hoạt venv hoặc nhập đường dẫn công cụ bằng tay.

`setup.cmd` chuẩn bị Node/npm, Python portable, Rust/MSVC/Windows SDK cục bộ, cài các package frontend và thư viện AI, rồi tải Whisper `small`, Qwen `qwen2.5:3b` và VieNeu-TTS mặc định. Script kiểm tra phiên bản, hash hoặc chữ ký của các gói tải trước khi dùng; SDK được giải nén tại project, không chạy bộ cài MSI của SDK. Nếu Windows đã có WebView2, setup dùng runtime đó; nếu thiếu, setup tải trình cài Microsoft có chữ ký và cài WebView2 cho tài khoản hiện tại. WebView2 là thành phần Windows dùng chung, được xử lý riêng với các công cụ cục bộ của project.

Khi thiết lập hoàn tất, `dev.cmd` tự chạy Vite và mở cửa sổ Tauri. Mỗi lần dùng sau chỉ cần:

```powershell
.\dev.cmd
```

Có thể dùng `npm run setup` và `npm run desktop` sau khi Node/npm đã có trên PATH; các file `.cmd` là lối vào phù hợp cho máy mới chưa có npm. `npm run dev` chỉ chạy Vite, còn `dev.cmd` chạy đầy đủ ứng dụng desktop.

### Chỉ cài công cụ trước, tải AI sau

Nếu muốn mở giao diện trước khi tải model:

```powershell
.\setup.cmd -SkipAi
.\dev.cmd
```

Vào **Hệ thống → Thiết lập tự động** để cài thư viện AI/model còn thiếu. Trong **Cài đặt**, có thể chọn Qwen 3B hoặc 7B trước khi bấm Setup; cấu hình mới mặc định 3B. AUTO dùng CPU cho các bước hỗ trợ khi máy chưa có CUDA; việc cài driver/CUDA để dùng GPU toàn pipeline là bước riêng.

### Kiểm tra hoặc chạy lại thiết lập

```powershell
.\setup.cmd -CheckOnly
```

Lệnh này chỉ kiểm tra công cụ build, Node/Python, package frontend và WebView2; không tải/cài thêm thành phần hoặc khởi động Ollama. Kiểm tra AI/model đầy đủ ở **Hệ thống → Kiểm tra lại** trong ứng dụng. Khi mất mạng hoặc tải dở, đóng ứng dụng, chờ tác vụ đang xử lý hoàn tất rồi chạy lại `setup.cmd`; các gói hợp lệ đã có được dùng lại. Nếu chỉ cần chuẩn bị công cụ frontend/native, chạy lại với `-SkipAi`.

Nếu Windows Application Control hoặc chính sách của tổ chức chặn chương trình vừa tải/biên dịch, giữ thông báo lỗi để kiểm tra với người quản trị máy; script không đổi hoặc vượt qua chính sách bảo vệ. Nếu setup báo cổng Ollama dev 11435 đang bận, đóng phiên ứng dụng/thiết lập khác trước khi chạy lại.

**Fresh-clone verification:** đã dựng Python/Node/Rust/MSVC/SDK từ các archive chính thức đã xác minh trong bản sao chỉ chứa mã nguồn, không chép runtime/toolchain hay `node_modules` đã cài. `setup.cmd -SkipAi`, kiểm tra đường dẫn, 25 test giao diện, build frontend và build Tauri debug đều đạt khi PATH ban đầu chỉ có công cụ Windows. Lượt này dùng WebView2 có sẵn và kiểm thử luồng setup AI bằng fixture; chưa tải lại toàn bộ model hay nghiệm thu trên máy/VM Windows mới. Xem [kết quả kiểm thử](docs/testing.md). Bộ cài phát hành được xử lý sau.

## Test trên VS Code

Mở thư mục repo trong VS Code, hoàn tất `setup.cmd`, rồi chạy `dev.cmd` trong terminal. Hot reload cập nhật giao diện Vue/TypeScript/CSS khi lưu file; Tauri tự biên dịch lại khi mã Rust thay đổi. Python nạp mã mới ở lượt xử lý tiếp theo. Dùng `.\dev.cmd -CheckOnly` để xem các đường dẫn launcher đang dùng.

Bản debug luôn dùng `.dev` trong thư mục mã nguồn, kể cả khi gọi Tauri trực tiếp. Các đường dẫn công cụ và dữ liệu app quản lý trong dev được giới hạn trong project.

### Dữ liệu và dependency trong dev

Thư mục dữ liệu ứng dụng là `<repo>/.dev`:

| Đường dẫn trong `.dev` | Nội dung |
| --- | --- |
| `runtime/python311-nuget`, `venv` | Python 3.11.9 portable và thư viện AI |
| `tools/node`, `tools/ffmpeg/bin`, `tools/ollama` | Node/npm, FFmpeg/FFprobe và Ollama portable |
| `models/whisper`, `models/vieneu`, `models/ollama` | Model nhận dạng, giọng đọc và dịch |
| `projects`, `output`, `jobs` | Dự án video, kết quả và checkpoint cũ |
| `downloads`, `cache`, `temp`, `logs` | Gói tải, cache, tệp tạm và nhật ký |
| `profile`, `webview` | Hồ sơ của tiến trình phụ và dữ liệu WebView của app |

Ollama dev chỉ dùng dịch vụ do app quản lý tại `127.0.0.1:11435` và model trong `.dev/models/ollama`. Không dùng lại daemon ở cổng 11434 hoặc công cụ Python/FFmpeg/Ollama từ PATH/AppData. Có thể chọn video nguồn ngoài project; các tệp app tạo được lưu trong `.dev`. Muốn đăng ký một thư mục dự án có sẵn khi dev, sao chép thư mục đó vào `.dev/projects` trước.

**Lịch sử kiểm thử tại workspace phát triển:** môi trường `.dev` đã đạt **18 đã cài, 0 còn thiếu**; hai clip mẫu Anh/Trung đã lồng tiếng và xuất video thành công, giữ nguyên nguồn và payload hình. Kiểm tra Whisper phát hiện được model tải thiếu/hỏng và Setup có thể tải lại an toàn. Đây là kết quả trên môi trường đã chuẩn bị; nghiệm thu bootstrap từ bản clone mới được ghi riêng ở đầu README. Xem [kết quả kiểm thử và bằng chứng](docs/testing.md).

Rust/MSVC/Windows SDK và cache Cargo được tạo trong `<repo>/.verification/toolchain`; sản phẩm biên dịch nằm trong `<repo>/.verification/target`. Source bootstrap và metadata phiên bản/hash nằm trong `scripts/toolchain/`, nên người clone repo có thể dựng lại toolchain. `.dev`, `.verification`, `node_modules` và các file build không được đưa vào Git. Bộ cài phát hành được để lại cho giai đoạn sau.

## Thiết lập trong ứng dụng

1. Mở ứng dụng bằng `dev.cmd` sau khi hoàn tất `setup.cmd`. Bộ cài NSIS chưa được nghiệm thu.
2. Nếu đã chạy setup đầy đủ với model mặc định, kiểm tra môi trường sẽ báo các thành phần sẵn sàng. Nếu dùng `-SkipAi` hoặc đổi model, vào **Hệ thống** và bấm **Thiết lập tự động**. Ứng dụng kiểm tra các thành phần trong `.dev`, chuẩn bị Python 3.11 portable nếu cần, FFmpeg/FFprobe, Ollama portable, thư viện AI và các model đang chọn.
3. Chờ quá trình thiết lập hoàn tất. Màn hình hiển thị tiến độ và nhật ký. Nếu mất mạng hoặc dừng giữa chừng, bấm **Tiếp tục thiết lập**; các thành phần hợp lệ đã có được dùng lại. Không cần nhập lệnh hoặc tự điền đường dẫn.
4. Khi hiện **Các thành phần bắt buộc đã sẵn sàng**, bấm **Về bàn dựng**, chọn video và bắt đầu xử lý.

Lần thiết lập đầu cần Internet và vài GB dung lượng trống. Python dùng gói NuGet portable, không chạy Windows Installer; thư viện nằm trong venv riêng. FFmpeg và Ollama dev được giải nén vào `.dev/tools`. Các gói portable được kiểm tra SHA-256 và chữ ký của executable tương ứng trước khi dùng. Dịch vụ Ollama giới hạn ở loopback và tắt cloud.

Màn hình kiểm tra luôn liệt kê phần đã có, còn thiếu và chưa thể kiểm tra, kể cả khi Python Engine chưa khởi động được. CPU/RAM/GPU được đọc độc lập với Python. Các ô đường dẫn trong **Cài đặt** dành cho người muốn cấu hình riêng; thiết lập thông thường tự lưu các đường dẫn cần dùng.

Nếu thiết lập thất bại, **Chi tiết thiết lập** tự mở và giữ lỗi gốc. Nhật ký dev đầy đủ nằm ở `.dev/logs/setup-latest.log`; giao diện hiện đường dẫn và nút sao chép. Đóng/mở ứng dụng vẫn đọc lại được log gần nhất.

### Thiết lập thủ công dành cho người phát triển

Các script dưới đây mặc định dùng `.dev` của checkout. Dùng nút thiết lập trong ứng dụng để cài cả runtime còn thiếu; `setup-deps.ps1` dành cho môi trường đã có Python portable trong `.dev`.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup-deps.ps1 -DownloadModels
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check-deps.ps1
```

`setup-deps.ps1` tạo venv riêng, cài thư viện vào venv và tải model khi có `-DownloadModels`. Các đường dẫn công cụ trong dev luôn được ghim vào `.dev`; không tìm Python qua `py` hoặc FFmpeg/Ollama qua PATH. Đóng app trước khi chạy kiểm tra/thiết lập độc lập: script tự khởi động rồi dừng Ollama tại cổng 11435 và báo lỗi nếu cổng đang bị tiến trình khác chiếm. `-ReleaseProfile` dành cho hồ sơ phát hành cũ, không dùng trong lượt test dev này.

Khi chọn model khác, chạy lại setup với `-WhisperModel` hoặc `-OllamaModel` rồi chọn cùng tên trong ứng dụng. Mặc định mới là Whisper `small` và Qwen `qwen2.5:3b`; 7B vẫn có thể chọn để so sánh chất lượng và thời gian trên cùng video. [faster-whisper](https://github.com/SYSTRAN/faster-whisper) · [Qwen 3B](https://ollama.com/library/qwen2.5:3b) · [Qwen 7B](https://ollama.com/library/qwen2.5:7b)

Trong **Kiểm tra hệ thống**, chọn **Kiểm tra lại**. **Bắt đầu xử lý** khả dụng khi môi trường sẵn sàng, video có audio và đã chọn một giọng. `check-deps.ps1 -Json` xuất báo cáo JSON và trả mã `1` nếu môi trường chưa sẵn sàng. Node.js, npm, Rust và MSVC dùng để phát triển hoặc build.

### Chế độ phần cứng

| Chế độ | Whisper | Ollama | VieNeu-TTS |
| --- | --- | --- | --- |
| AUTO | CUDA khi đủ runtime, CPU khi CUDA không khả dụng | Ollama tự chọn CPU hoặc GPU | PyTorch/CUDA khi đã cài đủ runtime và weights, hoặc ONNX/CPU |
| CPU | CTranslate2 INT8 | Yêu cầu `num_gpu=0` | ONNX FP32 |
| GPU | CUDA FP16, yêu cầu NVIDIA tương thích | Kiểm tra model dùng VRAM trong `/api/ps` | Yêu cầu NVIDIA và PyTorch CUDA |

Để bật GPU cho toàn pipeline, chuẩn bị driver NVIDIA và CUDA 12/cuBLAS/cuDNN 9 cho CTranslate2. Chạy `setup-deps.ps1 -Gpu -DownloadModels` để cài thêm PyTorch CUDA và tải weights tương ứng. Ollama có thể hỗ trợ GPU AMD theo runtime riêng của nó. Chế độ GPU toàn pipeline vẫn cần CUDA cho Whisper và VieNeu-TTS. Lỗi hết VRAM được hiển thị để người dùng có thể đổi model hoặc chọn CPU. [faster-whisper GPU requirements](https://github.com/SYSTRAN/faster-whisper#gpu) · [VieNeu-TTS 3.8.3](https://pypi.org/project/vieneu/3.8.3/) · [Ollama Windows](https://docs.ollama.com/windows)

### Phiên bản và model

| Thành phần | Cấu hình mặc định | Nguồn |
| --- | --- | --- |
| Nhận dạng | `faster-whisper==1.2.1`, CTranslate2 `4.8.5`, PyAV `18.1.0`, Whisper `small` đa ngôn ngữ | [faster-whisper](https://github.com/SYSTRAN/faster-whisper) · [CTranslate2](https://github.com/OpenNMT/CTranslate2) · [PyAV](https://github.com/PyAV-Org/PyAV/releases/tag/v18.1.0) |
| Dịch | Qwen 2.5 3B hoặc 7B, JSON schema qua Ollama loopback | [3B và giấy phép Qwen](https://ollama.com/library/qwen2.5:3b) · [7B Apache 2.0](https://ollama.com/library/qwen2.5:7b) · [Structured outputs](https://docs.ollama.com/capabilities/structured-outputs) |
| Giọng đọc | SDK `vieneu==3.8.3`, VieNeu-TTS v3 Turbo 48 kHz với revision model cố định | [SDK Apache 2.0](https://github.com/pnnbao97/VieNeu-TTS) · [Weights Apache 2.0](https://huggingface.co/pnnbao-ump/VieNeu-TTS-v3-Turbo) |
| Codec TTS | OpenMOSS Nano ONNX cho CPU, OpenMOSS Nano cho CUDA, revision cố định | [ONNX và giấy phép](https://huggingface.co/OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX) · [PyTorch và giấy phép](https://huggingface.co/OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano) |

Giọng hiển thị lấy từ preset của VieNeu-TTS. Engine dùng model local và bật chế độ offline của Hugging Face. Các lượt tải package và model chỉ diễn ra khi người dùng bấm nút thiết lập hoặc chạy script thiết lập; build có thể tải dependency phát triển. Ollama nhận bản dịch qua loopback.

## Sử dụng

1. Chọn hoặc thả video ở **Bàn dựng**. App tạo hoặc mở lại thư mục dự án. Mặc định tham chiếu video gốc; bật sao chép nguồn nếu muốn giữ nguyên bộ trong dự án. Chọn track, ngôn ngữ, giọng, tốc độ, thuật ngữ và văn phong rồi lưu thiết lập.
2. Trong **Dự án**, chọn **Tự động** để chạy xuyên suốt hoặc **Từng bước** để chạy/xem kết quả riêng. Có thể dừng sau một bước; nút **Tạm dừng sau nhóm hiện tại** lưu kết quả trước khi trả quyền chỉnh sửa.
3. Nhận dạng có lời gốc/mốc thời gian để sửa; dịch hiển thị từng nhóm đã lưu; tạo giọng có WAV từng câu; đồng bộ có audio tổng và cảnh báo. Có thể nhập SRT/JSON hoặc WAV thay thế qua hộp thoại. Những bước phụ thuộc được đánh dấu cần cập nhật.
4. Chỉnh một câu dịch rồi tạo lại giọng và xuất lại: những WAV còn hợp lệ được tái sử dụng. Các bản xuất lưu trong `07-output` của dự án với phiên bản riêng. Khi đổi model hoặc ngữ cảnh dịch, bản sửa tay được giữ và các câu tự động liên quan cần cập nhật.
5. Trong **Dự án → Dự án đã lưu**, bấm **Xóa** cạnh dự án cần bỏ, kiểm tra tên/thư mục rồi xác nhận **Đưa vào Thùng rác**. Thư mục dự án cùng dữ liệu và bản xuất bên trong được đưa vào Thùng rác; video nguồn tham chiếu bên ngoài và tài nguyên dùng chung được giữ nguyên. Có thể hủy bằng **Giữ dự án**. Xóa một dự án khác không làm mất bản sửa của dự án đang mở. Mở thư mục/output, gỡ khỏi danh sách hoặc dọn tệp tạm trong phần **Thư mục và dữ liệu dự án**. Dùng **Mở thư mục dự án** để đăng ký lại thư mục đã di chuyển; chọn lại nguồn chỉ chấp nhận video có SHA-256 khớp.
6. Công việc cũ có nút **Chuyển thành dự án**: sao chép checkpoint/audio/bản xuất vào cấu trúc mới, giữ nguyên dữ liệu cũ. Bật đối chiếu packet hash khi cần kiểm tra luồng hình. [FFmpeg stream copy](https://ffmpeg.org/ffmpeg.html#Streamcopy) · [FFprobe packet hashes](https://ffmpeg.org/ffprobe.html)

MVP giữ luồng hình chính, dùng track audio người dùng chọn (hoặc mặc định/track đầu tiên), và thay toàn bộ audio bằng giọng đọc tiếng Việt. Mốc thời gian tính từ đầu luồng hình. Engine dành một khoảng riêng cho từng đoạn, chèn khoảng lặng và giới hạn biên độ trước khi ghép. WAV PCM 48 kHz có giới hạn kích thước xấp xỉ 4 GiB. Câu quá dài vẫn cần rà soát cảnh báo đồng bộ và sửa lại; kiểm tra định dạng tự động không chứng minh bản dịch đúng nghĩa.

## Phát triển và build

Luồng chuẩn là `setup.cmd` rồi `dev.cmd` như phần đầu README. Bootstrap trong `scripts/toolchain/` dựng Rust/MSVC/SDK từ metadata nguồn chính thức có phiên bản/hash cố định vào `.verification/toolchain`. Launcher dùng Node 22.22.3 tại `.dev/tools/node` và nạp môi trường từ `scripts/toolchain/env.ps1`. Native debug ghim hồ sơ `.dev` ở backend.

Tauri tự quản lý frontend khi chạy dev. Chỉ chạy Vite riêng khi sửa phần web và không cần các thao tác native. Build bộ cài và nghiệm thu trên máy khác được hoãn đến sau giai đoạn test dev; script `scripts/build-windows.ps1` hiện có nhưng chưa được nghiệm thu cho cấu hình mới.

Lượt nâng cấp dự án theo thư mục ngày 11/11/2026 đã build được native debug và chạy 14 bài Rust đạt. UI được kiểm tra bằng bridge giả lập; lượt mở cửa sổ Tauri thật với báo cáo hệ thống thuộc lần nghiệm thu trước đó. Các lượt build cũ từng bị Windows Application Control chặn với `os error 4551`; bước build debug hiện đã chạy được. Chưa xác minh bộ cài NSIS trên máy Windows khác. `src-tauri/Cargo.lock` cố định các phiên bản Rust dependency. Xem [kết quả kiểm thử và giới hạn](docs/testing.md).

## Cấu trúc và dữ liệu

| Đường dẫn | Trách nhiệm |
| --- | --- |
| `src/` | Giao diện Vue Composition API, Tailwind, cài đặt, lịch sử, editor |
| `src-tauri/` | Hộp thoại, quyền đọc media theo tệp, quản lý cây tiến trình Windows, IPC và NSIS |
| `engine/dubbing/` | CLI JSON Lines, FFprobe/FFmpeg, provider AI, timeline, kiểm tra hệ thống và checkpoint |
| `engine/tests/` | Unit test, kiểm thử FFmpeg thật và bộ kiểm thử AI chạy theo yêu cầu |
| `scripts/` | Kiểm tra độc lập Python, cài dependency từ nút thiết lập, kiểm thử PowerShell và build Windows |

Tauri chạy Python bằng mảng đối số, gửi một JSON qua stdin và chuyển sự kiện JSON Lines trên stdout thành progress/log. Khi hủy, engine nhận dấu hủy ở checkpoint. Nếu đang kẹt trong native inference, Windows Job Object dừng cả cây tiến trình. Lần mở tiếp theo phân biệt tác vụ bị hủy với tiến trình lỗi và dọn tệp xuất tạm.

Dự án dev mới nằm trong `.dev/projects/<tên-video>-<id-ngắn>`. Manifest `project.json` dùng đường dẫn tương đối cho tệp thuộc dự án; `01-source` đến `07-output` chứa dữ liệu từng bước và `logs` chứa nhật ký. WAV đã dùng được sao chép độc lập vào dự án nên dọn cache không làm mất kết quả. `.dev` giữ chỉ mục dự án, cache/tài nguyên dùng chung và `jobs/<id>` của phiên bản cũ. Bản dịch được ghi sau mỗi nhóm thành công; ASR hiện vẫn lưu checkpoint khi hoàn thành bước. Hồ sơ release cũ ở `%LOCALAPPDATA%\com.aivideodubbing.desktop` không được dùng khi dev.

## Kiểm thử

```powershell
$devRoot = Join-Path (Get-Location) '.dev'
$node = Join-Path $devRoot 'tools\node\node.exe'
$npmCli = Join-Path $devRoot 'tools\node\node_modules\npm\bin\npm-cli.js'
$env:PATH = "$devRoot\tools\node;$devRoot\tools\ffmpeg\bin;$env:PATH"
& $node $npmCli run build
& $node $npmCli run test:ui
$runtime = Join-Path $devRoot 'venv\Scripts\python.exe'
Push-Location engine
try { & $runtime -B -m unittest discover -s tests -v } finally { Pop-Location }
```

Lệnh trên dùng FFmpeg/FFprobe của project cho các test media. Hai bài live AI mặc định được bỏ qua; kiểm tra đầy đủ qua cửa sổ Tauri với clip ngắn. [Hướng dẫn kiểm thử và nghiệm thu](docs/testing.md)
