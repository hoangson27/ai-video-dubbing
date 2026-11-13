# Kiểm thử AI Video Dubbing

## Thiết lập từ bản clone mới

Luồng người dùng cần nghiệm thu, từ PowerShell trong thư mục có `package.json`:

```powershell
.\setup.cmd
.\dev.cmd
```

Máy thử cần Windows 10/11 x64, Windows PowerShell 5.1 và Internet; không chuẩn bị trước Node, Python, Rust hoặc Visual Studio. Có thể clone bằng Git hoặc giải nén ZIP. Thử cả đường dẫn có khoảng trắng hoặc ký tự ngoài ASCII. Setup đầy đủ phải chuẩn bị công cụ build, package frontend, runtime AI và model mặc định; ứng dụng mở được và kiểm tra môi trường sẵn sàng.

Trường hợp chỉ kiểm tra frontend/native:

```powershell
.\setup.cmd -SkipAi
.\dev.cmd -CheckOnly
.\setup.cmd -CheckOnly
```

`setup.cmd -CheckOnly` chỉ đọc trạng thái, không tải/cài. Sau `-SkipAi`, nhóm AI còn thiếu là kết quả dự kiến; vào **Hệ thống → Thiết lập tự động** trong app để hoàn tất. Đóng app và chờ tác vụ hiện tại hoàn tất trước khi chạy lại setup có cài thư viện/model. `npm run setup` và `npm run desktop` là lệnh tiện dụng khi npm đã có trên PATH; các file `.cmd` vẫn chạy được khi máy chưa có Node/npm toàn cục.

Source và metadata tái lập native toolchain nằm trong `scripts/toolchain/`. Bootstrap dựng công cụ vào `.verification/toolchain`, không yêu cầu thư mục này có sẵn trong clone. SDK MSI được đọc để giải nén payload đã xác minh, không được chạy làm bộ cài. Runtime/model/cache của ứng dụng nằm trong `.dev`. WebView2 dùng bản Windows đã cài; nếu thiếu, setup dùng bootstrapper Microsoft có chữ ký để cài cho tài khoản hiện tại. Không thay PATH hệ thống, registry/chính sách bảo vệ hoặc shortcut của người dùng để chuẩn bị các công cụ cục bộ.

**Fresh-clone verification — 13/11/2026:** đã tạo bản sao chỉ chứa mã nguồn tại `.verification/clone-bootstrap-proof`. Python 3.11.9, Node 22.22.3, Rust 1.99.1, MSVC 14.44.35207 và Windows SDK 10.0.26100.0 được dựng thật từ archive chính thức đã xác minh lại hash; không chép runtime/toolchain đã cài, `node_modules`, model hay target biên dịch từ workspace gốc. Manifest nhỏ trong source ghim 36 artifact. Bootstrap biên dịch, liên kết và chạy được cả C++ lẫn Rust bằng PATH chỉ gồm toolchain cục bộ và Windows System32. Receipt ghi phiên bản manifest, kết quả link và danh sách file để không nhận nhầm một thư mục toolchain còn thiếu thành sẵn sàng.

`setup.cmd -SkipAi` đã cài 71 package từ `package-lock.json` và hoàn tất với PATH ban đầu chỉ có công cụ Windows; `setup.cmd -CheckOnly` trả `ready=true`. `dev.cmd -CheckOnly` xác nhận các đường dẫn Node/npm/Rust/cache/model thuộc bản sao. **25/25 test giao diện**, build frontend, **build Tauri debug offline** và **21/21 test Rust** đạt. Build native dùng Cargo home/target riêng, chỉ chép cache archive và index registry để tránh tải lại; không chép Cargo source đã giải nén hoặc target cũ. Lượt Rust trong sandbox có một lỗi Windows 5 khi hỏi tiến trình sở hữu cổng TCP; chạy lại cùng suite ngoài sandbox đạt đầy đủ. Không mở cửa sổ app thứ hai để tránh ảnh hưởng phiên đang xử lý video.

Kiểm thử bootstrap native đạt **10/10**, gồm hash/cache, đường dẫn và junction thoát thư mục, rollback, receipt sai kiểu JSON, manifest đổi và thiếu thư viện ngoài danh sách file tối thiểu. PowerShell đạt **45 checkpoint**: installer 11, inventory 7, dev profile 9, controller setup 5, WebView2 10 và khóa sửa Python 3. Một nhánh tạo junction trong suite installer bị sandbox từ chối và dùng fixture reparse thay thế; test junction thật của bootstrap native đã chạy riêng và đạt. Hai lỗi review được sửa và kiểm thử lại: receipt JSON `[]` phải báo chưa sẵn sàng để cài lại; sửa Python phải tạo và giữ khóa engine ngay cả khi chưa có file khóa. Review cuối không còn lỗi bắt buộc sửa trong phạm vi này.

Bằng chứng cục bộ, không đưa vào Git: `.verification/clone-proof-bootstrap-runtimes.log`, `clone-bootstrap-native.log`, `clone-proof-setup.log`, `clone-proof-check.json`, `clone-proof-launch-paths.json`, `clone-proof-ui-tests.log`, `clone-proof-frontend-build.log`, `clone-proof-cargo-build.log`, `clone-proof-cargo-test-unsandboxed.log`, `clone-final-summary.json` và `clone-final-tests/`.

Giới hạn: đây là bản sao nguồn trên cùng máy Windows, không phải máy/VM mới. WebView2 157.0.4341.46 có sẵn được dùng lại; nhánh tải/cài WebView2 được kiểm thử bằng fixture, không cài lại runtime hệ thống. Lượt này dùng `-SkipAi` để giữ nguyên venv/model và tác vụ AI đang chạy; controller đầy đủ và việc giữ khóa khi cài AI được kiểm thử bằng fixture. Không coi đây là bằng chứng tải lại toàn bộ model từ đầu hoặc nghiệm thu bộ cài NSIS. Kết quả inference thật của môi trường dev được ghi riêng ở dưới.

Các trường hợp cần kiểm tra thêm: cache tải bị thiếu/hỏng được tải lại; archive có đường dẫn vượt thư mục bị từ chối; setup bị gián đoạn giữ công cụ hợp lệ; lần chạy lại dùng cache hợp lệ; lỗi công cụ truyền mã thoát rõ; WebView2 đã có được dùng lại, installer thiếu chữ ký bị chặn. Không tự vượt qua Windows Application Control. Nếu cổng dev Ollama 11435 đã bị tiến trình khác chiếm, setup báo lỗi thay vì nhận nhầm daemon.

## Lưu dự án khi Windows giữ file

Đã tái hiện đúng `WinError 5` khi thay JSON trong lúc có handle đọc chưa cho phép đổi tên; cùng file thay được sau khi handle đóng. File dự án được báo lỗi không có cờ ReadOnly, và phép kiểm tra quyền rename hiện tại thành công mà không ghi dữ liệu. Chưa xác định tiến trình ngoài nào giữ file tại thời điểm xảy ra lỗi. Bằng chứng: `.verification/project-save-lock-proof.json`.

`write_json` nay dùng file tạm riêng cùng thư mục, đóng file trước khi thay nguyên tử, và retry tối đa khoảng 2,175 giây cho Windows 5/32/33. Manifest cũ không bị ghi đè trực tiếp; nếu thay vẫn thất bại, JSON mới hoàn chỉnh được giữ ở `.pending.json` và lỗi nêu rõ đường dẫn. Lỗi ghi/dung lượng không retry sai loại; cleanup không che lỗi gốc. Các file phục hồi không bị thao tác dọn tệp tạm xóa mất. Sáu test persistence đã thất bại trước sửa và đạt sau sửa; toàn bộ Python chạy bằng `.dev` đạt **90/92 bài, 2 bài live bỏ qua**. Log: `.verification/project-save-python-tests.log`. Không chạy lại video dài hoặc dừng tác vụ người dùng để test; thay đổi áp dụng cho tiến trình Python mới.

## Xóa dự án từ danh sách

Mỗi dòng trong **Dự án đã lưu** có nút **Xóa**, mở xác nhận theo đúng dự án của dòng đó. Xác nhận đưa thư mục vào Thùng rác qua backend hiện có; video nguồn tham chiếu ngoài thư mục được giữ nguyên. Năm test handler trong `tests/project-actions.test.mjs` chạy App thật với native bridge giả lập: hủy không gửi lệnh, xóa đúng ID đã xác nhận, giữ editor/draft/media của dự án khác, dọn trạng thái dự án đang mở sau thành công, lỗi backend giữ nguyên danh sách và chặn thao tác khi đang bận. Chạy cùng `npm run test:ui`; không xóa dự án thật trong bộ test.

## Lịch sử xác minh môi trường dev trong project — 12/11/2026

Chạy từ PowerShell tại thư mục mã nguồn:

```powershell
.\dev.cmd
```

Tauri tự chạy Vite và mở cửa sổ native. Không cần chạy giao diện riêng. Dùng `.\dev.cmd -CheckOnly` để xem Node/npm, Rust và các đường dẫn cache/model được chọn. Node/npm nằm trong `.dev/tools/node`; Rust/MSVC/SDK nằm trong `.verification/toolchain`. Debug backend luôn ghim dữ liệu vào `<repo>/.dev`, kể cả khi chạy Tauri trực tiếp. Những bằng chứng trong mục này thuộc môi trường đã được chuẩn bị tại workspace, không thay cho kiểm tra bản clone mới phía trên.

Python portable, venv, FFmpeg/FFprobe, Ollama portable, model, cache, nhật ký, dự án/output và WebView profile đều nằm trong `.dev`. Ollama dev dùng dịch vụ do app sở hữu tại `127.0.0.1:11435`; không dùng daemon hệ thống ở cổng 11434. Worker không tìm công cụ qua PATH, registry hoặc AppData. Video nguồn có thể ở ngoài checkout; dữ liệu dự án do app ghi phải ở trong `.dev`. Windows, PowerShell, WebView2 runtime và driver vẫn là điều kiện của máy.

Đã sao chép và xác minh SHA-256 của **24.897 tệp, khoảng 6,67 GiB** sang `.dev`, giữ nguyên bản cũ. Báo cáo: `.verification/dev-migration-audit.json`. Venv được tạo lại đường dẫn interpreter nền và launcher; Python/pip cục bộ chạy được, `pip check` không có yêu cầu hỏng. FFmpeg và Ollama tại `.dev/tools` đã chạy thật; kiểm tra độc lập đã khởi động và dừng daemon của chính project.

**Trạng thái kiểm tra dev hiện tại:** Setup hoàn tất với Whisper `small`, VieNeu-TTS ONNX và Qwen `qwen2.5:3b`. Kiểm tra độc lập trả `ready=true`, **18 đã cài, 0 còn thiếu**; cửa sổ Tauri khởi động bằng `scripts/dev.ps1` cũng hiển thị hệ thống sẵn sàng, 100%, và các đường dẫn trong `.dev`. Hai clip mẫu đã chạy hết pipeline và xuất video trong `.dev/projects`: Anh **86,235 giây**, Trung **19,938 giây**. Lượt đầu gồm khởi động model, nên các số này không phải benchmark so sánh model. Cả hai giữ nguyên SHA-256 nguồn và payload packet hình; từng đoạn có bản dịch và WAV. Bằng chứng: `.verification/dev-live-summary.json`, `dev-live-en.jsonl`, `dev-live-zh.jsonl`; báo cáo môi trường: `.verification/dev-final-inventory.json`.

Các kiểm thử cuối đã đạt: Rust **21 bài** và build native debug; Python **86 bài, 84 đạt và 2 live mặc định bỏ qua**; frontend **20 bài** và build bằng Node/npm cục bộ; PowerShell inventory **7 nhóm** và dev profile **9 nhóm**, cùng các fixture cài đặt/lease/offline orchestration. Hai clip inference được chạy riêng bằng harness quản lý daemon dev, như bằng chứng phía trên. Log Python: `.verification/dev-python-tests-after-whisper-integrity.log`.

Lượt thực tế đã phát hiện và sửa hai lỗi: audio nguồn ngắn hơn phần hình cần được bù khoảng lặng khi tách, trong khi WAV thay thế nhập thủ công vẫn phải khớp thời lượng; file Whisper tải dở vẫn tồn tại nên bị kiểm tra theo sự tồn tại bỏ sót. File `model.bin` 401.604.608 byte được giữ dự phòng và thay bằng bản chính thức 483.546.902 byte, SHA-256 đã xác minh; xem `.verification/dev-whisper-repair.json`. Model tải qua Hugging Face nay phải có metadata hoàn tất và khớp SHA-256; Setup tải vào thư mục tạm, xác minh rồi mới thay bản cũ, giữ backup. Mười regression integrity/setup và các test FFmpeg thật đã đạt. Rà soát độc lập không còn finding.

Các lỗi đăng ký Python MSI của lượt reset trước đã được khôi phục và gỡ bằng trình cài chính thức, kết quả **0 bản ghi MSI còn lại**; xem `.verification/python-msi-recovery-result.json`. Setup hiện dùng gói NuGet CPython 3.11.9 portable nên không lặp lại bước Windows Installer. Bộ cài phát hành và chạy trên máy khác được để lại sau giai đoạn test dev.

## Lịch sử nâng cấp dự án theo thư mục — 11/11/2026

Bản nâng cấp bổ sung manifest/index theo dự án, thư mục 01-source đến 07-output, trạng thái từng bước, auto/manual, tạm dừng an toàn, checkpoint từng nhóm dịch, chỉnh lời gốc/mốc thời gian/bản dịch, nhập WAV/SRT/JSON, WAV riêng trong dự án, lịch sử xuất, đăng ký thư mục đã chuyển chỗ, liên kết lại nguồn bằng SHA-256 và sao chép dữ liệu cũ sang dự án.

Đã kiểm tra bộ Python trên Python hệ thống 3.12, không cài lại môi trường AI của ứng dụng: **63 bài tổng cộng, 55 đạt, 8 bỏ qua**. Hai bài cần inference thật, sáu bài cần FFmpeg/FFprobe trong PATH. FFmpeg WinGet có sẵn được giữ nguyên, nhưng lần gọi trực tiếp trong sandbox bị Access denied; không sao chép công cụ để né hạn chế. Log: `.verification/project-workflow-python-tests.log`.

Rust **14 bài đạt**, `cargo build --offline --features custom-protocol` thành công với toolchain của workspace. Bộ UI/state **20 bài đạt**, `npm run build` đạt. PowerShell có **13 nhóm fixture đạt**, không cài phần mềm thật. Browser QA với bridge giả lập kiểm tra các chế độ, kết quả từng bước, tạm dừng, nhập artifact, giữ draft khi có sự kiện mới, mở lại dự án, relink nguồn và xác nhận xóa. Các kích thước 1440/800/390px không có lỗi JavaScript hoặc tràn ngang. Bằng chứng: `.verification/ui/projects/report.json`; kiểm thử log cài đặt trước đó cũng được chạy lại và đạt. Browser QA chưa xác nhận thao tác Recycle Bin/Explorer thực tế hoặc inference trên bản nâng cấp.

Rà soát độc lập đã phát hiện và sửa: dọn nhầm source/output có `.part.` trong tên; sửa một câu vô tình xác nhận lại bản dịch từ model cũ; công việc cũ bị quên xuất hiện lại; công việc cũ bị gián đoạn không chuyển được thành dự án; xóa dự án cha có thể bao gồm dự án con. Các trường hợp có regression fixture. Khi Ollama/model không sẵn sàng, cached translation dùng receipt cũ chỉ nếu provider/model/prompt vẫn trùng và digest hiện tại chưa biết; digest mới đã biết vẫn làm kết quả tự động cần cập nhật.

Lượt reset để test Setup từ đầu từng để lại năm bản ghi MSI sau khi runtime Python bị xóa. Trạng thái này đã được xử lý trong lượt khôi phục và chuyển sang portable ghi ở đầu tài liệu; không còn là blocker hiện tại. Chất lượng/tốc độ thực tế của Qwen3B so với 7B, runtime GPU và bộ cài NSIS vẫn cần nghiệm thu riêng.

Checklist test dev: chạy Tauri, chọn model trong Cài đặt, Setup; dùng video mẫu ngắn, chạy auto rồi thử chế độ từng bước; dừng sau nhận dạng/dịch để sửa; tạm dừng và tiếp tục để kiểm tra số đoạn đã lưu; mở folder và xem các bản xuất; thử gỡ/xóa trên dự án mẫu. Với job cũ, dùng Chuyển thành dự án và kiểm tra dữ liệu cũ còn nguyên.

## Bộ kiểm thử tự động

```powershell
$devRoot = Join-Path (Get-Location) '.dev'
$node = Join-Path $devRoot 'tools\node\node.exe'
$npmCli = Join-Path $devRoot 'tools\node\node_modules\npm\bin\npm-cli.js'
$runtime = Join-Path $devRoot 'venv\Scripts\python.exe'
$env:PATH = "$devRoot\tools\node;$devRoot\tools\ffmpeg\bin;$env:PATH"
& $node $npmCli run build
& $node $npmCli run test:ui
Push-Location engine
try { & $runtime -B -m unittest discover -s tests -v } finally { Pop-Location }
. .\scripts\toolchain\env.ps1
cargo test --manifest-path src-tauri/Cargo.toml
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/tests/dependency-inventory.test.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/tests/install-deps.test.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/tests/dev-profile.test.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/tests/webview2.test.ps1
$bootstrapPython = Join-Path $devRoot 'runtime\python311-nuget\python.exe'
& $bootstrapPython -B -m unittest discover -s scripts/tests -p test_toolchain.py -v
```

Bộ Python kiểm tra ánh xạ ID khi dịch, retry JSON, lỗi Ollama/model, fallback CUDA và lỗi hết bộ nhớ, timeline có khoảng lặng, đoạn đọc quá dài, đoạn chồng mốc, hủy và khôi phục checkpoint. Các regression mới kiểm tra đường dẫn dev, cấu hình cũ trỏ ra ngoài project, chặn HTTP khi chưa sở hữu daemon và chặn đọc/ghi dự án ngoài `.dev`. Bài kiểm tra media tạo clip bằng FFmpeg, đọc metadata, ghép MP4 và MKV, so sánh packet hash và kiểm tra tệp nguồn. PATH trong lệnh trên ưu tiên FFmpeg/FFprobe của project. Hai bài live inference mặc định được bỏ qua.

## Hai clip thật với model local

Chuẩn bị một video tiếng Trung phổ thông và một video tiếng Anh, mỗi clip khoảng 15–60 giây, có nhiều câu thoại và một khoảng lặng rõ. Chọn MP4 H.264/AAC cho phép so sánh packet hash giữa hai file MP4. Chạy app bằng `scripts/dev.ps1`, hoàn tất **Thiết lập tự động**, chọn Whisper `small` và Qwen `qwen2.5:3b`, rồi xử lý từng clip trong Tauri. Native backend khởi động và xác nhận quyền sở hữu daemon dev; không mở Ollama hệ thống bằng tay.

Xác nhận dự án và bản xuất nằm trong `.dev/projects`, từng đoạn có transcript/bản dịch/WAV, SHA-256 video nguồn giữ nguyên và đối chiếu packet hình đạt. Nghe bản xuất để kiểm tra nội dung tiếng Việt, thuật ngữ, khoảng lặng, âm lượng và mốc lời thoại. Thử chạy tự động, dừng sau bước, sửa một câu và xuất lại để xác nhận WAV không đổi được dùng lại.

Hai bài trong `engine/tests/test_live.py` là harness CLI đã dùng cho các lượt lịch sử bên dưới; chưa truyền xác nhận sở hữu daemon cho hồ sơ dev. Lượt dev hiện tại dùng `.verification/dev-live-smoke.ps1` và `.verification/dev-live-smoke.py`: tự sở hữu daemon 11435, dùng đúng Python/model của `.dev`, ghi JSON Lines và so sánh nguồn/payload hình. Đóng app trước khi chạy lại harness này. Các tệp `.verification` là bằng chứng tại workspace, không được đưa vào Git. Chất lượng ngữ nghĩa vẫn cần người nghe đối chiếu.

## Nghiệm thu giao diện và bộ cài

| Tình huống | Cách kiểm tra | Kết quả mong đợi |
| --- | --- | --- |
| Chọn file và kéo thả | Mở MP4 qua hộp thoại, rồi kéo một MKV hoặc MOV vào cửa sổ | Preview hoặc tùy chọn proxy, metadata đúng, chỉ nhận một video |
| Tiếng Trung và tiếng Anh | Chạy một clip `zh`, một clip `en`, sau đó thử tự nhận diện | Transcript chia đoạn đúng thứ tự, bản tiếng Việt phát được, video không mất hình |
| Clip không có audio | Tạo bằng `ffmpeg -i input.mp4 -an -c:v copy silent.mp4` | Báo không có audio và vô hiệu hóa nút bắt đầu |
| File lỗi hoặc định dạng khác | Chọn MP4 hỏng và kéo file `.wmv` | Báo lỗi cụ thể, không tạo bản xuất |
| Câu dịch dài và nhiều đoạn | Kéo dài một câu trong editor, xuất lại, sau đó rút ngắn và xuất lần nữa | Hiện cảnh báo khi vượt mốc, đoạn không đổi dùng cache, các khoảng lặng được giữ |
| Thiếu Ollama hoặc Qwen | Dùng fixture môi trường thiếu công cụ; chọn một model chưa tải trong app rồi kiểm tra lại | Thành phần thiếu được báo đúng; dev không dùng công cụ hoặc daemon hệ thống thay thế |
| Không có CUDA | Chạy kiểm tra ở AUTO và GPU trên máy không có CUDA | AUTO dùng CPU cho các provider hỗ trợ. GPU báo thành phần không tương thích |
| Python dừng đột ngột | Trong khi xử lý clip thử, dừng tiến trình engine từ Task Manager | Giao diện báo lỗi và mở lại được checkpoint. Tiếp tục tác vụ đã lưu |
| Hủy | Hủy trong lúc dịch hoặc TTS, kiểm tra Task Manager rồi tiếp tục | UI trở về trạng thái ổn định, cây tiến trình engine dừng, kết quả đã lưu còn dùng được |
| Máy Windows khác | Cài `AI-Video-Dubbing-Setup.exe` trên máy không có toolchain, chạy setup deps và xử lý một clip | Cửa sổ mở vào Kiểm tra hệ thống. Cấu hình model xong thì xuất được video |

Phần bộ cài được nghiệm thu ở giai đoạn sau bằng tài khoản thường. Xác nhận thư mục cài chứa `engine/dubbing`, `engine/requirements.txt`, các script `setup-deps.ps1`, `check-deps.ps1`, `dependency-inventory.ps1`, `install-deps.ps1`, `dev-profile.ps1` và README. Xác nhận file xuất có đúng thời lượng hình, độ phân giải, FPS và codec. Khi chuyển container, so sánh packet hash có thể báo chênh do định dạng đóng gói bitstream.

## Nghiệm thu thiết lập tự động và kết quả lịch sử

Trong môi trường dev, mở ứng dụng và bấm **Thiết lập tự động**. Không cài thủ công Python hoặc nhập đường dẫn trước. Xác nhận phần kiểm tra vẫn liệt kê đủ các thành phần, tiến độ và nhật ký hiển thị trong lúc tải, các phần hợp lệ trong `.dev` được dùng lại. Khi hoàn tất, ứng dụng tự lưu đường dẫn và cho chuyển về bàn dựng. Đóng/mở ứng dụng rồi xác nhận Ollama của project được khởi động lại. Thử dừng khi tải và bấm **Tiếp tục thiết lập**; thử mất mạng và thiếu dung lượng để xem thông báo/thử lại. Không thay đổi chính sách bảo mật Windows tự động. Kiểm tra trên tài khoản Windows mới được để lại cho giai đoạn bộ cài.

Các bài PowerShell dùng fixture offline để kiểm tra lựa chọn phiên bản, danh sách đầy đủ khi chưa có Python 3.11, merge báo cáo AI, truyền tham số an toàn, hủy, kiểm tra/tải lại cache lỗi và khôi phục venv riêng. Bài orchestration chạy thân script thật với các thao tác tải/cài được thay bằng fixture, không cài phần mềm lên hệ điều hành. Ba bài Python kiểm tra dùng lại model và giữ nguyên weights do người dùng cung cấp.

Các kết quả từ đây đến cuối tài liệu thuộc hồ sơ AppData trước khi chuyển sang `.dev`.

Ngày 11/11/2026: frontend build và 8 bài workflow đạt, PowerShell có 12 nhóm kiểm thử đạt, Rust có 6 bài đạt. Bộ Python trên CPython 3.11.9 chạy 27 bài: 25 đạt, 2 bài live được bỏ qua trong lượt này. `cargo check` và `cargo build --features custom-protocol` thành công. Đã mở bản native debug và kiểm tra trực tiếp qua Windows UI Automation: 18 hàng dependency, Python 3.12/3.14 đã có, FFmpeg/FFprobe được nhận diện, RAM 15,7 GB, RTX 3050 Laptop 4096 MB và Intel UHD. Nút **Kiểm tra lại** chạy IPC thật và giữ nguyên danh sách trong lúc kiểm tra. Kiểm tra giao diện trạng thái cài/thử lại/hủy/thành công bằng bridge giả lập đã qua ở 1280×860 và 800×650.

Lượt thiết lập thực tế tiếp theo đã được chạy trực tiếp từ nút trong cửa sổ Tauri trên máy này. Python 3.11.9, Ollama, venv và các package AI được cài; Whisper `small`, VieNeu-TTS và Qwen `qwen2.5:7b` được tải thật. FFmpeg/FFprobe đã có được dùng lại. Báo cáo cuối: **18 đã cài, 0 còn thiếu**, 25 preset giọng và `ready=true`.

Lượt thực tế phát hiện hai lỗi mà fixture trước đó chưa bao phủ: Windows PowerShell 5.1 làm mất dấu ngoặc kép trong Python `-c`, và .NET Process không kế thừa `Push-Location` nếu chưa đặt `WorkingDirectory`. Hai lỗi đã được sửa bằng truyền tham số qua ProcessStartInfo với quoting đầy đủ và đặt thư mục làm việc rõ ràng. Kiểm thử regression dùng Python 3.11 thật đã thất bại trước sửa và đạt sau sửa. stdout/stderr, mã thoát và lệnh chạy được lưu vào `logs/setup-latest.log`; lỗi gốc tự mở trong giao diện, có đường dẫn và nút sao chép. Nhật ký cũ không được phép ghi đè kết quả readiness mới.

Hai bài live Anh/Trung đã chạy lại trên venv vừa cài, Whisper `small`, Qwen 7B và VieNeu-TTS ONNX/CPU, hoàn tất trong **124,049 giây**. Cả hai xuất được video, giữ nguyên SHA-256 tệp nguồn và payload packet hình. Bằng chứng nằm trong `.verification/installed-e2e-20261111-204715/live-test.log`. Lượt gọi CLI đầu trong sandbox bị chặn đọc FFmpeg ở WinGet; lượt kiểm thử được cấp quyền thực thi ngoài sandbox đã đạt. NSIS và chạy trên máy Windows khác vẫn chưa được nghiệm thu; thử cài mới toàn bộ FFmpeg trên tài khoản sạch còn là một trường hợp bổ sung.

Sau các bản sửa thực tế: **12 test workflow, 7 test Rust và 14 nhóm test PowerShell** đạt. Kiểm thử UI log gồm khôi phục lỗi sau restart, tự mở chi tiết, đọc log bền vững, sao chép và xử lý clipboard bị chặn đã đạt ở hai kích thước cửa sổ, không lỗi JavaScript hoặc tràn ngang. Bằng chứng: `.verification/ui/setup-logs/report.json`.

## Kết quả pipeline AI và các lượt build trước đó

Frontend đã qua `npm run build` và 5 bài kiểm thử workflow. Năm màn hình ở trạng thái chờ đã được kiểm tra bằng Edge headless ở kích thước 1280×860 và 800×650. Lỗi cắt nội dung vùng chọn video ở cửa sổ hẹp đã được sửa và kiểm tra lại. Bộ Python chạy 22 bài kiểm thử unit/media thành công với FFmpeg và FFprobe thật. Hai bài live được bật và chạy riêng trên CPython 3.11.9, hoàn tất trong 23,224 giây.

`check-deps.ps1` đã chạy bằng Windows PowerShell 5.1 với Python 3.11.9 và FFmpeg/FFprobe thật. Script trả JSON UTF-8 hợp lệ, nhận diện package, model và 25 preset giọng. Mã thoát `1` và `ready=false` khớp môi trường thử: Ollama đã dừng và Python embedded không phải venv của ứng dụng.

Lượt live dùng cache trống, Whisper `tiny`, Qwen `qwen2.5:0.5b` trên Ollama local và VieNeu-TTS v3 Turbo ONNX/CPU với giọng Hải Đăng. Cả clip tiếng Anh và tiếng Trung tạo được bản xuất. SHA-256 toàn tệp nguồn giữ nguyên và payload packet video đầu ra khớp nguồn. Transcript và bản dịch của các model nhỏ vẫn có lỗi, nên kết quả này xác nhận pipeline vận hành. Chất lượng ngữ nghĩa của cấu hình mặc định Whisper `small` và Qwen `7b` cần lượt đánh giá riêng.

Đã tải và kiểm tra checksum Rust 1.99.1, chuẩn bị MSVC và Windows SDK cục bộ trong thư mục kiểm thử. Công cụ biên dịch chạy được và chương trình C thử đã biên dịch/liên kết thành công. Tuy nhiên, Windows Application Control chặn chạy chương trình vừa tạo. Lệnh `scripts/build-windows.ps1 -SkipInstall` thực tế đã build frontend thành công rồi dừng ở Cargo:

```text
error: failed to run custom build command for `serde_core v1.0.229`
could not execute process ...\build-script-build (never executed)
An Application Control policy has blocked this file. (os error 4551)
```

Trong lượt build cũ, `cargo check` và `cargo test` cũng dừng với `os error 4551`. Nhật ký cũ nằm trong `.verification/toolchain/native-build.log`, `cargo-check.log` và `cargo-test.log`; thư mục này không được đóng gói. Kết quả native debug mới đã được ghi trong mục Thiết lập tự động phía trên và thay thế giới hạn compile/khởi động cửa sổ của lượt cũ. Agent không thay đổi chính sách Windows. Hộp thoại/kéo thả, bộ cài NSIS và chạy trên máy Windows khác vẫn cần nghiệm thu riêng.

| Milestone | Phần đã xác minh | Phần cần môi trường desktop/build |
| --- | --- | --- |
| 1 | Vue, TypeScript, Tailwind build thành công, giao diện được kiểm tra ở hai kích thước | Hộp thoại và kéo thả trong cửa sổ Tauri |
| 2 | CLI JSON Lines, System Check độc lập Python qua IPC WebView2 thật và FFprobe/FFmpeg | Pipeline video đầy đủ qua IPC |
| 3 | Nhận dạng, dịch bằng model thật trên CPU | Đánh giá ngữ nghĩa bằng model mặc định |
| 4 | VieNeu-TTS thật, timeline và ghép stream copy | Inference CUDA với đủ runtime |
| 5 | Cache, chỉnh sửa, tạo lại giọng, khôi phục và hủy qua bộ test | Nghiệm thu tương tác trên ứng dụng native |
| 6 | Bản native debug, unit test Rust/PowerShell, cấu hình NSIS và tài nguyên được kiểm tra | Setup mới toàn bộ, bộ cài và cài thử trên máy Windows khác |
