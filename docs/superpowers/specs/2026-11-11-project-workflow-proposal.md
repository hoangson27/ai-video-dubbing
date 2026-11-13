# Đề xuất quản lý dự án video và xử lý từng bước

Ngày: 2026-11-11. Trạng thái: đề xuất để người dùng xem xét, chưa triển khai.

## Mục tiêu và hướng đề xuất

Người dùng muốn xử lý nhanh nhưng vẫn đúng nghĩa, tự nhiên và phù hợp thời lượng; mỗi video có một thư mục chứa dữ liệu và kết quả; có thể xem, sửa, chạy lại từng bước hoặc chạy tự động từ đầu tới cuối. Tiếp tục dùng Vue/Tauri/Python và các provider hiện có. Mở rộng cơ chế job thành dự án và tách bộ điều phối thành các bước có kết quả lưu bền vững.

Các mặc định trong tài liệu là đề xuất của người thiết kế, chưa phải lựa chọn đã được người dùng xác nhận. Phạm vi này chưa bao gồm dựng phim nhiều track, tách nhạc nền hay tự động phân vai nhiều nhân vật.

## Đánh giá hiện trạng

- Đã có checkpoint sau ASR/dịch hoàn tất, cache transcript và âm thanh, chỉnh bản dịch từng đoạn, dịch lại, tạo lại giọng và xuất lại video. Video đầu ra sử dụng stream copy để giữ luồng hình.
- Dữ liệu hiện phân tán: `jobs/<id>` có manifest/transcript/bản dịch/lời đọc tổng/log; âm thanh từng đoạn và preview thường nằm ở cache chung; video xuất nằm ở thư mục exports hoặc đường dẫn cấu hình chung.
- Chưa có trạng thái riêng và thao tác chạy độc lập cho từng bước. Giao diện chủ yếu hiển thị tiến độ và log; lời gốc và mốc thời gian chưa sửa được.
- Dịch chỉ ghi checkpoint khi dịch xong tất cả đoạn. Nếu nhóm cuối thất bại, các nhóm dịch thành công trong lần chạy đó chưa được lưu để tiếp tục. ASR cũng chưa lưu kết quả tăng dần.
- Dịch tối đa 8 đoạn/1.800 ký tự mỗi yêu cầu, tuần tự. Với 1.113 đoạn, cần ít nhất 140 yêu cầu thành công. Một câu rỗng khiến cả nhóm được gửi lại nguyên trạng.
- Cache bản dịch chưa đưa model dịch và phiên bản prompt vào khóa; cần sửa khi hỗ trợ đổi cấu hình và lịch sử phiên bản.

Căn cứ chính: `engine/dubbing/jobs.py:67`, `engine/dubbing/pipeline.py:178`, `engine/dubbing/pipeline.py:213`, `engine/dubbing/pipeline.py:262`, `engine/dubbing/providers.py:255`, `src/components/ProcessMonitor.vue:29`, `src/components/TranscriptEditor.vue:63`.

## 1. Tăng tốc theo bằng chứng

### Ưu tiên không thay đổi model trước

1. Lưu bản dịch đã hợp lệ ngay sau từng nhóm; tiếp tục từ các ID còn thiếu khi chạy lại. Giữ ngữ cảnh, thứ tự và những câu người dùng sửa.
2. Khi phản hồi có một phần hợp lệ, chỉ giữ các giá trị có ID khớp chính xác và nội dung hợp lệ. Những câu lỗi được thử lại với ngữ cảnh; chia nhóm nhỏ dần nếu lỗi lặp lại. Phản hồi không thể phân tích hoặc có ID trùng phải được xử lý như phản hồi lỗi, không ghép câu theo vị trí.
3. Điều chỉnh kích thước nhóm và ngân sách token theo nội dung thực tế, gồm cả glossary/ngữ cảnh. Ghi nhận lỗi cắt phản hồi, câu rỗng, ID sai, lỗi kết nối và hết bộ nhớ riêng biệt để chọn cách thử lại.
4. Dùng lại các kết quả có đầu vào và cấu hình còn khớp. Chỉ tính lại phần bị ảnh hưởng khi sửa.
5. Ghi thời gian từng bước, số yêu cầu/thử lại, token đầu vào/ra, thiết bị thực tế và số đoạn dùng cache. Hiển thị tiến độ theo số đoạn hoàn thành thay vì phần trăm cố định suốt bước dịch.

Mỗi lần thử lại có giới hạn. Nếu không tạo được kết quả hợp lệ, đánh dấu đúng đoạn cần xử lý và giữ các kết quả còn lại. Không tự điền bản dịch trống hoặc lược bỏ lời để hoàn tất video.

### Dùng phần cứng theo từng bước

Cấu hình đã kiểm tra trước đây: i5-11400H, RAM 15,7 GB, RTX 3050 Laptop 4 GB; Whisper đã từng phải dùng CPU vì thiếu thư viện CUDA. Đây là thông tin phiên kiểm tra trước, không phải phép đo hiệu suất hiện tại.

- Tách cấu hình thiết bị cho ASR, dịch và TTS; chế độ Auto chọn theo khả năng đã kiểm tra thực sự.
- Hoàn thiện hỗ trợ GPU cho ASR, rồi đo Whisper `small` với cấu hình phù hợp VRAM. Chỉ có GPU xuất hiện trong danh sách chưa đủ để khẳng định provider sử dụng được nó.
- Bắt đầu bằng lịch chạy tuần tự các model dùng GPU, giải phóng model trước khi chuyển bước. Chạy đồng thời dịch và TTS chỉ là thử nghiệm sau khi đã đo tài nguyên; không phải mặc định trên GPU 4 GB.
- Giữ model TTS một lần trong bước tổng hợp hiện tại và tiếp tục tận dụng cache. Xem xét worker giữ model cho các thao tác chỉnh sửa liên tiếp sau khi profiling chứng minh thời gian tải là đáng kể.
- Giữ stream copy cho xuất video. Tối ưu chuẩn hóa audio và số lần gọi FFmpeg sau khi đo cho thấy đây là phần đáng kể.

Tài liệu chính thức: [Ollama về bộ nhớ và yêu cầu song song](https://docs.ollama.com/faq#how-does-ollama-handle-concurrent-requests), [structured outputs và kiểm tra phản hồi](https://docs.ollama.com/capabilities/structured-outputs), [CTranslate2 GPU runtime](https://opennmt.net/CTranslate2/installation.html). Các phiên bản runtime phải tương thích với dependency đã pin trong dự án, không áp dụng máy móc yêu cầu của phiên bản mới hơn.

### Chọn tốc độ và chất lượng

Đề xuất ba preset dễ hiểu: Nhanh, Cân bằng, Ưu tiên chất lượng. Mặc định Cân bằng, dùng cấu hình hiện tại làm mốc so sánh. Model nhỏ hơn, mức lượng tử hóa khác, batch và thiết bị chỉ được đưa vào preset sau khi đo trên máy đích.

Đo một tập mẫu đại diện có đoạn khó nghe, tên riêng, số liệu, câu dài và nhiều ngữ cảnh. So sánh thời gian từng bước, lỗi/thử lại, bộ nhớ, độ đúng lời gốc, bản dịch, phát âm và độ khớp thời lượng. Kết quả hợp lệ về JSON không chứng minh đúng nghĩa. Không công bố hệ số tăng tốc hay hứa giữ nguyên chất lượng khi chưa đo.

Giữ glossary, văn phong và cách gọi nhân vật theo dự án. Cảnh báo câu quá dài, mất nội dung/số liệu nghi ngờ hoặc vượt thời lượng để người dùng duyệt. Với câu dài, ưu tiên sửa bản dịch và tạo lại đúng câu đó trước khi tăng tốc hoặc cắt âm thanh. Cảnh báo tự động là gợi ý kiểm tra, không phải thước đo độ chính xác tuyệt đối.

## 2. Một thư mục cho mỗi video

Người dùng chọn thư mục gốc lưu dự án một lần; mặc định đề xuất là `Videos/AI Video Dubbing`. Sau khi chọn video và xác nhận thêm vào dự án, tạo thư mục có tên video rút gọn cùng ID ngắn để tránh trùng. Chọn lại video đã đăng ký mở dự án tương ứng; thao tác Tạo bản sao dự án dành cho thử cấu hình khác.

```text
Ten-video-ab12cd34/
  project.json
  01-source/       # thông tin nguồn, metadata, preview nếu cần
  02-audio/        # âm thanh nguồn đã tách
  03-transcript/   # lời gốc JSON và SRT
  04-translation/  # bản dịch JSON và SRT
  05-voice/        # âm thanh từng đoạn và các take được giữ
  06-aligned/      # lời đọc tổng, timeline, cảnh báo đồng bộ
  07-output/      # video xuất v001, v002...
  logs/           # sự kiện, lỗi và thông tin hiệu suất
```

Mỗi bước có một dữ liệu cấu trúc chuẩn; SRT/TXT là định dạng xem và trao đổi. Nội dung do ứng dụng sở hữu dùng đường dẫn tương đối. Manifest có phiên bản schema, ID cố định, nguồn, cấu hình theo dự án, chữ ký đầu vào, trạng thái/kết quả từng bước, chỉnh sửa và các bản xuất kèm nguồn gốc phiên bản.

Mặc định tham chiếu video gốc để thêm nhanh và tránh sao chép file lớn. Có tùy chọn Sao chép video gốc vào dự án để mang nguyên bộ sang nơi khác. Bản gốc bên ngoài luôn được giữ nguyên. Nếu nguồn bị chuyển chỗ, có thao tác Chọn lại video gốc và xác minh danh tính; không âm thầm gắn một video khác với kết quả cũ.

Âm thanh từng đoạn đã được dùng phải có bản riêng bền vững trong dự án, kể cả khi lấy từ cache chung. Model, Python và công cụ vẫn dùng chung bên ngoài các dự án. Dọn cache không làm mất kết quả trong dự án.

Các thao tác trong danh sách dự án:

- Mở thư mục bằng Explorer; mở video đầu ra; xem dung lượng và lần xử lý gần nhất.
- Gỡ khỏi danh sách: giữ nguyên thư mục, có thể mở/đăng ký lại qua manifest.
- Xóa dữ liệu dự án: đưa thư mục dự án vào Recycle Bin, thể hiện rõ cả các video xuất bên trong. Không xóa nguồn tham chiếu, model hay dữ liệu dự án khác. Không cho xóa dự án đang chạy; thao tác từ thư mục đã xác minh quyền sở hữu, không lần theo đường dẫn bên ngoài hoặc junction trong manifest.
- Dọn tệp tạm là thao tác riêng; giữ lời gốc/bản dịch/kết quả đã chỉnh và các bản xuất.

Chỉ mục danh sách dự án có thể dựng lại từ các manifest. Có thao tác Mở thư mục dự án để đăng ký dự án đã chuyển vị trí. Thư mục có nguồn tham chiếu ngoài dự án chỉ di chuyển đủ bộ khi người dùng đã sao chép nguồn vào trong.

## 3. Các bước có thể xem và can thiệp

| Bước | Kết quả nhìn thấy | Thao tác thủ công đề xuất |
| --- | --- | --- |
| Đọc video | Video, metadata và danh sách track âm thanh | Chọn nguồn/track cần xử lý |
| Tách âm thanh | File âm thanh có thể nghe | Nghe, chạy lại, nhập bản audio thay thế đã kiểm tra độ dài |
| Nhận dạng | Lời gốc theo mốc thời gian | Sửa chữ và thời gian, nhập transcript/SRT có kiểm tra |
| Dịch | Lời gốc cạnh bản Việt, số đoạn đã xong và cảnh báo | Sửa/dịch lại câu hoặc nhóm được chọn, glossary/văn phong |
| Tạo giọng | WAV từng câu, trạng thái và độ dài | Nghe, đổi lựa chọn giọng, tạo lại câu, nhập WAV thay thế hợp lệ |
| Đồng bộ | Timeline, lời đọc tổng và cảnh báo | Sửa mốc/độ dài; quay lại sửa câu dịch hoặc giọng quá dài |
| Xuất | Preview và file video phiên bản mới | Xuất lại từ kết quả còn hợp lệ, mở vị trí file |

Chế độ **Tự động** chạy một mạch qua các bước hợp lệ, tự khôi phục trong giới hạn. Khi không thể tiếp tục an toàn, dừng tại bước lỗi và chỉ rõ đoạn cần xử lý. Không yêu cầu người dùng duyệt từng bước.

Chế độ **Từng bước** chạy bước được chọn khi đã đủ đầu vào; người dùng duyệt kết quả rồi tiếp tục. Có tùy chọn Dừng sau bước này trong chế độ tự động, hữu ích sau nhận dạng, dịch hoặc tạo giọng. Hai chế độ dùng chung bộ điều phối và dữ liệu.

Người dùng có thể xem kết quả đang được tạo theo nhóm/câu mà không cần đọc log. Muốn chỉnh trong khi chạy thì Tạm dừng sau nhóm hiện tại, lưu checkpoint và nhường quyền ghi cho trình sửa. Không cho hai tác vụ sửa cùng dữ liệu. Đây là tạm dừng an toàn giữa các đơn vị công việc; không hứa đóng băng tức thì một yêu cầu model đang chạy.

Giao diện cập nhật sự kiện kết quả từng bước/nhóm/câu, trạng thái và cảnh báo; nhật ký kỹ thuật là phần chi tiết mở thêm. Danh sách đoạn thoại dài cần hiển thị theo vùng nhìn thấy hoặc phân trang. Đọc trạng thái phải thực sự chỉ đọc, tách khỏi logic sửa trạng thái job bị gián đoạn hiện có trong `Jobs.load`.

## 4. Chỉnh sửa, phiên bản và chạy lại

Mỗi bước lưu trạng thái chưa chạy/đang chạy/đã có kết quả/cần chạy lại/lỗi/chờ duyệt, chữ ký đầu vào, model thực tế, artifact và lỗi cụ thể. Giữ kết quả thành công gần nhất trong khi chạy thay thế; ghi tạm rồi công bố nguyên tử sau kiểm tra. Các chỉnh sửa thủ công là một revision có nguồn gốc rõ ràng; không bị ghi đè âm thầm bởi ASR/dịch lại.

| Thay đổi | Phần cần cập nhật |
| --- | --- |
| Đổi nguồn/track/audio thay thế | Nhận dạng và các bước sau; giữ phiên bản trước để tham khảo |
| Sửa lời gốc | Bản dịch câu liên quan và ngữ cảnh lân cận cần kiểm tra; sau đó giọng và đầu ra bị ảnh hưởng |
| Chỉ sửa mốc thời gian | Đồng bộ/xuất; kiểm tra lại thời lượng câu, tái dùng WAV nếu còn phù hợp |
| Sửa một câu dịch | Giọng câu đó, đồng bộ và xuất; câu đã duyệt khác vẫn được giữ |
| Đổi giọng/model TTS/tốc độ | Âm thanh bị ảnh hưởng, đồng bộ và xuất |
| Đổi model dịch/glossary/văn phong | Bản dịch tự động được đánh dấu cần cập nhật; bản sửa tay giữ riêng để người dùng quyết định |
| Đổi thiết lập đồng bộ | Đồng bộ và xuất |
| Đổi định dạng xuất | Chỉ xuất |

Chữ ký bản dịch phải gồm model/digest, phiên bản provider/prompt và ngữ cảnh liên quan. Khi các phụ thuộc ngữ cảnh làm phạm vi tính lại chưa chắc chắn, đánh dấu cần duyệt rộng hơn thay vì hứa chỉ ảnh hưởng đúng một câu.

Tiếp tục dùng cache còn đúng. Chạy lại yêu cầu tính lại bước/phần được chọn; không xóa kết quả cũ trước khi có kết quả mới hợp lệ. Bản xuất v001/v002 ghi rõ revision đã dùng; video cũ không bị nhận là kết quả của lần sửa mới.

## 5. Thứ tự thực hiện đề xuất

1. **Nền dữ liệu và khôi phục:** manifest theo bước, lưu từng nhóm dịch, chữ ký cache đúng, checkpoint và sự kiện tiến độ. Đây là nền để vừa tăng tốc vừa hỗ trợ can thiệp.
2. **Thư mục dự án:** lưu các artifact/output đúng dự án, danh sách/mở folder/gỡ/xóa/dọn tệp tạm, nguồn tham chiếu hoặc sao chép.
3. **Điều khiển từng bước:** xem kết quả trực tiếp, tạm dừng/tiếp tục, duyệt và sửa lời gốc/bản dịch/âm thanh; chạy lại phần bị ảnh hưởng; chế độ tự động dùng cùng cơ chế.
4. **Tối ưu có số đo:** runtime GPU, batch/ngân sách token, preset model, chi phí audio và worker giữ model nếu cần.

Không thay đổi hoặc di chuyển dữ liệu của tác vụ người dùng đang chạy. Job cũ được đăng ký như dự án cũ và tiếp tục sử dụng; chuyển từng job khi đã nghỉ bằng cách sao chép, đưa WAV phụ thuộc và bản xuất đã chọn vào dự án, xác minh rồi chuyển chỉ mục. Giữ dữ liệu cũ tới một lần dọn riêng; không bắt dịch/nhận dạng lại chỉ để đổi cách lưu. Provenance cũ thiếu thông tin được ghi rõ, không tự suy đoán.

ASR lưu tăng dần cũng hữu ích nhưng việc tiếp tục chính xác giữa chừng cần thiết kế chunk/overlap/ngữ cảnh. Đưa vào phần tối ưu sau checkpoint dịch; không mô tả checkpoint toàn bước hiện tại như khả năng tiếp tục ASR giữa chừng.

## Tiêu chí chấp nhận

- Hủy hoặc lỗi ở một nhóm dịch muộn rồi mở lại không làm mất các nhóm đã được lưu; không đổi ID hoặc ghi đè câu người dùng sửa.
- Thư mục chứa đủ các artifact đã hoàn tất và output, mở lại được; dọn cache dùng chung không phá âm thanh dự án.
- Mỗi bước có kết quả xem/nghe được và trạng thái thật; auto và từng bước tạo kết quả cùng quy tắc.
- Sửa một câu dịch chỉ tạo lại giọng bị ảnh hưởng và phần tổng hợp/xuất cần thiết; phiên bản xuất cũ còn truy vết.
- Xóa/gỡ dự án có đúng ý nghĩa đã mô tả; nguồn ngoài dự án và model dùng chung còn nguyên; thao tác bị chặn khi dự án đang chạy.
- Có phép đo trước/sau trên cùng mẫu, cùng điều kiện cold/warm cache, cùng thiết bị; kiểm tra nội dung và nghe thử ngoài kiểm tra định dạng.
- Dữ liệu cũ và tác vụ đang chạy không bị gián đoạn bởi quá trình nâng cấp.
