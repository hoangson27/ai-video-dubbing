use crate::{LocalFiles, canonical_local_file, has_extension, local_path, path_string, process::EngineProcess};
use serde_json::{Value, json};
use std::{
    collections::HashSet,
    fs,
    io::{self, BufRead, BufReader, Read, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicU64, Ordering},
        mpsc::{self, RecvTimeoutError},
    },
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tauri::{AppHandle, Emitter, Manager, State};

mod dependencies;

const CANCEL_GRACE: Duration = Duration::from_secs(3);
const TICK: Duration = Duration::from_millis(100);
const CANCELLED: &str = "Đã hủy xử lý.";
const BOOTSTRAP: &str = "Môi trường xử lý chưa được cài. Vào Hệ thống và bấm Thiết lập tự động.";
static MARKER_SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[derive(Default)]
pub struct EngineState {
    active: Mutex<Option<Arc<Control>>>,
    ollama: Mutex<Option<EngineProcess>>,
}

struct Control {
    cancelled: AtomicBool,
    marker: PathBuf,
    pausable: AtomicBool,
}

struct Lease<'a> {
    state: &'a EngineState,
    control: Arc<Control>,
    keep_cancel_marker: bool,
}

impl Drop for Lease<'_> {
    fn drop(&mut self) {
        if let Ok(mut active) = self.state.active.lock() {
            let _ = fs::remove_file(self.control.marker.with_extension("pause"));
            *active = None;
            if !self.keep_cancel_marker && self.control.cancelled.load(Ordering::Acquire) {
                let _ = fs::remove_file(&self.control.marker);
            }
        }
    }
}

impl EngineState {
    fn reserve(&self, marker: PathBuf) -> Result<Lease<'_>, String> {
        let mut active = self.active.lock().map_err(|_| "Không thể truy cập tiến trình xử lý.")?;
        if active.is_some() {
            return Err("Một tác vụ đang chạy. Hãy chờ hoặc hủy tác vụ hiện tại.".to_owned());
        }
        let control = Arc::new(Control { cancelled: AtomicBool::new(false), pausable: AtomicBool::new(false), marker });
        *active = Some(control.clone());
        Ok(Lease {
            state: self,
            control,
            keep_cancel_marker: false,
        })
    }
}

#[tauri::command]
pub fn cancel_engine(state: State<'_, EngineState>) -> Result<(), String> {
    let active = state.active.lock().map_err(|_| "Không thể truy cập tiến trình xử lý.")?;
    if let Some(control) = active.as_ref()
        && !control.cancelled.swap(true, Ordering::AcqRel)
    {
        fs::File::create_new(&control.marker)
            .map_err(|error| format!("Không thể lưu dấu hủy tác vụ: {error}"))?;
    }
    Ok(())
}

#[tauri::command]
pub fn pause_engine(state: State<'_, EngineState>) -> Result<(), String> {
    let active = state.active.lock().map_err(|_| "Không thể truy cập tiến trình xử lý.")?;
    let control = active.as_ref().ok_or("Không có tác vụ đang chạy.")?;
    if !control.pausable.load(Ordering::Acquire) { return Err("Tác vụ này không hỗ trợ tạm dừng.".into()); }
    let path = control.marker.with_extension("pause");
    if !path.is_file() { fs::File::create_new(path).map_err(|error| format!("Không lưu được yêu cầu tạm dừng: {error}"))?; }
    Ok(())
}

pub fn shutdown(state: State<'_, EngineState>) {
    let _ = cancel_engine(state.clone());
    dependencies::shutdown(&state);
}

#[tauri::command]
pub async fn engine_request(app: AppHandle, request: Value) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || handle_request(&app, request))
        .await
        .map_err(|error| format!("Tiến trình xử lý gặp lỗi: {error}"))?
}

fn handle_request(app: &AppHandle, mut request: Value) -> Result<Value, String> {
    let action = request.get("action").and_then(Value::as_str)
        .ok_or("Thiếu thao tác cho Python Engine.")?.to_owned();
    let timeout = match action.as_str() {
        "probe" | "list_jobs" | "get_job" | "edit_segment" | "get_setup_log" | "open_project" | "update_project" | "edit_source_segment" |
        "open_project_folder" | "open_project_output" | "forget_project" | "delete_project" | "clean_project" => Duration::from_secs(90),
        "check" | "prepare_preview" | "preview_voice" => Duration::from_secs(300),
        "run" | "run_stage" | "create_project" | "export" | "regenerate_segment" | "retranslate_segment" | "import_artifact" | "migrate_project" | "relink_source" | "install_dependencies" => Duration::from_secs(12 * 3600),
        _ => return Err("Thao tác Python Engine không hợp lệ.".to_owned()),
    };
    let files = app.state::<LocalFiles>();
    {
        let sources = files.sources.lock().map_err(|_| "Không thể đọc danh sách video.")?;
        require_approved_source(&mut request, &sources)?;
    }
    {
        let artifacts = files.artifacts.lock().map_err(|_| "Không đọc được danh sách tệp thay thế.")?;
        crate::projects::require_approved_artifact(&mut request, &artifacts)?;
    }
    {
        let directories = files.directories.lock().map_err(|_| "Không đọc được danh sách thư mục đã chọn.")?;
        crate::projects::require_approved_directory(&mut request, &directories)?;
    }

    let data_dir = crate::profile::data_dir(app)?;
    if !request["settings"].is_object() { request["settings"] = json!({}); }
    let owned = dependencies::owned_ollama_alive(&app.state::<EngineState>())?;
    request["settings"]["ollama_owned"] = json!(owned);
    if let Some(root) = crate::profile::dev_root() {
        crate::profile::apply_request(&mut request, &root, owned)?;
    } else {
        request["settings"]["dev_mode"] = json!(false);
    }
    if action == "get_setup_log" {
        return Ok(dependencies::saved_setup_log(&data_dir));
    }
    let marker_dir = data_dir.join("cancel");
    fs::create_dir_all(&marker_dir).map_err(|error| format!("Không thể tạo thư mục ứng dụng: {error}"))?;
    let clock = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_nanos();
    let sequence = MARKER_SEQUENCE.fetch_add(1, Ordering::Relaxed);
    let marker = marker_dir.join(format!("{clock}-{}-{sequence}", std::process::id()));
    let state = app.state::<EngineState>();
    let mut lease = state.reserve(marker)?;
    lease.control.pausable.store(matches!(action.as_str(), "run" | "run_stage" | "export"), Ordering::Release);
    if crate::projects::is_native_action(&action) {
        return crate::projects::handle(&data_dir, &action, request["job_id"].as_str().unwrap_or_default());
    }
    request["data_dir"] = Value::String(path_string(&data_dir)?);
    request["cancel_path"] = Value::String(path_string(&lease.control.marker)?);
    request["pause_path"] = Value::String(path_string(&lease.control.marker.with_extension("pause"))?);
    if setting(&request, "projects_dir").is_empty() {
        let projects = app.path().video_dir().unwrap_or_else(|_| data_dir.clone()).join("AI Video Dubbing");
        request["settings"]["projects_dir"] = json!(path_string(&projects)?);
    }

    if matches!(action.as_str(), "check" | "install_dependencies") {
        return dependencies::handle(app, request, &action, &data_dir, &lease.control, timeout);
    }

    let python = setting(&request, "python_path");
    let venv_python = venv_python(&data_dir);
    validate_settings(&request)?;
    let mut command = python_command(python, &venv_python)?;
    command
        .args(["-X", "utf8", "-E", "-s", "-B", "-u", "-m", "dubbing"])
        .env_remove("PYLAUNCHER_ALLOW_INSTALL")
        .env_remove("PYLAUNCHER_ALWAYS_INSTALL")
        .env_remove("PYLAUNCHER_DRYRUN")
        .env("PYTHON_MANAGER_AUTOMATIC_INSTALL", "false")
        .current_dir(engine_dir(app)?)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    crate::profile::configure_child(&mut command)?;

    let payload = serde_json::to_vec(&request).map_err(|error| error.to_string())?;
    let result = execute(app, command, payload, &lease.control, timeout);
    // Leave the marker after a failed cancellation so job recovery can identify it.
    lease.keep_cancel_marker = result.is_err() && matches!(
        action.as_str(),
        "run" | "run_stage" | "export" | "edit_segment" | "regenerate_segment" | "retranslate_segment"
    );
    match result {
        Ok(value) => {
            register_result_media(&files, &data_dir, &action, &value);
            Ok(value)
        }
        Err(error) => Err(error),
    }
}

pub fn require_approved_source(request: &mut Value, sources: &HashSet<PathBuf>) -> Result<(), String> {
    let action = request["action"].as_str().unwrap_or_default();
    if !matches!(action, "probe" | "prepare_preview" | "run" | "create_project" | "relink_source") {
        return Ok(());
    }
    if action == "run" && request["source"].is_null() && request["job_id"].as_str().is_some_and(|id| !id.is_empty()) {
        return Ok(());
    }
    let source = request["source"].as_str().and_then(|path| canonical_local_file(Path::new(path)))
        .filter(|path| sources.contains(path))
        .ok_or("Hãy chọn video nguồn bằng hộp thoại hoặc kéo và thả tệp.")?;
    request["source"] = Value::String(path_string(&source)?);
    Ok(())
}

fn setting<'a>(request: &'a Value, name: &str) -> &'a str {
    request["settings"][name].as_str().unwrap_or_default().trim()
}

fn validate_settings(request: &Value) -> Result<(), String> {
    for (key, label) in [("python_path", "Python"), ("ffmpeg_path", "FFmpeg"), ("ffprobe_path", "FFprobe")] {
        let value = setting(request, key);
        let path = Path::new(value);
        if !value.is_empty() && (!local_path(path) || (cfg!(windows) && !has_extension(path, &["exe"]))) {
            return Err(format!("{label}: hãy nhập đường dẫn tuyệt đối đến tệp thực thi cục bộ."));
        }
    }
    for (key, label) in [("tts_model_path", "Mô hình VieNeu-TTS"), ("output_dir", "Thư mục xuất"), ("projects_dir", "Thư mục dự án")] {
        let value = setting(request, key);
        if !value.is_empty() && !local_path(Path::new(value)) {
            return Err(format!("{label}: hãy nhập đường dẫn tuyệt đối trên ổ đĩa cục bộ."));
        }
    }
    Ok(())
}

fn venv_python(data_dir: &Path) -> PathBuf {
    if cfg!(windows) {
        data_dir.join("venv").join("Scripts").join("python.exe")
    } else {
        data_dir.join("venv").join("bin").join("python")
    }
}

fn python_command(explicit: &str, venv: &Path) -> Result<Command, String> {
    if let Some(root) = crate::profile::dev_root() {
        let managed = canonical_local_file(&venv_python(&root)).ok_or(BOOTSTRAP)?;
        let canonical_root = dunce::canonicalize(&root).map_err(|error| error.to_string())?;
        if !managed.starts_with(canonical_root)
            || (!explicit.is_empty() && canonical_local_file(Path::new(explicit)).as_ref() != Some(&managed)) {
            return Err("Python phát triển phải dùng môi trường .dev/venv của dự án.".into());
        }
        return Ok(Command::new(managed));
    }
    if !explicit.is_empty() {
        let path = canonical_local_file(Path::new(explicit)).ok_or("Không tìm thấy tệp thực thi Python đã cấu hình.")?;
        if cfg!(windows) && !has_extension(&path, &["exe"]) {
            return Err("Đường dẫn Python phải trỏ đến tệp .exe.".to_owned());
        }
        return Ok(Command::new(path));
    }
    if venv.is_file() {
        return Ok(Command::new(venv));
    }
    Err(BOOTSTRAP.to_owned())
}

fn engine_dir(app: &AppHandle) -> Result<PathBuf, String> {
    #[cfg(debug_assertions)]
    {
        let source = Path::new(env!("CARGO_MANIFEST_DIR")).join("../engine");
        if source.join("dubbing/__main__.py").is_file() {
            return dunce::canonicalize(source).map_err(|error| error.to_string());
        }
    }
    let resource = app.path().resource_dir().map_err(|error| error.to_string())?.join("engine");
    if !resource.join("dubbing/__main__.py").is_file() {
        return Err("Thiếu Python Engine trong tài nguyên ứng dụng. Hãy cài lại ứng dụng.".to_owned());
    }
    Ok(resource)
}

fn execute(app: &AppHandle, mut command: Command, payload: Vec<u8>, control: &Control, timeout: Duration) -> Result<Value, String> {
    if control.cancelled.load(Ordering::Acquire) {
        return Err(CANCELLED.to_owned());
    }
    let mut process = EngineProcess::spawn(&mut command)
        .map_err(|error| format!("Không thể khởi động Python Engine: {error}"))?;
    let mut stdin = process.child.stdin.take().expect("piped stdin");
    let stdout = process.child.stdout.take().expect("piped stdout");
    let stderr = process.child.stderr.take().expect("piped stderr");
    let write = thread::spawn(move || stdin.write_all(&payload));
    let errors = thread::spawn(move || stderr_tail(stderr));
    let (send, receive) = mpsc::sync_channel(64);
    let output = thread::spawn(move || {
        for line in BufReader::new(stdout).lines() {
            let unreadable = line.is_err();
            if send.send(line).is_err() || unreadable {
                break;
            }
        }
    });

    let started = Instant::now();
    let mut cancelled_at = None;
    let mut terminal = None;
    let mut closed = false;
    let mut status = None;
    let read_result = loop {
        if status.is_none() {
            status = match process.try_wait() {
                Ok(status) => status,
                Err(error) => break Err(format!("Không thể theo dõi Python Engine: {error}")),
            };
        }
        if status.is_some() && closed {
            break Ok(());
        }
        if started.elapsed() >= timeout {
            break Err("Python Engine đã vượt quá thời gian xử lý cho phép.".to_owned());
        }
        if control.cancelled.load(Ordering::Acquire)
            && cancelled_at.get_or_insert_with(Instant::now).elapsed() >= CANCEL_GRACE
        {
            break Err(CANCELLED.to_owned());
        }
        if closed {
            thread::sleep(TICK);
            continue;
        }
        match receive.recv_timeout(TICK) {
            Ok(Ok(line)) => receive_line(app, &line, &mut terminal),
            Ok(Err(error)) => break Err(format!("Không thể đọc dữ liệu Python Engine: {error}")),
            Err(RecvTimeoutError::Disconnected) => closed = true,
            Err(RecvTimeoutError::Timeout) => {}
        }
    };
    drop(receive);
    drop(process);
    let _ = output.join();
    let write_result = write.join().unwrap_or_else(|_| Err(io::Error::other("Python stdin writer panicked")));
    let stderr = errors.join().unwrap_or_default();
    read_result?;
    if let Some(Err(error)) = terminal {
        return Err(error);
    }
    if let Some(Ok(data)) = terminal
        && status.is_some_and(|status| status.success())
    {
        return Ok(data);
    }
    if control.cancelled.load(Ordering::Acquire) {
        return Err(CANCELLED.to_owned());
    }
    let status = status.map(|code| code.to_string()).unwrap_or_default();
    if !stderr.trim().is_empty() {
        return Err(format!("Python Engine đã dừng ({status})\n{}", stderr.trim()));
    }
    write_result.map_err(|error| format!("Không thể gửi yêu cầu cho Python Engine: {error}"))?;
    Err(format!("Python Engine đã dừng mà không trả kết quả ({status})."))
}

fn receive_line(app: &AppHandle, line: &str, terminal: &mut Option<Result<Value, String>>) {
    let Ok(event) = serde_json::from_str::<Value>(line) else {
        if !line.trim().is_empty() {
            let message: String = line.chars().take(2000).collect();
            let _ = app.emit_to("main", "engine-event", json!({"type":"log", "level":"info", "message":message}));
        }
        return;
    };
    match event["type"].as_str() {
        Some("result") if terminal.is_none() => {
            *terminal = Some(event.get("data").cloned().ok_or_else(|| "Kết quả Python thiếu trường data.".to_owned()));
        }
        Some("error") if terminal.is_none() => {
            *terminal = Some(Err(event["message"].as_str().unwrap_or("Python Engine gặp lỗi.").to_owned()));
        }
        Some("progress" | "log") => {
            let _ = app.emit_to("main", "engine-event", event);
        }
        Some("project") => {
            if let Ok(data_dir) = crate::profile::data_dir(app) {
                register_result_media(&app.state::<LocalFiles>(), &data_dir, "project", &event["data"]);
            }
            let _ = app.emit_to("main", "engine-event", event);
        }
        _ => {}
    }
}

fn stderr_tail(mut reader: impl Read) -> String {
    const LIMIT: usize = 8192;
    let mut bytes = Vec::new();
    let mut buffer = [0; 4096];
    while let Ok(count) = reader.read(&mut buffer) {
        if count == 0 {
            break;
        }
        bytes.extend_from_slice(&buffer[..count]);
        if bytes.len() > LIMIT {
            bytes.drain(..bytes.len() - LIMIT);
        }
    }
    String::from_utf8_lossy(&bytes).into_owned()
}

fn register_result_media(files: &LocalFiles, data_dir: &Path, action: &str, result: &Value) {
    let Ok(mut media) = files.generated_media.lock() else { return; };
    let mut sources_to_approve = Vec::new();
    let app_root = dunce::canonicalize(data_dir).unwrap_or_else(|_| data_dir.to_path_buf());
    let mut register = |path: &Value, output: bool, project_root: Option<&Path>| {
        if let Some(path) = path.as_str().and_then(|path| canonical_local_file(Path::new(path)))
            && has_extension(&path, &["mp4", "mkv", "mov", "wav"])
            && (output || path.starts_with(&app_root) || project_root.is_some_and(|root| path.starts_with(root)))
        {
            media.insert(path);
        }
    };
    let jobs = match action {
        "prepare_preview" | "preview_voice" => {
            register(&result["path"], false, None);
            return;
        }
        "probe" => {
            register(&result["preview_path"], false, None);
            return;
        }
        "check" => return,
        "list_jobs" => result.as_array().map(Vec::as_slice).unwrap_or_default(),
        _ => std::slice::from_ref(result),
    };
    for job in jobs {
        let root = job["id"].as_str().and_then(|id| crate::projects::owned_project(data_dir, id).ok());
        let project_root = root.as_deref();
        let legacy = root.as_deref().is_none_or(|path| !path.join("project.json").is_file());
        register(&job["output"], legacy, project_root);
        if root.is_some() {
            register(&job["source"], true, project_root);
            if let Some(source) = job["source"].as_str().and_then(|value| canonical_local_file(Path::new(value)))
                && has_extension(&source, &["mp4", "mkv", "mov"])
            { sources_to_approve.push(source); }
        }
        for path in job["artifacts"].as_object().into_iter().flat_map(|artifacts| artifacts.values()) {
            register(path, false, project_root);
        }
        for exported in job["exports"].as_array().into_iter().flatten() {
            if let Some(path) = exported["path"].as_str()
                && crate::projects::owned_media_path(data_dir, job, Path::new(path)).is_some()
            { register(&exported["path"], false, project_root); }
        }
        for segment in job["segments"].as_array().into_iter().flatten() {
            register(&segment["audio_path"], false, project_root);
        }
    }
    drop(register);
    drop(media);
    if let Ok(mut approved) = files.sources.lock() { approved.extend(sources_to_approve); }
}

#[cfg(all(test, debug_assertions))]
mod profile_tests {
    use super::*;

    #[test]
    fn dev_python_cannot_execute_an_existing_global_binary() {
        let executable = std::env::current_exe().unwrap();
        assert!(python_command(executable.to_str().unwrap(), &PathBuf::from("missing-python")).is_err());
    }
}
