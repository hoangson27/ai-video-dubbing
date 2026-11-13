mod engine;
mod media;
mod process;
mod projects;
mod profile;

#[cfg(test)]
mod tests;

use std::{
    collections::HashSet,
    path::{Path, PathBuf},
    sync::Mutex,
};
use tauri::{AppHandle, DragDropEvent, Emitter, Manager, WebviewEvent};
use tauri_plugin_dialog::DialogExt;

#[derive(Default)]
struct LocalFiles {
    sources: Mutex<HashSet<PathBuf>>,
    artifacts: Mutex<HashSet<PathBuf>>,
    directories: Mutex<HashSet<PathBuf>>,
    generated_media: Mutex<HashSet<PathBuf>>,
    media_urls: Mutex<Vec<PathBuf>>,
}

fn local_path(path: &Path) -> bool {
    if !path.is_absolute() {
        return false;
    }
    #[cfg(windows)]
    {
        use std::path::{Component, Prefix};
        matches!(
            path.components().next(),
            Some(Component::Prefix(prefix))
                if matches!(prefix.kind(), Prefix::Disk(_) | Prefix::VerbatimDisk(_))
        )
    }
    #[cfg(not(windows))]
    true
}

fn canonical_local_file(path: &Path) -> Option<PathBuf> {
    if !local_path(path) {
        return None;
    }
    let canonical = dunce::canonicalize(path).ok()?;
    (local_path(&canonical) && canonical.is_file()).then_some(canonical)
}

fn path_string(path: &Path) -> Result<String, String> {
    path.to_str()
        .map(str::to_owned)
        .ok_or_else(|| "Đường dẫn có ký tự không được hỗ trợ.".to_owned())
}

fn has_extension(path: &Path, extensions: &[&str]) -> bool {
    path.extension().and_then(|ext| ext.to_str()).is_some_and(|ext| {
        extensions.iter().any(|allowed| ext.eq_ignore_ascii_case(allowed))
    })
}

fn approve_video(app: &AppHandle, path: &Path) -> Result<String, String> {
    let path = canonical_local_file(path)
        .ok_or("Không tìm thấy tệp video trên ổ đĩa cục bộ.")?;
    if !has_extension(&path, &["mp4", "mkv", "mov"]) {
        return Err("Chỉ hỗ trợ tệp video MP4, MKV hoặc MOV.".to_owned());
    }
    let text = path_string(&path)?;
    app.state::<LocalFiles>()
        .sources
        .lock()
        .map_err(|_| "Danh sách video hiện không khả dụng.")?
        .insert(path);
    Ok(text)
}

#[tauri::command]
async fn pick_video(app: AppHandle) -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        app.dialog()
            .file()
            .set_title("Chọn video nguồn")
            .add_filter("Video", &["mp4", "mkv", "mov"])
            .blocking_pick_file()
            .map(|file| {
                let path = file.into_path().map_err(|error| error.to_string())?;
                approve_video(&app, &path)
            })
            .transpose()
    })
    .await
    .map_err(|error| error.to_string())?
}

#[tauri::command]
async fn pick_artifact(app: AppHandle, kind: String) -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let extensions: &[&str] = match kind.as_str() {
            "audio" => &["wav"],
            "transcript" | "translation" => &["srt", "json"],
            _ => return Err("Loại tệp thay thế không hợp lệ.".to_owned()),
        };
        app.dialog().file().set_title("Chọn kết quả thay thế").add_filter("Tệp kết quả", extensions)
            .blocking_pick_file().map(|file| {
                let path = file.into_path().map_err(|error| error.to_string())?;
                let path = canonical_local_file(&path).filter(|path| has_extension(path, extensions))
                    .ok_or("Hãy chọn tệp kết quả phù hợp trên ổ đĩa cục bộ.")?;
                let value = path_string(&path)?;
                app.state::<LocalFiles>().artifacts.lock().map_err(|_| "Không lưu được tệp đã chọn.")?.insert(path);
                Ok(value)
            }).transpose()
    }).await.map_err(|error| error.to_string())?
}

#[tauri::command]
async fn pick_directory(app: AppHandle) -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        app.dialog()
            .file()
            .set_title("Chọn thư mục lưu dự án hoặc video")
            .blocking_pick_folder()
            .map(|folder| {
                let path = folder.into_path().map_err(|error| error.to_string())?;
                if !local_path(&path) {
                    return Err("Hãy chọn thư mục trên ổ đĩa cục bộ.".to_owned());
                }
                let path = dunce::canonicalize(&path).map_err(|error| error.to_string())?;
                if !local_path(&path) || !path.is_dir() {
                    return Err("Hãy chọn thư mục trên ổ đĩa cục bộ.".to_owned());
                }
                app.state::<LocalFiles>().directories.lock().map_err(|_| "Không lưu được thư mục đã chọn.")?.insert(path.clone());
                path_string(&path)
            })
            .transpose()
    })
    .await
    .map_err(|error| error.to_string())?
}

pub fn run() {
    let mut context = tauri::generate_context!();
    if profile::dev_root().is_some() {
        for window in &mut context.config_mut().app.windows {
            if window.label == "main" { window.create = false; }
        }
    }
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .manage(LocalFiles::default())
        .manage(engine::EngineState::default())
        .setup(|app| {
            if let Some(root) = profile::dev_root() {
                let directory = root.join("webview");
                std::fs::create_dir_all(&directory)?;
                let config = app.config().app.windows.iter().find(|window| window.label == "main")
                    .ok_or("Thiếu cấu hình cửa sổ ứng dụng.")?;
                tauri::WebviewWindowBuilder::from_config(app, config)?.data_directory(directory).build()?;
            }
            Ok(())
        })
        .on_webview_event(|webview, event| {
            if webview.label() == "main"
                && let WebviewEvent::DragDrop(DragDropEvent::Drop { paths, .. }) = event
            {
                let app = webview.app_handle().clone();
                let paths = paths.clone();
                tauri::async_runtime::spawn_blocking(move || {
                    let result = match paths.as_slice() {
                        [path] => approve_video(&app, path),
                        _ => Err("Hãy thả một video mỗi lần.".to_owned()),
                    };
                    match result {
                        Ok(path) => {
                            let _ = app.emit_to("main", "video-dropped", path);
                        }
                        Err(error) => {
                            let _ = app.emit_to("main", "video-drop-error", error);
                        }
                    }
                });
            }
        })
        .register_asynchronous_uri_scheme_protocol(media::PROTOCOL, media::serve)
        .invoke_handler(tauri::generate_handler![
            pick_video,
            pick_artifact,
            pick_directory,
            media::media_url,
            engine::engine_request,
            engine::cancel_engine,
            engine::pause_engine,
        ])
        .build(context)
        .expect("could not start AI Video Dubbing");
    app.run(|app, event| {
        if matches!(event, tauri::RunEvent::ExitRequested { .. }) {
            engine::shutdown(app.state());
        }
    });
}
