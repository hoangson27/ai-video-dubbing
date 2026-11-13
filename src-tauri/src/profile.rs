use crate::path_string;
use serde_json::{Value, json};
use std::{fs, path::{Path, PathBuf}, process::Command};
use tauri::{AppHandle, Manager};

pub fn dev_root() -> Option<PathBuf> {
    if cfg!(debug_assertions) {
        Some(Path::new(env!("CARGO_MANIFEST_DIR")).parent().expect("repository root").join(".dev"))
    } else {
        None
    }
}

pub fn data_dir(app: &AppHandle) -> Result<PathBuf, String> {
    dev_root().map(Ok).unwrap_or_else(|| app.path().app_local_data_dir().map_err(|error| error.to_string()))
}

pub fn ollama_port() -> u16 { if dev_root().is_some() { 11435 } else { 11434 } }

pub fn ollama_host() -> &'static str {
    if dev_root().is_some() { "127.0.0.1:11435" } else { "127.0.0.1:11434" }
}

pub fn apply_request(request: &mut Value, root: &Path, owned: bool) -> Result<(), String> {
    if !request["settings"].is_object() { request["settings"] = json!({}); }
    request["data_dir"] = json!(path_string(root)?);
    request["settings"]["dev_mode"] = json!(true);
    request["settings"]["dev_root"] = json!(path_string(root)?);
    for (key, relative) in [("python_path","venv/Scripts/python.exe"), ("ffmpeg_path","tools/ffmpeg/bin/ffmpeg.exe"),
        ("ffprobe_path","tools/ffmpeg/bin/ffprobe.exe"), ("tts_model_path","models/vieneu"),
        ("projects_dir","projects"), ("output_dir","output")] {
        request["settings"][key] = json!(path_string(&root.join(relative))?);
    }
    request["settings"]["ollama_host"] = json!("127.0.0.1:11435");
    request["settings"]["ollama_owned"] = json!(owned);
    Ok(())
}

pub fn apply_child(command: &mut Command, root: &Path) -> Result<(), String> {
    for (key, relative) in [("USERPROFILE","profile"), ("APPDATA","profile/AppData/Roaming"),
        ("LOCALAPPDATA","profile/AppData/Local"), ("OLLAMA_MODELS","models/ollama"),
        ("PIP_CACHE_DIR","cache/pip"), ("HF_HOME","cache/huggingface"), ("HF_HUB_CACHE","cache/huggingface/hub"),
        ("HUGGINGFACE_HUB_CACHE","cache/huggingface/hub"), ("TORCH_HOME","cache/torch"), ("TORCH_EXTENSIONS_DIR","cache/torch_extensions"),
        ("HF_ASSETS_CACHE","cache/huggingface/assets"), ("HUGGINGFACE_ASSETS_CACHE","cache/huggingface/assets"), ("HF_XET_CACHE","cache/huggingface/xet"),
        ("XDG_CACHE_HOME","cache"), ("UV_CACHE_DIR","cache/uv"), ("NUMBA_CACHE_DIR","cache/numba"),
        ("GRADIO_TEMP_DIR","cache/gradio"), ("PYTHONUSERBASE","profile/python"), ("PYTHONPYCACHEPREFIX","cache/pycache"),
        ("TEMP","temp"), ("TMP","temp")] {
        let directory = root.join(relative);
        fs::create_dir_all(&directory).map_err(|error| format!("Không tạo được {}: {error}", directory.display()))?;
        command.env(key, directory);
    }
    command.env("AIVD_DEV_MODE", "1").env("AIVD_DEV_ROOT", root)
        .env("OLLAMA_HOST", "127.0.0.1:11435").env("OLLAMA_NO_CLOUD", "1")
        .env("HF_TOKEN_PATH", root.join("cache/huggingface/token"))
        .env("PIP_CONFIG_FILE", "nul").env("PYTHON_MANAGER_AUTOMATIC_INSTALL", "false")
        .env("PYTHONNOUSERSITE", "1");
    for name in ["PYTHONHOME", "PYTHONPATH", "PYLAUNCHER_ALLOW_INSTALL", "PYLAUNCHER_ALWAYS_INSTALL", "PYLAUNCHER_DRYRUN",
        "PIP_TARGET", "PIP_PREFIX", "PIP_USER", "PIP_ROOT", "PIP_LOG", "PIP_BUILD_TRACKER", "PIP_PYTHON", "PIP_SRC", "TRANSFORMERS_CACHE"] {
        command.env_remove(name);
    }
    Ok(())
}

pub fn configure_child(command: &mut Command) -> Result<(), String> {
    if let Some(root) = dev_root() { apply_child(command, &root)?; }
    Ok(())
}
