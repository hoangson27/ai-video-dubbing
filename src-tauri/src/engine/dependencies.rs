use super::*;
use std::net::{SocketAddr, TcpStream};
use std::io::{Seek, SeekFrom};

const PATH_SETTINGS: [&str; 3] = ["python_path", "ffmpeg_path", "ffprobe_path"];

fn setup_log_path(data_dir: &Path) -> PathBuf {
    data_dir.join("logs").join("setup-latest.log")
}

pub(super) fn saved_setup_log(data_dir: &Path) -> Value {
    let path = setup_log_path(data_dir);
    let lines = (|| -> io::Result<Vec<String>> {
        let mut file = fs::File::open(&path)?;
        let size = file.metadata()?.len();
        let start = size.saturating_sub(128 * 1024);
        file.seek(SeekFrom::Start(start))?;
        let mut bytes = Vec::new();
        file.take(128 * 1024).read_to_end(&mut bytes)?;
        let text = String::from_utf8_lossy(&bytes);
        let mut lines: Vec<String> = text.lines().skip(usize::from(start > 0)).map(str::to_owned).collect();
        if lines.len() > 400 { lines.drain(..lines.len() - 400); }
        Ok(lines)
    })().unwrap_or_default();
    json!({"path":path.to_string_lossy(),"lines":lines})
}

fn write_setup_event(data_dir: &Path, event: Value) -> Result<(), String> {
    let path = setup_log_path(data_dir);
    let mut output = fs::OpenOptions::new().create(true).append(true).open(&path)
        .map_err(|error| format!("Không ghi được nhật ký {}: {error}", path.display()))?;
    writeln!(output, "{event}").map_err(|error| format!("Không ghi được nhật ký thiết lập: {error}"))
}

fn settings_patch(request: &mut Value, result: &Value) -> Result<Value, String> {
    let candidate = result["settings_patch"].as_object()
        .ok_or("Trình cài đặt không trả về cấu hình môi trường.")?;
    let mut patch = serde_json::Map::new();
    for key in PATH_SETTINGS {
        let Some(value) = candidate.get(key) else { continue; };
        let path = value.as_str().and_then(|value| canonical_local_file(Path::new(value)))
            .filter(|path| !cfg!(windows) || has_extension(path, &["exe"]))
            .ok_or_else(|| format!("Trình cài đặt trả về đường dẫn {key} không hợp lệ."))?;
        if request["settings"]["dev_mode"] == true
            && let Some(root) = crate::profile::dev_root() {
            let relative = match key {
                "python_path" => "venv/Scripts/python.exe",
                "ffmpeg_path" => "tools/ffmpeg/bin/ffmpeg.exe",
                "ffprobe_path" => "tools/ffmpeg/bin/ffprobe.exe",
                _ => unreachable!(),
            };
            let canonical_root = dunce::canonicalize(&root).map_err(|error| error.to_string())?;
            if !path.starts_with(canonical_root) || canonical_local_file(&root.join(relative)).as_ref() != Some(&path) {
                return Err(format!("Trình cài đặt phải dùng {key} trong môi trường .dev của dự án."));
            }
        }
        patch.insert(key.to_owned(), Value::String(path_string(&path)?));
    }
    if patch.is_empty() {
        return Err("Trình cài đặt chưa thiết lập được môi trường Python.".to_owned());
    }
    if !request["settings"].is_object() {
        request["settings"] = json!({});
    }
    for (key, value) in &patch {
        request["settings"][key] = value.clone();
    }
    Ok(Value::Object(patch))
}

fn script_command(app: &AppHandle, name: &str) -> Result<Command, String> {
    let engine = engine_dir(app)?;
    let scripts = engine.parent().ok_or("Không xác định được tài nguyên ứng dụng.")?.join("scripts");
    let script = canonical_local_file(&scripts.join(name))
        .ok_or_else(|| format!("Thiếu tài nguyên scripts/{name}. Hãy cài lại ứng dụng."))?;
    let system = std::env::var_os("SystemRoot").unwrap_or_else(|| r"C:\Windows".into());
    let executable = Path::new(&system).join("System32/WindowsPowerShell/v1.0/powershell.exe");
    let mut command = Command::new(executable);
    command.args(["-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"])
        .arg(script)
        .current_dir(scripts)
        .env_remove("PYLAUNCHER_ALLOW_INSTALL")
        .env_remove("PYLAUNCHER_ALWAYS_INSTALL")
        .env("PYTHON_MANAGER_AUTOMATIC_INSTALL", "false")
        .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
    crate::profile::configure_child(&mut command)?;
    Ok(command)
}

fn log(app: &AppHandle, level: &str, message: &str) {
    let _ = app.emit_to("main", "engine-event", json!({"type":"log", "level":level, "message":message}));
}

fn endpoint_running() -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], crate::profile::ollama_port()));
    TcpStream::connect_timeout(&address, Duration::from_millis(150)).is_ok()
}

pub(super) fn owned_ollama_alive(state: &EngineState) -> Result<bool, String> {
    let mut owned = state.ollama.lock().map_err(|_| "Không thể theo dõi dịch vụ Ollama.")?;
    if let Some(process) = owned.as_mut()
        && process.try_wait().map_err(|error| error.to_string())?.is_none() {
        return if crate::profile::dev_root().is_some() {
            Ok(listener_pid(SocketAddr::from(([127,0,0,1], crate::profile::ollama_port())))? == Some(process.child.id()))
        } else { Ok(true) };
    }
    *owned = None;
    Ok(false)
}

fn reusable_daemon(owned: &mut Option<EngineProcess>, address: SocketAddr, strict: bool) -> Result<bool, String> {
    if let Some(process) = owned.as_mut()
        && process.try_wait().map_err(|error| error.to_string())?.is_none() {
        if !strict || listener_pid(address)? == Some(process.child.id()) { return Ok(true); }
        return Err(format!("Ollama do ứng dụng quản lý chưa sở hữu cổng {}.", address.port()));
    }
    *owned = None;
    let running = if strict { listener_pid(address)?.is_some() }
        else { TcpStream::connect_timeout(&address, Duration::from_millis(150)).is_ok() };
    if running && strict {
        return Err(format!("Cổng Ollama {} đang được tiến trình khác sử dụng. Hãy đóng tiến trình đó trước khi thiết lập môi trường dự án.", address.port()));
    }
    Ok(running)
}

#[cfg(windows)]
fn listener_pid(address: SocketAddr) -> Result<Option<u32>, String> {
    // The listener PID is checked without sending a request to a potentially foreign daemon.
    #[link(name = "iphlpapi")]
    unsafe extern "system" {
        fn GetExtendedTcpTable(table: *mut std::ffi::c_void, size: *mut u32, ordered: i32, family: u32, class: u32, reserved: u32) -> u32;
    }
    let std::net::IpAddr::V4(ip) = address.ip() else { return Err("Ollama phải dùng IPv4 loopback.".into()); };
    let mut table = vec![0u32; 256];
    loop {
        let mut bytes = u32::try_from(table.len() * 4).map_err(|error| error.to_string())?;
        let status = unsafe { GetExtendedTcpTable(table.as_mut_ptr().cast(), &mut bytes, 0, 2, 3, 0) };
        if status == 122 { table.resize((bytes as usize).div_ceil(4), 0); continue; }
        if status != 0 { return Err(format!("Không xác định được tiến trình sở hữu cổng Ollama (Windows {status}).")); }
        let count = table[0] as usize;
        if count > (table.len() - 1) / 6 { return Err("Bảng cổng Ollama không hợp lệ.".into()); }
        for row in table[1..1 + count * 6].chunks_exact(6) {
            if u16::from_be(row[2] as u16) == address.port()
                && (row[1] == 0 || row[1] == u32::from_ne_bytes(ip.octets())) {
                return Ok(Some(row[5]));
            }
        }
        return Ok(None);
    }
}

#[cfg(not(windows))]
fn listener_pid(address: SocketAddr) -> Result<Option<u32>, String> {
    if TcpStream::connect_timeout(&address, Duration::from_millis(150)).is_ok() {
        return Err("Không xác định được tiến trình sở hữu cổng Ollama trên hệ điều hành này.".into());
    }
    Ok(None)
}

fn ollama_path(value: &Value) -> Result<PathBuf, String> {
    let path = value.as_str().and_then(|path| canonical_local_file(Path::new(path)))
        .filter(|path| !cfg!(windows) || has_extension(path, &["exe"]))
        .ok_or_else(|| "Không tìm thấy Ollama đã cài đặt trên ổ đĩa cục bộ.".to_owned())?;
    if let Some(root) = crate::profile::dev_root() {
        let expected = root.join("tools/ollama/ollama.exe");
        let canonical_root = dunce::canonicalize(&root).map_err(|error| error.to_string())?;
        let managed = canonical_local_file(&expected).filter(|path| path.starts_with(canonical_root));
        if managed.as_ref() != Some(&path) { return Err("Ollama phát triển phải nằm trong .dev/tools/ollama.".into()); }
    }
    Ok(path)
}

fn start_ollama(app: &AppHandle, data_dir: &Path, path: &Path, control: &Control) -> Result<(), String> {
    let state = app.state::<EngineState>();
    let mut owned = state.ollama.lock().map_err(|_| "Không thể theo dõi dịch vụ Ollama.")?;
    let strict = crate::profile::dev_root().is_some();
    let address = SocketAddr::from(([127,0,0,1], crate::profile::ollama_port()));
    if reusable_daemon(&mut owned, address, strict)? { return Ok(()); }
    if control.cancelled.load(Ordering::Acquire) {
        return Err(CANCELLED.to_owned());
    }
    log(app, "info", "Đang khởi động Ollama trên máy...");
    let output = fs::OpenOptions::new().create(true).append(true).open(data_dir.join("ollama.log"))
        .map_err(|error| format!("Không thể mở nhật ký Ollama: {error}"))?;
    let mut command = Command::new(path);
    command.arg("serve").env("OLLAMA_HOST", crate::profile::ollama_host()).env("OLLAMA_NO_CLOUD", "1")
        .stdin(Stdio::null()).stdout(Stdio::from(output.try_clone().map_err(|error| error.to_string())?))
        .stderr(Stdio::from(output));
    crate::profile::configure_child(&mut command)?;
    let mut process = EngineProcess::spawn(&mut command)
        .map_err(|error| format!("Không thể khởi động Ollama: {error}"))?;
    for _ in 0..100 {
        if control.cancelled.load(Ordering::Acquire) {
            return Err(CANCELLED.to_owned());
        }
        if let Some(status) = process.try_wait().map_err(|error| error.to_string())? {
            // A pre-existing Ollama process may have acquired the port concurrently.
            if !strict && endpoint_running() { return Ok(()); }
            return Err(format!("Ollama đã dừng ({status}). Xem nhật ký {}.", data_dir.join("ollama.log").display()));
        }
        let ready = if strict { listener_pid(address)? == Some(process.child.id()) } else { endpoint_running() };
        if ready {
            *owned = Some(process);
            return Ok(());
        }
        thread::sleep(TICK);
    }
    Err("Ollama chưa sẵn sàng. Xem ollama.log trong thư mục dữ liệu ứng dụng.".to_owned())
}

fn restart_managed_ollama(app: &AppHandle, data_dir: &Path, control: &Control) {
    let receipt = data_dir.join("managed-ollama.json");
    if !receipt.metadata().is_ok_and(|metadata| metadata.len() <= 16_384) { return; }
    let Some(path) = fs::read(&receipt).ok()
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
        .and_then(|value| ollama_path(&value["path"]).ok()) else { return; };
    if let Err(error) = start_ollama(app, data_dir, &path, control) {
        log(app, "warning", &error);
    }
}

pub(super) fn handle(
    app: &AppHandle, request: Value, action: &str, data_dir: &Path,
    control: &Control, timeout: Duration,
) -> Result<Value, String> {
    if action == "install_dependencies" {
        fs::create_dir_all(data_dir.join("logs")).map_err(|error| format!("Không tạo được thư mục nhật ký: {error}"))?;
        fs::write(setup_log_path(data_dir), b"").map_err(|error| format!("Không tạo được nhật ký thiết lập: {error}"))?;
        write_setup_event(data_dir, json!({"type":"log","level":"info","message":"Đang bắt đầu thiết lập tự động."}))?;
    }
    let mut result = handle_inner(app, request, action, data_dir, control, timeout);
    if action == "install_dependencies" {
        let event = match &result {
            Ok(report) if report["ready"] == true => json!({"type":"result","data":{"ready":true},"message":"Thiết lập hoàn tất. Hệ thống sẵn sàng."}),
            Ok(_) => json!({"type":"result","data":{"ready":false},"message":"Thiết lập chưa hoàn tất. Xem các thành phần chưa sẵn sàng."}),
            Err(error) => json!({"type":"error","message":error}),
        };
        let _ = write_setup_event(data_dir, event);
    }
    if let Ok(report) = result.as_mut() { report["setup_log"] = saved_setup_log(data_dir); }
    result
}

fn handle_inner(
    app: &AppHandle, mut request: Value, action: &str, data_dir: &Path,
    control: &Control, timeout: Duration,
) -> Result<Value, String> {
    request["engine_dir"] = Value::String(path_string(&engine_dir(app)?)?);
    let validation = validate_settings(&request).err();
    let mut patch = None;
    if action == "install_dependencies" {
        if let Some(error) = validation.as_ref() { return Err(error.clone()); }
        log(app, "info", "Đang chuẩn bị cài các thành phần còn thiếu...");
        let mut command = script_command(app, "install-deps.ps1")?;
        command.arg("-InstallMissing");
        let payload = serde_json::to_vec(&request).map_err(|error| error.to_string())?;
        let result = execute(app, command, payload, control, timeout)?;
        patch = Some(settings_patch(&mut request, &result)?);
        if !result["ollama_path"].is_null() {
            let path = ollama_path(&result["ollama_path"])?;
            fs::write(data_dir.join("managed-ollama.json"), serde_json::to_vec(&json!({"path": path_string(&path)?}))
                .map_err(|error| error.to_string())?).map_err(|error| format!("Không thể lưu cấu hình Ollama: {error}"))?;
            // Closing the setup Job also terminates any vendor post-install child process.
            thread::sleep(Duration::from_millis(300));
            start_ollama(app, data_dir, &path, control)?;
        }
        log(app, "info", "Đã cài đặt. Đang kiểm tra lại môi trường...");
    } else {
        // Only restart a service previously opted into by the install button.
        restart_managed_ollama(app, data_dir, control);
    }
    if control.cancelled.load(Ordering::Acquire) { return Err(CANCELLED.to_owned()); }
    // Invalid settings must still produce a complete inventory, without executing the invalid paths.
    if validation.is_some() {
        for key in ["python_path", "ffmpeg_path", "ffprobe_path", "tts_model_path", "output_dir"] {
            let path = setting(&request, key);
            if !path.is_empty() && (!local_path(Path::new(path))
                || (cfg!(windows) && PATH_SETTINGS.contains(&key) && !has_extension(Path::new(path), &["exe"])))
            {
                request["settings"][key] = Value::String(String::new());
            }
        }
    }
    request["action"] = Value::String("check".to_owned());
    request["settings"]["ollama_owned"] = json!(owned_ollama_alive(&app.state::<EngineState>())?);
    let payload = serde_json::to_vec(&request).map_err(|error| error.to_string())?;
    let mut report = execute(app, script_command(app, "dependency-inventory.ps1")?, payload, control, Duration::from_secs(300))?;
    if let Some(error) = validation {
        report["ready"] = Value::Bool(false);
        if let Some(checks) = report["checks"].as_array_mut() {
            checks.push(json!({"id":"settings", "name":"Cài đặt", "status":"incompatible", "required":true,"detail":error}));
        }
    }
    if let Some(patch) = patch { report["settings_patch"] = patch; }
    Ok(report)
}

pub(super) fn shutdown(state: &EngineState) {
    if let Ok(mut process) = state.ollama.lock() { *process = None; }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dev_daemon_cannot_reuse_an_unowned_listener() {
        let listener = std::net::TcpListener::bind(SocketAddr::from(([127,0,0,1], 0))).unwrap();
        let address = listener.local_addr().unwrap();
        let error = reusable_daemon(&mut None, address, true).unwrap_err();
        assert!(error.contains("Ollama"));
    }

    #[test]
    #[cfg(debug_assertions)]
    fn dev_installer_patch_rejects_existing_executables_outside_managed_tools() {
        let mut request = json!({"settings":{"dev_mode":true}});
        let result = json!({"settings_patch":{"python_path":std::env::current_exe().unwrap()}});
        assert!(settings_patch(&mut request, &result).is_err());
        assert!(request["settings"]["python_path"].is_null());
    }

    #[test]
    #[cfg(windows)]
    fn listener_owner_matches_the_actual_process_and_port() {
        let listener = std::net::TcpListener::bind(SocketAddr::from(([127,0,0,1], 0))).unwrap();
        let address = listener.local_addr().unwrap();
        assert_eq!(listener_pid(address).unwrap(), Some(std::process::id()));
        drop(listener);
        assert_eq!(listener_pid(address).unwrap(), None);
    }

    #[test]
    fn installer_patch_cannot_replace_user_model_or_output_choices() {
        let executable = std::env::current_exe().unwrap();
        let mut request = json!({"settings":{"ollama_model":"qwen2.5:7b", "output_dir":"chosen-output"}});
        let result = json!({"settings_patch":{"python_path":executable,"ollama_model":"untrusted-model","output_dir":"other-output"}});
        let patch = settings_patch(&mut request, &result).unwrap();
        assert_eq!(request["settings"]["ollama_model"], "qwen2.5:7b");
        assert_eq!(request["settings"]["output_dir"], "chosen-output");
        assert_eq!(patch.as_object().unwrap().len(), 1);
        assert!(request["settings"]["python_path"].as_str().unwrap().contains(executable.file_name().unwrap().to_str().unwrap()));
    }

    #[test]
    fn installer_patch_rejects_missing_or_relative_executables() {
        for candidate in [json!({}), json!({"python_path":"python.exe"}), json!({"python_path":42})] {
            assert!(settings_patch(&mut json!({"settings":{}}), &json!({"settings_patch":candidate})).is_err());
        }
    }

    #[test]
    fn saved_setup_log_is_bounded_and_keeps_latest_terminal_error() {
        let base = std::env::temp_dir().join(format!("dubbing-setup-log-{}-{}", std::process::id(), MARKER_SEQUENCE.fetch_add(1, Ordering::Relaxed)));
        fs::create_dir_all(base.join("logs")).unwrap();
        let path = setup_log_path(&base);
        let lines = (0..600).map(|number| format!("{{\"type\":\"log\",\"message\":\"line {number}\"}}\n")).collect::<String>();
        fs::write(&path, lines).unwrap();
        write_setup_event(&base, json!({"type":"error","message":"Original Python error"})).unwrap();
        let result = saved_setup_log(&base);
        assert_eq!(result["lines"].as_array().unwrap().len(), 400);
        assert!(result["lines"].as_array().unwrap().last().unwrap().as_str().unwrap().contains("Original Python error"));
        fs::remove_file(path).unwrap();
        fs::remove_dir(base.join("logs")).unwrap();
        fs::remove_dir(base).unwrap();
    }
}
