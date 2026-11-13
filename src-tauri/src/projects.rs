use crate::{canonical_local_file, has_extension, local_path, path_string};
use serde_json::{Value, json};
use std::{collections::HashSet, fs, path::{Path, PathBuf}, process::Command};

fn read_json(path: &Path) -> Result<Value, String> {
    let bytes = fs::read(path).map_err(|error| format!("Không đọc được dữ liệu dự án: {error}"))?;
    serde_json::from_slice(&bytes).map_err(|error| format!("Dữ liệu dự án không hợp lệ: {error}"))
}

fn valid_id(id: &str) -> bool {
    id.len() == 32 && id.bytes().all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn is_reparse(metadata: &fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        metadata.file_attributes() & 0x400 != 0
    }
    #[cfg(not(windows))]
    metadata.file_type().is_symlink()
}

fn plain_root(path: &Path) -> Result<PathBuf, String> {
    if !local_path(path) || path.parent().is_none() {
        return Err("Thư mục dự án phải nằm trên ổ đĩa cục bộ.".into());
    }
    let mut ancestor = Some(path);
    while let Some(item) = ancestor {
        let metadata = fs::symlink_metadata(item).map_err(|error| format!("Không kiểm tra được thư mục: {error}"))?;
        if is_reparse(&metadata) {
            return Err("Không thao tác trên thư mục dự án qua liên kết hoặc junction.".into());
        }
        ancestor = item.parent();
    }
    dunce::canonicalize(path).map_err(|error| format!("Không tìm thấy thư mục dự án: {error}"))
}

pub(crate) fn owned_project(data: &Path, id: &str) -> Result<PathBuf, String> {
    if !valid_id(id) { return Err("Mã dự án không hợp lệ.".into()); }
    let index_path = data.join("project-index.json");
    let index = if index_path.is_file() { read_json(&index_path)? } else { json!({"projects":{}}) };
    if let Some(path) = index["projects"][id].as_str() {
        let directory = plain_root(Path::new(path))?;
        #[cfg(debug_assertions)]
        if !directory.starts_with(plain_root(data)?) {
            return Err("Dự án phát triển phải nằm trong thư mục dữ liệu của dự án. Hãy sao chép dự án vào .dev/projects trước khi mở hoặc chỉnh sửa.".into());
        }
        let marker = read_json(&directory.join(".dubbing-project"))?;
        let manifest = read_json(&directory.join("project.json"))?;
        if marker["version"] != 1 || marker["id"] != id || manifest["id"] != id {
            return Err("Không xác nhận được thư mục thuộc dự án này.".into());
        }
        return Ok(directory);
    }
    if index["forgotten"].as_array().is_some_and(|items| items.contains(&json!(id))) {
        return Err("Dự án đã được gỡ khỏi danh sách.".into());
    }
    let directory = plain_root(&data.join("jobs").join(id))?;
    let manifest = read_json(&directory.join("job.json"))?;
    if manifest["id"] != id { return Err("Mã thư mục công việc không khớp.".into()); }
    Ok(directory)
}

pub(crate) fn owned_media_path(data: &Path, job: &Value, path: &Path) -> Option<PathBuf> {
    let root = owned_project(data, job["id"].as_str()?).ok()?;
    let canonical = canonical_local_file(path)?;
    canonical.starts_with(root).then_some(canonical)
}

pub(crate) fn unregister_project(data: &Path, id: &str) -> Result<(), String> {
    if !valid_id(id) { return Err("Mã dự án không hợp lệ.".into()); }
    let file = data.join("project-index.json");
    let mut index = if file.is_file() { read_json(&file)? } else { json!({"version":1,"projects":{}}) };
    if let Some(projects) = index["projects"].as_object_mut() { projects.remove(id); }
    if !index["forgotten"].is_array() { index["forgotten"] = json!([]); }
    let forgotten = index["forgotten"].as_array_mut().ok_or("Chỉ mục dự án không hợp lệ.")?;
    if !forgotten.contains(&json!(id)) { forgotten.push(json!(id)); }
    let temporary = file.with_extension("part.json");
    fs::write(&temporary, serde_json::to_vec_pretty(&index).map_err(|error| error.to_string())?)
        .and_then(|_| fs::rename(&temporary, &file))
        .map_err(|error| format!("Không cập nhật được danh sách dự án: {error}"))
}

pub(crate) fn require_approved_artifact(request: &mut Value, approved: &HashSet<PathBuf>) -> Result<(), String> {
    if request["action"] != "import_artifact" { return Ok(()); }
    let path = request["path"].as_str().and_then(|value| canonical_local_file(Path::new(value)))
        .filter(|path| approved.contains(path)).ok_or("Hãy chọn tệp thay thế bằng hộp thoại của ứng dụng.")?;
    let extensions: &[&str] = match request["stage"].as_str() {
        Some("extract" | "synthesize") => &["wav"],
        Some("transcribe" | "translate") => &["srt", "json"],
        _ => return Err("Bước này không nhận tệp thay thế.".into()),
    };
    if !has_extension(&path, extensions) { return Err("Định dạng tệp thay thế không phù hợp với bước đã chọn.".into()); }
    request["path"] = json!(path_string(&path)?);
    Ok(())
}

pub(crate) fn require_approved_directory(request: &mut Value, approved: &HashSet<PathBuf>) -> Result<(), String> {
    if request["action"] != "open_project" { return Ok(()); }
    let path = request["path"].as_str().filter(|value| local_path(Path::new(value)))
        .and_then(|value| dunce::canonicalize(value).ok()).filter(|path| path.is_dir() && approved.contains(path))
        .ok_or("Hãy chọn thư mục dự án bằng hộp thoại của ứng dụng.")?;
    request["path"] = json!(path_string(&path)?);
    Ok(())
}

fn plain_files(root: &Path) -> Result<Vec<PathBuf>, String> {
    let mut pending = vec![root.to_path_buf()];
    let mut files = Vec::new();
    while let Some(directory) = pending.pop() {
        for entry in fs::read_dir(&directory).map_err(|error| format!("Không đọc được thư mục dự án: {error}"))? {
            let path = entry.map_err(|error| error.to_string())?.path();
            let metadata = fs::symlink_metadata(&path).map_err(|error| error.to_string())?;
            if is_reparse(&metadata) { return Err("Dự án chứa liên kết hoặc junction; hãy xử lý liên kết trước khi xóa/dọn.".into()); }
            if metadata.is_dir() { pending.push(path); } else if metadata.is_file() { files.push(path); }
        }
    }
    Ok(files)
}

fn open_path(path: &Path) -> Result<(), String> {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        Command::new("explorer.exe").arg(path).creation_flags(0x08000000).spawn()
            .map(|_| ()).map_err(|error| format!("Không mở được tệp hoặc thư mục: {error}"))
    }
    #[cfg(not(windows))]
    Command::new("xdg-open").arg(path).spawn().map(|_| ()).map_err(|error| error.to_string())
}

fn recycle(root: &Path) -> Result<(), String> {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let script = "$ErrorActionPreference='Stop'; Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($env:DUBBING_PROJECT_DELETE, [Microsoft.VisualBasic.FileIO.UIOption]::OnlyErrorDialogs, [Microsoft.VisualBasic.FileIO.RecycleOption]::SendToRecycleBin, [Microsoft.VisualBasic.FileIO.UICancelOption]::ThrowException)";
        let output = Command::new("powershell.exe").args(["-NoProfile", "-NonInteractive", "-Command", script])
            .env("DUBBING_PROJECT_DELETE", root).creation_flags(0x08000000).output()
            .map_err(|error| format!("Không gửi được dự án vào Thùng rác: {error}"))?;
        if output.status.success() { Ok(()) } else { Err(format!("Chưa xóa được dự án: {}", String::from_utf8_lossy(&output.stderr).trim())) }
    }
    #[cfg(not(windows))]
    { let _ = root; Err("Xóa vào Thùng rác hiện hỗ trợ Windows.".into()) }
}

pub(crate) fn is_native_action(action: &str) -> bool {
    matches!(action, "open_project_folder" | "open_project_output" | "forget_project" | "delete_project" | "clean_project")
}

pub(crate) fn handle(data: &Path, action: &str, id: &str) -> Result<Value, String> {
    let directory = owned_project(data, id)?;
    match action {
        "open_project_folder" => open_path(&directory)?,
        "open_project_output" => {
            let file = if directory.join("project.json").is_file() { "project.json" } else { "job.json" };
            let manifest = read_json(&directory.join(file))?;
            let output = manifest["output"].as_str().ok_or("Dự án chưa có video đầu ra hiện tại.")?;
            let output = if Path::new(output).is_absolute() { PathBuf::from(output) } else { directory.join(output) };
            let output = canonical_local_file(&output).filter(|path| has_extension(path, &["mp4", "mkv", "mov"]))
                .ok_or("Không tìm thấy video đầu ra.")?;
            if file == "project.json" && !output.starts_with(&directory) { return Err("Video đầu ra không nằm trong dự án.".into()); }
            open_path(&output)?;
        }
        "forget_project" => unregister_project(data, id)?,
        "delete_project" => {
            let files = plain_files(&directory)?;
            if files.iter().any(|path| path.file_name().is_some_and(|name| name == ".dubbing-project") && path.parent() != Some(directory.as_path())) {
                return Err("Thư mục này chứa dự án khác. Hãy di chuyển dự án con ra ngoài trước khi xóa.".into());
            }
            let index_path = data.join("project-index.json");
            if index_path.is_file() {
                let index = read_json(&index_path)?;
                for (other_id, path) in index["projects"].as_object().into_iter().flatten() {
                    if other_id != id && path.as_str().and_then(|value| dunce::canonicalize(value).ok()).is_some_and(|path| path.starts_with(&directory)) {
                        return Err("Thư mục này chứa dữ liệu của dự án khác; chưa thể xóa.".into());
                    }
                }
            }
            recycle(&directory)?;
            unregister_project(data, id)?;
        }
        "clean_project" => {
            let files = plain_files(&directory)?;
            let manifest_name = if directory.join("project.json").is_file() { "project.json" } else { "job.json" };
            let manifest = read_json(&directory.join(manifest_name))?;
            let mut protected = HashSet::new();
            let mut protect = |value: &Value| {
                if let Some(value) = value.as_str() {
                    let path = if Path::new(value).is_absolute() { PathBuf::from(value) } else { directory.join(value) };
                    if let Some(path) = canonical_local_file(&path) { protected.insert(path); }
                }
            };
            protect(&manifest["source"]);
            protect(&manifest["output"]);
            for value in manifest["artifacts"].as_object().into_iter().flat_map(|items| items.values()) { protect(value); }
            for value in manifest["segments"].as_array().into_iter().flatten() { protect(&value["audio_path"]); }
            for value in manifest["exports"].as_array().into_iter().flatten() { protect(&value["path"]); }
            let mut removed = 0;
            for file in files {
                let name = file.file_name().and_then(|name| name.to_str()).unwrap_or_default();
                let folder = file.parent().and_then(|path| path.file_name()).and_then(|value| value.to_str()).unwrap_or_default();
                let generated_folder = matches!(folder, "02-audio" | "03-transcript" | "04-translation" | "05-voice" | "06-aligned");
                let scratch_suffix = [".part.wav", ".part.srt", ".part.json"].iter().any(|suffix| name.ends_with(suffix));
                let mux_scratch = folder == "07-output" && name.starts_with(&format!(".dub-{id}-")) && has_extension(&file, &["mp4", "mkv"]);
                let canonical = dunce::canonicalize(&file).map_err(|error| error.to_string())?;
                if !protected.contains(&canonical) && ((generated_folder && scratch_suffix) || mux_scratch) {
                    fs::remove_file(&file).map_err(|error| format!("Không dọn được tệp tạm: {error}"))?;
                    removed += 1;
                }
            }
            return Ok(json!({"path":path_string(&directory)?,"removed":removed}));
        }
        _ => return Err("Thao tác dự án không hợp lệ.".into()),
    }
    Ok(json!({"path":path_string(&directory)?}))
}
